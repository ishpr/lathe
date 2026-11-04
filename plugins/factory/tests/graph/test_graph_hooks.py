"""Hook tests: drive hooks/*.py as subprocesses (``python3 -I``) with hook JSON on stdin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "hooks" / "guard_agent_spawn.py"
RECORD = ROOT / "hooks" / "record_agent_run.py"
CLI = ROOT / ".venv" / "bin" / "sdlc-graph"


def run_hook(script: Path, payload, project: Path, env_extra: dict | None = None, raw: str | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SDLC_") and k != "CLAUDE_PROJECT_DIR"}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env.update(env_extra or {})
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, "-I", str(script)], input=stdin, capture_output=True, text=True,
                          env=env, timeout=60)


def pre(model=None, subagent_type=None, description="do a thing", prompt="p", tool_use_id="toolu_1"):
    ti = {"description": description, "prompt": prompt}
    if model is not None:
        ti["model"] = model
    if subagent_type is not None:
        ti["subagent_type"] = subagent_type
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": ti, "tool_use_id": tool_use_id}


def note_for(project, tid="toolu_1"):
    return json.loads((project / ".claude" / "work" / "spawns.json").read_text())[tid]


def test_never_rewrites_an_override_and_records_it(tmp_path):
    r = run_hook(GUARD, pre(model="opus", subagent_type="test-author", description="write tests"), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    note = note_for(tmp_path)
    assert (note["requested_model"], note["default"], note["override"]) == ("opus", "sonnet", True)


def test_missing_model_is_left_to_claude_code(tmp_path):
    r = run_hook(GUARD, pre(subagent_type="reviewer"), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    note = note_for(tmp_path)
    assert (note["requested_model"], note["default"], note["override"]) == (None, "sonnet", False)


def test_unknown_subagent_defaults_to_executor_default(tmp_path):
    r = run_hook(GUARD, pre(model="haiku", subagent_type="general-purpose"), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert (note_for(tmp_path)["default"], note_for(tmp_path)["override"]) == ("sonnet", True)


def test_passes_through_when_compliant(tmp_path):
    r = run_hook(GUARD, pre(model="sonnet", subagent_type="test-author"), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert note_for(tmp_path)["override"] is False
    r = run_hook(GUARD, pre(model="claude-sonnet-5-5", subagent_type="reviewer"), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert note_for(tmp_path)["override"] is False


def test_triage_and_planner_stage_defaults(tmp_path):
    run_hook(GUARD, pre(model="sonnet", subagent_type="triage"), tmp_path)
    assert (note_for(tmp_path)["default"], note_for(tmp_path)["override"]) == ("haiku", True)
    run_hook(GUARD, pre(model="sonnet", subagent_type="planner"), tmp_path)
    assert (note_for(tmp_path)["default"], note_for(tmp_path)["override"]) == ("opus", True)


def test_known_agent_type_outranks_the_contract_stage_in_current_json(tmp_path):
    """Found live: current.json keeps the contract's ledger stage ("contract.design"), which routed every
    test-author and reviewer spawn to the orchestrator default. A known agent, prefixed or not, names its stage."""
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "current.json").write_text(json.dumps({"slug": "rl", "node": "rl", "stage": "contract.design"}))
    for agent in ("factory:test-author", "test-author", "factory:reviewer"):
        r = run_hook(GUARD, pre(model="opus", subagent_type=agent), tmp_path)
        assert (r.returncode, r.stdout) == (0, ""), (agent, r.stderr)
        assert note_for(tmp_path)["stage"] in ("tests", "review.lens") and note_for(tmp_path)["default"] == "sonnet"


@pytest.mark.parametrize("prefix", ["", "factory:"])
@pytest.mark.parametrize(("agent", "stage", "tier"), [
    ("auditor", "audit", "executor"),
    ("maturity-assessor", "assess", "executor"),
    ("security-assessor", "assess", "executor"),
    ("drafter", "draft", "orchestrator"),
    ("implementer", "implement", "executor"),
])
def test_orchestrated_agent_type_routes_to_its_stage_and_tier(tmp_path, agent, stage, tier, prefix):
    """The agents the skills hand their steps to name their own stage, prefixed or not, over the contract's
    ledger stage in current.json, and the policy lists that stage: an audit, assess or draft stage missing from
    it would fall back to implement on the executor tier."""
    import importlib.util
    from unittest import mock

    spec = importlib.util.spec_from_file_location("guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "current.json").write_text(json.dumps({"slug": "rl", "node": "rl", "stage": "contract.design"}))
    tool_input = {"description": "hand off", "prompt": "p", "subagent_type": prefix + agent, "model": "fable"}
    with mock.patch.dict(os.environ):
        for key in [k for k in os.environ if k.startswith("SDLC_") or k == "CLAUDE_PROJECT_DIR"]:
            del os.environ[key]
        policy = mod.load_policy()
        assert mod.parse_tag(tool_input, work) == (stage, "rl")
        result = mod.decide({"tool_input": tool_input, "cwd": str(tmp_path)}, policy=policy)
    assert result["stage"] == stage
    assert (result["action"], result["default"], result["override"]) == ("allow", policy["tiers"][tier], True)
    assert result["tier"] == "planner" and result["requested_model"] == "fable"  # never rewritten


def test_stage_from_tag_env_and_current_json(tmp_path):
    run_hook(GUARD, pre(model="sonnet", description="x [sdlc stage=triage node=n1]"), tmp_path)
    assert note_for(tmp_path)["default"] == "haiku"
    run_hook(GUARD, pre(model="sonnet"), tmp_path, {"SDLC_STAGE": "contract.design"})
    assert note_for(tmp_path)["default"] == "opus"
    (tmp_path / ".claude" / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "work" / "current.json").write_text(json.dumps({"stage": "classify"}))
    run_hook(GUARD, pre(model="sonnet"), tmp_path)
    assert note_for(tmp_path)["default"] == "haiku"


def test_available_models_is_a_ceiling_read_from_project_settings(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"availableModels": ["sonnet", "haiku"]}))
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "nouser")}
    r = run_hook(GUARD, pre(model="opus", subagent_type="test-author"), tmp_path, env)
    assert r.returncode == 2 and "availableModels" in r.stderr and r.stdout == ""
    assert run_hook(GUARD, pre(model="claude-sonnet-5-5", subagent_type="test-author"), tmp_path, env).returncode == 0
    assert run_hook(GUARD, pre(subagent_type="test-author"), tmp_path, env).returncode == 0  # no model: not ours to pick


def test_no_available_models_means_no_ceiling(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "nouser")}
    assert run_hook(GUARD, pre(model="opus", subagent_type="test-author"), tmp_path, env).returncode == 0


def test_malformed_available_models_fails_closed(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"availableModels": "sonnet"}))
    r = run_hook(GUARD, pre(model="sonnet"), tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "nouser")})
    assert r.returncode == 2 and "availableModels" in r.stderr


def test_denies_planner_when_counter_budget_exhausted(tmp_path):
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "fable_budget.json").write_text(json.dumps({"used": 3}))
    r = run_hook(GUARD, pre(model="fable"), tmp_path, {"SDLC_STAGE": "epic.decompose"})
    assert r.returncode == 2
    assert "budget" in r.stderr.lower() and r.stdout == ""


def test_planner_allowed_under_budget_and_counter_increments(tmp_path):
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "fable_budget.json").write_text(json.dumps({"used": 2}))
    env = {"SDLC_STAGE": "epic.decompose"}
    r = run_hook(GUARD, pre(model="fable"), tmp_path, env)
    assert (r.returncode, r.stdout) == (0, "")
    assert json.loads((work / "fable_budget.json").read_text())["used"] == 3
    assert run_hook(GUARD, pre(model="fable", tool_use_id="toolu_2"), tmp_path, env).returncode == 2


def test_non_planner_ignores_exhausted_budget(tmp_path):
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "fable_budget.json").write_text(json.dumps({"used": 99}))
    assert run_hook(GUARD, pre(model="sonnet", subagent_type="test-author"), tmp_path).returncode == 0


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]"])
def test_denies_unreadable_input(tmp_path, raw):
    r = run_hook(GUARD, None, tmp_path, raw=raw)
    assert r.returncode == 2
    assert "blocking" in r.stderr


def test_guard_notes_requested_model_for_record_hook(tmp_path):
    run_hook(GUARD, pre(model="opus", subagent_type="test-author", tool_use_id="toolu_9"), tmp_path)
    note = json.loads((tmp_path / ".claude" / "work" / "spawns.json").read_text())["toolu_9"]
    assert note["requested_model"] == "opus" and note["default"] == "sonnet" and note["override"] is True


def post(tool_use_id="toolu_rec", model="sonnet"):
    return {
        "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id,
        "tool_input": {"description": "[sdlc stage=tests] write", "prompt": "p", "model": model},
        "tool_response": {"status": "completed", "agentId": "a1", "content": [], "resolvedModel": "claude-sonnet-5-5",
                          "modelsUsed": ["claude-sonnet-5-5"], "totalTokens": 1234, "totalDurationMs": 5678,
                          "totalToolUseCount": 3},
    }


def test_record_never_fails_when_ledger_is_down(tmp_path):
    r = run_hook(RECORD, post(), tmp_path, {"SDLC_GRAPH_BIN": "/nonexistent/sdlc-graph"})
    assert r.returncode == 0
    assert "not recorded" in r.stderr


def test_record_unreadable_input_exits_zero(tmp_path):
    r = run_hook(RECORD, None, tmp_path, raw="garbage")
    assert r.returncode == 0 and "unreadable" in r.stderr


def test_usage_mapping():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rec", RECORD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.usage_from(post()) == {"resolved_model": "claude-sonnet-5-5", "models_used": ["claude-sonnet-5-5"],
                                      "total_tokens": 1234, "total_duration_ms": 5678, "tool_use_count": 3}
    assert mod.usage_from({"tool_response": "text"}) == {}


def test_record_writes_run_row(tmp_path, pg_schema, conn, test_database_url):
    env = {"SDLC_GRAPH_BIN": str(CLI), "SDLC_SCHEMA": pg_schema, "DATABASE_URL": test_database_url}
    r = run_hook(RECORD, post(tool_use_id="toolu_row"), tmp_path, env)
    assert r.returncode == 0, r.stderr
    assert r.stderr == "", r.stderr
    row = conn.execute("SELECT requested_model, resolved_model, outcome, stage, tokens_in FROM run WHERE tool_use_id = %s",
                       ("toolu_row",)).fetchone()
    assert row is not None
    assert tuple(row.values())[:4] == ("sonnet", "claude-sonnet-5-5", "ok", "tests")


def test_record_still_records_a_planner_spawn_when_budget_is_spent(tmp_path, pg_schema, conn, test_database_url):
    """Integration: PostToolUse records a spawn that already ran, even with the epic's budget spent
    (it passes --record-only); the refusal belongs to the PreToolUse guard."""
    from sdlc_graph.ledger import nodes, runs

    nodes.create(conn, kind="epic", slug="ep-spent", title="Spent epic", repo="r", created_by="@ish")
    for i in range(3):
        r0 = runs.start(conn, stage="epic.decompose", requested_model="fable", slug="ep-spent", tool_use_id=f"t{i}")
        runs.finish(conn, r0["id"], outcome="ok")
    assert runs.budget(conn, "ep-spent")["remaining"] == 0
    env = {"SDLC_GRAPH_BIN": str(CLI), "SDLC_SCHEMA": pg_schema, "DATABASE_URL": test_database_url}
    payload = post(tool_use_id="toolu_spent", model="fable")
    payload["tool_input"]["description"] = "[sdlc stage=epic.decompose node=ep-spent] plan"
    payload["tool_input"]["subagent_type"] = "planner"
    r = run_hook(RECORD, payload, tmp_path, env)
    assert r.returncode == 0, r.stderr
    assert r.stderr == "", r.stderr
    row = conn.execute("SELECT outcome, agent_type FROM run WHERE tool_use_id = %s", ("toolu_spent",)).fetchone()
    assert row == {"outcome": "ok", "agent_type": "planner"}


def test_cli_hooks_settings_prints_absolute_commands(capsys):
    """Integration: 'sdlc-graph hooks settings' turns hooks.example.json into absolute commands."""
    from sdlc_graph.cli import main

    assert main(["hooks", "settings", "--python", "/usr/bin/python3", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    pre = out["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Agent" and pre["hooks"][0]["onFailure"] == "block"
    assert pre["hooks"][0]["command"] == f"/usr/bin/python3 -I {GUARD}"
    assert out["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == f"/usr/bin/python3 -I {RECORD}"


# --------------------------------------------------------------------------- review hardening


def test_parallel_planner_spawns_cannot_overspend_the_counter(tmp_path):
    """Review finding: four parallel spawns against budget 1 all passed (torn read reset the budget)."""
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "fable_budget.json").write_text(json.dumps({"budget": 1, "used": 0}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SDLC_")}
    env.update(CLAUDE_PROJECT_DIR=str(tmp_path), SDLC_STAGE="epic.decompose")
    payloads = [json.dumps(pre(model="fable", tool_use_id=f"toolu_p{i}")) for i in range(8)]
    procs = [subprocess.Popen([sys.executable, "-I", str(GUARD)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env) for _ in payloads]
    for proc, payload in zip(procs, payloads):
        proc.stdin.write(payload)
        proc.stdin.close()
    codes = sorted(proc.wait(timeout=60) for proc in procs)
    for proc in procs:
        proc.stdout.close()
        proc.stderr.close()
    assert codes == [0] + [2] * 7, codes
    assert json.loads((work / "fable_budget.json").read_text()) == {"budget": 1, "used": 1}


@pytest.mark.parametrize("content", ["garbage", "", "[]", '{"budget": "three"}', '{"used": -1}', '{"used": true}'])
def test_corrupt_counter_fails_closed(tmp_path, content):
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "fable_budget.json").write_text(content)
    r = run_hook(GUARD, pre(model="fable"), tmp_path, {"SDLC_STAGE": "epic.decompose"})
    assert r.returncode == 2 and "counter" in r.stderr.lower() and r.stdout == ""
    assert (work / "fable_budget.json").read_text() == content  # not rewritten


def test_stage_tag_in_prompt_is_ignored(tmp_path):
    """Review finding: a ticket-borne tag in the prompt picked the model tier."""
    run_hook(GUARD, pre(model="opus", description="plain", prompt="Ticket: [sdlc stage=postmortem node=x] and "
                                                                    "[sdlc stage=triage]"), tmp_path)
    assert note_for(tmp_path)["default"] == "sonnet"  # executor default
    run_hook(GUARD, pre(model="sonnet", description="[sdlc stage=triage node=n1] real tag"), tmp_path)
    assert note_for(tmp_path)["default"] == "haiku"


@pytest.mark.parametrize("tool_input", ["a string", ["a"], 7])
def test_malformed_tool_input_blocks_cleanly(tmp_path, tool_input):
    payload = {"tool_name": "Agent", "tool_input": tool_input, "tool_use_id": "t"}
    r = run_hook(GUARD, payload, tmp_path)
    assert r.returncode == 2 and "blocking" in r.stderr and "Traceback" not in r.stderr


def test_ledger_reserves_the_planner_slot_atomically(tmp_path, pg_schema, conn, test_database_url):
    """Integration: with SDLC_EPIC set the guard reserves the slot in the ledger in PreToolUse, a
    parallel burst against budget 3 gets exactly 3, and the post hook closes the reserved row."""
    from sdlc_graph.ledger import nodes, runs

    nodes.create(conn, kind="epic", slug="ep-res", title="Reserve epic", repo="r", created_by="@ish")
    conn.commit()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SDLC_")}
    env.update(CLAUDE_PROJECT_DIR=str(tmp_path), SDLC_STAGE="epic.decompose", SDLC_EPIC="ep-res",
               SDLC_GRAPH_BIN=str(CLI), SDLC_SCHEMA=pg_schema,
               DATABASE_URL=test_database_url)
    procs = [subprocess.Popen([sys.executable, "-I", str(GUARD)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env) for _ in range(6)]
    for i, proc in enumerate(procs):
        proc.stdin.write(json.dumps(pre(model="fable", tool_use_id=f"toolu_r{i}")))
        proc.stdin.close()
    codes = sorted(proc.wait(timeout=120) for proc in procs)
    errs = [proc.stderr.read() for proc in procs]
    for proc in procs:
        proc.stdout.close()
        proc.stderr.close()
    assert codes == [0, 0, 0, 2, 2, 2], (codes, errs)
    assert runs.budget(conn, "ep-res")["remaining"] == 0
    assert not (tmp_path / ".claude" / "work" / "fable_budget.json").exists()  # the ledger decided, not the file
    spawns = json.loads((tmp_path / ".claude" / "work" / "spawns.json").read_text())
    reserved = [v for v in spawns.values() if v.get("run_id")]
    assert len(reserved) == 3
    tid = next(k for k, v in spawns.items() if v.get("run_id"))
    payload = post(tool_use_id=tid, model="fable")
    payload["tool_input"]["description"] = "[sdlc stage=epic.decompose node=ep-res] plan"
    r = run_hook(RECORD, payload, tmp_path, {k: v for k, v in env.items() if k.startswith(("SDLC_", "DATABASE"))})
    assert r.returncode == 0 and r.stderr == "", r.stderr
    rows = conn.execute("SELECT count(*) AS n FROM run WHERE tool_use_id = %s", (tid,)).fetchone()
    assert rows["n"] == 1
    assert conn.execute("SELECT outcome FROM run WHERE tool_use_id = %s", (tid,)).fetchone()["outcome"] == "ok"


# --------------------------------------------------------------------------- model routing config

SWITCH = ROOT / "hooks" / "record_model_switch.py"


def test_model_switch_logs_and_exits_zero(tmp_path):
    r = run_hook(SWITCH, {"hook_event_name": "PostModelSwitch", "from_model": "opus", "to_model": "haiku", "session_id": "s1"}, tmp_path)
    assert r.returncode == 0
    line = (tmp_path / ".claude" / "work" / "model_switches.jsonl").read_text().strip()
    rec = json.loads(line)
    assert (rec["event"], rec["from_model"], rec["to_model"]) == ("PostModelSwitch", "opus", "haiku")


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", ""])
def test_model_switch_garbage_never_fails(tmp_path, raw):
    r = run_hook(SWITCH, None, tmp_path, raw=raw)
    assert r.returncode == 0


def test_model_switch_unwritable_dir_exits_zero(tmp_path):
    (tmp_path / ".claude").write_text("a file, not a directory")
    r = run_hook(SWITCH, {"from_model": "a", "to_model": "b"}, tmp_path)
    assert r.returncode == 0 and "not logged" in r.stderr


def test_model_switch_registered_for_both_events():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    for ev in ("PreModelSwitch", "PostModelSwitch"):
        assert "record_model_switch.py" in json.dumps(hooks[ev])


def test_spawn_guard_subagent_types_have_real_agents():
    import re
    src = GUARD.read_text()
    m = re.search(r"SUBAGENT_STAGES[^=]*=\s*\{(.*?)\}", src, re.S)
    names = set(re.findall(r'"([\w-]+)":', m.group(1)))
    assert {"planner", "triage"} <= names
    policy = json.loads((ROOT / "sdlc_graph" / "ledger" / "routing_policy.json").read_text())
    for name in names:
        text = (ROOT / "agents" / f"{name}.md").read_text()
        fm = text.split("---")[1]
        assert f"name: {name}" in fm
        model = re.search(r"^model:\s*(\w+)", fm, re.M).group(1)
        stage = re.search(rf'"{name}":\s*"([\w.]+)"', src).group(1)
        assert model == policy["tiers"][policy["stages"][stage]], name
        assert "Write" not in fm.split("tools:")[1].split("\n")[0] if name in ("planner", "triage") else True


def test_run_row_records_default_and_override(tmp_path, pg_schema, conn, test_database_url):
    """Integration: an Opus request on a tests-stage spawn is not rewritten; the run row has the requested and
    resolved model and meta.default / meta.override."""
    env = {"SDLC_GRAPH_BIN": str(CLI), "SDLC_SCHEMA": pg_schema, "DATABASE_URL": test_database_url}
    payload = post(tool_use_id="toolu_ov", model="opus")
    assert run_hook(GUARD, payload, tmp_path, env).returncode == 0
    assert run_hook(RECORD, payload, tmp_path, env).returncode == 0
    row = conn.execute("SELECT requested_model, resolved_model, meta FROM run WHERE tool_use_id = %s", ("toolu_ov",)).fetchone()
    assert (row["requested_model"], row["resolved_model"]) == ("opus", "claude-sonnet-5-5")
    assert row["meta"]["default"] == "sonnet" and row["meta"]["override"] is True
