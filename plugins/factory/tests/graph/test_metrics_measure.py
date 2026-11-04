"""metrics.snapshot: the DESIGN section 18.5 measurements (turns per merged contract, guard trips, denials, model
overrides) and the hand-off hook's guard-block recording path."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sdlc_graph.ledger import metrics, nodes, runs

REPO = "sample-app"
HOOKS = Path(__file__).resolve().parents[2] / "hooks"


def mk(c, slug, state):
    nodes.create(c, kind="contract", slug=slug, title=slug, repo=REPO, created_by="@a", state=state)


def run(c, slug, stage, model, *, meta=None, usage=None, outcome="ok"):
    r = runs.start(c, stage=stage, requested_model=model, slug=slug, meta=meta)
    return runs.finish(c, r["id"], outcome=outcome, usage=usage or {})


def test_turns_and_cost_per_merged_contract(clean_conn):
    c = clean_conn
    mk(c, "m1", "merged")
    mk(c, "m2", "merged")
    mk(c, "open", "implementing")
    run(c, "m1", "tests", "sonnet", usage={"turns": 12, "cost_usd": 0.4})
    run(c, "m1", "implement", "sonnet", usage={"turns": 20, "cost_usd": 1.0})
    run(c, "m2", "implement", "sonnet", usage={"turns": 8, "cost_usd": 0.6})
    run(c, "m2", "verify", "sonnet", usage={"tool_use_count": 9})  # a hook-recorded hand-off: no turns
    run(c, "open", "implement", "sonnet", usage={"turns": 99, "cost_usd": 9.0})  # not merged: not counted
    s = metrics.snapshot(c, repo=REPO)
    assert s["merged_contracts"] == 2
    assert s["turns_per_merged_contract"] == 20.0  # (12 + 20 + 8) / 2
    assert s["runs_with_turns"] == 3
    assert s["cost_per_merged_contract"] == 1.0
    empty = metrics.snapshot(c, repo="elsewhere")
    assert (empty["merged_contracts"], empty["turns_per_merged_contract"], empty["runs_with_turns"]) == (0, None, 0)


def test_guard_trips_from_gate_denials_and_hook_blocks(clean_conn):
    c = clean_conn
    mk(c, "k1", "implementing")
    mk(c, "k2", "implementing")
    gate = [
        {"tool": "Edit", "target": "tests/test_x.py", "rule": "guard guard_edit:frozen-test", "reason": "guard_refused"},
        {"tool": "Write", "target": "app/admin.py", "rule": "guard guard_edit:scope", "reason": "guard_refused"},
        {"tool": "Bash", "target": "cat .env", "rule": "Bash(cat .env)"},  # a permission denial, not a guard trip
    ]
    run(c, "k1", "implement", "sonnet", usage={"denials": gate})
    # the hand-off hook saw the same scope block (detail cut): one trip, not two; plus a secret block of its own
    run(c, "k1", "implement", "sonnet", usage={"guard_blocks": [
        {"ts": "2026-11-03T10:00:00Z", "hook": "guard_edit", "rule": "scope", "detail": "app/admin.py"},
        {"ts": "2026-11-03T10:01:00Z", "hook": "guard_edit", "rule": "secret", "detail": "app/config.py"},
        {"ts": "2026-11-03T10:02:00Z", "hook": "guard_bash", "rule": "crash", "detail": "x"},  # a failed guard, ignored
    ]})
    # the same rule and target on another node is another trip
    run(c, "k2", "tests", "sonnet", usage={"guard_blocks": [
        {"ts": "2026-11-03T10:00:00Z", "hook": "guard_edit", "rule": "scope", "detail": "app/admin.py"},
        {"ts": "2026-11-03T10:03:00Z", "hook": "guard_bash", "rule": "force-push", "detail": "git push -f"},
    ]})
    g = metrics.snapshot(c, repo=REPO)["guard_trips"]
    assert g["by_kind"] == {"frozen_test": 1, "scope": 2, "secret": 1, "other": 1}
    assert g["total"] == 5
    assert g["by_rule"] == {"force-push": 1, "frozen-test": 1, "scope": 2, "secret": 1}
    assert g["runs_with_trips"] == 3


def test_guard_kind():
    assert metrics.guard_kind("frozen-test") == "frozen_test"
    assert metrics.guard_kind("test-config") == "frozen_test"
    assert metrics.guard_kind("outside-root") == "scope"
    assert metrics.guard_kind("guard: app/x.py is outside the contract's allowed_paths") == "scope"
    assert metrics.guard_kind("credential-read") == "secret"
    assert metrics.guard_kind("destructive-git") == "other"


def test_denial_rate(clean_conn):
    c = clean_conn
    mk(c, "d1", "implementing")
    run(c, "d1", "implement", "sonnet", usage={"denials": []})
    run(c, "d1", "implement", "sonnet", usage={"denials": [{"tool": "Bash", "target": "rg x", "rule": "Bash(rg x)"},
                                                            {"tool": "Bash", "target": "a && b", "form": True}]})
    run(c, "d1", "verify", "sonnet", usage={"denials": [{"tool": "Write", "rule": "guard guard_edit:scope",
                                                         "reason": "guard_refused"}]})
    run(c, "d1", "verify", "sonnet")  # a hook-recorded hand-off: the gate never looked, so not in the rate
    d = metrics.snapshot(c, repo=REPO)["denials"]
    assert d == {"runs_checked": 3, "runs_denied": 2, "rate": 0.667, "total": 3, "form": 1, "capability": 2}
    assert metrics.snapshot(c, repo="elsewhere")["denials"]["rate"] is None


def test_model_override_rate(clean_conn):
    c = clean_conn
    mk(c, "o1", "implementing")
    run(c, "o1", "implement", "sonnet")                       # explicit, the default tier: not an override
    run(c, "o1", "implement", "opus")                         # explicit, another tier: an override
    run(c, "o1", "tests", "unspecified")                      # no request: the default applied
    # the spawn guard rewrote an explicit haiku request to the default: the request is what counts
    run(c, "o1", "verify", "haiku", usage={"rewritten_to": "sonnet"})
    run(c, "o1", None, "opus")                                # no stage, no default: not in the rate
    m = metrics.snapshot(c, repo=REPO)["model_overrides"]
    assert m == {"runs": 4, "explicit": 3, "overrides": 2, "rate": 0.5, "rewritten": 1}


# --------------------------------------------------------------------------- the recording path


def _load(name):
    spec = importlib.util.spec_from_file_location(f"t_{name}", HOOKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_agent_run_copies_blocks_since_the_spawn(tmp_path):
    rec = _load("record_agent_run")
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    lines = [
        {"ts": "2026-11-03T09:59:59Z", "hook": "guard_edit", "rule": "scope", "detail": "before the spawn"},
        {"ts": "2026-11-03T10:00:00Z", "hook": "guard_edit", "rule": "frozen-test", "detail": "tests/t.py" + "x" * 200},
        {"ts": "2026-11-03T10:00:05Z", "hook": "guard_bash", "rule": "crash", "detail": "a guard that failed"},
    ]
    (work / "blocks.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    got = rec.guard_blocks(work, "2026-11-03T10:00:00Z")
    assert [b["rule"] for b in got] == ["frozen-test"]
    assert len(got[0]["detail"]) == 80
    assert rec.guard_blocks(work, None) == []
    assert rec.guard_blocks(work, "not a time") == []
    assert rec.guard_blocks(tmp_path / "nowhere", "2026-11-03T10:00:00Z") == []


def test_record_agent_run_puts_blocks_on_the_run(tmp_path, monkeypatch):
    rec = _load("record_agent_run")
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "spawns.json").write_text(json.dumps({"toolu_1": {"stage": "implement", "node": "k1", "requested_model": "opus",
                                                              "model": "sonnet", "rewritten": True,
                                                              "at": "2026-11-03T10:00:00Z", "agent_type": "factory:implementer"}}))
    (work / "blocks.jsonl").write_text(json.dumps({"ts": "2026-11-03T10:00:01Z", "hook": "guard_edit", "rule": "scope",
                                                   "detail": "app/admin.py"}) + "\n")
    calls = []

    def fake_cli(args):
        calls.append(args)
        return (0, {"id": 7}, "") if args[:2] == ["run", "start"] else (0, {}, "")

    monkeypatch.setattr(rec.guard, "run_cli", fake_cli)
    monkeypatch.setattr(rec.guard, "work_dir", lambda _h: work)
    rec._record_run({"tool_name": "Agent", "tool_use_id": "toolu_1", "tool_input": {"subagent_type": "factory:implementer"},
                     "tool_response": {"status": "completed", "totalToolUseCount": 3}})
    finish = next(a for a in calls if a[:2] == ["run", "finish"])
    usage = json.loads(finish[finish.index("--usage") + 1])
    assert usage["guard_blocks"] == [{"ts": "2026-11-03T10:00:01Z", "hook": "guard_edit", "rule": "scope", "detail": "app/admin.py"}]
    assert usage["rewritten_to"] == "sonnet"


def test_spawn_note_records_when_the_hand_off_began(tmp_path):
    guard = _load("guard_agent_spawn")
    work = tmp_path / "work"
    guard.note_spawn(work, {"tool_use_id": "toolu_2", "tool_input": {"subagent_type": "x"}},
                     {"action": "allow", "stage": "tests", "node": None, "requested_model": None, "model": "sonnet", "tier": "executor"})
    note = json.loads((work / "spawns.json").read_text())["toolu_2"]
    assert note["at"].endswith("Z") and note["rewritten"] is False
