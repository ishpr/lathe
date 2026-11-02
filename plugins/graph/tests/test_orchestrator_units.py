"""Orchestrator pieces that need no executor: path matching, prime, judge parsing, the hook entry,
the schema additions and the step table."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sdlc_graph import cli, db
from sdlc_graph.ledger import evidence, intake, nodes, transitions
from sdlc_graph.orchestrator import CONFORMANCE_KIND, contract_blocks, gitops, judge, prime, steps, store

ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = f"{sys.executable} -I -m sdlc_graph.orchestrator.fake_claude"


@pytest.mark.parametrize("path,allowed,out", [
    ("app/ratelimit/x.py", ["app/ratelimit/**"], False),
    ("app/ratelimit/deep/x.py", ["app/ratelimit/"], False),
    ("app/ratelimit", ["app/ratelimit"], False),
    ("app/ratelimit/x.py", ["app/ratelimit"], False),
    ("app/main.py", ["app/*.py"], False),
    ("app/sub/main.py", ["app/*.py"], True),
    ("tests/a/test_x.py", ["**/test_*.py"], False),
    ("drift/outside.txt", ["app/**", "tests/**"], True),
    ("README.md", ["./README.md"], False),
])
def test_outside_matches_run_headless_dialect(path, allowed, out):
    assert (gitops.outside([path], allowed) == [path]) is out


def test_changed_files_sees_commits_edits_and_untracked_but_not_work_dir(tmp_path):
    r = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / "a.txt").write_text("1")
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@e"]
    subprocess.run([*g, "add", "a.txt"], check=True)
    subprocess.run([*g, "commit", "-qm", "a"], check=True)
    start = gitops.head(r)
    (r / "b.txt").write_text("2")
    subprocess.run([*g, "add", "b.txt"], check=True)
    subprocess.run([*g, "commit", "-qm", "b"], check=True)
    (r / "a.txt").write_text("changed")
    (r / "c.txt").write_text("untracked")
    (r / ".claude" / "work").mkdir(parents=True)
    (r / ".claude" / "work" / "current.json").write_text("{}")
    assert gitops.changed_files(r, start) == ["a.txt", "b.txt", "c.txt"]
    assert gitops.dirty_files(r) == ["a.txt", "c.txt"]
    gitops.reset_to(r, start)
    assert gitops.changed_files(r, start) == [] and (r / ".claude/work/current.json").exists()


def test_step_table_matches_the_state_machine():
    machine = transitions.machine("contract")
    edges = {(t["from"], t["to"]) for t in machine["transitions"]}
    for state, step in steps.STEPS.items():
        assert (step.from_state, step.to_state) in edges and state == step.from_state
    assert steps.step_for("drafted") is None and steps.step_for("in_review") is None
    assert steps.done_states() == {"in_review", "approved", "merged"} and steps.done_states(True) == {"merged"}


def test_conformance_kind_registered():
    assert CONFORMANCE_KIND in evidence.KINDS
    evidence.validate(CONFORMANCE_KIND, {"score": 0.9, "findings": []})
    with pytest.raises(ValueError):
        evidence.validate(CONFORMANCE_KIND, {"score": 0.9})


def test_schema_has_the_orchestrator_columns(clean_conn, pg_schema):
    cols = {r["column_name"] for r in clean_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'run'", (pg_schema,))}
    assert set(store.RUN_COLUMNS) <= cols
    assert "orchestrator_lock" in db.list_tables(clean_conn, pg_schema)
    nodes.create(clean_conn, kind="feature", slug="f-sev", title="f", repo="r", created_by="@a")
    d = store.escalate(clean_conn, "f-sev", key="k", question="q?", severity="P0", options=["retry", "skip"])
    assert d["severity"] == "P0" and d["meta"]["key"] == "k"
    assert store.escalate(clean_conn, "f-sev", key="k", question="again?")["id"] == d["id"]   # deduped
    with pytest.raises(Exception):
        clean_conn.execute("UPDATE decision SET severity = 'P9' WHERE id = %s", (d["id"],))


# --------------------------------------------------------------------------- judge


def test_parse_output_structured_and_text():
    data, _ = judge.parse_output(json.dumps({"type": "result", "structured_output": {"choice": "a"}}))
    assert data == {"choice": "a"}
    data, _ = judge.parse_output(json.dumps({"type": "result", "result": "```json\n{\"score\": 0.4, \"findings\": []}\n```"}))
    assert data["score"] == 0.4
    with pytest.raises(judge.ModelCallError):
        judge.parse_output(json.dumps({"type": "result", "is_error": True, "result": "boom"}))
    with pytest.raises(judge.ModelCallError):
        judge.parse_output("not json")


def test_judge_with_fake_claude(clean_conn, monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_CLAUDE", FAKE_CLAUDE)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    nodes.create(clean_conn, kind="feature", slug="f-judge", title="f", repo="r", created_by="@a")
    out = judge.judge(clean_conn, "f-judge", question="retry?", options=["retry", "escalate"], context={"x": 1})
    assert out == {"choice": "retry", "reason": "fake judge", "escalate": False}
    call = json.loads(log.read_text().splitlines()[0])["argv"]
    assert call[call.index("--max-turns") + 1] == "6" and call[call.index("--output-format") + 1] == "json"
    assert call[call.index("--model") + 1] == "opus" and "--json-schema" in call
    monkeypatch.setenv("FAKE_JUDGE_CHOICE", "dance")
    assert judge.judge(clean_conn, "f-judge", question="q", options=["retry"], context={})["escalate"] is True
    rows = store.step_runs(clean_conn, "f-judge", "judge")
    assert [r["outcome"] for r in rows] == ["ok", "escalate"] and rows[0]["model_tier"] == "orchestrator"


# --------------------------------------------------------------------------- prime


def _run_prime(project: Path, *extra: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["prime", "--project-dir", str(project), *extra])
    return rc, buf.getvalue()


def test_prime_prints_contract_state_and_next_step(clean_conn, tmp_path):
    res = intake.contract_draft(clean_conn, slug="prime-me", title="Prime this contract", repo="r", actor="@a",
                                ticket_text="Prime it.", touched_symbols=["app/p/"], capabilities={"paths": ["app/p/**"]},
                                acceptance=[{"id": "AC1", "text": "works"}])
    assert res["status"] == "created"
    contract_blocks.set_blocks(clean_conn, "prime-me", actor="@a", defaults=["Make the smallest change that satisfies the acceptance criteria.",
                                         "Follow the nearest existing pattern in the code being changed.",
                                         "Prefer the option the Decisions above imply.",
                                         "When none applies, choose the reversible option."],
                               capabilities={"commands": [], "egress": [], "data": []})
    assert transitions.transition(clean_conn, "prime-me", "ready", "@a")["ok"]
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "current.json").write_text(json.dumps({"slug": "prime-me", "ticket": "Prime this\x1b[31m contract",
                                                   "allowed_paths": ["app/p/**"], "tier": "low"}))
    rc, out = _run_prime(tmp_path)
    assert rc == 0
    assert "Active contract: prime-me" in out and "ledger state: ready -> tests_frozen" in out
    assert "/sdlc:tests" in out and "\x1b" not in out
    rc, out = _run_prime(tmp_path, "--json")
    assert rc == 0 and json.loads(out)["ledger"]["state"] == "ready"


def test_prime_is_silent_without_current_json_and_never_fails(tmp_path, monkeypatch):
    assert _run_prime(tmp_path) == (0, "")
    work = tmp_path / ".claude" / "work"
    work.mkdir(parents=True)
    (work / "current.json").write_text(json.dumps({"slug": "nowhere", "allowed_paths": ["x/**"]}))
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none")
    rc, out = _run_prime(tmp_path)
    assert rc == 0 and "ledger unreachable" in out and "nowhere" in out
    (work / "current.json").write_text("{broken")
    rc, out = _run_prime(tmp_path)
    assert rc == 0 and "unreadable" in out


def test_session_start_hook_runs_prime_only_with_current_json(tmp_path):
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "sdlc-graph prime" in cmd and ".claude/work/current.json" in cmd and cmd.rstrip().endswith("exit 0")
    # With no current.json the hook prints nothing and exits 0 (no uv, no database needed).
    proc = subprocess.run(["bash", "-c", cmd], env={"CLAUDE_PROJECT_DIR": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(ROOT),
                                                    "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout == ""


def test_orchestrate_skill_is_human_only():
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text()
    head = text.split("---")[1]
    assert "name: orchestrate" in head and "disable-model-invocation: true" in head
    assert "sdlc-graph orchestrate" in text and "--stop" in text


def test_snapshot_and_restore_keep_uncommitted_work_from_before_the_step(tmp_path):
    r = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@e"]
    (r / "a.txt").write_text("1")
    (r / "b.txt").write_text("b")
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "init"], check=True)
    start = gitops.head(r)
    assert gitops.snapshot(r) is None
    # uncommitted work an earlier step left: an edit, a deletion, a new file
    (r / "a.txt").write_text("earlier step")
    (r / "b.txt").unlink()
    (r / "c.txt").write_text("new")
    (r / ".claude" / "work").mkdir(parents=True)
    (r / ".claude" / "work" / "current.json").write_text("{}")
    snap = gitops.snapshot(r)
    assert snap and gitops.head(r) == start and gitops.dirty_files(r) == ["a.txt", "b.txt", "c.txt"]
    # this step drifts: more edits, a new file, a commit
    (r / "a.txt").write_text("this step")
    (r / "d.txt").write_text("drift")
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "drift"], check=True)
    gitops.restore(r, start, snap)
    assert gitops.head(r) == start
    assert (r / "a.txt").read_text() == "earlier step" and not (r / "b.txt").exists()
    assert (r / "c.txt").read_text() == "new" and not (r / "d.txt").exists()
    assert (r / ".claude" / "work" / "current.json").exists()
    assert gitops.dirty_files(r) == ["a.txt", "b.txt", "c.txt"]


# --------------------------------------------------------------------------- executor modes and credential recording

CRED_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "ANTHROPIC_AWS_WORKSPACE_ID",
             "ANTHROPIC_AWS_API_KEY", "CLAUDE_CODE_USE_BEDROCK")


def test_auth_source_detection_and_precedence():
    from sdlc_graph.orchestrator import auth

    assert auth.detect_auth_source({}) == "subscription"
    assert auth.detect_auth_source({"ANTHROPIC_API_KEY": "  "}) == "subscription"
    assert auth.detect_auth_source({"CLAUDE_CODE_USE_BEDROCK": "1"}) == "bedrock"
    aws = {"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1", "ANTHROPIC_AWS_WORKSPACE_ID": "ws"}
    assert auth.detect_auth_source(aws) == "claude_platform_aws"
    assert auth.detect_auth_source({"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1"}) == "subscription"      # no workspace id
    assert auth.detect_auth_source({**aws, "CLAUDE_CODE_USE_BEDROCK": "1"}) == "claude_platform_aws"
    tok = {**aws, "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    assert auth.detect_auth_source(tok) == "oauth_token"
    assert auth.detect_auth_source({**tok, "ANTHROPIC_API_KEY": "k"}) == "api_key"


def test_executor_mode_is_validated_and_defaults_to_local():
    from sdlc_graph.orchestrator import auth, config

    assert config.Config(repo=".").executor_mode == "local"
    with pytest.raises(ValueError):
        config.Config(repo=".", executor_mode="cloud")
    assert auth.check_mode("local", {}) == "subscription"


@pytest.mark.parametrize("env,source", [
    ({"ANTHROPIC_API_KEY": "sk-dummy"}, "api_key"),
    ({"CLAUDE_CODE_OAUTH_TOKEN": "tok-dummy"}, "oauth_token"),
    ({"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1", "ANTHROPIC_AWS_WORKSPACE_ID": "ws-dummy", "ANTHROPIC_AWS_API_KEY": "k"}, "claude_platform_aws"),
    ({"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1", "ANTHROPIC_AWS_WORKSPACE_ID": "ws-dummy"}, "claude_platform_aws"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "bedrock"),
])
def test_runner_accepts_each_credential_form(env, source):
    from sdlc_graph.orchestrator import auth

    assert auth.check_mode("runner", env) == source


def test_runner_refuses_without_a_credential_and_names_the_four_options():
    from sdlc_graph.orchestrator import auth

    with pytest.raises(ValueError) as exc:
        auth.check_mode("runner", {"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1"})
    msg = str(exc.value)
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AWS_WORKSPACE_ID", "CLAUDE_CODE_USE_BEDROCK"):
        assert name in msg


def test_the_step_executor_passes_permission_prompts_none_and_never_bare():
    text = (Path(__file__).resolve().parent.parent / "sdlc_graph" / "orchestrator" / "step_executor.sh").read_text()
    cmdline = next(line for line in text.splitlines() if "claude -p" in line and line.startswith("(cd"))
    assert "--permission-prompts none" in cmdline and "--permission-mode dontAsk" in cmdline
    assert "--bare" not in cmdline
