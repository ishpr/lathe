"""Regression tests for the autonomy review findings (docs/DESIGN.md section 14): the unattended executor's
allow list has no blanket runner, the settings deny the file-writing and human-only forms, /ship clears the work state
through workfile.py, the skill permission checker reads every command and the staging Write tool, and
`acceptance.py --ready` refuses decisions that only look decided. One file for both pack forms (`repo.form`)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _paths(repo):
    reg = repo.registration_path().parent
    if repo.form == "kit":
        root = reg.parent
        return SimpleNamespace(root=root, skills=reg / "skills", settings=reg / "settings.json",
                               script=lambda n: repo.script_path(n), plugin_form=False)
    root = reg.parent
    return SimpleNamespace(root=root, skills=root / "skills", settings=root / "templates" / "settings.project.json",
                           script=lambda n: repo.script_path(n), plugin_form=True)


def _checker(repo):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cspm_under_test", repo.script_path("check_skill_permissions.py"))
    mod = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def _rules(repo):
    p = _paths(repo)
    perms = json.loads(p.settings.read_text(encoding="utf-8"))["permissions"]
    return _checker(repo), perms


def _expand(repo, command: str) -> list[str]:
    """The spellings a skill can use for a plugin script: bare (kit) or quoted/unquoted plugin root (plugin)."""
    return [command]


# ---------------------------------------------------------------------------------------------
# finding 1: no blanket `uv run*` / `make *` in the headless allow list
# ---------------------------------------------------------------------------------------------

def test_run_headless_default_tools_have_no_blanket_runner(repo):
    text = repo.script_path("run_headless.sh").read_text(encoding="utf-8")
    m = re.search(r"^DEFAULT_TOOLS='([^']*)'", text, re.M)
    assert m, "run_headless.sh lost its DEFAULT_TOOLS line"
    rules = re.findall(r"Bash\([^)]*\)", m.group(1))
    assert "Bash(uv run*)" not in rules and "Bash(make *)" not in rules and "Bash(make*)" not in rules
    mod = _checker(repo)
    pats = [r[5:-1] for r in rules]
    for cmd in ("uv run git push origin HEAD:main", "uv run gh pr merge 12 --admin", "uv run git commit --no-verify -m x",
                "uv run git reset --hard HEAD~1", "uv run rm -rf tests", "uv run bash -lc id", "make deploy-prod"):
        assert not any(mod.rule_matches(r, cmd) for r in pats), cmd
    for cmd in ("uv run pytest -q", "uv run ruff check .", "uv run mypy app", "make test", "make lint"):
        assert any(mod.rule_matches(r, cmd) for r in pats), cmd


# ---------------------------------------------------------------------------------------------
# findings 2 and 7: the settings deny --output writes, commit -n, and the quoted human-only forms
# ---------------------------------------------------------------------------------------------

def test_settings_deny_git_output_writes_and_commit_bypass_and_quoted_human_forms(repo):
    mod, perms = _rules(repo)
    deny = mod.bash_patterns(perms["deny"], {})
    allow = mod.bash_patterns(perms["allow"], {"test_command": "python3 -m pytest", "lint_command": "ruff", "typecheck_command": "mypy",
                                               "format_command": "ruff format"})
    stamp = ".claude/work/last_verify.json"
    must_deny = [
        f"git log -1 --format=x --output={stamp}", f"git diff --output=.claude/baseline.json", f"git show HEAD --output=x",
        f"git log --outp={stamp}", "git commit -m x --no-verify", "git commit -n -m x", "git commit -m x -n",
        "python3 -I scripts/freeze_tests.py --unfreeze tests/x.py", "python3 -I scripts/ratchet.py --update",
        "python3 -I scripts/ratchet.py --prune",
        'python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --unfreeze tests/x.py',
        'python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --update',
        'python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --prune',
        "python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py --update",
    ]
    for cmd in must_deny:
        assert any(mod.rule_matches(r, cmd) for r in deny), f"no deny rule matches: {cmd}"
    # the ordinary forms stay allowed and are not denied
    fine = ["git log -1 --oneline", "git diff --stat", "git show HEAD --name-only", "git commit -m x"]
    for cmd in fine:
        assert any(mod.rule_matches(r, cmd) for r in allow), cmd
        assert not any(mod.rule_matches(r, cmd) for r in deny), cmd


def test_every_checked_skill_command_that_a_human_alone_runs_has_a_deny_rule(repo):
    p = _paths(repo)
    r = repo.run_script("check_skill_permissions.py", "--root", str(p.root), "--skills-dir", str(p.skills), "--settings", str(p.settings))
    assert "human-only, but no deny rule" not in r.out, r.text


# ---------------------------------------------------------------------------------------------
# finding 3: /ship's clear step goes through workfile.py --clear
# ---------------------------------------------------------------------------------------------

def _stamp(repo, ok=True):
    repo.write(".claude/work/last_verify.json", json.dumps({"ok": ok, "fingerprint": repo.fingerprint()}))


def test_ship_clears_the_work_state_through_workfile_clear_not_rm(repo):
    p = _paths(repo)
    text = (p.skills / "ship" / "SKILL.md").read_text(encoding="utf-8")
    assert "workfile.py" in text and "--clear" in text
    assert "rm -f .claude/work/current.json" not in text and 'rm -rf ".claude/work/' not in text


def test_workfile_clear_removes_the_contract_and_slug_dir_after_a_matching_stamp(repo):
    repo.contract(slug="rl")
    repo.write(".claude/work/rl/assumptions.md", "x\n")
    _stamp(repo)
    r = repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "rl")
    assert r.rc == 0, r.text
    assert not repo.exists(".claude/work/current.json") and not repo.exists(".claude/work/rl")
    assert repo.exists(".claude/work/last_verify.json")  # the stamp stays
    assert repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "rl").rc == 0  # already clear is fine


@pytest.mark.parametrize("slug", ["../x", "a/b", "", "UPPER", "-x", ".hidden", "x" * 60])
def test_workfile_clear_refuses_a_bad_slug(repo, slug):
    repo.contract(slug="rl")
    _stamp(repo)
    r = repo.run_script("workfile.py", "--root", str(repo.path), f"--clear={slug}")
    assert r.rc == 1 and repo.exists(".claude/work/current.json"), r.text


def test_workfile_clear_refuses_without_a_green_matching_stamp(repo):
    repo.contract(slug="rl")
    r = repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "rl")
    assert r.rc == 1 and "stamp" in r.text and repo.exists(".claude/work/current.json")
    _stamp(repo, ok=False)
    assert repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "rl").rc == 1
    _stamp(repo)
    repo.write("app/new.py", "x = 1\n")  # the tree changed after verify
    r = repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "rl")
    assert r.rc == 1 and "no longer matches" in r.text and repo.exists(".claude/work/rl")


def test_workfile_clear_refuses_a_symlinked_slug_directory(repo, tmp_path):
    repo.contract(slug="rl")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    shutil.rmtree(repo.path / ".claude" / "work" / "rl")
    (repo.path / ".claude" / "work" / "evil").symlink_to(target)
    _stamp(repo)
    r = repo.run_script("workfile.py", "--root", str(repo.path), "--clear", "evil")
    assert r.rc == 1 and (target / "keep.txt").exists(), r.text


# ---------------------------------------------------------------------------------------------
# findings 3, 10, 11: the permission checker reads every command, skipped parts and the staging Write tool
# ---------------------------------------------------------------------------------------------

def _plant(repo, tmp_path, body: str, front: str = "allowed-tools:\n  - Read\n  - Bash(git status *)\n", alone: bool = False):
    p = _paths(repo)
    skills = tmp_path / "skills"
    if alone:
        skills.mkdir()
    else:
        shutil.copytree(p.skills, skills)
    (skills / "planted").mkdir()
    (skills / "planted" / "SKILL.md").write_text(f"---\nname: planted\n{front}---\n{body}", encoding="utf-8")
    return p, skills


def _run_checker(repo, tmp_path, p, skills, *extra):
    return repo.run_script("check_skill_permissions.py", "--root", str(tmp_path), "--skills-dir", str(skills),
                           "--settings", str(p.settings), *extra)


def test_checker_flags_rm_docker_alembic_and_unknown_compound_parts(repo, tmp_path):
    p, skills = _plant(repo, tmp_path,
                       "1. Clean up with:\n   ```\n   rm -rf .claude/work/x\n   docker compose up -d\n   alembic upgrade head\n   ```\n"
                       "2. Then `git status --short | frobnicate -1` and `mkdir -p docs/x` and `curl https://x.invalid`.\n")
    r = _run_checker(repo, tmp_path, p, skills)
    assert r.rc == 1, r.text
    for needle in ("rm -rf .claude/work/x", "docker compose up -d", "alembic upgrade head", "unknown program 'frobnicate'",
                   "mkdir -p docs/x", "curl https://x.invalid"):
        assert f"{needle}" in r.out and "UNCOVERED" in r.out, (needle, r.out)
    assert re.search(r"UNCOVERED\tskills/planted/SKILL.md:\d+\trm -rf .claude/work/x", r.out)


def test_checker_covers_a_command_once_a_rule_allows_it(repo, tmp_path):
    p, skills = _plant(repo, tmp_path, "1. Run `tail -1 out.txt`.\n", "allowed-tools:\n  - Read\n  - Bash(tail *)\n")
    r = _run_checker(repo, tmp_path, p, skills)
    assert "tail -1" not in r.out or "UNCOVERED\tskills/planted/SKILL.md:6\ttail -1" not in r.out
    assert r.rc == 0, r.text


def test_checker_fails_a_skill_that_stages_files_without_a_write_rule(repo, tmp_path):
    p, skills = _plant(repo, tmp_path, "Write the file to `.sdlc-stage/x/questions.md` and move it.\n",
                       "allowed-tools:\n  - Read\n", alone=True)
    own = _run_checker(repo, tmp_path, p, skills, "--own-only")
    assert own.rc == 1 and "Edit(.sdlc-stage/**)" in own.out, own.text
    # a Write(<path>) rule never matches in Claude Code (an Edit rule covers the Write tool), so it covers nothing
    (skills / "planted" / "SKILL.md").write_text(
        "---\nname: planted\nallowed-tools:\n  - Read\n  - Write(.sdlc-stage/**)\n---\nWrite the file to `.sdlc-stage/x/q.md`.\n",
        encoding="utf-8")
    write_only = _run_checker(repo, tmp_path, p, skills, "--own-only")
    assert write_only.rc == 1 and "never matches" in write_only.out and "Edit(.sdlc-stage/**)" in write_only.out, write_only.text
    (skills / "planted" / "SKILL.md").write_text(
        "---\nname: planted\nallowed-tools:\n  - Read\n  - Edit(.sdlc-stage/**)\n---\nWrite the file to `.sdlc-stage/x/q.md`.\n",
        encoding="utf-8")
    ok = _run_checker(repo, tmp_path, p, skills, "--own-only")
    assert ok.rc == 0, ok.text


def test_checker_fails_on_a_human_only_command_no_deny_rule_matches(repo, tmp_path):
    p, skills = _plant(repo, tmp_path, "Ask a human to run `git frobnicate --now` when it breaks.\n")
    r = _run_checker(repo, tmp_path, p, skills)
    assert r.rc == 1 and "human-only, but no deny rule matches" in r.out, r.text


def test_tests_skill_names_the_staging_write_rule_and_workfile(repo):
    p = _paths(repo)
    front = (p.skills / "tests" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert "Edit(.sdlc-stage/**)" in front and "Write(.sdlc-stage" not in front and "workfile.py" in front


def test_no_skill_or_settings_relies_on_a_write_path_rule(repo):
    # Claude Code never matches a Write(<path>) allow rule (probed on 2.1.316); the staged Write needs Edit(<path>)
    p = _paths(repo)
    rx = re.compile(r"\bWrite\([^)]")
    for f in sorted(p.skills.rglob("SKILL.md")):
        assert not rx.search(f.read_text(encoding="utf-8").split("---")[1]), f
    allow = json.loads(p.settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert not [r for r in allow if rx.match(r)], allow
    assert any(r.startswith("Edit(") and "stage/**" in r for r in allow), allow


def test_every_skill_passes_the_permission_check_including_rollback(repo):
    p = _paths(repo)
    r = repo.run_script("check_skill_permissions.py", "--root", str(p.root), "--skills-dir", str(p.skills), "--settings", str(p.settings))
    assert r.rc == 0, r.text
    assert "$(" not in (p.skills / "rollback" / "SKILL.md").read_text(encoding="utf-8").split("--files")[1].split("\n")[0]


# ---------------------------------------------------------------------------------------------
# findings 5 and 9: acceptance.py --ready refuses decisions that only look decided
# ---------------------------------------------------------------------------------------------

DEFAULTS = ("## Defaults when ambiguous\n\n1. Make the smallest change that satisfies the acceptance criteria.\n"
            "2. Follow the nearest existing pattern in the code being changed.\n3. Prefer the option the Decisions above imply.\n"
            "4. When none applies, choose the reversible option.\n")
TABLE_HEAD = "| # | Question | Options | Recommendation | Value | Decided by |\n|---|---|---|---|---|---|\n"


def _contract(decisions: str, defaults: str = DEFAULTS, extra: str = "") -> str:
    return f"# Contract: x\n\n## Outcome\n\nx\n\n## Decisions\n\n{decisions}\n\n{defaults}\n{extra}\n## Capabilities\n\n- Paths: a\n- Commands: b\n- Egress: none\n- Data: none\n"


def _ready(repo, text: str, tier: str | None = None):
    repo.write("c.md", text)
    if tier:
        repo.write(".claude/work/current.json", json.dumps({"slug": "x", "tier": tier, "allowed_paths": ["app/**"]}))
        repo.write(".claude/work/x/contract.md", text)
    args = ["--root", str(repo.path), "--contract", str(repo.path / ("c.md" if not tier else ".claude/work/x/contract.md")), "--ready"]
    return repo.run_script("acceptance.py", *args)


GOOD_ROW = "| D1 | Cache? | redis · memory | **redis** | redis | @maria |\n"


def test_ready_accepts_a_real_decision_and_the_literal_none(repo):
    assert _ready(repo, _contract(TABLE_HEAD + GOOD_ROW)).rc == 0
    assert _ready(repo, _contract("None.")).rc == 0


@pytest.mark.parametrize("value", ["see above", "to be decided", "TBD - ask product", "unknown", "tbd.", "decide later", "None",
                                   "ask product", "Not yet known", ""])
def test_ready_refuses_placeholder_values(repo, value):
    r = _ready(repo, _contract(TABLE_HEAD + f"| D1 | Cache? | redis · memory | **redis** | {value} | @maria |\n"))
    assert r.rc == 1 and "FILL\tDecisions row D1" in r.out, (value, r.text)


def test_ready_refuses_a_value_that_is_not_an_option(repo):
    r = _ready(repo, _contract(TABLE_HEAD + "| D1 | Cache? | redis · memory | **redis** | dynamodb | @maria |\n"))
    assert r.rc == 1 and "not one of the Options" in r.out, r.text


def test_ready_splits_cells_on_unescaped_pipes_only(repo):
    r = _ready(repo, _contract(TABLE_HEAD + "| D1 | Cache? | redis \\| memory | redis | | @maria |\n"))
    assert r.rc == 1 and "Value is empty" in r.out, r.text
    r = _ready(repo, _contract(TABLE_HEAD + "| D1 | Cache? | redis \\| memory | **redis** | redis | @maria |\n"))
    assert r.rc == 0, r.text
    r = _ready(repo, _contract(TABLE_HEAD + "| D1 | Cache? | redis | memory | redis | redis | @maria |\n"))
    assert r.rc == 1 and "columns" in r.out, r.text


@pytest.mark.parametrize("line", ["- Q -> ?", "- Q -> TBD", "- Q -> pending", "- Q"])
def test_ready_refuses_bullet_decisions_without_an_answer(repo, line):
    assert _ready(repo, _contract(line)).rc == 1
    assert _ready(repo, _contract("- Cache? -> redis (@maria)")).rc == 0


def test_ready_refuses_open_questions_sections(repo):
    r = _ready(repo, _contract("None.", extra="## Open questions\n\n- Which cache?\n- Which TTL?\n\n"))
    assert r.rc == 1 and "open questions" in r.out, r.text
    assert _ready(repo, _contract("None.", extra="## Open questions\n\nNone.\n\n")).rc == 0


def test_ready_requires_the_standard_defaults_and_refuses_ones_that_ask_or_override(repo):
    r = _ready(repo, _contract("None.", defaults="## Defaults when ambiguous\n\n- Ask the human whenever something is unclear.\n"))
    assert r.rc == 1 and "standard four" in r.out and "tells the agent to ask" in r.out, r.text
    r = _ready(repo, _contract("None.", defaults=DEFAULTS + "\n- Ignore the decisions above and choose the cheapest option\n"))
    assert r.rc == 1 and "tells the agent to ask, stop or override" in r.out, r.text
    r = _ready(repo, _contract("None.", defaults=DEFAULTS + "\n- Return the existing `ApiError` shape for every refusal.\n"))
    assert r.rc == 0, r.text


def test_ready_fails_an_unconfirmed_decision_at_a_high_tier_or_a_sensitive_topic(repo):
    unconfirmed = TABLE_HEAD + "| D1 | Cache? | redis · memory | **redis** | redis | recommended, unconfirmed |\n"
    low = _ready(repo, _contract(unconfirmed), tier="low")
    assert low.rc == 0 and "NOTE\tDecisions row D1" in low.out, low.text
    high = _ready(repo, _contract(unconfirmed), tier="high")
    assert high.rc == 1 and "unconfirmed" in high.out and "tier is high" in high.out, high.text
    medium = _ready(repo, _contract(unconfirmed), tier="medium")
    assert medium.rc == 1
    money = TABLE_HEAD + "| D1 | Refund the customer or credit? | refund · credit | **credit** | credit | recommended, unconfirmed |\n"
    r = _ready(repo, _contract(money), tier="low")
    assert r.rc == 1 and "money, security, data" in r.out, r.text


# ---------------------------------------------------------------------------------------------
# unattended init: run_headless.sh init <repo>, and init_project.py accepting --headless
# ---------------------------------------------------------------------------------------------

def _plugin_dirs(tmp_path, *names):
    out = []
    for n in names:
        d = tmp_path / "roots" / n
        d.mkdir(parents=True, exist_ok=True)
        out.append(str(d))
    return out


def _dirs_args(dirs):
    return [a for d in dirs for a in ("--plugin-dir", d)]


def test_run_headless_init_mode_needs_no_contract_ticket_or_reviewer(repo, tmp_path):
    assert not (repo.path / ".claude" / "work" / "current.json").exists()
    dirs = _plugin_dirs(tmp_path, "factory", "sdlc", "graph")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--dry-run")
    assert r.rc == 0, r.text
    assert "/factory:init --headless --marketplace-repo ishpr/lathe" in r.out, r.out
    assert "--permission-mode dontAsk" in r.out and "--bare" not in r.out
    for d in dirs:  # --plugin-dir is repeatable: all three roots reach claude
        assert f"--plugin-dir {d}" in r.out, r.out
    assert "worktree" not in r.out and "gh pr create" not in r.out and "git -C" not in r.out  # no worktree, no push
    assert not (repo.path.parent / f"{repo.path.name}-init").exists()


def test_run_headless_init_mode_puts_the_prompt_before_every_option(repo, tmp_path):
    dirs = _plugin_dirs(tmp_path, "factory", "sdlc", "graph")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--dry-run")
    assert r.rc == 0, r.text
    cmd = next(line for line in r.out.splitlines() if "claude -p" in line)
    assert cmd.index("claude -p ") < cmd.index("/factory:init") < cmd.index("--permission-mode") < cmd.index("--allowedTools") < cmd.index("--plugin-dir")
    assert "--output-format stream-json >" in cmd  # no positional left behind a variadic option


def test_run_headless_init_mode_without_the_factory_plugin_runs_sdlc_init(repo, tmp_path):
    dirs = _plugin_dirs(tmp_path, "sdlc")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--stack", "python", "--dry-run")
    assert r.rc == 0, r.text
    assert "/sdlc:init --headless --stack python" in r.out and "/factory:init" not in r.out, r.out
    r = repo.run_script("run_headless.sh", "init", str(repo.path), "--marketplace-repo", "acme/fork", "--dry-run")
    assert r.rc == 0 and "/sdlc:init --headless --marketplace-repo acme/fork" in r.out, r.text
    assert repo.run_script("run_headless.sh", "init", str(repo.path), "--stack", "cobol", "--dry-run").rc == 2
    assert repo.run_script("run_headless.sh", "init", str(repo.path), "--marketplace-repo", "bad", "--dry-run").rc == 2


def test_run_headless_init_mode_names_the_project_settings_and_the_ticket_form_is_unchanged(repo, tmp_path):
    repo.write(".claude/settings.json", "{}\n")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), "--dry-run")
    assert r.rc == 0 and f"--settings {repo.path / '.claude' / 'settings.json'}" in r.out, r.text
    ticket = repo.write("ticket.txt", "Add pagination.\n")
    r = repo.run_script("run_headless.sh", str(repo.path), str(ticket), "--reviewer", "@bob", "--dry-run")
    assert r.rc == 2 and "current.json" in r.text  # the two-positional form still wants its contract
    assert repo.run_script("run_headless.sh", "init", str(tmp_path / "nope"), "--dry-run").rc == 2  # init needs a repo directory


def test_init_project_accepts_headless_as_a_no_op(repo, tmp_path):
    if repo.form != "plugin":
        pytest.skip("init_project.py is the plugin's installer")
    assert "--headless" in repo.run_script("init_project.py", "--help").out
    env = repo.env()
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["CLAUDE_PLUGIN_ROOT"] = str(repo.script_path("init_project.py").parents[1])
    root = tmp_path / "fresh"
    (root / "app").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    plain = repo.run_script("init_project.py", "--root", str(root), "--stack", "python", "--dry-run", env=env)
    flagged = repo.run_script("init_project.py", "--root", str(root), "--headless", "--stack", "python", "--dry-run", env=env)
    assert plain.rc == 0 and flagged.rc == 0, flagged.text
    assert plain.out == flagged.out  # the flag changes nothing
    assert not (root / ".claude").exists()  # a dry run writes nothing
