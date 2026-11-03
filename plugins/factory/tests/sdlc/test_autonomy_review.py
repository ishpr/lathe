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
    dirs = _plugin_dirs(tmp_path, "factory")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--dry-run")
    assert r.rc == 0, r.text
    assert "/factory:init --headless --marketplace-repo ishpr/lathe" in r.out, r.out
    assert "--permission-mode dontAsk" in r.out and "--bare" not in r.out
    assert r.out.count("--plugin-dir ") == 1 and f"--plugin-dir {dirs[0]}" in r.out, r.out  # one plugin, one root
    for tool in ("mcp__plugin_factory_graph__graph_status", "mcp__graph__graph_status"):
        assert tool in r.out, r.out  # init asks the graph whether it is configured
    two = _plugin_dirs(tmp_path, "factory", "other")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(two), "--dry-run")
    for d in two:  # --plugin-dir is still repeatable
        assert f"--plugin-dir {d}" in r.out, r.out
    assert "worktree" not in r.out and "gh pr create" not in r.out and "git -C" not in r.out  # no worktree, no push
    assert not (repo.path.parent / f"{repo.path.name}-init").exists()


def test_run_headless_init_mode_puts_the_prompt_before_every_option(repo, tmp_path):
    dirs = _plugin_dirs(tmp_path, "factory")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--dry-run")
    assert r.rc == 0, r.text
    cmd = next(line for line in r.out.splitlines() if "claude -p" in line)
    assert cmd.index("claude -p ") < cmd.index("/factory:init") < cmd.index("--permission-mode") < cmd.index("--allowedTools") < cmd.index("--plugin-dir")
    assert "--output-format stream-json >" in cmd  # no positional left behind a variadic option


def test_run_headless_init_mode_always_runs_factory_init_and_forwards_its_flags(repo, tmp_path):
    dirs = _plugin_dirs(tmp_path, "factory")
    r = repo.run_script("run_headless.sh", "init", str(repo.path), *_dirs_args(dirs), "--stack", "python", "--dry-run")
    assert r.rc == 0, r.text
    assert "/factory:init --headless --marketplace-repo ishpr/lathe --stack python" in r.out, r.out
    assert r.out.count("/factory:init --headless") >= 1 and "sdlc:init" not in r.out
    r = repo.run_script("run_headless.sh", "init", str(repo.path), "--dry-run")  # no plugin dir: the plugin is installed
    assert r.rc == 0 and "/factory:init --headless --marketplace-repo ishpr/lathe" in r.out, r.text
    r = repo.run_script("run_headless.sh", "init", str(repo.path), "--marketplace-repo", "acme/fork", "--dry-run")
    assert r.rc == 0 and "/factory:init --headless --marketplace-repo acme/fork" in r.out, r.text
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


# ---------------------------------------------------------------------------------------------
# Node runners: no blanket package-manager rule in the headless list or the settings template
# ---------------------------------------------------------------------------------------------

NODE_OK = ["pnpm test", "pnpm test --run", "pnpm exec vitest run src", "pnpm vitest run", "pnpm exec playwright test e2e", "pnpm lint",
           "pnpm typecheck", "npm test", "npm run test", "npx vitest run", "npx --no-install vitest run", "yarn test", "yarn vitest run",
           "bun test", "bunx vitest run"]
NODE_NOT = ["pnpm install", "pnpm add evilpkg", "pnpm dlx evilpkg", "pnpm exec rm -rf .", "pnpm run deploy", "pnpm publish", "pnpm lint --fix",
            "npm install evilpkg", "npm publish", "npm run deploy", "npx evilpkg", "npx --yes vitest run", "yarn add evilpkg", "yarn publish",
            "bun add evilpkg", "bun run deploy", "bunx evilpkg", "pnpm testx"]


def test_run_headless_default_tools_cover_the_node_forms_and_no_blanket_package_manager(repo):
    text = repo.script_path("run_headless.sh").read_text(encoding="utf-8")
    m = re.search(r"^DEFAULT_TOOLS='([^']*)'", text, re.M)
    rules = re.findall(r"Bash\([^)]*\)", m.group(1))
    for blanket in ("Bash(pnpm *)", "Bash(pnpm*)", "Bash(npm *)", "Bash(npm*)", "Bash(npx *)", "Bash(npx*)", "Bash(yarn *)", "Bash(bun *)", "Bash(bunx *)"):
        assert blanket not in rules, blanket
    mod = _checker(repo)
    pats = [r[5:-1] for r in rules]
    for cmd in NODE_OK:
        assert any(mod.rule_matches(r, cmd) for r in pats), cmd
    for cmd in NODE_NOT:
        assert not any(mod.rule_matches(r, cmd) for r in pats), cmd


def test_settings_template_allows_the_node_forms_and_no_blanket_package_manager(repo):
    if repo.form != "plugin":
        pytest.skip("the kit's settings are hard-coded for its own stack")
    mod, perms = _rules(repo)
    allow = mod.bash_patterns(perms["allow"], {"test_command": "pnpm test", "lint_command": "pnpm exec eslint", "typecheck_command": "pnpm typecheck",
                                               "format_command": "pnpm exec prettier"})
    for blanket in ("pnpm *", "npm *", "npx *", "yarn *", "bun *", "bunx *"):
        assert blanket not in allow, blanket
    for cmd in NODE_OK + ["pnpm install --frozen-lockfile", "npm ci"]:
        assert any(mod.rule_matches(r, cmd) for r in allow), cmd
    for cmd in ("pnpm dlx evilpkg", "pnpm publish", "npm publish", "npx evilpkg", "pnpm install", "npm install evilpkg", "yarn add evilpkg", "pnpm exec rm -rf ."):
        assert not any(mod.rule_matches(r, cmd) for r in allow), cmd


# ---------------------------------------------------------------------------------------------
# the ticket records the whole contract in the ledger; the tests step freezes only a test that fails for the
# intended reason (review findings: drafts with no blocks, mid-word slugs, a frozen test that could never pass)
# ---------------------------------------------------------------------------------------------

def _skill(repo, name: str) -> str:
    return (_paths(repo).skills / name / "SKILL.md").read_text(encoding="utf-8")


def _front_tools(text: str) -> list[str]:
    front = text.split("---", 2)[1]
    return [ln.strip()[2:].strip() for ln in front.splitlines() if ln.strip().startswith("- ")]


def test_ticket_skill_drafts_from_the_whole_contract_and_stops_on_missing_blocks(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's ticket skill")
    text = _skill(repo, "ticket")
    tools = _front_tools(text)
    for name in ("contract_draft", "contract_update"):
        assert f"mcp__plugin_factory_graph__{name}" in tools and f"mcp__graph__{name}" in tools, name
    step = text[text.index("8. **Record the contract in the ledger**"):]
    assert "`contract_md` (the WHOLE" in step and "Pass nothing partial" in step and "`feature`" in step
    assert "`blocks.missing`" in step and "ESCALATION: the contract lacks" in step
    assert "Never carry on" in step and "never set `strict` to false" in step
    assert "call `contract_update` with `slug` and the WHOLE contract.md text" in step
    assert "5a. **If the graph MCP is available**: register" not in text  # the partial draft is gone
    assert '"node": "<slug>", "stage": "contract.design"' in text


def test_ticket_skills_cut_the_slug_at_a_hyphen_and_factory_forwards_the_feature(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's ticket skills")
    text = _skill(repo, "ticket")
    assert 'ticket_intake.py" --slug <that text>' in text and "never inside a word" in text
    assert "[--feature <slug>]" in text.split("---", 2)[1]
    # one ticket skill since the merge: it reads the feature from the flag or from the ticket's own Feature:/Epic: line
    assert "`Feature:` line" in text and "`Epic:` line" in text and "the `--feature <slug>` argument" in text
    step = text[text.index("8. **Record the contract in the ledger**"):]
    assert "`feature` (the feature slug from **Feature** above" in step
    assert "cut at the last hyphen at or before the 40th" in text or "cuts at the last hyphen at or before the 40th" in text


def test_tests_skill_classifies_every_failure_before_it_freezes(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's tests skill")
    text = _skill(repo, "tests")
    assert 'Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" *)' in _front_tools(text)
    assert text.index("tests_failing_first.py\" <the new test files>") < text.index("5. Freeze them:")
    for reason in ("not_implemented", "assertion", "status_mismatch", "import_error", "attribute_error", "name_error",
                   "syntax_error", "collection_error"):
        assert f"`{reason}`" in text, reason
    assert "raise NotImplementedError" in text and "inside the contract's allowed paths" in text
    assert "`tests_failing_first payload:`" in text and '{"test": "<node id>", "reason": "<reason>"}' in text


def test_verify_skill_keeps_the_ledger_contract_current_and_its_frozen_check(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's verify skill")
    text = _skill(repo, "verify")
    tools = _front_tools(text)
    assert "mcp__plugin_factory_graph__contract_update" in tools and "mcp__graph__contract_update" in tools
    assert "call `contract_update` with `slug` and its WHOLE text" in text
    assert "`freeze_tests.py --check`" in text


def test_the_graph_server_has_the_contract_tools_the_skills_name(repo):
    if repo.form != "plugin":
        pytest.skip("the graph server ships in the plugin form only")
    server = _paths(repo).root / "sdlc_graph" / "mcp_server.py"  # one plugin since the merge: never skipped there
    assert server.exists(), server
    src = server.read_text(encoding="utf-8")
    assert re.search(r"def contract_draft\([^)]*feature[^)]*contract_md", src, re.S)
    assert "def contract_update(slug: str, contract_md: str)" in src


# ---------------------------------------------------------------------------------------------
# headless smoke round 1 (review findings): the TODO-age git log was denied because the model put --format before
# -S; init listed an unreachable graph and missing Grep/Glob tools as failures; /factory:epic on main wrote nothing
# because the edit guard refused it and no branch rule existed; the ticket session tried Edit on a work file
# ---------------------------------------------------------------------------------------------

def test_audit_names_the_literal_git_log_form_its_rule_covers(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's audit skill")
    text = _skill(repo, "audit")
    assert "Bash(git log -S *)" in _front_tools(text)
    assert '`git log -S "<text of the line>" --format="%h %ad" --date=short -- <file>`' in text
    assert "every other option after it" in text
    p = _paths(repo)
    r = repo.run_script("check_skill_permissions.py", "--root", str(p.root), "--skills-dir", str(p.skills / "audit"),
                        "--own-only", "--verbose")
    assert r.rc == 0, r.out
    assert re.search(r'covered\tskills/audit/SKILL.md:\d+\tgit log -S "ARG" --format="%h %ad" --date=short -- ARG', r.out), r.out


def test_init_never_lists_an_unreachable_graph_or_missing_grep_tools_as_failures(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's init skill")
    text = _skill(repo, "init")
    assert "**not a failure**: it never goes under\n   Failures" in text
    failures = text[text.index("   - **Failures**"):text.index("9. **Summary.**")]
    assert "the graph off\n     or not reachable" in failures and "missing Grep or Glob tools" in failures
    assert "with the Skill tool, so its own allow rules apply" in text and "never reorder its options" in text
    # the console's headless upgrade: the sandbox refused .claude/settings.json; init now stages it and goes on
    assert "a settings file init staged under `.sdlc-stage/`" in failures
    assert "not a denial, not a failure, and the script went on" in text and "never try the copy yourself" in text


def test_epic_leaves_a_protected_branch_before_its_first_write(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's epic skill")
    text = _skill(repo, "epic")
    tools = _front_tools(text)
    for rule in ("Bash(git branch --show-current)", "Bash(git rev-parse --verify --quiet refs/heads/docs/epic-*)",
                 "Bash(git switch -c docs/epic-*)"):
        assert rule in tools, rule
    assert "Bash(git switch -c *)" not in tools  # only the epic's own branch prefix
    deliver = text[text.index("6. **Deliver.**"):text.index("## The shapes")]
    assert deliver.index("**Leave a protected branch before the first") < deliver.index("Then run `mkdir -p docs/epics`")
    assert "`git switch -c docs/epic-<slug>`" in deliver and "`git switch -c docs/epic-<slug>-2`" in deliver
    assert "not a git repository (the command fails): change\n   nothing" in deliver
    # smoke round 2: one run read a plugin file with sed inside a compound Bash, took the denial for "Bash is
    # denied", wrote on main, was blocked by the edit guard and gave up without trying the allowed switch
    rules = text[text.index("## Two rules a headless run depends on"):text.index("## Workflow")]
    assert "**A denial is one command.**" in rules and "one per call" in rules
    assert "read with the Read tool, never with `sed`, `cat` or `head`" in rules
    assert "**The branch switch is allowed, and it comes before the first write.**" in rules
    assert "`Blocked: you are on main`" in rules and "run it, then write the same file again" in rules
    assert "never reported\n  as unsaved while the switch has not been tried" in rules
    assert "renaming the branch is not allowed and not needed" in rules  # smoke round 3: one run tried to rename
    assert "the first command is `git branch --show-current`, on\n   its own" in text


def test_verify_records_the_frozen_tests_as_acceptance_when_the_contract_has_no_lines(repo):
    """Two console contracts were refused at `verified` twice each (missing acceptance_covered) before their verify
    sessions recorded the frozen tests' results as acceptance evidence; the skill says to do it on the first attempt."""
    if repo.form != "plugin":
        pytest.skip("the plugin's verify skill")
    text = _skill(repo, "verify")
    assert "A contract with no\n   acceptance lines: say so; that is a finding against the contract, not a pass." in text
    assert "record what stands in for them on this first attempt, not after a refusal" in text
    assert '`acceptance` with `{"passed": <frozen tests that passed>, "failed": <that failed>, "source": "frozen tests"' in text
    assert "the ledger's `verified` guard counts a passed\n   test per criterion" in text
    assert '"choice": ..., "decision": ...}]}' in text  # the recorded assumptions keep their Decision column


def test_ticket_says_work_files_are_never_edited_in_place(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's ticket skill")
    text = _skill(repo, "ticket")
    assert "**Never call Edit or Write on a path under `.claude/work/`**" in text
    assert "it is the guard working, not an error to retry" in text
