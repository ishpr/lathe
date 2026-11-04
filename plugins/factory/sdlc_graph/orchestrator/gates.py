"""Gates: what the orchestrator checks after a session ends, before it calls ``transition``.

It does not trust the session's word. In order:

0. **ledger**: a transition on the node during the run by anything but the orchestrator or a human
   (a session that moved the state itself) fails the run as ``unauthorized_transition`` and escalates.
   "Already transitioned" is accepted only for a transition the orchestrator made for this run.
1. **drift**: ``git diff --name-only <start commit>`` plus untracked files, minus the uncommitted
   changes that were already there at dispatch, must be a subset of the contract's ``allowed_paths``;
   so must gitignored files that are new since dispatch, and a symlink may not resolve outside the
   worktree (``.claude/work/`` is exempt only for the files the orchestrator and the pack write).
   A violation saves the tree under ``refs/sdlc/discarded/run-<id>``, then resets the worktree to
   the start commit; the retry's prompt names the files.
2. **frozen tests** (every step after ``tests``): each file in the newest ``tests_frozen`` evidence
   hashes to its recorded sha256. A change resets like drift.
3. **expected evidence**: every kind the step expects has a row recorded since the run started, and
   the orchestrator checks it against disk, git and GitHub instead of reading it: ``tests`` needs each
   acceptance test file on disk, containing the test, listed in ``tests_frozen`` with its real sha256
   (and, with a test command, failing); ``verify`` re-runs the test command: ``--test-command``, else the stack's
   ``commands.test``; with neither and acceptance tests named, the step is refused (``require_test_command``);
   ``pr_url`` is the PR ``gh pr view sdlc/<slug>`` reports, on the origin repository.
4. **a change**: ``tests`` and ``implement`` must leave a change inside the allowed paths that this
   run made (HEAD moved, or uncommitted files differ from the dispatch snapshot).
5. **ratchet**: no ``ratchet`` (or ``verify_stamp.ratchet``) evidence since the start with ``new > 0``.
6. **conformance**: the session's assumptions (``.claude/work/<slug>/assumptions.md``, read by the
   orchestrator after every step and recorded as ``assumptions`` evidence when they change) must not
   contradict a resolved decision, must be well formed, and need a ``## Defaults when ambiguous`` block on
   the contract; a failure is ``conformance_low`` with the findings (and ``conformance`` evidence, score 0).
   Then, after ``implement``, the reviewer's score must reach the threshold; below it the loop re-enters
   implement with the findings (at most twice), then escalates.
7. ``transition`` to the step's target state as ``agent:orchestrator``; a guard refusal is a failure.

Before 1, the session's stream-json is read for permission denials (``autonomy.denials``). A step that was
denied a tool call is ``permission_denied`` (after the drift and frozen resets still run, so nothing out of
scope survives): not a failure to retry but an escalation naming the exact tool, command or path and the
rule that would have allowed it (design section 14: "the agent needed something it did not have" is a
contract defect). A denial of form (``autonomy.is_form``: a Bash command refused only for its shell shape,
or the commit guard's message rules) names no missing rule, so it does not decide the outcome: the gates
judge the step on its evidence, and the run records the denials with ``form: true``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from sdlc_graph.ledger import evidence, nodes, transitions

from sdlc_graph.ledger.transitions import actor_class

from . import ACTOR, CONFORMANCE_KIND, autonomy, gitops, judge
from .config import Config, clean_env
from .steps import CONFORMANCE_LOW, NEEDS_HUMAN, PERMISSION_DENIED, STEP_BY_NAME


@dataclass
class Verdict:
    outcome: str                              # "ok", "superseded", a FAILURES outcome, or conformance_low
    reasons: list[str] = field(default_factory=list)
    transitioned: bool = False
    transition: dict[str, Any] | None = None
    reset_from: str | None = None             # HEAD discarded by a drift/frozen reset
    conformance: dict[str, Any] | None = None
    evidence_ids: list[int] = field(default_factory=list)
    result: dict[str, Any] | None = None
    denials: list[dict[str, Any]] = field(default_factory=list)
    assumptions: dict[str, Any] | None = None
    chain_since: str | None = None            # a resumed step: the first attempt's start, from which evidence counts

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    def summary(self, step: str, attempt: int) -> str:
        head = f"{step} attempt {attempt}: {self.outcome}"
        bits = list(self.reasons)
        if self.transition and self.transition.get("ok"):
            bits.append(f"transition {self.transition.get('from_state')} -> {self.transition.get('to_state')}")
        if self.reset_from:
            bits.append(f"worktree reset (discarded {self.reset_from[:12]})")
        if self.conformance:
            bits.append(f"conformance {self.conformance['score']:.2f}")
        return head + ("; " + "; ".join(bits) if bits else "")


def read_result(run: dict[str, Any]) -> dict[str, Any] | None:
    path = (run.get("meta") or {}).get("result_file")
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _ts(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value))


def since(conn: psycopg.Connection, slug: str, kind: str, started_at: Any) -> list[dict[str, Any]]:
    t0 = _ts(started_at)
    return [e for e in evidence.list_for(conn, slug, kind) if _ts(e["at"]) >= t0]


def gh_command() -> list[str]:
    return shlex.split(os.environ.get("ORCHESTRATOR_GH") or "gh")


_REMOTE = re.compile(r"^(?:[a-z+]+://)?(?:[^@/\s]+@)?([^/:\s]+)[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
_PR_URL = re.compile(r"^https://([^/\s]+)/([^/\s]+)/([^/\s]+)/pull/\d+/?$", re.I)
PR_FIELDS = "url,headRefName,baseRefName,state,reviewDecision,mergeCommit"


def origin_repo(wt: str | Path) -> tuple[str, str, str] | None:
    """``(host, owner, repo)`` of the worktree's ``origin`` remote, lower-cased."""
    m = _REMOTE.match(gitops.git(wt, "remote", "get-url", "origin", check=False).strip())
    return (m[1].lower(), m[2].lower(), m[3].lower()) if m else None


def verified_pr(wt: str | Path, slug: str) -> tuple[dict[str, Any] | None, str]:
    """The PR GitHub reports for the contract's branch, or ``(None, why)``. The PR must live on the
    ``origin`` repository and its head must be ``sdlc/<slug>``: a URL the session wrote down is never
    used. Returns ``(info, "")`` with ``info`` the ``gh pr view --json`` object."""
    origin = origin_repo(wt)
    if origin is None:
        return None, "the worktree has no origin remote to check a pull request against"
    branch = gitops.branch_for(slug)
    try:
        proc = subprocess.run([*gh_command(), "pr", "view", branch, "--json", PR_FIELDS], cwd=wt, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)
        info = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return None, f"gh pr view {branch} failed: {exc}"
    if not isinstance(info, dict) or not info.get("url"):
        return None, f"GitHub reports no pull request for branch {branch}"
    m = _PR_URL.match(str(info["url"]))
    if not m or (m[1].lower(), m[2].lower(), m[3].lower()) != origin:
        return None, f"the pull request {info['url']} is not on the origin repository {'/'.join(origin)}"
    if info.get("headRefName") != branch:
        return None, f"the pull request {info['url']} has head {info.get('headRefName')!r}, not {branch!r}"
    return info, ""


def pr_from_github(wt: str | Path, slug: str) -> str | None:
    """The verified PR URL of the contract's branch (a fact, not the session's word), or None."""
    info, _ = verified_pr(wt, slug)
    return str(info["url"]) if info else None


def acceptance_tests(node: dict[str, Any]) -> list[str]:
    return [str(a["test"]) for a in node.get("acceptance") or [] if isinstance(a, dict) and a.get("test")]


def tests_unsubstantiated(conn: psycopg.Connection, node: dict[str, Any], wt: Path, started_at: Any) -> list[str]:
    """What is wrong with the ``tests`` step's claim: every acceptance test file must exist in the
    worktree, contain the test, and be listed in the newest ``tests_frozen`` with its actual sha256."""
    slug = node["slug"]
    rows = since(conn, slug, "tests_frozen", started_at)
    files = (max(rows, key=lambda r: (r["at"], r["id"]))["payload"].get("files") or {}) if rows else {}
    root = Path(os.path.realpath(wt))
    problems: list[str] = []
    for test in acceptance_tests(node):
        rel, _, name = test.partition("::")
        path = Path(os.path.realpath(wt / rel))
        if root not in path.parents or not path.is_file():
            problems.append(f"acceptance test file {rel} does not exist in the worktree")
            continue
        leaf = re.split(r"[\[]", name.split("::")[-1])[0] if name else ""
        if leaf and leaf not in path.read_text(errors="replace"):
            problems.append(f"{rel} does not contain the acceptance test {leaf}")
        if rel not in files:
            problems.append(f"{rel} is not listed in the tests_frozen evidence")
        elif files[rel] != gitops.sha256_file(path):
            problems.append(f"{rel}: the tests_frozen sha256 does not match the file on disk")
    return list(dict.fromkeys(problems))


def stack_test_command(wt: Path) -> list[str] | None:
    """The stack's own test command from the worktree's ``.claude/sdlc.json`` (``commands.test``), split into argv."""
    import shlex

    try:
        cfg = json.loads((wt / ".claude" / "sdlc.json").read_text(encoding="utf-8"))
        cmd = (cfg.get("commands") or {}).get("test")
        return shlex.split(cmd) if isinstance(cmd, str) and cmd.strip() and cmd.strip() != "true" else None
    except (OSError, ValueError, AttributeError):
        return None


def run_tests(cfg: Config, wt: Path, node: dict[str, Any], *, expect_pass: bool) -> str | None:
    """Run the test command on the acceptance tests in the worktree (a restricted environment): ``cfg.test_command``,
    else the stack's ``commands.test`` from ``.claude/sdlc.json``. Returns a problem description, or None when the
    result is what the step claims.

    With neither, and ``cfg.require_test_command`` (the default), the step is refused when the contract names acceptance
    tests: the session wrote the verify stamp and the tests_* evidence itself, so on its own word they prove nothing."""
    ids = acceptance_tests(node)
    cmd = cfg.test_command or stack_test_command(wt)
    if not cmd:
        if ids and cfg.require_test_command:
            return ("no test command to check the session's evidence with: pass --test-command, or set commands.test in "
                    ".claude/sdlc.json (the orchestrator will not take a verify stamp or failing-first tests on the session's word; "
                    "--no-require-tests turns this off)")
        return None
    if not ids:
        return None
    argv = [*cmd, *ids] if any("pytest" in w for w in cmd) else list(cmd)
    try:
        proc = subprocess.run(argv, cwd=wt, capture_output=True, text=True, timeout=cfg.test_timeout,
                              stdin=subprocess.DEVNULL, env={**clean_env(worktree=wt), "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired:
        return f"the test command timed out after {cfg.test_timeout:.0f}s"
    except OSError as exc:
        return f"the test command could not run: {exc}"
    if expect_pass and proc.returncode != 0:
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-3:]
        return f"the test command fails on the acceptance tests (exit {proc.returncode}): {' | '.join(tail)[:300]}"
    if not expect_pass and proc.returncode == 0:
        return "the acceptance tests pass before any implementation, so they prove nothing"
    return None


def foreign_transitions(conn: psycopg.Connection, slug: str, started_at: Any) -> list[dict[str, Any]]:
    """Transitions on the node since ``started_at`` made by a non-human other than the orchestrator."""
    rows = conn.execute(
        """SELECT t.* FROM transition t JOIN node n ON n.id = t.node_id
           WHERE n.slug = %s AND t.at >= %s::timestamptz AND t.actor <> %s ORDER BY t.id""",
        (slug, started_at, ACTOR)).fetchall()
    return [r for r in rows if actor_class(r["actor"]) != "human"]


def orchestrator_moved(conn: psycopg.Connection, slug: str, to_state: str, run_id: Any, started_at: Any) -> bool:
    """Whether the orchestrator itself made the transition to ``to_state`` for this run (restart recovery)."""
    row = conn.execute(
        """SELECT 1 FROM transition t JOIN node n ON n.id = t.node_id
           WHERE n.slug = %s AND t.to_state = %s AND t.actor = %s AND t.at >= %s::timestamptz
             AND t.reason LIKE %s LIMIT 1""",
        (slug, to_state, ACTOR, started_at, f"orchestrator run #{run_id} (%")).fetchone()
    return row is not None


def questions_file(wt: str | Path, slug: str) -> Path:
    return Path(wt) / ".claude" / "work" / slug / "questions.md"


def read_questions(wt: str | Path, slug: str) -> str | None:
    """The session's question for a human (dispatch moves any older file away, so a file here is new)."""
    try:
        text = questions_file(wt, slug).read_text(errors="replace").strip()
    except OSError:
        return None
    return text or None


def live_frozen(conn: psycopg.Connection, slug: str, wt: str | Path | None) -> dict[str, Any] | None:
    """The newest ``tests_frozen`` row that still describes the branch: not superseded (a ``superseded`` row
    names it: its attempt was set aside) and, when the worktree exists, on a commit reachable from its HEAD.
    None when there is none (nothing is frozen yet)."""
    rows = evidence.live(evidence.list_for(conn, slug))
    rows = [r for r in rows if r["kind"] == "tests_frozen"]
    if wt is not None and Path(wt).is_dir():
        rows = [r for r in rows if gitops.reachable(wt, str((r.get("payload") or {}).get("commit") or ""))]
    return max(rows, key=lambda r: (r["at"], r["id"])) if rows else None


def supersede_attempt(conn: psycopg.Connection, slug: str, run: dict[str, Any], reason: str) -> dict[str, Any] | None:
    """Mark the ``tests_failing_first`` and ``tests_frozen`` rows ``run`` recorded as superseded (one ``superseded``
    row naming them), because its commits were set aside. Returns that row, or None when it recorded none."""
    rows = [r for kind in evidence.SUPERSEDABLE for r in since(conn, slug, kind, run["started_at"])]
    gone = evidence.superseded_ids(evidence.list_for(conn, slug))
    ids = sorted({int(r["id"]) for r in rows} - gone)
    if not ids:
        return None
    return evidence.add(conn, slug, "superseded", {"ids": ids, "reason": reason, "run_id": run["id"],
                                                    "step": run.get("step"), "by": ACTOR})


def frozen_violations(conn: psycopg.Connection, slug: str, wt: str | Path) -> list[str]:
    row = live_frozen(conn, slug, wt)
    if row is None:
        return []
    files = row["payload"].get("files") or {}
    bad = []
    for path, sha in files.items():
        if "#" in path:  # pseudo-paths such as pyproject.toml#tool.pytest are the pack's to check
            continue
        got = gitops.sha256_file(Path(wt) / path)
        if got != sha:
            bad.append(f"{path} ({'deleted' if got is None else 'changed'})")
    return bad


def ratchet_new(conn: psycopg.Connection, slug: str, started_at: Any) -> int:
    worst = 0
    for e in since(conn, slug, "ratchet", started_at):
        worst = max(worst, int(e["payload"].get("new") or 0))
    for e in since(conn, slug, "verify_stamp", started_at):
        r = e["payload"].get("ratchet")
        if isinstance(r, dict):
            worst = max(worst, int(r.get("new") or 0))
    return worst


def _drift_verdict(conn: psycopg.Connection | None, wt: Path, run: dict[str, Any], start: str, snap: str | None, what: str,
                   files: list[str], ignored: list[str], result: dict[str, Any] | None, outcome: str = "drift") -> Verdict:
    label = f"run-{run['id']}"
    was = gitops.restore(wt, start, snap, label=label, extra=ignored)
    if conn is not None:
        supersede_attempt(conn, run["slug"], run, f"{what}: run #{run['id']} was reset to {start[:12]}")
    return Verdict(outcome, [f"{what}: {', '.join(files)}; reset to {start[:12]} (the tree before the reset is kept at "
                             f"refs/sdlc/discarded/{label})"], reset_from=was, result=result)


def evaluate(conn: psycopg.Connection, cfg: Config, run: dict[str, Any], *, crashed: bool = False) -> Verdict:
    """Run the gates for a session that has ended. Never raises for a gate failure.

    Denials of form (``autonomy.is_form``: a Bash command refused for its shell shape alone, the commit guard's
    message rules) do not decide the outcome: the step is judged on its evidence, and the verdict carries them
    (``denials``, each with ``form: true``, plus one reason line, which a failed step's retry prompt shows)."""
    seen: list[dict[str, Any]] = []
    v = _evaluate(conn, cfg, run, crashed=crashed, seen=seen)
    redone = [d for d in seen if d.get("form")]
    if redone and v.outcome != PERMISSION_DENIED:
        v.denials = v.denials or seen
        v.reasons.append(autonomy.redone_text(run["step"], redone))
    if v.chain_since is None:
        try:
            chain = resume_chain(conn, run["slug"], run["step"], run)
        except (KeyError, psycopg.Error):
            chain = []
        v.chain_since = chain[-1]["started_at"] if chain else None
    return v


#: Outcomes after which the worktree is kept as it is and the same step runs again: a human answered the session's
#: question, a refused call was reported, or an attempt changed nothing and recorded nothing. The next attempt
#: resumes the step.
RESUMABLE = frozenset({NEEDS_HUMAN, PERMISSION_DENIED, "no_change", "no_evidence"})


def resume_chain(conn: psycopg.Connection, slug: str, step: str, run: dict[str, Any]) -> list[dict[str, Any]]:
    """The finished earlier attempts of ``step`` that this run resumes, newest first: each ended in a
    :data:`RESUMABLE` outcome, reset nothing, and started from the same ledger state. A drift or frozen reset, a low
    conformance score or a transition in between ends the chain, because what came before it was discarded or
    judged. Empty when this run is a fresh attempt."""
    from . import store

    chain: list[dict[str, Any]] = []
    from_state = (run.get("meta") or {}).get("from_state")
    earlier = [r for r in store.step_runs(conn, slug, step) if int(r["id"]) < int(run["id"]) and r.get("finished_at")]
    for prev in reversed(earlier):
        pmeta = prev.get("meta") or {}
        # a conformance failure that came from assumption bookkeeping alone did not judge the work: resumable too
        bookkeeping = prev.get("outcome") == CONFORMANCE_LOW and (pmeta.get("conformance") or {}).get("source") == "assumptions"
        if (prev.get("outcome") not in RESUMABLE and not bookkeeping) or pmeta.get("reset_from") \
                or pmeta.get("from_state") != from_state:
            break
        chain.append(prev)
    return chain


def resumed_work(wt: Path, chain_start: str, dirty_at_chain_start: dict[str, Any], base: str, allowed: list[str],
                 slug: str) -> set[str]:
    """Files inside ``allowed`` that differ from ``chain_start`` (committed since, or uncommitted) and are not the same as
    on ``base``: the work the step's earlier attempts did, without what a merge of the base branch brought in and
    without files that were already dirty before the chain's first attempt and are untouched since (work a judged
    or discarded attempt left behind is not the resumed step's own)."""
    whole = set(gitops.changed_files(wt, chain_start, slug))
    try:
        # `git diff <base>` lists tracked files that differ from the base; a file the base does not have is work too
        differs_from_base = set(gitops._z(gitops.git(wt, "diff", "--name-only", "-z", base)))
        on_base = set(gitops._z(gitops.git(wt, "ls-tree", "-r", "--name-only", "-z", base)))
    except gitops.GitError:
        differs_from_base, on_base = whole, set()
    work = {f for f in whole if f in differs_from_base or f not in on_base}
    work = {f for f in work if not (f in dirty_at_chain_start and gitops.sha256_file(wt / f) == dirty_at_chain_start[f])}
    return {f for f in work if not gitops.outside([f], allowed)}


def _evaluate(conn: psycopg.Connection, cfg: Config, run: dict[str, Any], *, crashed: bool, seen: list[dict[str, Any]]) -> Verdict:
    step = STEP_BY_NAME[run["step"]]
    slug = run["slug"]
    meta = run.get("meta") or {}
    wt = Path(meta["worktree"])
    start = run["start_commit"]
    node = nodes.get_row(conn, slug)
    result = read_result(run)

    foreign = foreign_transitions(conn, slug, run["started_at"])
    if foreign:
        who = ", ".join(sorted({f"{t['actor']} ({t['from_state']} -> {t['to_state']})" for t in foreign}))
        return Verdict("unauthorized_transition", [f"the ledger state was moved during the run by {who}; only the orchestrator "
                                                   f"transitions, after its gates"], result=result)
    if node["state"] == step.to_state:
        if orchestrator_moved(conn, slug, step.to_state, run["id"], run["started_at"]):
            return Verdict("ok", ["already transitioned (recovered after a restart)"], transitioned=True, result=result)
        return Verdict("superseded", [f"the contract was moved to {node['state']} while the step ran"], result=result)
    if node["state"] != step.from_state:
        return Verdict("superseded", [f"the contract moved to {node['state']} while the step ran"], result=result)
    asked = read_questions(wt, slug)
    if asked:
        return Verdict(NEEDS_HUMAN, [f"the session stopped to ask a human: {asked[:400]}"], result=result)
    denied = autonomy.denials(run.get("log_path"), result, wt, since=run["started_at"])
    try:
        run_allowed = Path(str(meta.get("allowed_tools_file") or "")).read_text(encoding="utf-8").splitlines()
    except OSError:
        run_allowed = []
    for d in denied:
        if run_allowed:
            autonomy.name_missing_part(d, run_allowed)   # a chain denied for one unallowed part names that part's rule
        d["form"] = autonomy.is_form(d)
    blocking = [d for d in denied if not d["form"]]
    seen.extend(denied)
    if (crashed or result is None) and not blocking:
        return Verdict("crashed", ["the executor exited without a result file"], result=result)
    try:
        found = autonomy.ingest_assumptions(conn, slug, wt, run_id=run["id"], step=step.name, started_at=run["started_at"])
    except (OSError, ValueError) as exc:
        found = {"assumptions": [], "problems": [f"assumptions.md could not be recorded: {exc}"], "evidence_id": None}

    def denial(v: Verdict | None = None) -> Verdict:
        text = autonomy.denial_text(step.name, blocking)
        return Verdict(PERMISSION_DENIED, [text, *(v.reasons if v else [])], reset_from=v.reset_from if v else None,
                       result=result, denials=denied, assumptions=found)

    allowed = list((node.get("capabilities") or {}).get("paths") or [])
    chain = resume_chain(conn, slug, step.name, run)
    chain_since = chain[-1]["started_at"] if chain else None
    since_at = chain_since or run["started_at"]
    try:
        # uncommitted changes that were already there at dispatch and are untouched are not this session's, but
        # committing them is (the tests step commits the spec an escalated attempt left untracked)
        changed = gitops.session_changes(wt, start, meta.get("dirty_at_start"), slug, allowed)
        ignored = gitops.new_ignored(wt, meta.get("ignored_at_start"), slug) if "ignored_at_start" in meta else []
        escaping = gitops.escaping_links(wt, changed + ignored)
        moved = gitops.head(wt) != start or bool(gitops.new_dirty(wt, meta.get("dirty_at_start")))
        if chain:
            # A resumed step (a human answered, a refusal was redone, an attempt changed nothing) is judged on the
            # whole step's work: what the earlier attempts committed or left in the worktree inside allowed_paths
            # counts, and so does their evidence. Files that are the same as the base branch came from a merge of
            # it, not from a session. The console's second contract resumed its implement step with the work
            # already there, touched nothing, and was judged three times to have done nothing.
            first = chain[-1]
            kept = resumed_work(wt, first.get("start_commit") or start, (first.get("meta") or {}).get("dirty_at_start") or {},
                                meta.get("base") or "main", allowed, slug)
            changed = sorted(set(changed) | kept)
            moved = moved or bool(kept)
    except gitops.GitError as exc:
        return Verdict("executor_error", [str(exc)], result=result)

    out = sorted(set(gitops.outside(changed, allowed)) | set(gitops.outside(ignored, allowed)) | set(escaping))
    if out:
        what = "changed files outside allowed_paths" + (" or links leaving the worktree" if escaping else "")
        v = _drift_verdict(conn, wt, run, start, meta.get("start_snapshot"), what, out, gitops.outside(ignored, allowed), result)
        return denial(v) if blocking else v
    if step.frozen_check:
        bad = frozen_violations(conn, slug, wt)
        if bad:
            v = _drift_verdict(conn, wt, run, start, meta.get("start_snapshot"), "frozen tests changed", bad, [], result,
                               "frozen_changed")
            return denial(v) if blocking else v
    if blocking:
        return denial()

    reasons: list[str] = []
    ids: list[int] = []
    for kind in step.expects:
        rows = since(conn, slug, kind, since_at)
        if kind == "pr_url":
            info, why = verified_pr(wt, slug)
            if info is None:
                reasons.append(f"pr_url not confirmed by GitHub: {why}")
                continue
            url = str(info["url"]).rstrip("/")
            rows = [r for r in rows if str((r["payload"] or {}).get("url") or "").rstrip("/") == url]
            if not rows:
                rows = [evidence.add(conn, slug, "pr_url", {"url": url, "ingested_by": "orchestrator", "checked_with": "gh pr view"})]
        if rows:
            ids.append(int(rows[-1]["id"]))
        else:
            reasons.append(f"no {kind} evidence recorded during the run")
    if reasons:
        return Verdict("no_evidence", reasons, evidence_ids=ids, result=result)
    if step.needs_change and (not changed or not moved):
        return Verdict("no_change", [f"the {step.name} step left no change of its own inside allowed_paths"], result=result,
                       chain_since=chain_since)
    problems: list[str] = []
    if step.name == "tests":
        problems = tests_unsubstantiated(conn, node, wt, run["started_at"]) or frozen_violations(conn, slug, wt)
        problems = problems or [p for p in [run_tests(cfg, wt, node, expect_pass=False)] if p]
    elif step.name == "verify":
        stamp = since(conn, slug, "verify_stamp", run["started_at"])
        acc = (stamp[-1]["payload"].get("acceptance") if stamp else None) or {}
        if isinstance(acc, dict) and int(acc.get("failed") or 0) > 0:
            problems.append(f"the verify stamp reports {acc.get('failed')} failing acceptance tests")
        problems += [p for p in [run_tests(cfg, wt, node, expect_pass=True)] if p]
    if problems:
        return Verdict("unverified", problems, evidence_ids=ids, result=result)
    new = ratchet_new(conn, slug, run["started_at"])
    if new > 0:
        return Verdict("ratchet", [f"the ratchet reports {new} new finding(s)"], evidence_ids=ids, result=result)

    issues = autonomy.assumption_issues(conn, node, found, wt)
    if not issues and found.get("assumptions") and cfg.conformance and not step.conformance:
        try:
            issues = judge.assumptions_review(conn, cfg, slug, wt, found["assumptions"], step=step.name)
        except judge.ModelCallError as exc:
            return Verdict("executor_error", [f"assumption reviewer failed: {exc}"], result=result)
    if issues:
        findings = [{"criterion": "assumptions", "verdict": "violated", "detail": i} for i in issues]
        ev = evidence.add(conn, slug, CONFORMANCE_KIND, {
            "score": 0.0, "findings": findings, "threshold": cfg.conformance_threshold, "passed": False,
            "attempt": int(run.get("attempt") or 1), "source": "assumptions", "run_id": run["id"],
            "assumptions_evidence_id": found.get("evidence_id")})
        ids.append(int(ev["id"]))
        return Verdict(CONFORMANCE_LOW, [f"conformance (assumptions): {i}" for i in issues],
                       conformance={"score": 0.0, "findings": findings, "passed": False, "evidence_id": ev["id"],
                                    "threshold": cfg.conformance_threshold, "source": "assumptions"},
                       evidence_ids=ids, result=result, assumptions=found)

    conf = None
    if step.conformance and cfg.conformance:
        try:
            conf = judge.conformance(conn, cfg, slug, wt, start, attempt=int(run.get("attempt") or 1))
        except judge.ModelCallError as exc:
            return Verdict("executor_error", [f"conformance reviewer failed: {exc}"], result=result)
        ids.append(int(conf["evidence_id"]))
        if not conf["passed"]:
            findings = [f"{f.get('criterion')}: {f.get('verdict')}: {f.get('detail')}" for f in conf["findings"]
                        if isinstance(f, dict) and f.get("verdict") != "met"]
            return Verdict(CONFORMANCE_LOW, [f"conformance {conf['score']:.2f} < {cfg.conformance_threshold}", *findings],
                           conformance=conf, evidence_ids=ids, result=result)

    if cfg.guard:
        cfg.guard()   # the lock is still ours (raises LockLost): a daemon that lost it never transitions
    res = transitions.transition(conn, slug, step.to_state, ACTOR,
                                 reason=f"orchestrator run #{run['id']} ({step.name} attempt {run.get('attempt')}): gates passed")
    if not res.get("ok"):
        return Verdict("guard_refused", [f"transition to {step.to_state} refused: missing {', '.join(res.get('missing') or [])}"],
                       transition=res, conformance=conf, evidence_ids=ids, result=result)
    return Verdict("ok", list(found.get("notes") or []), transitioned=True, transition={**res, "from_state": step.from_state, "to_state": step.to_state},
                   conformance=conf, evidence_ids=ids, result=result)
