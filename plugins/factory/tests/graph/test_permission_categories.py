"""Permissions without a script of commands (docs/DESIGN.md section 18.5): an unattended executor's allow list opens
read-only commands by category, the pack's required deny rules close their write and run forms in every executor's
settings, and a denial that still happens escalates with the missing rule named.

The unattended decision is computed the way Claude Code documents it (https://code.claude.com/docs/en/permissions):
every part of a chain or pipe is checked, deny is evaluated before allow and wins, a rule's `*` matches any text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc_graph.orchestrator import autonomy, config, dispatch, store
from sdlc_graph.orchestrator.steps import STEPS

from test_orchestrator import A, cfg_for, env, feature, repo, tick_until  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = json.loads((ROOT / "templates" / "required_denies.json").read_text())["deny"]
NODE = {"capabilities": {"paths": ["app/ratelimit/**"], "commands": [], "egress": [], "data": [], "secrets": []}}


def _executor(tmp_path: Path, state: str = "ready") -> tuple[list[str], list[str]]:
    """(allow, deny) of the step that starts from ``state``: the compiled --allowedTools and the executor's settings deny."""
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "sdlc.json").write_text(json.dumps({"stack": "python", "commands": {"test": "python3 -m pytest -q"}}))
    cfg = config.Config(repo=tmp_path, plugin_dirs=[])
    allow = autonomy.allowed_tools(NODE, STEPS[state].name, cfg)["tools"]
    deny = dispatch.settings_for(NODE, STEPS[state], cfg)["permissions"]["deny"]
    return allow, deny


def _bash(rules: list[str]) -> list[str]:
    return [r[5:-1] for r in rules if r.startswith("Bash(") and r.endswith(")")]


def unattended(cmd: str, allow: list[str], deny: list[str]) -> str:
    """'allowed', 'denied by <rule>' or 'no rule for <part>', as dontAsk decides a Bash call."""
    parts = autonomy.command_parts(cmd)
    for part in parts:
        hit = next((r for r in _bash(deny) if autonomy.rule_matches(r, part)), None)
        if hit:
            return f"denied by Bash({hit})"
    for part in parts:
        if not any(autonomy.rule_matches(r, part) for r in _bash(allow)):
            return f"no rule for {part}"
    return "allowed"


ALLOWED = [
    "rg pattern",
    "rg -n 'limit|quota' app | head -20",
    "find . -name '*.py' -newer app/main.py",
    "cat README.md",
    "sed -n 1,40p app/main.py",
    "jq .dependencies package.json",
    "sort out.txt | uniq -c",
    "wc -l app/main.py",
    "diff app/a.py app/b.py",
    "git log -p -3 -- app",
    "git blame app/main.py",
    "git grep -n limiter",
    "git status && git diff --stat",
    "git stash list",
    "git remote -v",
    "git describe --tags",
    "git branch -a",
    "python3 -m pytest -q -k limiter",
]

DENIED = [
    "find . -delete",
    "find . -name '*.pyc' -exec rm {} +",
    "find . -execdir sh -c id {} +",
    "find . -fprint out.txt",
    "git push --force origin feat/x",
    "git status && git push --force origin feat/x",
    "git reset --hard HEAD~1",
    "git commit --no-verify -m x",
    "gh pr merge 3",
    "sed -n -i 's/a/b/' app/main.py",
    "rg --pre ./run.sh pattern",
    "jq -n env",
    "sort -o app/main.py app/main.py",
    "git grep -O vim pattern",
    "git diff --output=.claude/baseline.json",
    "python3 -I scripts/ratchet.py --update",
]

NOT_ALLOWED = ["uv run python -c 'import os'", "make deploy", "bash -c id", "npx some-tool", "python3 app/main.py",
               "git branch -D main", "awk '{print}' app/main.py", "uniq a.txt b.txt"]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_a_category_allowed_command_is_not_denied_unattended(tmp_path, cmd):
    allow, deny = _executor(tmp_path)
    assert unattended(cmd, allow, deny) == "allowed"


@pytest.mark.parametrize("cmd", DENIED)
def test_a_denied_command_is_still_denied_whatever_the_category_allows(tmp_path, cmd):
    allow, deny = _executor(tmp_path, "verified")   # the ship step has the widest list: push and PR rules
    assert unattended(cmd, allow, deny).startswith("denied by "), cmd


@pytest.mark.parametrize("cmd", NOT_ALLOWED)
def test_no_blanket_runner_or_write_form_is_opened(tmp_path, cmd):
    allow, deny = _executor(tmp_path, "verified")
    assert unattended(cmd, allow, deny) != "allowed", cmd


def test_every_executor_settings_file_carries_every_required_deny_rule(tmp_path):
    for step in ("ready", "tests_frozen", "verified"):
        _, deny = _executor(tmp_path, step)
        assert not set(REQUIRED) - set(deny), step
    # reading .env is closed for the Read tool and for Claude Code's file commands in Bash (cat, head, tail, sed)
    assert "Read(**/.env)" in REQUIRED and "Read(./.env)" in deny


def test_the_required_denies_are_the_settings_templates_and_a_missing_file_refuses_the_run(tmp_path, monkeypatch):
    tpl = json.loads((ROOT / "templates" / "settings.project.json").read_text())["permissions"]["deny"]
    assert not set(REQUIRED) - set(tpl) and len(REQUIRED) >= 56
    pack = tmp_path / "pack" / "scripts"
    pack.mkdir(parents=True)
    (pack / "run_headless.sh").write_text("DEFAULT_TOOLS='Read'\n")
    monkeypatch.setenv(autonomy.RUN_HEADLESS_ENV, str(pack / "run_headless.sh"))
    with pytest.raises(FileNotFoundError, match="required deny rules"):
        autonomy.required_denies([])


def test_a_chain_denied_for_one_part_names_that_parts_rule_and_is_not_a_form_denial():
    allow = ["Bash(rg *)", "Bash(head *)"]
    d = autonomy.describe("Bash", {"command": "rg -n x app | frob -1"})
    autonomy.name_missing_part(d, allow)
    assert d["missing_part"] == "frob -1" and d["rule"] == "Bash(frob -1 *)" and not autonomy.is_form(d)
    shaped = autonomy.describe("Bash", {"command": "rg -n 'a|b' app | head -1 > out.txt"})
    autonomy.name_missing_part(shaped, allow)    # every part is allowed: refused for its redirection, a form denial
    assert "missing_part" not in shaped and autonomy.is_form(shaped)


@pytest.mark.parametrize("command,rule", [("find . -delete", "Bash(find . -delete *)"),
                                          ("git push --force origin feat/x", "Bash(git push --force origin feat/x *)")])
def test_a_denial_in_an_unattended_run_escalates_naming_the_rule(feature, repo, env, monkeypatch, command, rule):
    conn = feature
    monkeypatch.setenv("FAKE_DENIED_COMMAND", command)
    env({f"{A}:tests": "denied"})
    cfg = cfg_for(repo)
    tick_until(conn, cfg, lambda r: bool(store.open_escalations(conn, [A])))
    assert [r["outcome"] for r in store.step_runs(conn, A, "tests")] == ["permission_denied"]
    esc = store.open_escalations(conn, [A])
    assert len(esc) == 1 and esc[0]["severity"] == "P1"
    q = esc[0]["question"]
    assert f"Bash `{command}`" in q and f"rule that would have allowed it: `{rule}`" in q, q
    assert "cannot pre-approve" in q, q   # a forbidden form: the human refuses it or changes the step, never a contract rule
    assert esc[0]["meta"]["denials"] == [{"tool": "Bash", "target": command, "rule": rule}]
