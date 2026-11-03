"""Integration items of the autonomy build: the contract's autonomy blocks attached at draft time (intake,
CLI, MCP), a ledger-rendered contract.md that the sdlc plugin's ``acceptance.py --ready`` accepts, the
``assumptions`` evidence kind in both payload shapes, and PreToolUse hook blocks reported through
``hook_response`` events."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sdlc_graph import cli
from sdlc_graph.ledger import evidence, intake, nodes, transitions
from sdlc_graph.orchestrator import autonomy, contract_blocks, dispatch

from test_mcp import _call, server  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_MD = ROOT / "demo" / "data" / "rate-limit.contract.md"
ACCEPTANCE_PY = ROOT.parent / "sdlc" / "scripts" / "acceptance.py"
CAPS = {"paths": ["app/ratelimit/**", "tests/ratelimit/**"]}
ACC = [{"id": "AC1", "text": "the 101st request gets 429", "test": "tests/ratelimit/test_limiter.py::test_over_limit"}]
STANDARD = list(contract_blocks.POLICY_EXAMPLE)


def _draft(conn, slug="rate-limit", **kw):
    args = dict(slug=slug, title="Per-key rate limiting at 100 requests a minute", repo="sample-app", actor="@ish",
                ticket_text="Rate limit the API per key.", touched_symbols=["app/ratelimit/"], capabilities=CAPS,
                acceptance=ACC)
    args.update(kw)
    return intake.contract_draft(conn, **args)


# --------------------------------------------------------------------------- draft-time blocks


def test_contract_text_at_draft_time_attaches_both_blocks_and_ready_passes(clean_conn):
    out = _draft(clean_conn, contract_text=CONTRACT_MD.read_text(), tier="high")
    assert out["status"] == "created" and out["blocks"]["missing"] == [] and out["blocks"]["unknown"] == []
    node = out["node"]
    assert node["attrs"]["defaults"][:4] == STANDARD
    assert node["capabilities"]["commands"] == ["uv run pytest tests/ratelimit -q", "bash scripts/smoke_rate_limit.sh"]
    assert node["capabilities"]["paths"] == CAPS["paths"]          # an explicit key wins over the block's line
    assert node["capabilities"]["egress"] == ["pypi.org", "files.pythonhosted.org"]   # the keys not passed come from it
    assert node["contract_md"] == CONTRACT_MD.read_text()
    assert [d["handle"] for d in node["attrs"]["decisions"]] == ["@live"]   # confirmed, so tier high may be ready
    ok, missing = transitions.evaluate(nodes.get_row(clean_conn, "rate-limit"), "ready", [], "@ish")
    assert ok, missing


def test_explicit_defaults_replace_the_texts_and_no_blocks_leaves_the_guard_asking(clean_conn):
    out = _draft(clean_conn, defaults=STANDARD, capabilities={**CAPS, "commands": [], "egress": [], "data": []})
    assert out["status"] == "created" and out["blocks"]["missing"] == []
    assert nodes.get_row(clean_conn, "rate-limit")["attrs"]["defaults"] == STANDARD
    plain = _draft(clean_conn, slug="headers", title="Expose X-RateLimit headers on every response",
                   touched_symbols=["app/headers/"])
    assert "blocks" not in plain
    r = transitions.transition(clean_conn, "headers", "ready", "@ish")
    assert not r["ok"] and {"defaults_present", "capabilities_present"} <= set(r["missing"])


def test_a_refused_capability_creates_nothing(clean_conn):
    bad = CONTRACT_MD.read_text().replace("- egress: `pypi.org`", "- egress: `not a host!`")
    with pytest.raises(ValueError):
        _draft(clean_conn, contract_text=bad)
    with pytest.raises(LookupError):
        nodes.get_row(clean_conn, "rate-limit")


def test_cli_node_draft_takes_contract_and_default(clean_conn, capsys):
    code = cli.main(["node", "draft", "--slug", "c-cli", "--title", "Per-key rate limiting from the command line",
                     "--repo", "sample-app", "--actor", "@ish", "--ticket", "- [ ] AC1: it works",
                     "--touched", "app/ratelimit/", "--tier", "high", "--contract", str(CONTRACT_MD), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "created" and out["blocks"]["missing"] == []
    code = cli.main(["node", "draft", "--slug", "c-cli2", "--title", "Expose rate headers from the command line",
                     "--repo", "sample-app", "--actor", "@ish", "--ticket", "x", "--touched", "app/headers/",
                     "--default", STANDARD[0], "--default", STANDARD[1], "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and nodes.get_row(clean_conn, "c-cli2")["attrs"]["defaults"] == STANDARD[:2]
    # two lines are not the standard four, and nothing declared the capabilities: the ready guard will ask for both
    assert out["blocks"]["missing"] == ["## Defaults when ambiguous", "## Capabilities (paths, commands, egress, data)"]


def test_mcp_contract_draft_takes_contract_md(server, clean_conn):  # noqa: F811
    args = {"slug": "c-mcp", "title": "Per-key rate limiting through MCP", "repo": "sample-app",
            "ticket_text": "Rate limit the API per key.", "touched_symbols": ["app/ratelimit/"],
            "contract_md": CONTRACT_MD.read_text()}
    # the MCP tool is strict by default: the demo file has no Acceptance criteria and no Risk tier
    is_error, out = _call(server, "contract_draft", args)
    assert not is_error and out["status"] == "refused" and out["node"] is None, out
    assert out["blocks"]["missing"] == ["## Acceptance criteria", "## Risk tier"]
    with pytest.raises(LookupError):
        nodes.get_row(clean_conn, "c-mcp")
    is_error, out = _call(server, "contract_draft", {**args, "strict": False})
    assert not is_error and out["status"] == "created" and out["blocks"]["missing"] == ["## Acceptance criteria", "## Risk tier"], out
    assert nodes.get_row(clean_conn, "c-mcp")["attrs"]["defaults"][:4] == STANDARD


@pytest.mark.skipif(not ACCEPTANCE_PY.is_file(), reason="the sdlc plugin is not next to this one")
def test_a_ledger_rendered_contract_passes_acceptance_ready(clean_conn, tmp_path):
    _draft(clean_conn, contract_text=CONTRACT_MD.read_text())
    clean_conn.execute("UPDATE node SET acceptance = %s WHERE slug = 'rate-limit'",
                       (json.dumps(ACC + [{"id": "AC2", "text": "does it reset?", "command": "python3 -m pytest tests/ratelimit -q"}]),))
    node = nodes.get_row(clean_conn, "rate-limit")
    text = dispatch.contract_md(clean_conn, {**node, "id": str(node["id"])})
    assert "## Defaults when ambiguous" in text and "## Capabilities" in text and "\nNone.\n" in text
    path = tmp_path / "contract.md"
    path.write_text(text)
    proc = subprocess.run([sys.executable, "-I", str(ACCEPTANCE_PY), "--root", str(tmp_path), "--contract", str(path), "--ready"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "READY" in proc.stdout and "FILL" not in proc.stdout


# --------------------------------------------------------------------------- assumptions evidence


def test_assumptions_evidence_accepts_both_shapes_and_normalises_items(clean_conn):
    _draft(clean_conn)
    orch = {"assumptions": [{"default_applied": "fixed window", "where": "app/ratelimit/limiter.py", "why": "unspecified"}]}
    plugin = {"path": ".claude/work/rate-limit/assumptions.md", "count": 1,
              "items": [{"id": "A1", "stage": "implement", "where": "app/ratelimit/store.py", "ambiguity": "TTL unstated",
                         "default": "reversible option", "choice": "60 s TTL"}]}
    assert evidence.add(clean_conn, "rate-limit", "assumptions", orch)["kind"] == "assumptions"
    assert evidence.add(clean_conn, "rate-limit", "assumptions", plugin)["kind"] == "assumptions"
    assert evidence.add(clean_conn, "rate-limit", "assumptions", {"path": "a.md", "count": 0, "items": []})
    for bad in ({}, {"assumptions": [{"default_applied": "x"}]}, {"path": "a.md", "count": -1, "items": []},
                {"path": "a.md", "items": []}):
        with pytest.raises(ValueError):
            evidence.validate("assumptions", bad)
    assert evidence.assumption_items(plugin)[0] | {} == {
        **plugin["items"][0], "default_applied": "reversible option", "where": "app/ratelimit/store.py", "why": "TTL unstated"}
    assert evidence.assumption_items(orch) == orch["assumptions"]


def test_mcp_evidence_add_assumptions(server, clean_conn):  # noqa: F811
    _draft(clean_conn)
    is_error, out = _call(server, "evidence_add", {"slug": "rate-limit", "kind": "assumptions", "payload": {
        "path": ".claude/work/rate-limit/assumptions.md", "count": 0, "items": []}})
    assert not is_error and out.get("kind") == "assumptions", out


def test_session_recorded_plugin_shape_assumptions_are_ingested(clean_conn, tmp_path):
    import datetime as dt

    _draft(clean_conn)
    t0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
    evidence.add(clean_conn, "rate-limit", "assumptions", {"path": "x", "count": 2, "items": [
        {"default": "smallest change", "where": "app/ratelimit/limiter.py", "ambiguity": "window unstated"},
        {"default": "half an item"}]})
    got = autonomy.ingest_assumptions(clean_conn, "rate-limit", tmp_path, run_id=1, step="implement", started_at=t0)
    assert got["recorded"] and [a["default_applied"] for a in got["assumptions"]] == ["smallest change"]
    assert any("missing where, why" in p for p in got["problems"])


# --------------------------------------------------------------------------- hook_response denials


def _write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def test_hook_response_exit_2_with_blocked_is_a_guard_refusal_not_a_permission_denial(tmp_path):
    use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                                         "input": {"command": "git push origin HEAD:main"}}]}}
    log = _write(tmp_path / "s.jsonl", [
        use,
        {"type": "system", "subtype": "hook_response", "hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse",
         "exit_code": 2, "stderr": "Blocked: no pushes to a protected branch; open a PR.", "tool_use_id": "t1"},
        # a hook that failed without blocking, a PostToolUse exit 2 and an ok hook are not denials
        {"type": "system", "subtype": "hook_response", "hook_name": "PreToolUse:Bash", "exit_code": 1, "stderr": "Traceback"},
        {"type": "system", "subtype": "hook_response", "hook_name": "PostToolUse:Bash", "exit_code": 2, "stderr": "Blocked: x"},
        {"type": "system", "subtype": "hook_response", "hook_name": "PreToolUse:Edit", "exit_code": 0, "stdout": "ok"},
        {"type": "result", "permission_denials": []},
    ])
    got = autonomy.denials(log, None, tmp_path)
    assert [(d["tool"], d["target"], d["reason"], d["source"]) for d in got] == [
        ("Bash", "git push origin HEAD:main", "guard_refused", "hook_response")]
    # the tool result that shows the same block is the same denial, reported once
    log = _write(tmp_path / "s2.jsonl", [
        use,
        {"type": "system", "subtype": "hook_response", "hook_name": "PreToolUse:Bash", "exit_code": 2,
         "stderr": "Blocked: no pushes to a protected branch; open a PR."},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content":
            "PreToolUse:Bash hook blocking error from command: guard_bash.py: Blocked: no pushes to a protected branch; open a PR."}]}},
    ])
    got = autonomy.denials(log, None, tmp_path)
    assert [(d["source"], d["reason"]) for d in got] == [("hook", "guard_refused")]
    # a real permission_denied system event stays a permission denial (no guard reason)
    log = _write(tmp_path / "s3.jsonl", [
        {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_input": {"command": "make deploy"},
         "tool_use_id": "t9"}])
    got = autonomy.denials(log, None, tmp_path)
    assert [(d["source"], d.get("reason")) for d in got] == [("system", None)]
