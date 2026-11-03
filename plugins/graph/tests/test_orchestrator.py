"""The orchestrator with the fake executor: loop, gates, drift reset, retry, stall escalation, lock.

No model is called: ``ORCHESTRATOR_EXECUTOR`` is ``fake_executor`` (it writes the expected evidence,
nothing, drift, or hangs, per ``ORCH_FAKE_PLAN``) and ``ORCHESTRATOR_CLAUDE`` is ``fake_claude`` for
the conformance reviewer. Every test builds its own git repo under tmp_path; worktrees go next to it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sdlc_graph import cli
from sdlc_graph.ledger import decisions, evidence, intake, nodes, transitions
from sdlc_graph.orchestrator import config, contract_blocks, dispatch, gitops, loop, steps, store

FAKE = f"{sys.executable} -I -m sdlc_graph.orchestrator.fake_executor"
FAKE_CLAUDE = f"{sys.executable} -I -m sdlc_graph.orchestrator.fake_claude"
FAKE_GH = f"{sys.executable} -I -m sdlc_graph.orchestrator.fake_gh"
FEATURE = "per-key-rate-limit"
A, B = "rate-limit", "rate-limit-headers"
GIT = ["-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *GIT, *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "sample-app"
    (r / "app").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "app" / "__init__.py").write_text("")
    (r / "app" / "main.py").write_text("APP = 'sample-app'\n")
    (r / "README.md").write_text("fixture\n")
    (r / ".gitignore").write_text(".claude/work/\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    _git(r, "remote", "add", "origin", "https://github.com/example/sample-app.git")
    return r


@pytest.fixture
def env(monkeypatch, tmp_path: Path):
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    monkeypatch.setenv("ORCHESTRATOR_EXECUTOR", FAKE)
    monkeypatch.setenv("ORCHESTRATOR_CLAUDE", FAKE_CLAUDE)
    monkeypatch.setenv("ORCHESTRATOR_GH", FAKE_GH)
    monkeypatch.setenv("ORCHESTRATOR_NOTIFY", "none")
    monkeypatch.setenv("ORCH_FAKE_PLAN", str(plan))
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(tmp_path / "claude-state"))
    monkeypatch.delenv("FAKE_CLAUDE_SCORES", raising=False)

    def set_plan(d: dict[str, str]) -> None:
        plan.write_text(json.dumps(d))

    return set_plan


def _contract(conn, slug: str, title: str, area: str, *, depends_on=None) -> None:
    res = intake.contract_draft(
        conn, slug=slug, title=title, repo="sample-app", actor="@ish", feature=FEATURE,
        ticket_text=f"{title}.", touched_symbols=[f"app/{area}/"], depends_on=depends_on,
        capabilities={"paths": [f"app/{area}/**", f"tests/{area}/**"]},
        acceptance=[{"id": "AC1", "text": f"{area} works", "test": f"tests/{area}/test_{area}.py::test_{area}_works"},
                    {"id": "AC2", "text": f"{area} refuses bad input", "test": f"tests/{area}/test_{area}.py::test_{area}_refuses"}],
        tier="low")
    assert res["status"] == "created", res
    blocks(conn, slug)
    assert transitions.transition(conn, slug, "ready", "@ish")["ok"]


DEFAULTS = ["Make the smallest change that satisfies the acceptance criteria.",
            "Follow the nearest existing pattern in the code being changed.",
            "Prefer the option the Decisions above imply.",
            "When none applies, choose the reversible option and record it in assumptions.md."]


def blocks(conn, slug: str, *, commands=None) -> None:
    """The contract's ## Defaults when ambiguous and ## Capabilities blocks (the ready guard requires both)."""
    contract_blocks.set_blocks(conn, slug, actor="@ish", defaults=DEFAULTS,
                               capabilities={"commands": list(commands or []), "egress": [], "data": []})


@pytest.fixture
def feature(clean_conn):
    nodes.create(clean_conn, kind="feature", slug=FEATURE, title="Per-key rate limit", repo="sample-app", created_by="@ish")
    _contract(clean_conn, A, "Per-key rate limiting at 100 requests a minute", "ratelimit")
    _contract(clean_conn, B, "Expose X-RateLimit headers on every response", "headers", depends_on=[A])
    return clean_conn


def cfg_for(repo: Path, **kw) -> config.Config:
    base = dict(repo=repo, tick=0.05, stall_minutes=0.01, max_runs=2, reviewer="@ish", require_test_command=False)
    base.update(kw)
    return config.Config(**base)


def tick_until(conn, cfg, pred, *, limit: int = 400):
    reps = []
    for _ in range(limit):
        reps.append(loop.orchestrate(FEATURE, cfg, once=True, quiet=True))
        if pred(reps[-1]):
            return reps
        time.sleep(0.05)
    raise AssertionError(f"condition not reached; last tick: {json.dumps(reps[-1]['last'], default=str)[:2000]}")


def state(conn, slug):
    return nodes.get_row(conn, slug)["state"]


def runs_of(conn, slug, step=None):
    return [r for r in store.step_runs(conn, slug, step) if r.get("step")] if step else conn.execute(
        "SELECT r.* FROM run r JOIN node n ON n.id = r.node_id WHERE n.slug = %s ORDER BY r.id", (slug,)).fetchall()


# --------------------------------------------------------------------------- the brief's "done looks like"


def test_walks_two_contracts_with_drift_reset_and_stall_escalation(feature, repo, env, capsys, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STACK", "1")   # B starts while A is in_review: stacked on A's branch
    conn = feature
    env({f"{A}:implement:1": "drift",
         f"{B}:tests:1": "hang", f"{B}:tests:2": "hang", f"{B}:tests:3": "hang"})
    args = ["orchestrate", FEATURE, "--once", "--repo", str(repo), "--tick", "0.05", "--stall-minutes", "0.01",
            "--reviewer", "@ish", "--quiet", "--json", "--no-require-tests"]

    # Phase 1: tick (through the CLI, one tick per process-like call) until B escalates.
    for _ in range(400):
        assert cli.main(args) == 0
        if store.open_escalations(conn, [B]):
            break
        time.sleep(0.05)
    capsys.readouterr()
    assert state(conn, A) == "in_review"
    esc = store.open_escalations(conn, [B])
    assert len(esc) == 1 and esc[0]["severity"] == "P1" and "failed 3 times" in esc[0]["question"]
    stalled = [r for r in store.step_runs(conn, B, "tests") if r["outcome"] == "stalled"]
    assert len(stalled) == 3
    assert state(conn, B) == "ready"

    # The drift on A's first implement attempt was reset to the start commit and retried.
    impl = store.step_runs(conn, A, "implement")
    assert [r["outcome"] for r in impl] == ["drift", "ok"]
    assert impl[0]["meta"]["reset_from"] and "drift/" in impl[0]["summary"]
    assert any("outside allowed_paths" in f for f in impl[1]["meta"]["feedback"])
    wt_a = gitops.worktree_path(repo, A)
    assert not (wt_a / "drift").exists()
    assert gitops.outside(gitops.changed_files(wt_a, _git(repo, "rev-parse", "main").strip()),
                          ["app/ratelimit/**", "tests/ratelimit/**"]) == []

    # Every guarded transition cites evidence; the gate-free one follows a conformance row.
    trans = conn.execute("SELECT t.* FROM transition t JOIN node n ON n.id = t.node_id WHERE n.slug = %s "
                         "AND t.actor = 'agent:orchestrator' ORDER BY t.id", (A,)).fetchall()
    assert [(t["from_state"], t["to_state"]) for t in trans] == [
        ("ready", "tests_frozen"), ("tests_frozen", "implementing"), ("implementing", "verified"), ("verified", "in_review")]
    for t in trans:
        if t["from_state"] != "tests_frozen":
            assert t["evidence_ids"], t
    kinds = {e["kind"] for e in evidence.list_for(conn, A)}
    assert {"tests_failing_first", "tests_frozen", "conformance", "verify_stamp", "pr_url"} <= kinds
    assert evidence.list_for(conn, A, "conformance")[0]["payload"]["score"] >= 0.8

    # A human answers the escalation; B retries with a fresh session and walks to in_review.
    decisions.resolve(conn, esc[0]["id"], "retry", actor="@ish")
    env({})
    for _ in range(400):
        rc = cli.main(args)
        assert rc == 0
        if state(conn, B) == "in_review":
            break
        time.sleep(0.05)
    capsys.readouterr()
    assert state(conn, B) == "in_review"
    tests_b = store.step_runs(conn, B, "tests")
    assert [r["outcome"] for r in tests_b] == ["stalled", "stalled", "stalled", "ok"]
    assert tests_b[-1]["attempt"] == 4

    # The feature is done in the default mode; the loop never sets it released.
    out = loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)
    assert out["done"] and out["stopped"] == "done"
    assert state(conn, FEATURE) not in ("released", "evaluated")
    # B stacked on A's branch (the one unmerged dependency, in_review, with --stack).
    meta = store.step_runs(conn, B, "tests")[0]["meta"]
    assert meta["base"] == gitops.branch_for(A) and meta["pr_base"] == gitops.branch_for(A) and meta["stacked_on"] == A
    # Run rows carry the orchestrator columns.
    r = store.step_runs(conn, A, "ship")[-1]
    assert r["session_id"] and r["pid"] and r["start_commit"] and r["log_path"] and r["summary"]
    assert r["requested_model"] == "sonnet" and r["last_assistant_message"].startswith("PR https://")


def test_lock_prevents_a_second_daemon(feature, repo, env):
    conn = feature
    holder = store.new_owner()
    store.acquire_lock(conn, FEATURE, holder, 60)
    with pytest.raises(store.LockHeld):
        loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)
    rc = cli.main(["orchestrate", FEATURE, "--once", "--repo", str(repo), "--quiet", "--json"])
    assert rc == 1
    assert store.step_runs(conn, A, "tests") == []          # the refused daemon dispatched nothing
    assert store.lock_status(conn, FEATURE)["owner"] == holder

    # A lock older than three of its holder's ticks is stale and is taken over.
    conn.execute("UPDATE orchestrator_lock SET heartbeat_at = now() - interval '10 minutes'")
    out = loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)
    assert out["ticks"] == 1
    assert store.lock_status(conn, FEATURE) is None          # released on exit


def test_stop_releases_the_lock_and_the_daemon_exits(feature, repo, env):
    conn = feature
    env({"*": "hang"})
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(loop.orchestrate(FEATURE, cfg_for(repo, stall_minutes=30), quiet=True)))
    t.start()
    for _ in range(100):
        if store.lock_status(conn, FEATURE) and store.open_runs(conn, A):
            break
        time.sleep(0.05)
    assert cli.main(["orchestrate", FEATURE, "--stop", "--json"]) == 0
    t.join(timeout=10)
    assert not t.is_alive() and out["stopped"] == "lock_lost"
    wt = gitops.worktree_path(repo, A)
    assert wt.is_dir()                                       # worktrees are left
    run = store.open_runs(conn, A)[0]
    from sdlc_graph.orchestrator import watchdog

    watchdog.stop(run["pid"])


# --------------------------------------------------------------------------- gates


def test_nothing_written_fails_three_times_then_p1(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "nothing"})
    tick_until(conn, cfg_for(repo), lambda r: bool(store.open_escalations(conn, [A])))
    outs = [r["outcome"] for r in store.step_runs(conn, A, "tests")]
    assert outs == ["no_evidence"] * 3
    assert "no tests_failing_first evidence" in store.step_runs(conn, A, "tests")[0]["summary"]
    assert state(conn, A) == "ready"


def test_frozen_test_edit_is_reset(feature, repo, env):
    conn = feature
    env({f"{A}:implement:1": "frozen_edit"})
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "implementing")
    impl = store.step_runs(conn, A, "implement")
    assert [r["outcome"] for r in impl] == ["frozen_changed", "ok"]
    frozen = evidence.list_for(conn, A, "tests_frozen")[-1]["payload"]["files"]
    wt = gitops.worktree_path(repo, A)
    for path, sha in frozen.items():
        assert gitops.sha256_file(wt / path) == sha


def test_parametrized_failing_first_ids_cover_the_acceptance_tests(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "ok+param_ids"})
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "tests_frozen")
    assert [r["outcome"] for r in store.step_runs(conn, A, "tests")] == ["ok"]
    assert all(t.endswith(("[3]", "[100]")) for t in evidence.list_for(conn, A, "tests_failing_first")[-1]["payload"]["tests"])


def test_a_refused_tests_attempt_set_aside_does_not_freeze_its_tests_for_the_retry(feature, repo, env):
    conn = feature
    env({f"{A}:tests:1": "ok+misnamed_ids"})   # files and commits right, ids miss: the transition guard refuses
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "tests_frozen")
    runs = store.step_runs(conn, A, "tests")
    assert [r["outcome"] for r in runs] == ["guard_refused", "ok"], [r["summary"] for r in runs]
    assert "frozen_tests_match" in runs[0]["summary"]
    assert any("were set aside" in f for f in runs[1]["meta"]["feedback"])
    first = [e for kind in ("tests_failing_first", "tests_frozen") for e in evidence.list_for(conn, A, kind)
             if e["at"] < runs[1]["started_at"]]
    sup = evidence.list_for(conn, A, "superseded")
    assert len(sup) == 1 and sorted(sup[0]["payload"]["ids"]) == sorted(e["id"] for e in first) and len(first) == 2
    assert sup[0]["payload"]["run_id"] == runs[0]["id"]
    # the retry wrote the tests again (the frozen-test guard did not block it) and froze them at a commit on the branch
    wt = gitops.worktree_path(repo, A)
    files, at = dispatch.frozen_from_ledger(conn, A, wt)
    assert files and at != first[0]["payload"]["commit"] and gitops.reachable(wt, at)
    assert not gitops.reachable(wt, first[0]["payload"]["commit"])
    assert not store.open_escalations(conn, [A])


def test_frozen_tests_on_a_commit_off_the_branch_are_not_frozen(feature, repo, env):
    conn = feature
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    evidence.add(conn, A, "tests_frozen", {"files": {"tests/ratelimit/test_ratelimit.py": "0" * 64}, "commit": "f" * 40})
    assert dispatch.frozen_from_ledger(conn, A, wt) == ({}, None)
    head = gitops.head(wt)
    evidence.add(conn, A, "tests_frozen", {"files": {"tests/ratelimit/test_ratelimit.py": "1" * 64}, "commit": head})
    assert dispatch.frozen_from_ledger(conn, A, wt) == ({"tests/ratelimit/test_ratelimit.py": "1" * 64}, head)


def test_ratchet_new_findings_fail_the_step_and_the_retry_keeps_uncommitted_work(feature, repo, env):
    conn = feature
    env({f"{A}:verify:1": "ratchet"})
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "verified")
    verify = store.step_runs(conn, A, "verify")
    assert [r["outcome"] for r in verify] == ["ratchet", "ok"]
    # implement left its work uncommitted (the pack commits app code at ship); the failed verify
    # did not change it, so the retry ran in place: no reset, no escalation.
    assert store.open_escalations(conn, [A]) == []
    assert "app/ratelimit/rate_limit.py" in verify[1]["meta"]["dirty_at_start"]


def test_drift_in_verify_restores_the_uncommitted_implement_work(feature, repo, env):
    conn = feature
    env({f"{A}:verify:1": "drift"})
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "verified")
    verify = store.step_runs(conn, A, "verify")
    assert [r["outcome"] for r in verify] == ["drift", "ok"]
    wt = gitops.worktree_path(repo, A)
    assert not (wt / "drift").exists()
    assert (wt / "app" / "ratelimit" / "rate_limit.py").read_text().count("VALUE_") == 1   # implement's work survived
    assert "app/ratelimit/rate_limit.py" in gitops.dirty_files(wt)


def test_conformance_below_threshold_reenters_twice_then_escalates(feature, repo, env, monkeypatch):
    conn = feature
    monkeypatch.setenv("FAKE_CLAUDE_SCORES", "0.5,0.6,0.4")
    tick_until(conn, cfg_for(repo), lambda r: bool(store.open_escalations(conn, [A])))
    impl = store.step_runs(conn, A, "implement")
    assert [r["outcome"] for r in impl] == ["conformance_low"] * 3       # first try + two re-entries
    assert any("conformance 0.50" in f for f in impl[1]["meta"]["feedback"])
    impl_file = gitops.worktree_path(repo, A) / "app" / "ratelimit" / "rate_limit.py"
    assert impl_file.read_text().count("VALUE_") == 3                    # each re-entry kept the previous work
    scores = [e["payload"]["score"] for e in evidence.list_for(conn, A, "conformance")]
    assert scores == [0.5, 0.6, 0.4]
    esc = store.open_escalations(conn, [A])[0]
    assert esc["severity"] == "P1" and "Conformance" in esc["question"]
    assert state(conn, A) == "tests_frozen"


def test_crash_with_uncommitted_changes_escalates_and_keeps_the_worktree(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "crash_dirty"})
    tick_until(conn, cfg_for(repo), lambda r: bool(store.open_escalations(conn, [A])))
    assert [r["outcome"] for r in store.step_runs(conn, A, "tests")] == ["crashed"]
    esc = store.open_escalations(conn, [A])[0]
    wt = gitops.worktree_path(repo, A)
    assert esc["severity"] == "P1" and str(wt) in esc["question"] and esc["meta"]["worktree"] == str(wt)
    assert (wt / "app" / "ratelimit" / "half_done.py").exists()


def test_skip_counts_as_done(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "crash"})
    tick_until(conn, cfg_for(repo), lambda r: bool(store.open_escalations(conn, [A])))
    decisions.resolve(conn, store.open_escalations(conn, [A])[0]["id"], "skip", actor="@ish")
    env({})
    tick_until(conn, cfg_for(repo), lambda r: state(conn, B) == "in_review")
    assert loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)["done"]
    assert state(conn, A) == "ready"


def test_capacity_and_drafted_contracts_wait(feature, repo, env):
    conn = feature
    res = intake.contract_draft(conn, slug="audit-log", title="Audit every key rotation in a log table", repo="sample-app",
                                actor="@ish", feature=FEATURE, ticket_text="Audit rotations.", touched_symbols=["app/audit/"],
                                capabilities={"paths": ["app/audit/**"]}, acceptance=[{"id": "AC1", "text": "logged"}])
    assert res["status"] == "created"
    env({"*": "hang"})
    cfg = cfg_for(repo, max_runs=1, stall_minutes=30)
    rep = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["last"]
    by = {e["slug"]: e for e in rep["contracts"]}
    assert by["audit-log"]["action"] == "waiting_on_human" and by["audit-log"]["state"] == "drafted"
    assert by[A]["action"] == "dispatched"
    assert by[B]["action"] == "waiting_on_dependency"
    rep = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["last"]
    assert {e["slug"]: e["action"] for e in rep["contracts"]}[A] == "running"
    from sdlc_graph.orchestrator import watchdog

    watchdog.stop(store.open_runs(conn, A)[0]["pid"])


def test_topo_order_and_cycle(feature):
    conn = feature
    assert loop.topo_order(conn, FEATURE) == [A, B]
    nodes.link(conn, A, B, "depends_on")
    with pytest.raises(ValueError, match="cycle") as exc:
        loop.topo_order(conn, FEATURE)
    assert isinstance(exc.value, loop.DependencyCycle) and exc.value.slugs == [A, B]


def test_dispatch_writes_current_json_settings_and_prompt(feature, repo, env):
    conn = feature
    env({"*": "hang"})
    cfg = cfg_for(repo, stall_minutes=30, plugin_dirs=[])
    from sdlc_graph.orchestrator.steps import STEPS

    run = dispatch.dispatch(conn, cfg, A, STEPS["ready"], feature=FEATURE, attempt=1, feedback=["fix X"])
    wt = gitops.worktree_path(repo, A)
    cur = json.loads((wt / ".claude/work/current.json").read_text())
    assert cur["slug"] == A and cur["allowed_paths"] == ["app/ratelimit/**", "tests/ratelimit/**"]
    assert cur["step"] == "tests" and cur["reviewer"] == "@ish" and cur["tier"] == "low" and len(cur["acceptance"]) == 2
    assert (wt / f".claude/work/{A}/contract.md").read_text().startswith("# Per-key rate limiting")
    meta = run["meta"]
    settings = json.loads(Path(meta["settings_file"]).read_text())
    assert "mcp__graph__contract_transition" in settings["permissions"]["deny"]
    assert settings["sandbox"]["filesystem"]["allowWrite"]
    assert "PreToolUse" in settings["hooks"]                # graph plugin not loaded -> hooks inline
    prompt = Path(meta["prompt_file"]).read_text()
    assert prompt.startswith("/sdlc:tests\n") and "fix X" in prompt and "do not call" in prompt
    assert run["step"] == "tests" and run["attempt"] == 1 and run["requested_model"] == "sonnet"
    assert run["start_commit"] == gitops.head(wt)
    from sdlc_graph.orchestrator import watchdog

    watchdog.stop(run["pid"])
    ship = dispatch.settings_for(nodes.get_row(conn, A), STEPS["verified"], cfg)
    assert "github.com" in ship["sandbox"]["network"]["allowedDomains"]


def test_real_executor_needs_live(feature, repo, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_EXECUTOR", raising=False)
    assert cli.main(["orchestrate", FEATURE, "--once", "--repo", str(repo), "--json"]) == 2


def test_refinery_merges_only_a_verified_approved_pr(feature, repo, env, tmp_path, monkeypatch):
    conn = feature
    calls = tmp_path / "gh-calls"
    monkeypatch.setenv("FAKE_GH_LOG", str(calls))
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "in_review")
    wt = str(gitops.worktree_path(repo, A))

    # Not approved: nothing is merged.
    assert "not merging" in loop.refinery(conn, A, wt)["detail"]
    assert "pr merge" not in calls.read_text()

    # A PR URL the session wrote down is never used: evidence pointing at someone else's approved PR changes nothing.
    monkeypatch.setenv("FAKE_GH_REVIEW", "APPROVED")
    evidence.add(conn, A, "pr_url", {"url": "https://github.com/some-other-org/unrelated-repo/pull/9"})
    monkeypatch.setenv("FAKE_GH_REPO", "some-other-org/unrelated-repo")          # GitHub reports a PR on another repository
    out = loop.refinery(conn, A, wt)
    assert "not on the origin repository" in out["detail"]
    monkeypatch.setenv("FAKE_GH_REPO", "")
    monkeypatch.setenv("FAKE_GH_HEAD", "someone/else")                              # ... or a different head branch
    assert "has head" in loop.refinery(conn, A, wt)["detail"]
    monkeypatch.delenv("FAKE_GH_HEAD")
    assert "pr merge" not in calls.read_text()
    assert "some-other-org" not in calls.read_text()

    # The branch's own PR on origin, approved: merged, with its URL re-derived from GitHub.
    out = loop.refinery(conn, A, wt)
    assert "merged on GitHub" in out["detail"]
    assert evidence.list_for(conn, A, "merge_sha")[0]["payload"]["sha"] == "a" * 40
    merge_calls = [c for c in calls.read_text().splitlines() if c.startswith("pr merge")]
    assert merge_calls == ["pr merge https://github.com/example/sample-app/pull/%s --merge" % merge_calls[0].split("/pull/")[1].split()[0]]


def test_feed_and_escalations(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "nothing"})
    tick_until(conn, cfg_for(repo), lambda r: bool(store.open_escalations(conn, [A])))
    items = cli_json(["feed", "--feature", FEATURE, "--limit", "200"])
    kinds = {i["kind"] for i in items}
    assert {"transition", "run", "decision"} <= kinds
    assert all(i["slug"] in (FEATURE, A, B) for i in items)
    assert all(isinstance(i["what"], str) and i["what"] for i in items)
    assert any(i["what"].startswith("(new) -> ") for i in items)
    esc = cli_json(["escalations", "--feature", FEATURE])
    assert len(esc) == 1 and esc[0]["severity"] == "P1" and esc[0]["slug"] == A
    assert esc[0]["resolve"].startswith(f"sdlc-graph decision resolve {esc[0]['id']}")


def cli_json(argv):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main([*argv, "--json"]) == 0
    return json.loads(buf.getvalue())


# --------------------------------------------------------------------------- review findings


def _other(conn, slug="other", area="other"):
    _contract(conn, slug, f"An independent change to {area}", area)


def test_slug_must_be_branch_safe(clean_conn):
    nodes.create(clean_conn, kind="feature", slug=FEATURE, title="f", repo="sample-app", created_by="@ish")
    for bad in ("bad slug..x", "x..y", "-lead", "a b", "end.", "x.lock", "a/b", "a~b"):
        with pytest.raises(ValueError, match="slug"):
            intake.contract_draft(clean_conn, slug=bad, title="Anything at all", repo="sample-app", actor="@ish", feature=FEATURE,
                                  ticket_text="x.", touched_symbols=["app/x/"])
    assert not gitops.valid_branch_name("sdlc/bad slug..x")


def test_one_contracts_git_problem_escalates_and_the_others_keep_going(feature, repo, env):
    conn = feature
    _other(conn)
    # A leftover directory where A's worktree should go: ensure_worktree refuses it.
    (gitops.worktree_path(repo, A)).mkdir()
    cfg = cfg_for(repo)
    for _ in range(3):                                          # the daemon survives every tick
        out = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
        assert out["stopped"] == "once"
        time.sleep(0.05)
    by = {e["slug"]: e for e in out["last"]["contracts"]}
    assert by[A]["action"] in ("error", "blocked_on_decision")
    esc = store.open_escalations(conn, [A])
    assert len(esc) == 1 and esc[0]["severity"] == "P1" and "not a worktree" in esc[0]["question"]
    assert runs_of(conn, A, "tests") == []
    tick_until(conn, cfg, lambda r: state(conn, "other") == "in_review")      # the independent contract finished


def test_a_worktree_deleted_by_hand_is_an_escalation_not_a_crash(feature, repo, env):
    import shutil

    conn = feature
    env({f"{A}:tests:1": "crash"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: any(x.get("finished") for x in r["last"]["contracts"]))
    wt = gitops.worktree_path(repo, A)
    shutil.rmtree(wt)
    out = tick_until(conn, cfg, lambda r: bool(store.open_escalations(conn, [A])))[-1]
    assert out["stopped"] == "once"
    q = store.open_escalations(conn, [A])[0]["question"]
    assert "is gone" in q and f"sdlc/{A}" in q
    # A human answers retry: a worktree is made again from the branch and the step runs.
    decisions.resolve(conn, store.open_escalations(conn, [A])[0]["id"], "retry", actor="@ish")
    env({})
    tick_until(conn, cfg, lambda r: state(conn, A) in ("tests_frozen", "implementing"))
    assert wt.exists()


def test_a_session_that_moves_the_ledger_itself_is_stopped_and_escalated(feature, repo, env):
    conn = feature
    env({f"{A}:tests:1": "drift+self_transition"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: bool(store.open_escalations(conn, [A])))
    first = runs_of(conn, A, "tests")[0]
    assert first["outcome"] == "unauthorized_transition" and "agent:claude" in first["summary"]
    assert "orchestrator" in store.open_escalations(conn, [A])[0]["question"]
    assert not [r for r in runs_of(conn, A, "tests") if r["outcome"] == "ok"]


def test_evidence_is_checked_against_disk_not_taken_on_the_sessions_word(feature, repo, env):
    conn = feature
    env({f"{A}:tests:1": "lie_tests"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: any(x.get("finished") for x in r["last"]["contracts"]))
    run = runs_of(conn, A, "tests")[0]
    assert run["outcome"] == "unverified"
    reasons = " ".join(run["meta"]["reasons"])
    assert "does not exist in the worktree" in reasons and "test_ratelimit.py" in reasons
    assert state(conn, A) == "ready"


def test_a_pr_url_must_be_the_pr_github_reports_for_the_branch(feature, repo, env):
    conn = feature
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: state(conn, A) == "verified")
    env({f"{A}:ship:1": "lie_pr"})
    tick_until(conn, cfg, lambda r: any(x.get("finished") for x in r["last"]["contracts"] if x["slug"] == A))
    run = runs_of(conn, A, "ship")[0]
    assert run["outcome"] == "no_evidence" and "not confirmed by GitHub" in run["summary"]
    assert state(conn, A) == "verified"
    env({})
    tick_until(conn, cfg, lambda r: state(conn, A) == "in_review")
    url = evidence.list_for(conn, A, "pr_url")[-1]["payload"]["url"]
    assert url.startswith("https://github.com/example/sample-app/pull/")


def test_the_orchestrator_can_run_the_acceptance_tests_itself(feature, repo, env):
    conn = feature
    cfg = cfg_for(repo, test_command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    # tests step: the fake's tests fail, as a first test run must; implement does not make them pass, so verify is refused
    tick_until(conn, cfg, lambda r: any(x.get("finished") and x["slug"] == A and x["finished"][0]["step"] == "verify"
                                        for x in r["last"]["contracts"]))
    assert runs_of(conn, A, "tests")[0]["outcome"] == "ok"
    verify = runs_of(conn, A, "verify")[0]
    assert verify["outcome"] == "unverified" and "fails on the acceptance tests" in verify["summary"]
    assert state(conn, A) == "implementing"


def test_lock_is_kept_while_a_slow_gate_runs_and_a_second_daemon_is_refused(feature, repo, env, monkeypatch, tmp_path):
    conn = feature
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    cfg = cfg_for(repo, tick=0.3)
    tick_until(conn, cfg, lambda r: any(x.get("action") == "dispatched" and x.get("step") == "implement" for x in r["last"]["contracts"]))
    time.sleep(1.0)                                              # the session is done; the next patrol gates it
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "2.5")               # the reviewer takes 8 ticks' worth of time
    result = {}
    t = threading.Thread(target=lambda: result.update(out=loop.orchestrate(FEATURE, cfg, once=True, quiet=True)))
    t.start()
    time.sleep(1.5)                                              # well past 3 ticks: a tick-only heartbeat would be stale
    with pytest.raises(store.LockHeld):
        loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    t.join()
    assert result["out"]["stopped"] == "once"
    assert len(log.read_text().splitlines()) == 1                # one reviewer call, not two
    impl = runs_of(conn, A, "implement")
    assert [r["outcome"] for r in impl] == ["ok"] and state(conn, A) == "implementing"


def test_a_daemon_that_lost_its_lock_does_not_transition(feature, repo, env, monkeypatch):
    conn = feature
    cfg = cfg_for(repo, tick=0.3)
    tick_until(conn, cfg, lambda r: any(x.get("action") == "dispatched" and x.get("step") == "implement" for x in r["last"]["contracts"]))
    time.sleep(1.0)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "1.5")
    result = {}
    t = threading.Thread(target=lambda: result.update(out=loop.orchestrate(FEATURE, cfg, once=True, quiet=True)))
    t.start()
    time.sleep(0.6)
    store.release_lock(conn, FEATURE)                            # --stop while the gate is running
    t.join()
    assert result["out"]["stopped"] == "lock_lost"
    assert state(conn, A) == "tests_frozen"                      # the gate finished but nothing was transitioned


def test_finish_run_is_conditional(feature):
    conn = feature
    run = store.start_run(conn, A, step="tests", stage=None, model="sonnet", attempt=1, start_commit="x")
    first = store.finish_run(conn, run["id"], "ok", summary="first")
    second = store.finish_run(conn, run["id"], "guard_refused", summary="second")
    assert first["outcome"] == "ok" and second["outcome"] == "ok" and second["summary"] == "first"


def test_the_session_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:CANARY_OWNER@db/x")
    monkeypatch.setenv("SDLC_DATABASE_URL", "postgresql://owner:CANARY_OWNER@db/x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_CANARY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "CANARY_AWS")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-keep")
    monkeypatch.setenv("MY_PROJECT_FLAG", "1")
    monkeypatch.delenv("ORCHESTRATOR_ENV_PASSTHROUGH", raising=False)
    node = {"slug": A}
    tests, ship = steps.STEP_BY_NAME["tests"], steps.STEP_BY_NAME["ship"]
    env = dispatch.executor_env(node, tests, "s", 1, 1)
    assert "CANARY_AWS" not in json.dumps(env) and "PATH" in env and env["ANTHROPIC_API_KEY"] == "sk-keep"
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env and "MY_PROJECT_FLAG" not in env
    assert env["SDLC_DATABASE_URL"] == "postgresql://owner:CANARY_OWNER@db/x"      # the ledger URL is explicit, never a stray variable
    assert dispatch.executor_env(node, ship, "s", 1, 1)["GITHUB_TOKEN"] == "ghp_CANARY"
    # the session gets the restricted role's URL when one is configured, never the daemon's
    cfg = config.Config(repo=Path("."), session_database_url="postgresql://sess:SESSION_PW@db/x")
    env = dispatch.executor_env(node, tests, "s", 1, 1, cfg)
    assert env["DATABASE_URL"] == env["SDLC_DATABASE_URL"] == "postgresql://sess:SESSION_PW@db/x"
    assert "CANARY_OWNER" not in json.dumps(env)
    monkeypatch.setenv("ORCHESTRATOR_ENV_PASSTHROUGH", "MY_PROJECT_FLAG")
    assert dispatch.executor_env(node, tests, "s", 1, 1)["MY_PROJECT_FLAG"] == "1"


def test_a_recycled_pid_is_not_the_executor_and_is_never_killed():
    from sdlc_graph.orchestrator import watchdog

    sid = "11111111-2222-3333-4444-555555555555"
    stranger = subprocess.Popen(["sleep", "600"], start_new_session=True)
    ours = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", "--session-id", sid], start_new_session=True)
    try:
        time.sleep(0.3)
        started, _ = dispatch.process_identity(ours.pid)
        run_stranger = {"pid": stranger.pid, "session_id": sid, "meta": {"pid_started": started}}
        run_ours = {"pid": ours.pid, "session_id": sid, "meta": {"pid_started": started}}
        assert watchdog.alive(stranger.pid, run_stranger) is False          # alive as a process, not as this run's executor
        watchdog.stop(stranger.pid, grace=0.2, run=run_stranger)
        assert stranger.poll() is None                                       # not signalled
        assert watchdog.alive(ours.pid, run_ours) is True
        assert watchdog.alive(ours.pid, {**run_ours, "meta": {"pid_started": "Mon Jan  1 00:00:00 2001"}}) is False
        watchdog.stop(ours.pid, grace=2, run=run_ours)
        assert ours.wait(timeout=5) is not None
    finally:
        for p in (stranger, ours):
            if p.poll() is None:
                p.kill()
            p.wait()


def test_a_reset_keeps_what_it_destroys_and_ignores_edits_that_predate_the_run(feature, repo, env):
    conn = feature
    plan = Path(os.environ["ORCH_FAKE_PLAN"])
    env({f"{A}:implement:1": "hold_drift"})
    cfg = cfg_for(repo, stall_minutes=5)
    wt = gitops.worktree_path(repo, A)
    # (a) a human edit that appears in the worktree while the session runs is saved before the drift reset
    tick_until(conn, cfg, lambda r: Path(str(plan) + ".started").exists())
    (wt / "app" / "ratelimit").mkdir(parents=True, exist_ok=True)
    (wt / "app" / "ratelimit" / "human_wip.py").write_text("# my work in progress\n")
    Path(str(plan) + ".release").write_text("1")
    tick_until(conn, cfg, lambda r: any(f["outcome"] == "drift" for x in r["last"]["contracts"] for f in x.get("finished", [])))
    drift = store.step_runs(conn, A, "implement")[0]
    assert drift["outcome"] == "drift" and not (wt / "app" / "ratelimit" / "human_wip.py").exists()
    ref = f"refs/sdlc/discarded/run-{drift['id']}"
    assert ref in drift["summary"]
    assert _git(wt, "show", f"{ref}:app/ratelimit/human_wip.py") == "# my work in progress\n"


def test_an_edit_that_predates_the_run_is_not_the_sessions_drift(feature, repo, env):
    conn = feature
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    (wt / "README.md").write_text("a human edited the readme\n")          # outside allowed_paths, there before any dispatch
    tick_until(conn, cfg_for(repo), lambda r: state(conn, A) == "in_review")
    assert all(r["outcome"] == "ok" for s in ("tests", "implement", "verify", "ship") for r in store.step_runs(conn, A, s))
    assert (wt / "README.md").read_text() == "a human edited the readme\n"


def test_a_retry_that_does_nothing_does_not_pass_on_the_previous_attempts_work(feature, repo, env, monkeypatch):
    conn = feature
    monkeypatch.setenv("FAKE_CLAUDE_SCORES", "0.5,0.95")
    env({f"{A}:implement:2": "nothing"})
    tick_until(conn, cfg_for(repo), lambda r: len([x for x in store.step_runs(conn, A, "implement") if x.get("finished_at")]) >= 2)
    impl = store.step_runs(conn, A, "implement")
    assert [r["outcome"] for r in impl][:2] == ["conformance_low", "no_change"]
    assert "no change of its own" in impl[1]["summary"]


def test_cost_is_reserved_for_open_sessions_and_the_humans_answer_acts(feature, repo, env, monkeypatch):
    conn = feature
    assert config.Config(repo=repo).max_cost_usd == 50.0
    _other(conn)
    monkeypatch.setenv("FAKE_COST", "2.0")
    cfg = cfg_for(repo, max_cost_usd=1.0, budget_usd=5.0, max_runs=2)
    out = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["last"]
    dispatched = [e for e in out["contracts"] if e.get("action") == "dispatched"]
    assert len(dispatched) == 1                                  # the second free slot is held back: spend reads $0 but $5 is reserved
    (d,) = [d for d in store.open_escalations(conn, [FEATURE]) if d["severity"] == "P2"]
    assert d["meta"]["step"] == "budget" and d["meta"]["limit"] == 1.0
    # 'raise' raises the limit (doubles it) and nothing more is asked at the old limit
    decisions.resolve(conn, d["id"], "raise", actor="@ish")
    assert store.budget_limit(conn, FEATURE, 1.0) == 2.0
    time.sleep(0.2)
    out = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["last"]
    (d2,) = [d for d in store.open_escalations(conn, [FEATURE]) if d["severity"] == "P2"]
    assert d2["meta"]["limit"] == 2.0 and d2["id"] != d["id"]
    # 'stop' ends the loop
    decisions.resolve(conn, d2["id"], "stop", actor="@ish")
    before = conn.execute("SELECT count(*) AS n FROM run").fetchone()["n"]
    res = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert res["stopped"] == "budget_stop"
    assert conn.execute("SELECT count(*) AS n FROM run").fetchone()["n"] == before
    assert store.budget_limit(conn, FEATURE, 100.0) == 100.0     # a larger --max-cost lifts it


def test_a_question_for_a_human_becomes_a_decision_not_a_failure(feature, repo, env):
    conn = feature
    env({f"{A}:tests:1": "ask"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: bool(store.open_escalations(conn, [A])))
    first = store.step_runs(conn, A, "tests")[0]
    assert first["outcome"] == "needs_human"
    (d,) = store.open_escalations(conn, [A])
    assert d["severity"] == "P1" and "per API key or per account" in d["question"] and d["options"] == []
    assert loop.history(conn, A, "tests")["failures"] == []
    for _ in range(3):                                           # nothing is dispatched while the question is open
        loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert len(store.step_runs(conn, A, "tests")) == 1
    decisions.resolve(conn, d["id"], "per account", actor="@ish")
    env({})
    tick_until(conn, cfg, lambda r: state(conn, A) == "tests_frozen")
    second = store.step_runs(conn, A, "tests")[1]
    assert second["attempt"] == 2 and "per account" in Path(second["meta"]["prompt_file"]).read_text()


def test_drift_also_sees_ignored_files_work_dir_strays_and_escaping_symlinks(feature, repo, env, tmp_path, monkeypatch):
    conn = feature
    (repo / ".gitignore").write_text(".claude/work/\n.env\ndist/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore more")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("FAKE_ESCAPE_DIR", str(outside))
    env({f"{A}:implement:1": "plant"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: any(f["outcome"] == "drift" for x in r["last"]["contracts"] for f in x.get("finished", [])))
    drift = store.step_runs(conn, A, "implement")[0]
    summary = drift["summary"]
    for name in (".env", "dist/payload.js", ".claude/work/anything.sh", "app/ratelimit/escape"):
        assert name in summary, summary
    wt = gitops.worktree_path(repo, A)
    assert not (wt / ".env").exists() and not (wt / "dist/payload.js").exists() and not (wt / "app/ratelimit/escape").is_symlink()
    assert _git(wt, "show", f"refs/sdlc/discarded/run-{drift['id']}:.env") == "SECRET=1\n"
    env({})
    tick_until(conn, cfg, lambda r: state(conn, A) == "in_review")                # the clean retry passes
    # tool caches are not drift
    assert gitops.outside(["app/ratelimit/x.py"], ["app/ratelimit/**"]) == []
    assert gitops.work_exempt(".claude/work/current.json", A) and not gitops.work_exempt(".claude/work/anything.sh", A)
    assert gitops.work_exempt(f".claude/work/{A}/notes.md", A)


def test_the_pack_staging_dir_of_the_contract_is_not_drift():
    assert gitops.work_exempt(f".sdlc-stage/{A}/pr-body.md", A) and gitops.work_exempt(".sdlc-stage/x.md")
    assert not gitops.work_exempt(f".sdlc-stage/{B}/pr-body.md", A) and not gitops.work_exempt(".sdlc-stage/x.md", A)


def test_a_restore_keeps_tracked_files_under_claude_work(repo):
    (repo / ".claude" / "work").mkdir(parents=True)
    (repo / ".claude" / "work" / "README.md").write_text("tracked work-dir readme\n")
    _git(repo, "add", "-f", ".claude/work/README.md")
    _git(repo, "commit", "-qm", "work readme")
    start = gitops.head(repo)
    (repo / "app" / "main.py").write_text("APP = 'changed'\n")      # uncommitted work of an earlier step
    snap = gitops.snapshot(repo)
    (repo / "app" / "new.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the attempt being set aside")
    gitops.restore(repo, start, snap)
    assert (repo / ".claude" / "work" / "README.md").read_text() == "tracked work-dir readme\n"
    assert (repo / "app" / "main.py").read_text() == "APP = 'changed'\n" and not (repo / "app" / "new.py").exists()
    assert gitops.head(repo) == start


def test_a_pushing_step_gets_a_token_helper_and_an_empty_gh_config(repo, tmp_path):
    assert dispatch.github_env({"PATH": os.environ["PATH"]}, tmp_path / "gh") == {}
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_", "GIT_"))}
    env = {**base, "GH_TOKEN": "test-token-not-real"}
    env.update(dispatch.github_env(env, tmp_path / "gh"))
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "gh") and (tmp_path / "gh").is_dir()
    _git(repo, "config", "credential.helper", "store --file=/nonexistent/should-not-be-used")
    out = subprocess.run(["git", "-C", str(repo), "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, env=env, check=True).stdout
    assert "username=x-access-token" in out and "password=test-token-not-real" in out


def test_a_malformed_database_url_does_not_leak_its_password(tmp_path, monkeypatch, capsys):
    from sdlc_graph import db
    from sdlc_graph.orchestrator import prime

    bad = "postgresql://u:CANARY_PW_zz@[::1/x"
    monkeypatch.setenv("DATABASE_URL", bad)
    work = tmp_path / "proj" / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "current.json").write_text(json.dumps({"slug": A, "ticket": "t", "node": A}))
    text = prime.prime(tmp_path / "proj")
    assert "CANARY_PW_zz" not in text and "ledger unreachable" in text
    assert cli.main(["feed"]) == 5                                # mapped, not re-raised as a traceback
    captured = capsys.readouterr()
    assert "CANARY_PW_zz" not in captured.out + captured.err
    assert "CANARY_PW_zz" not in db.redact_secrets(f"invalid URI: {bad}") and "password=***" in db.redact_secrets("password='a b' host=x")


# --------------------------------------------------------------------------- executor modes: CLI, run rows, feed, metrics


def _clear_creds(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "ANTHROPIC_AWS_WORKSPACE_ID",
              "ANTHROPIC_AWS_API_KEY", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.delenv(v, raising=False)


def test_cli_executor_defaults_to_local_and_runner_refuses_without_a_credential(feature, repo, env, monkeypatch, capsys):
    _clear_creds(monkeypatch)
    base = ["orchestrate", FEATURE, "--once", "--quiet", "--repo", str(repo), "--json", "--no-require-tests"]
    assert cli.main([*base, "--executor", "runner"]) == 2
    cap = capsys.readouterr()
    err = cap.out + cap.err                                      # --json prints the error object on stdout
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AWS_WORKSPACE_ID", "CLAUDE_CODE_USE_BEDROCK"):
        assert name in err
    with pytest.raises(SystemExit) as bad:
        cli.main([*base, "--executor", "bogus"])
    assert bad.value.code == 2
    assert cli.main(base) == 0                                   # default local: starts (fake executor), no credential needed
    assert {r["executor"] for r in runs_of(feature, A)} == {"local"}


@pytest.mark.parametrize("var,source", [
    ({"ANTHROPIC_API_KEY": "sk-dummy"}, "api_key"),
    ({"CLAUDE_CODE_OAUTH_TOKEN": "tok-dummy"}, "oauth_token"),
    ({"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1", "ANTHROPIC_AWS_WORKSPACE_ID": "ws-dummy"}, "claude_platform_aws"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "bedrock"),
])
def test_runner_starts_with_each_credential_and_the_run_row_records_it(feature, repo, env, monkeypatch, var, source, capsys):
    _clear_creds(monkeypatch)
    for k, v in var.items():
        monkeypatch.setenv(k, v)
    assert cli.main(["orchestrate", FEATURE, "--once", "--repo", str(repo), "--executor", "runner", "--json", "--no-require-tests"]) == 0
    err = capsys.readouterr().err
    assert ("tied to one person" in err) == (source == "oauth_token")
    rows = runs_of(feature, A)
    assert rows and {r["executor"] for r in rows} == {"runner"} and {r["auth_source"] for r in rows} == {source}


def test_local_run_without_credentials_is_a_subscription_run_and_feed_and_metrics_show_it(feature, repo, env, monkeypatch):
    _clear_creds(monkeypatch)
    conn = feature
    env({"*": "hang"})
    cfg = cfg_for(repo, stall_minutes=30, plugin_dirs=[])
    from sdlc_graph.orchestrator import watchdog
    from sdlc_graph.orchestrator.steps import STEPS

    run = dispatch.dispatch(conn, cfg, A, STEPS["ready"], feature=FEATURE, attempt=1)
    watchdog.stop(run["pid"])
    assert (run["executor"], run["auth_source"]) == ("local", "subscription")
    # a run that was not an orchestrated step carries neither column
    from sdlc_graph.ledger import runs as runs_mod

    plain = runs_mod.start(conn, stage="build", requested_model="sonnet", slug=A)
    assert plain["executor"] is None and plain["auth_source"] is None
    items = cli_json(["feed", "--feature", FEATURE, "--limit", "100"])
    assert any("[local/subscription]" in i["what"] and i["kind"] == "run" for i in items)
    snap = cli_json(["metrics", "snapshot"])
    assert snap["runs_by_executor"] == {"local": 1} and snap["runs_by_auth_source"] == {"subscription": 1}


def test_session_environment_carries_mode_and_auth_source(monkeypatch):
    _clear_creds(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    node = {"slug": "s", "title": "t", "state": "ready"}
    cfg = config.Config(repo=".", executor_mode="runner")
    env = dispatch.executor_env(node, steps.STEPS["ready"], "s", 1, 1, cfg)
    assert env["ORCH_EXECUTOR_MODE"] == "runner" and env["ORCH_AUTH_SOURCE"] == "api_key"
    assert dispatch.executor_env(node, steps.STEPS["ready"], "s", 1, 1)["ORCH_EXECUTOR_MODE"] == "local"
