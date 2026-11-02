"""The goal loop: ``orchestrate(feature)`` (docs/DESIGN.md section 12.2).

::

    acquire lock(feature)                         # ledger row; a second daemon exits
    until every contract is done:
      heartbeat (a released lock stops the daemon)
      for contract in topo_order(feature):          # depends_on edges
        patrol its open run (watchdog + gates)      # a finished session is gated, then transitioned
        done (in_review|approved|merged, or skipped by a human)? next
        waiting on a dependency, a decision or a human state? report, next
        defaults or capabilities block missing? 'contract incomplete' P1, next (never dispatched)
        third failure on this step? P1 decision, next
        capacity and budget allow? dispatch the one step its state maps to
      sleep(tick)

The loop is deterministic Python and its only state is the ledger and the lock row, so it can be
stopped and restarted at any point. It never sets the feature ``released`` and never merges
(``refinery`` merges only a PR GitHub already reports approved).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psycopg

from sdlc_graph import db
from sdlc_graph.ledger import decisions, evidence, nodes, transitions, workflow

from . import ACTOR, auth, gates, gitops, judge, store, watchdog
from .config import Config
from .dispatch import dispatch
from .steps import (CONFORMANCE_LOW, CONTRACT_INCOMPLETE, FAILURES, HUMAN_STATES, NEEDS_HUMAN, PERMISSION_DENIED,
                    done_states, step_for)


# --------------------------------------------------------------------------- graph


def contracts_of(conn: psycopg.Connection, feature: str) -> list[dict[str, Any]]:
    row = nodes.get_row(conn, feature)
    if row["kind"] != "feature":
        raise ValueError(f"{feature!r} is a {row['kind']}, not a feature")
    return [n["node"] for n in nodes.neighbours(conn, feature, rel="decomposes_into", direction="out")
            if n["node"]["kind"] == "contract"]


def topo_order(conn: psycopg.Connection, feature: str) -> list[str]:
    """The feature's contracts, dependencies first (``depends_on`` edges), ties by slug.

    Raises:
        ValueError: a dependency cycle among the contracts.
    """
    slugs = sorted(c["slug"] for c in contracts_of(conn, feature))
    deps = {s: sorted(n["node"]["slug"] for n in nodes.neighbours(conn, s, rel="depends_on", direction="out")
                      if n["node"]["slug"] in slugs) for s in slugs}
    order: list[str] = []
    ready = sorted(s for s in slugs if not deps[s])
    pending = {s: set(d) for s, d in deps.items() if d}
    while ready:
        s = ready.pop(0)
        order.append(s)
        for t in sorted(pending):
            pending[t].discard(s)
            if not pending[t]:
                del pending[t]
                ready.append(t)
        ready.sort()
    if pending:
        raise ValueError(f"depends_on cycle among {sorted(pending)}")
    return order


def dependencies(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    return [n["node"] for n in nodes.neighbours(conn, slug, rel="depends_on", direction="out") if n["node"]["kind"] == "contract"]


# --------------------------------------------------------------------------- failure accounting


def window_start(conn: psycopg.Connection, slug: str, step: str) -> str | None:
    """Failures count from when the node entered its state, or from the last resolved escalation
    of this step (a human answering 'retry' resets the count)."""
    import datetime as dt

    marks = [dt.datetime.fromisoformat(m) for m in (store.state_entered_at(conn, slug),) if m]
    resolved = store.resolved_escalations(conn, slug, step)
    if resolved:
        marks.append(dt.datetime.fromisoformat(resolved[0]["resolved_at"]))
    return max(marks).isoformat() if marks else None


def history(conn: psycopg.Connection, slug: str, step: str) -> dict[str, Any]:
    since = window_start(conn, slug, step)
    all_runs = store.step_runs(conn, slug, step, since=store.state_entered_at(conn, slug))
    window = store.step_runs(conn, slug, step, since=since)
    finished = [r for r in window if r.get("finished_at")]
    return {
        "since": since,
        "attempt": len(all_runs) + 1,
        "failures": [r for r in finished if r.get("outcome") in FAILURES],
        "conformance_lows": [r for r in finished if r.get("outcome") == CONFORMANCE_LOW],
        "last": finished[-1] if finished else None,
    }


# --------------------------------------------------------------------------- refinery


def refinery(conn: psycopg.Connection, slug: str, wt: str | None) -> dict[str, Any]:
    """Merge the contract's PR only when GitHub reports it approved (branch protection still
    applies on GitHub's side). The PR is re-derived from GitHub for the branch ``sdlc/<slug>`` and must
    live on the ``origin`` repository: a URL in the session's ``pr_url`` evidence is never merged.
    Records ``merge_sha`` evidence; the ledger's ``approved`` and ``merged`` transitions stay with a
    human or CI."""
    if not wt or not Path(wt).is_dir():
        return {"action": "refinery", "detail": "no worktree to find the pull request from"}
    info, why = gates.verified_pr(wt, slug)
    if info is None:
        return {"action": "refinery", "detail": f"not merging: {why}"}
    url = str(info["url"])
    gh = gates.gh_command()
    if info.get("state") == "OPEN" and info.get("reviewDecision") == "APPROVED":
        try:
            merged = subprocess.run([*gh, "pr", "merge", url, "--merge"], capture_output=True, text=True, timeout=120, cwd=wt,
                                    stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"action": "refinery", "detail": f"gh pr merge failed: {exc}"}
        if merged.returncode != 0:
            return {"action": "refinery", "detail": f"gh pr merge refused: {merged.stderr.strip()[:200]}"}
        again, why = gates.verified_pr(wt, slug)
        info = again or {}
    sha = ((info.get("mergeCommit") or {}).get("oid")) if info.get("state") == "MERGED" else None
    if sha and not any(e["payload"].get("sha") == sha for e in evidence.list_for(conn, slug, "merge_sha")):
        evidence.add(conn, slug, "merge_sha", {"sha": sha, "pr": url, "ingested_by": "orchestrator"})
        return {"action": "refinery", "detail": f"merged on GitHub ({sha[:12]}); a human or CI records approved/merged"}
    return {"action": "refinery", "detail": f"PR {info.get('state') or '?'}, review {info.get('reviewDecision') or 'none'}: not merging"}


# --------------------------------------------------------------------------- one tick


def _escalation_question(step: str, hist: dict[str, Any], kind: str) -> str:
    last = hist.get("last") or {}
    reasons = "; ".join((last.get("meta") or {}).get("reasons") or []) or last.get("outcome") or "unknown"
    if kind == "conformance":
        return (f"Conformance stayed below the threshold after {len(hist['conformance_lows'])} implement attempts. "
                f"Last: {reasons[:400]}. Retry implement, or skip the contract?")
    return (f"Step '{step}' failed {len(hist['failures'])} times (last: {last.get('outcome')}: {reasons[:400]}). "
            f"Retry with a fresh session, or skip the contract?")


def _prepare_retry(conn: psycopg.Connection, cfg: Config, slug: str, step: str, hist: dict[str, Any]) -> dict[str, Any]:
    """Decide where the next attempt starts. Returns ``{"start_commit", "feedback", "blocked"}``."""
    last = hist.get("last")
    if not last:
        return {"start_commit": None, "feedback": [], "blocked": None}
    meta = last.get("meta") or {}
    feedback = list(meta.get("reasons") or [])
    wt = meta.get("worktree")
    if last.get("outcome") == CONFORMANCE_LOW:
        return {"start_commit": None, "feedback": feedback, "blocked": None}   # keep the work, fix the findings
    if not wt or last.get("outcome") not in FAILURES:
        return {"start_commit": None, "feedback": feedback, "blocked": None}
    missing_key = f"{slug}:{step}:worktree-missing:{last['id']}"
    if not Path(wt).is_dir():
        if store.escalation_resolved(conn, slug, missing_key):   # a human answered: make a fresh worktree from the branch
            return {"start_commit": None, "feedback": feedback, "blocked": None}
        d = store.escalate(conn, slug, key=missing_key, severity="P1",
                           question=(f"The worktree {wt} is gone (removed by hand?) after a failed '{step}' run "
                                     f"(#{last['id']}, {last.get('outcome')}). Work that was only in it is lost; the branch "
                                     f"{gitops.branch_for(slug)} may still hold commits. Restore it or decide, then answer retry "
                                     f"(a new worktree is made from the branch) or skip."),
                           meta={"step": step, "worktree": wt, "run_id": last["id"]})
        return {"start_commit": None, "feedback": feedback, "blocked": d}
    start = last.get("start_commit")
    try:
        dirty = gitops.dirty_files(wt)
        fresh = gitops.new_dirty(wt, meta.get("dirty_at_start"))
        moved = bool(start) and gitops.head(wt) != start
    except gitops.GitError:
        dirty, fresh, moved = [], [], False
    # Never reset or delete uncommitted work: a session that died (crashed, stalled) leaving new
    # uncommitted changes, or one that moved HEAD over a dirty tree, goes to a human with the path.
    if (fresh and last.get("outcome") in ("crashed", "stalled")) or (moved and dirty):
        d = store.escalate(conn, slug, key=f"{slug}:{step}:dirty:{last['id']}", severity="P1",
                           question=(f"The worktree {wt} has uncommitted changes after a failed '{step}' run "
                                     f"(#{last['id']}, {last.get('outcome')}): {', '.join((fresh or dirty)[:10])}. It is never "
                                     f"reset or deleted automatically: inspect it, commit or discard by hand, then answer "
                                     f"retry or skip."),
                           meta={"step": step, "worktree": wt, "run_id": last["id"], "dirty": (fresh or dirty)[:50]})
        return {"start_commit": None, "feedback": feedback, "blocked": d}
    if moved:
        was = gitops.restore(wt, start, meta.get("start_snapshot"))
        # the tests that attempt froze went with its commits: they are not frozen for this one
        gates.supersede_attempt(conn, slug, last, f"run #{last['id']} ({last.get('outcome')}): its commits ({was[:12]}) "
                                                  f"were set aside; the retry starts again at {start[:12]}")
        feedback.append(f"the previous attempt's commits ({was[:12]}) were set aside; this attempt starts again at {start[:12]}")
    elif fresh:
        feedback.append(f"the previous attempt left uncommitted changes, kept for this attempt: {', '.join(fresh[:10])}")
    return {"start_commit": start, "feedback": feedback, "blocked": None}


def incomplete_blocks(node: dict[str, Any]) -> list[str]:
    """The autonomy blocks a contract lacks (design section 14): empty when it may be dispatched."""
    missing = [] if transitions.defaults_ok(node.get("attrs")) else ["## Defaults when ambiguous"]
    absent = transitions.capabilities_missing(node.get("capabilities"))
    if absent:
        missing.append("## Capabilities (" + ", ".join(absent) + ")")
    return missing


def _incomplete_escalation(conn: psycopg.Connection, slug: str, state: str, missing: list[str]) -> dict[str, Any]:
    return store.escalate(
        conn, slug, key=f"{slug}:contract-incomplete:{'+'.join(missing)}", severity="P1",
        question=(f"Contract incomplete: '{slug}' ({state}) has no {' and no '.join(missing)}, so an unattended session "
                  f"would have to ask or guess. The orchestrator does not dispatch it. Add the block(s) to the contract "
                  f"(python -m sdlc_graph.orchestrator.contract_blocks --slug {slug} --file <contract.md> --actor @you), "
                  f"then answer retry; or skip the contract."),
        meta={"step": "contract", "kind": CONTRACT_INCOMPLETE, "missing": missing})


def _error_escalation(conn: psycopg.Connection, slug: str, exc: BaseException) -> dict[str, Any]:
    """One contract's problem (a git error, a missing worktree, a database error) becomes a P1 decision keyed on
    the contract and the exception; the other contracts keep going."""
    from sdlc_graph.db import redact_secrets

    text = redact_secrets(str(exc)).strip().splitlines()[0] if str(exc).strip() else ""
    name = type(exc).__name__
    key = f"{slug}:error:{name}:{hashlib.sha1(redact_secrets(str(exc)).encode()).hexdigest()[:10]}"
    return store.escalate(conn, slug, key=key, severity="P1",
                          question=(f"The orchestrator hit {name} on '{slug}': {text[:500]}. Fix the cause (git state, the worktree, "
                                    f"the ledger), then answer retry, or skip the contract."),
                          meta={"step": "error", "error": name, "message": redact_secrets(str(exc))[:1000]})


def _budget(conn: psycopg.Connection, cfg: Config, feature: str, order: list[str], running: int) -> dict[str, Any] | None:
    """Over the feature's cost limit? Spend counts finished runs plus ``budget_usd`` reserved for every open run, so
    free slots are not all dispatched while spend still reads $0. Returns ``{"limit", "spent", "committed"}`` when
    over, else None."""
    limit = store.budget_limit(conn, feature, cfg.max_cost_usd)
    if limit is None:
        return None
    spent = store.feature_cost(conn, order)
    committed = spent + max(running, 0) * cfg.budget_usd
    return {"limit": limit, "spent": spent, "committed": committed} if committed >= limit else None


def tick(conn: psycopg.Connection, cfg: Config, feature: str) -> dict[str, Any]:
    """One patrol-and-dispatch pass over the feature. Returns the tick report.

    A problem in one contract (git, the filesystem, the database) escalates that contract and the pass goes on
    to the next; only a lost lock or an unreadable ledger ends it."""
    order = topo_order(conn, feature)
    done_set = done_states(cfg.refinery)
    feat = nodes.get_row(conn, feature)
    if feat["state"] == "ready" and order:
        transitions.transition(conn, feature, "building", ACTOR, reason="orchestrator started on its contracts")
    entries: list[dict[str, Any]] = []
    running = store.open_run_count(conn, order)
    stop_reason: str | None = None
    limit_now = store.budget_limit(conn, feature, cfg.max_cost_usd)
    if limit_now is not None and store.budget_stopped(conn, feature, limit_now):
        stop_reason = "budget_stop"   # a human answered 'stop'; patrol still finishes what is running below

    def contract(slug: str, entry: dict[str, Any]) -> None:
        nonlocal running
        still_running = False
        for run in store.open_runs(conn, slug):
            res = watchdog.patrol(conn, cfg, run)
            if res["status"] in ("running", "warned"):
                still_running = True
                entry.update(action="running" if res["status"] == "running" else "stall_warned",
                             run_id=run["id"], step=run["step"], attempt=run.get("attempt"))
            else:
                running -= 1
                v = res["verdict"]
                entry.setdefault("finished", []).append({"run_id": run["id"], "step": run["step"], "outcome": v.outcome,
                                                         "reasons": v.reasons})
                if v.outcome == NEEDS_HUMAN:
                    asked = gates.read_questions((run.get("meta") or {}).get("worktree", ""), slug) or "; ".join(v.reasons)
                    store.escalate(conn, slug, key=f"{slug}:{run['step']}:question:{run['id']}", severity="P1", options=[],
                                   question=f"The {run['step']} session stopped to ask: {asked[:1500]}  (answer in plain text; "
                                            f"the next session is told. Answer 'skip' to skip the contract.)",
                                   meta={"step": run["step"], "kind": "question", "run_id": run["id"]})
                elif v.outcome == PERMISSION_DENIED:
                    store.escalate(conn, slug, key=f"{slug}:{run['step']}:denied:{run['id']}", severity="P1",
                                   question=(f"{'; '.join(v.reasons)[:1800]} Nothing was retried. Add the rule where it says "
                                             f"(the contract's ## Capabilities, or .claude/sdlc.json), then answer retry; "
                                             f"or skip the contract."),
                                   meta={"step": run["step"], "kind": PERMISSION_DENIED, "run_id": run["id"],
                                         "denials": [{k: d.get(k) for k in ("tool", "target", "rule", "reason") if d.get(k)} for d in v.denials][:20],
                                         **({"reason": "guard_refused"} if any(d.get("reason") == "guard_refused" for d in v.denials) else {})})
                elif v.outcome == "unauthorized_transition":
                    store.escalate(conn, slug, key=f"{slug}:{run['step']}:unauthorized:{run['id']}", severity="P1",
                                   question=f"{'; '.join(v.reasons)[:600]} Review what the session did, then answer retry or skip.",
                                   meta={"step": run["step"], "run_id": run["id"]})
        node = nodes.get_row(conn, slug)
        entry["state"] = node["state"]
        if still_running:
            return
        if store.skipped(conn, slug):
            entry.update(action="skipped", done=True)
            return
        if node["state"] in done_set:
            entry.update(action="done", done=True)
            if cfg.refinery and node["state"] in ("in_review", "approved"):
                entry.update(refinery(conn, slug, str(gitops.worktree_path(cfg.repo, slug))), done=False)
            return
        if cfg.refinery and node["state"] in ("in_review", "approved"):
            entry.update(refinery(conn, slug, str(gitops.worktree_path(cfg.repo, slug))))
            return
        waiting = [d["slug"] for d in dependencies(conn, slug) if d["state"] not in done_set and not store.skipped(conn, d["slug"])]
        if waiting:
            entry.update(action="waiting_on_dependency", detail=waiting)
            return
        open_d = decisions.open_for(conn, slug)
        if open_d:
            entry.update(action="blocked_on_decision", detail=[{"id": d["id"], "question": d["question"]} for d in open_d])
            return
        if node["state"] in HUMAN_STATES:
            wf = workflow.next_step(conn, slug)
            detail = {"next_state": wf["next_state"], "missing": wf["missing"]}
            if node["state"] == "drafted" and incomplete_blocks(node):
                detail["contract_incomplete"] = incomplete_blocks(node)
            entry.update(action="waiting_on_human", detail=detail)
            return
        step = step_for(node["state"])
        if step is None:
            entry.update(action="nothing", detail=f"no orchestrated step for state {node['state']}")
            return
        missing_blocks = incomplete_blocks(node)
        if missing_blocks:
            d = _incomplete_escalation(conn, slug, node["state"], missing_blocks)
            entry.update(action=CONTRACT_INCOMPLETE, detail={"decision_id": d["id"], "severity": d["severity"],
                                                             "missing": missing_blocks})
            return
        hist = history(conn, slug, step.name)
        if len(hist["failures"]) >= cfg.max_failures:
            d = store.escalate(conn, slug, key=f"{slug}:{step.name}:failures:{hist['since']}", severity="P1",
                               question=_escalation_question(step.name, hist, "failures"),
                               meta={"step": step.name, "failures": [r["id"] for r in hist["failures"]],
                                     "worktree": str(gitops.worktree_path(cfg.repo, slug))})
            entry.update(action="escalated", detail={"decision_id": d["id"], "severity": d["severity"]})
            return
        if len(hist["conformance_lows"]) > cfg.conformance_reentries:
            d = store.escalate(conn, slug, key=f"{slug}:{step.name}:conformance:{hist['since']}", severity="P1",
                               question=_escalation_question(step.name, hist, "conformance"),
                               meta={"step": step.name, "runs": [r["id"] for r in hist["conformance_lows"]]})
            entry.update(action="escalated", detail={"decision_id": d["id"], "severity": d["severity"]})
            return
        if stop_reason:
            entry.update(action="stopped", detail="a human answered 'stop' to the budget question")
            return
        if running >= cfg.max_runs:
            entry.update(action="queued", detail=f"{running}/{cfg.max_runs} sessions running")
            return
        over = _budget(conn, cfg, feature, order, running)
        if over:
            d = store.escalate(conn, feature, key=f"{feature}:budget:{over['limit']:.2f}", severity="P2",
                               question=f"The feature's orchestrated runs have spent ${over['spent']:.2f} and ${over['committed'] - over['spent']:.2f} "
                                        f"is reserved for {running} open session(s) (limit ${over['limit']:.2f}). 'raise' doubles the limit, "
                                        f"'stop' ends the loop (a larger --max-cost also lifts it).",
                               options=["raise", "stop"], meta={"step": "budget", "limit": over["limit"], "raise_to": over["limit"] * 2})
            entry.update(action="budget_spent", detail={"decision_id": d["id"]})
            return
        prep = _prepare_retry(conn, cfg, slug, step.name, hist)
        if prep["blocked"]:
            entry.update(action="escalated", detail={"decision_id": prep["blocked"]["id"], "severity": "P1"})
            return
        feedback = prep["feedback"]
        if cfg.judge and len(hist["failures"]) == cfg.max_failures - 1:
            try:
                verdict = judge.judge(conn, slug, question=f"Step '{step.name}' failed twice. Retry it once more, or escalate?",
                                      options=["retry", "escalate"],
                                      context={"contract": nodes.get(conn, slug), "failures": hist["failures"]})
            except judge.ModelCallError as exc:
                verdict = {"choice": "retry", "reason": f"judge unavailable ({exc}); retrying", "escalate": False}
            if verdict["escalate"] or verdict["choice"] == "escalate":
                d = store.escalate(conn, slug, key=f"{slug}:{step.name}:judge:{hist['since']}", severity="P1",
                                   question=f"The judge escalated '{step.name}' after two failures: {verdict['reason']}",
                                   meta={"step": step.name})
                entry.update(action="escalated", detail={"decision_id": d["id"], "severity": "P1", "by": "judge"})
                return
            feedback.append(f"judge: {verdict['reason']}")
        run = dispatch(conn, cfg, slug, step, feature=feature, attempt=hist["attempt"], feedback=feedback,
                       start_commit=prep["start_commit"])
        if run.get("finished_at"):
            entry.update(action="dispatch_failed", detail=run.get("summary"))
            return
        running += 1
        entry.update(action="dispatched", run_id=run["id"], step=step.name, attempt=hist["attempt"], pid=run.get("pid"))

    for slug in order:
        entry: dict[str, Any] = {"slug": slug}
        entries.append(entry)
        if cfg.guard:
            cfg.guard()   # heartbeat + ownership: a daemon that lost the lock stops before touching the next contract
        try:
            contract(slug, entry)
        except store.LockLost:
            raise
        except Exception as exc:  # noqa: BLE001 - one contract's problem must not stop the others
            d = _error_escalation(conn, slug, exc)
            entry.update(action="error", detail={"error": type(exc).__name__, "decision_id": d["id"], "severity": d["severity"]})

    done = bool(entries) and all(e.get("done") for e in entries)
    return {"feature": feature, "done": done, "running": max(running, 0), "contracts": entries, "stop": stop_reason,
            "escalations": [{"id": d["id"], "slug": d["slug"], "severity": d["severity"], "question": d["question"]}
                            for d in store.open_escalations(conn, [feature, *order]) if d.get("severity")]}


# --------------------------------------------------------------------------- daemon


def _line(rep: dict[str, Any]) -> str:
    parts = [f"{e['slug']}={e.get('state')}:{e.get('action')}" for e in rep["contracts"]]
    return f"[orchestrate {rep['feature']}] " + " ".join(parts) + (" DONE" if rep["done"] else "")


class _Heartbeat(threading.Thread):
    """Refreshes the lock on its own connection while the main thread is busy in a slow gate or a git call."""

    def __init__(self, connect: Callable[[], psycopg.Connection], feature: str, owner: str, interval: float):
        super().__init__(daemon=True, name=f"heartbeat-{feature}")
        self._connect, self._feature, self._owner, self._interval = connect, feature, owner, interval
        self._halt = threading.Event()
        self.lost = False

    def run(self) -> None:
        try:
            conn = self._connect()
        except psycopg.Error:
            return
        try:
            while not self._halt.wait(self._interval):
                try:
                    store.heartbeat(conn, self._feature, self._owner)
                except store.LockLost:
                    self.lost = True
                    return
                except psycopg.Error:
                    continue
        finally:
            conn.close()

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=5)


def orchestrate(feature: str, cfg: Config, *, once: bool = False, max_ticks: int | None = None, owner: str | None = None,
                connect: Callable[[], psycopg.Connection] | None = None, quiet: bool = False) -> dict[str, Any]:
    """Run the goal loop for ``feature``.

    Returns ``{"feature", "owner", "ticks", "done", "stopped", "last"}``; ``stopped`` is ``done``,
    ``once``, ``max_ticks``, ``lock_lost``, ``budget_stop`` or ``signal``.

    The lock is kept fresh by a heartbeat thread (a tick with a slow gate outlasts three ticks), and every
    dispatch and every transition re-checks that the lock is still ours.

    Raises:
        store.LockHeld: another daemon holds a fresh lock. LookupError: unknown feature.
        ValueError: ``cfg.executor_mode`` is ``runner`` and no non-subscription credential is set.
    """
    source = auth.check_mode(cfg.executor_mode)       # ValueError (CLI exit 2) before anything is touched
    if cfg.executor_mode == "runner" and source == "oauth_token":
        print(f"orchestrate: executor runner, auth_source oauth_token ({auth.OAUTH_NOTE})", file=sys.stderr, flush=True)
    connect = connect or db.connect
    conn = connect()
    owner = owner or store.new_owner()
    stop_flag = {"signal": None}
    beat = _Heartbeat(connect, feature, owner, max(cfg.tick / 3, 0.02))
    old_guard = cfg.guard

    def guard() -> None:
        if beat.lost:
            raise store.LockLost(f"lost the lock on {feature!r}")
        store.heartbeat(conn, feature, owner)

    def _sig(signum, _frame):
        stop_flag["signal"] = signum

    old = {}
    if not once:
        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                old[s] = signal.signal(s, _sig)
            except ValueError:  # not the main thread
                pass
    ticks, last, stopped = 0, None, None
    try:
        topo_order(conn, feature)  # fail fast on an unknown feature or a cycle, before taking the lock
        store.acquire_lock(conn, feature, owner, cfg.tick, {"repo": str(cfg.repo), "refinery": cfg.refinery,
                                                            "max_runs": cfg.max_runs, "pid": os.getpid()})
        cfg.guard = guard
        beat.start()
        while True:
            try:
                guard()
                last = tick(conn, cfg, feature)
            except store.LockLost:
                stopped = "lock_lost"
                break
            ticks += 1
            if not quiet:
                print(_line(last), file=sys.stderr, flush=True)
            if last["done"]:
                stopped = "done"
                break
            if last.get("stop"):
                stopped = last["stop"]
                break
            if once:
                stopped = "once"
                break
            if max_ticks is not None and ticks >= max_ticks:
                stopped = "max_ticks"
                break
            deadline = time.monotonic() + cfg.tick
            while time.monotonic() < deadline and stop_flag["signal"] is None:
                time.sleep(min(0.2, cfg.tick))
            if stop_flag["signal"] is not None:
                stopped = "signal"
                break
    finally:
        cfg.guard = old_guard
        beat.stop()
        try:
            store.release_lock(conn, feature, owner)
        finally:
            for s, h in old.items():
                signal.signal(s, h)
            conn.close()
    return {"feature": feature, "owner": owner, "ticks": ticks, "done": bool(last and last["done"]),
            "stopped": stopped, "last": last}


def stop(conn: psycopg.Connection, feature: str) -> dict[str, Any]:
    """``--stop``: release the lock (the daemon exits at its next heartbeat). Worktrees and running
    sessions are left alone; the next ``orchestrate`` gates whatever the sessions left."""
    held = store.lock_status(conn, feature)
    released = store.release_lock(conn, feature)
    return {"feature": feature, "released": released, "was": held,
            "note": "worktrees are left in place; sessions still running finish and are gated by the next orchestrate"}
