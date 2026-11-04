"""The permission model without a script of commands (docs/DESIGN.md section 18.5): the settings template opens
read-only commands by category and keeps every required deny rule; check_skill_permissions.py lints the deny list,
refuses blanket runners and still checks the commands skills name; run_headless.sh passes the required denies to every
unattended run; guard_bash keeps `sed -n` read-only."""

from __future__ import annotations

import json
import re
import shutil

import pytest

CATEGORY_ALLOWS = ("Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(ls *)", "Bash(rg *)", "Bash(grep *)",
                   "Bash(find *)", "Bash(sed -n *)", "Bash(jq *)", "Bash(sort *)", "Bash(uniq -c)", "Bash(diff *)",
                   "Bash(file *)", "Bash(stat *)", "Bash(tree *)", "Bash(git status *)", "Bash(git log *)", "Bash(git show *)",
                   "Bash(git diff *)", "Bash(git blame *)", "Bash(git branch -a)", "Bash(git rev-parse *)",
                   "Bash(git ls-files *)", "Bash(git grep *)", "Bash(git stash list)", "Bash(git remote -v)",
                   "Bash(git describe *)")
FIND_DENIES = ("Bash(find *-delete*)", "Bash(find *-exec*)", "Bash(find *-ok*)", "Bash(find *-fprint*)")


@pytest.fixture
def pack(repo):
    if repo.form != "plugin":
        pytest.skip("the plugin's templates")
    root = repo.registration_path().parent.parent
    return root


def _template(pack):
    return json.loads((pack / "templates" / "settings.project.json").read_text(encoding="utf-8"))["permissions"]


def _required(pack):
    return json.loads((pack / "templates" / "required_denies.json").read_text(encoding="utf-8"))["deny"]


def test_the_template_opens_the_categories_and_keeps_every_required_deny(pack):
    perms, required = _template(pack), _required(pack)
    assert not [r for r in CATEGORY_ALLOWS if r not in perms["allow"]]
    assert not [r for r in required if r not in perms["deny"]]
    assert set(FIND_DENIES) <= set(required) and len(required) >= 56 + len(FIND_DENIES)
    assert len(required) == len(set(required))


def test_run_headless_lists_open_the_categories_and_no_blanket_runner(repo, pack):
    text = repo.script_path("run_headless.sh").read_text(encoding="utf-8")
    base = re.search(r"^DEFAULT_TOOLS='([^']*)'", text, re.M).group(1)
    for rule in ("Bash(rg *)", "Bash(find *)", "Bash(sed -n *)", "Bash(jq *)", "Bash(cat *)", "Bash(git blame *)", "Bash(git grep *)"):
        assert rule in base, rule
    mod = _checker(repo)
    assert not [r for r in re.findall(r"Bash\([^)]*\)", base) if mod.blanket_runner(r)]


def _checker(repo):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("csp_lint_under_test", repo.script_path("check_skill_permissions.py"))
    mod = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def _tree(tmp_path, pack, body="Run `rg -n limiter app | head -5` and `git log -p -1`.\n", settings=None):
    skills = tmp_path / "skills" / "planted"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(f"---\nname: planted\nallowed-tools:\n  - Read\n---\n{body}", encoding="utf-8")
    data = json.loads((pack / "templates" / "settings.project.json").read_text(encoding="utf-8"))
    if settings:
        settings(data["permissions"])
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(repo, tmp_path, settings, *extra):
    return repo.run_script("check_skill_permissions.py", "--root", str(tmp_path), "--skills-dir", str(tmp_path / "skills"),
                           "--settings", str(settings), *extra)


def test_the_check_passes_on_the_template_with_category_commands_chained(repo, tmp_path, pack):
    r = _run(repo, tmp_path, _tree(tmp_path, pack), "--verbose")
    assert r.rc == 0, r.text
    assert "0 missing; 0 blanket runner(s)" in r.out and r.out.strip().endswith(" 0 uncovered"), r.text
    assert "covered\tskills/planted/SKILL.md:6\trg -n limiter app\t" in r.out and "\thead -5\t" in r.out, r.text


@pytest.mark.parametrize("rule", ["Bash(find *-delete*)", "Bash(git push --force *)", "Read(**/.env)", "Bash(gh pr merge *)"])
def test_a_missing_required_deny_fails_the_check_and_names_the_rule(repo, tmp_path, pack, rule):
    path = _tree(tmp_path, pack, settings=lambda p: p["deny"].remove(rule))
    r = _run(repo, tmp_path, path)
    assert r.rc == 1 and f"MISSING-DENY\tsettings.json\t{rule}\t" in r.out and ", 1 missing;" in r.out, r.text


@pytest.mark.parametrize("rule", ["Bash", "Bash(*)", "Bash(uv run *)", "Bash(uv run:*)", "Bash(npx *)", "Bash(make *)",
                                  "Bash(bash *)", "Bash(sh -c *)", "Bash(python3 *)", "Bash(python3 -c *)"])
def test_a_blanket_runner_allow_rule_fails_the_check(repo, tmp_path, pack, rule):
    path = _tree(tmp_path, pack, settings=lambda p: p["allow"].append(rule))
    r = _run(repo, tmp_path, path)
    assert r.rc == 1 and f"BLANKET-RUNNER\tsettings.json\t{rule}\t" in r.out, r.text


def test_narrow_runner_rules_are_not_blanket(repo):
    mod = _checker(repo)
    for rule in ("Bash(uv run pytest *)", "Bash(make test)", "Bash(npx vitest *)", "Bash(python3 -m pytest *)",
                 "Bash(python3 -I scripts/*)", 'Bash(bash "/p/scripts/*)', "Bash(git status *)"):
        assert not mod.blanket_runner(rule), rule


def test_a_skill_that_names_a_command_no_rule_opens_still_fails(repo, tmp_path, pack):
    r = _run(repo, tmp_path, _tree(tmp_path, pack, body="Then run `curl https://x.invalid` and `uv run python -c 1`.\n"))
    assert r.rc == 1 and "UNCOVERED\tskills/planted/SKILL.md:6\tcurl https://x.invalid" in r.out, r.text
    assert "UNCOVERED\tskills/planted/SKILL.md:6\tuv run python -c 1" in r.out, r.text


def test_the_required_denies_file_can_be_named_and_a_bad_one_is_misuse(repo, tmp_path, pack):
    path = _tree(tmp_path, pack)
    extra = tmp_path / "req.json"
    extra.write_text(json.dumps({"deny": ["Bash(frobnicate *)"]}))
    r = _run(repo, tmp_path, path, "--required-denies", str(extra))
    assert r.rc == 1 and "MISSING-DENY\tsettings.json\tBash(frobnicate *)" in r.out, r.text
    extra.write_text("{}")
    assert _run(repo, tmp_path, path, "--required-denies", str(extra)).rc == 2


def test_run_headless_passes_the_required_denies_to_every_unattended_run(repo, pack):
    ticket = repo.write("ticket.txt", "Add pagination.\n")
    repo.contract()
    r = repo.run_script("run_headless.sh", str(repo.path), str(ticket), "--reviewer", "@bob", "--dry-run")
    assert r.rc == 0, r.text
    cmd = next(ln for ln in r.out.splitlines() if "--allowedTools" in ln).replace("\\", "")
    m = re.search(r"--disallowedTools (.*?) --(?:settings|plugin-dir|verbose)", cmd)
    assert m, cmd
    for rule in _required(pack):
        assert rule in m.group(1), rule


def test_run_headless_refuses_without_the_required_denies(repo, pack, tmp_path):
    copy = tmp_path / "pack"
    shutil.copytree(pack / "scripts", copy / "scripts")
    ticket = repo.write("ticket.txt", "Add pagination.\n")
    repo.contract()
    import subprocess

    import os

    p = subprocess.run(["bash", str(copy / "scripts" / "run_headless.sh"), str(repo.path), str(ticket), "--reviewer", "@bob", "--dry-run"],
                       capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmp_path)})
    assert p.returncode == 2 and "required_denies.json is missing" in p.stderr, p.stdout + p.stderr


@pytest.mark.parametrize("cmd,rule", [
    ("sed -n '1e id' app/main.py", "sed-exec"),
    ("sed 's/x/y/e' app/main.py", "sed-exec"),
    ("sed -n '1,5w out.txt' app/main.py", "sed-write"),
    ("sed -n -e 's/a/b/w out.txt' app/main.py", "sed-write"),
    ("sed -ni 's/a/b/' app/main.py", "sed-write"),
    ("sed --quiet --in-place 's/a/b/' app/main.py", "sed-write"),
])
def test_guard_keeps_sed_n_read_only(repo, cmd, rule):
    assert repo.bash(cmd).rc == 2, cmd
    assert repo.blocks()[-1]["rule"] == rule


@pytest.mark.parametrize("cmd", ["sed -n 1,40p app/main.py", "sed -n '/def /p' app/main.py", "sed -n -e '1p' -e '$p' app/main.py",
                                 "sed -n 's/e/E/p' app/main.py", "sed -n '/e/p' app/main.py", "sed 's/a/b/' app/main.py",
                                 "rg -n 'a|b' app | head -5", "find . -name '*.py' -newer app/main.py"])
def test_guard_lets_read_only_category_commands_run(repo, cmd):
    r = repo.bash(cmd)
    assert r.rc == 0, (cmd, r.text)
