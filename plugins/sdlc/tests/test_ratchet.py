"""Ratchet and baseline_diff (REC-116, REC-43, REC-24): baseline v2 counts, "more of the same",
v1 compatibility, prune, stale warnings, and the CI check that a PR may not grow its own baseline.

Violations come from the route-auth checker, which both forms ship: two files that each carry an
unauthenticated `GET /dup` produce the same key twice (`route-auth|GET /dup`), which is exactly
the "a second F401 in the same file" case. The rest of the baseline is whatever `--update`
recorded, so the tests do not depend on ruff or mypy being installed or on what they say.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

DUP = "route-auth|GET /dup"


def _route(path: str) -> str:
    return f'''\
from fastapi import APIRouter

router = APIRouter()


@router.get("{path}")
def handler():
    return []
'''


def _plant(repo, rel: str, path: str) -> None:
    """Write a module with one unauthenticated route and wire it into app/main.py, so the kit's
    dead-code checker (which also runs in the ratchet) sees one route-auth violation and nothing else."""
    repo.write(rel, _route(path))
    mod = rel[:-3].replace("/", ".")
    alias = "_m_" + mod.split(".")[-1]
    main = repo.read("app/main.py")
    if f"import {mod} as {alias}" not in main:
        repo.write("app/main.py", main + f"\nimport {mod} as {alias}\n\napp.include_router({alias}.router)\n")


def _baseline(repo, path: str = ".claude/baseline.json") -> dict:
    return json.loads(repo.read(path))


def _write_baseline(repo, counts: dict | None = None, version: int = 2, keys: list | None = None, path: str = ".claude/baseline.json") -> None:
    if version == 1:
        repo.write(path, json.dumps({"keys": keys or []}))
    else:
        repo.write(path, json.dumps({"version": 2, "head": "abc", "stack": "python", "counts": counts or {}}))


@pytest.fixture
def one(repo):
    """One recorded violation (GET /dup in app/a.py) and a baseline that says so."""
    _plant(repo, "app/a.py", "/dup")
    r = repo.ratchet("--update")
    assert r.rc == 0, r.text
    assert _baseline(repo)["counts"][DUP] == 1
    return repo


def test_help_works(repo):
    r = repo.run_script("ratchet.py", "--help")
    assert r.rc == 0 and "Baseline v2" in r.out


def test_update_writes_a_v2_baseline_with_counts(repo):
    _plant(repo, "app/a.py", "/dup")
    _plant(repo, "app/b.py", "/dup")
    r = repo.ratchet("--update")
    assert r.rc == 0
    data = _baseline(repo)
    assert data["version"] == 2
    assert data["counts"][DUP] == 2
    assert set(data) == {"version", "head", "stack", "counts"}
    assert data["head"] == repo.git("rev-parse", "--short", "HEAD").strip()
    assert "baseline written:" in r.out
    assert f"+ {DUP}  (new, 2)" in r.out


def test_clean_run_against_its_own_baseline_passes(one):
    r = one.ratchet()
    assert r.rc == 0, r.text
    assert "0 new key(s), 0 grown key(s)" in r.out


def test_a_new_key_fails(one):
    _plant(one, "app/c.py", "/other")
    r = one.ratchet()
    assert r.rc == 1
    assert "1 new key(s), 0 grown key(s)" in r.out
    assert "app/c.py" in r.out


def test_a_second_violation_with_the_same_key_fails_as_more_of_the_same(one):
    _plant(one, "app/b.py", "/dup")
    r = one.ratchet()
    assert r.rc == 1, r.text
    assert f"{DUP}: +1 more than baseline (1 -> 2)" in r.out
    assert "0 new key(s), 1 grown key(s) (+1 more)" in r.out


def test_a_lower_count_passes(one):
    _plant(one, "app/b.py", "/dup")
    assert one.ratchet("--update").rc == 0
    assert _baseline(one)["counts"][DUP] == 2
    (one.path / "app" / "b.py").unlink()
    r = one.ratchet()
    assert r.rc == 0, r.text


def test_all_tags_new_more_and_known(one):
    _plant(one, "app/b.py", "/dup")
    _plant(one, "app/c.py", "/other")
    r = one.ratchet("--all")
    assert r.rc == 1
    assert "NEW    app/c.py" in r.out
    assert "MORE   " in r.out
    assert "2 new key(s)" not in r.out and "1 new key(s), 1 grown" in r.out


def test_v1_baseline_loads_as_counts_of_one(one):
    keys = sorted(_baseline(one)["counts"])
    _write_baseline(one, version=1, keys=keys)
    r = one.ratchet()
    assert r.rc == 0, r.text
    assert "baseline is v1; run --update (humans) to store counts" in r.out
    _plant(one, "app/b.py", "/dup")
    r = one.ratchet()
    assert r.rc == 1 and "+1 more than baseline (1 -> 2)" in r.out  # v1 means "one allowed"


def test_stale_keys_warn_but_never_fail(one):
    data = _baseline(one)
    data["counts"]["route-auth|GET /ghost"] = 1
    _write_baseline(one, data["counts"])
    r = one.ratchet()
    assert r.rc == 0, r.text
    assert "stale: 1 key(s) no longer fire (run --prune)" in r.out
    assert "route-auth|GET /ghost" in r.out


def test_no_stale_line_when_nothing_is_stale(one):
    assert "stale:" not in one.ratchet().out


def test_prune_lowers_counts_and_drops_stale_keys(one):
    counts = _baseline(one)["counts"]
    counts[DUP] = 3
    counts["route-auth|GET /ghost"] = 2
    _write_baseline(one, counts)
    r = one.ratchet("--prune")
    assert r.rc == 0, r.text
    after = _baseline(one)["counts"]
    assert after[DUP] == 1
    assert "route-auth|GET /ghost" not in after
    assert f"v {DUP}  (3 -> 1)" in r.out
    assert "- route-auth|GET /ghost  (dropped, was 2)" in r.out
    assert "baseline pruned:" in r.out
    assert one.ratchet().rc == 0


def test_prune_never_raises_a_count(one):
    counts = _baseline(one)["counts"]
    counts[DUP] = 1
    _plant(one, "app/b.py", "/dup")  # now 2 firing
    _write_baseline(one, counts)
    assert one.ratchet("--prune").rc == 0
    assert _baseline(one)["counts"][DUP] == 1  # only --update may accept more debt
    assert one.ratchet().rc == 1


def test_prune_with_nothing_to_change_says_so(one):
    r = one.ratchet("--prune")
    assert r.rc == 0 and "no changes" in r.out


def test_prune_needs_a_full_run_and_an_existing_baseline(one, repo):
    assert one.ratchet("--prune", "--files", "app/a.py").rc == 2
    assert one.ratchet("--prune", "--fast").rc == 2
    (one.path / ".claude" / "baseline.json").unlink()
    r = one.ratchet("--prune")
    assert r.rc == 2 and "no baseline to prune" in r.out


def test_update_and_prune_together_and_unknown_flags_are_misuse(one):
    assert one.ratchet("--update", "--prune").rc == 2
    r = one.ratchet("--frobnicate")
    assert r.rc == 2 and "unknown argument" in r.err


def test_missing_option_value_is_misuse(one):
    assert one.run_script("ratchet.py", "--root").rc == 2


def test_files_limits_the_check_to_those_files(one):
    _plant(one, "app/b.py", "/dup")
    assert one.ratchet().rc == 1
    r = one.ratchet("--files", "app/a.py")
    assert r.rc == 0, r.text
    assert "stale:" not in r.out  # a partial run cannot tell what is stale


def test_baseline_option_points_at_another_file(one, tmp_path):
    """CI grades the PR tree against the base branch's baseline (REC-43)."""
    other = tmp_path / "base-baseline.json"
    other.write_text(json.dumps({"version": 2, "head": "x", "stack": "python", "counts": {}}))
    r = one.ratchet("--baseline", str(other))
    assert r.rc == 1 and re.search(r"[1-9]\d* new key\(s\)", r.out) and "GET /dup" in r.out  # not in that baseline -> new
    assert one.ratchet("--baseline", str(other), "--update").rc == 0
    assert _baseline(one, str(other))["counts"][DUP] == 1


def test_unreadable_baseline_is_a_tooling_error(one):
    one.write(".claude/baseline.json", "{not json")
    r = one.ratchet()
    assert r.rc == 2 and "unreadable" in r.err


def test_no_baseline_means_everything_is_new(repo):
    _plant(repo, "app/a.py", "/dup")
    r = repo.ratchet()
    assert r.rc == 1 and "1 new key(s)" in r.out


def test_clean_tree_with_no_baseline_passes(repo):
    r = repo.ratchet()
    assert r.rc == 0, r.text


# ---- form-specific ----

def test_plugin_uninitialised_repo_is_not_an_error(uninit):
    if uninit.form != "plugin":
        pytest.skip("the kit is always initialised")
    r = uninit.ratchet()
    assert r.rc == 0 and "not initialised" in r.out


def test_plugin_uninitialised_repo_is_an_error_under_ci(uninit):
    """Under CI=1 the ratchet must refuse to pass a repo with no config (REC-43: a PR cannot
    grade itself by deleting the configuration)."""
    if uninit.form != "plugin":
        pytest.skip("the kit is always initialised")
    import subprocess
    import sys

    script = f"{uninit.pack_env()['CLAUDE_PLUGIN_ROOT']}/scripts/ratchet.py"
    r = subprocess.run(
        [sys.executable, "-I", script, "--root", str(uninit.path)],
        env=uninit.env(CI="1"),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and "refusing to pass an unconfigured repo" in r.stderr


def test_plugin_reports_a_tool_version_pin_mismatch_on_stderr(repo):
    if repo.form != "plugin":
        pytest.skip("the kit pins versions in a constant")
    repo.set_config(tool_versions={"ruff": "0.0.1", "mypy": ""})
    r = repo.ratchet()
    assert r.rc == 0, r.text  # a warning, never a failure
    assert "tool_versions: ruff" in r.err
    assert "tool_versions: mypy" not in r.err  # an empty pin is not a pin


def test_plugin_counts_lint_findings_per_rule_and_file(repo):
    if repo.form != "plugin":
        pytest.skip("the kit runs ruff directly")
    lint = repo.write("lint.json", "[]")
    repo.write("fake_lint.py", "import pathlib, sys\nprint(pathlib.Path(sys.argv[1]).read_text())\n")
    repo.set_config(commands={"test": "true", "lint_report": f"{sys.executable} fake_lint.py lint.json", "lint_fix": "", "format": "", "typecheck": ""}, lint_format="ruff-json")

    def finding(row: int) -> dict:
        return {"filename": str(repo.path / "app" / "main.py"), "code": "F401", "message": "unused import", "location": {"row": row}}

    lint.write_text(json.dumps([finding(1)]))
    assert repo.ratchet("--update").rc == 0
    assert _baseline(repo)["counts"]["lint|app/main.py|F401"] == 1
    lint.write_text(json.dumps([finding(1)]))
    assert repo.ratchet().rc == 0
    lint.write_text(json.dumps([finding(5), finding(1)]))  # a line moved and a second one appeared
    r = repo.ratchet()
    assert r.rc == 1
    assert "lint|app/main.py|F401: +1 more than baseline (1 -> 2)" in r.out
    lint.write_text("[]")
    r = repo.ratchet()
    assert r.rc == 0 and "stale: 1 key(s)" in r.out


def test_kit_accepts_no_mypy_as_an_alias_for_fast(one):
    if one.form != "kit":
        pytest.skip("kit flag")
    assert one.ratchet("--no-mypy").rc == 0
    assert one.ratchet("--fast").rc == 0


def test_kit_runs_the_base_branchs_scripts_on_another_tree(one, tmp_path):
    """REC-43: `<base>/scripts/ratchet.py --root <pr-tree> --baseline <base baseline>`."""
    if one.form != "kit":
        pytest.skip("the plugin runs from the marketplace checkout")
    import shutil

    tree = tmp_path / "pr-tree"
    shutil.copytree(one.path, tree, ignore=shutil.ignore_patterns(".git"))
    (tree / "app" / "b.py").write_text(_route("/dup"))
    import subprocess

    base_ratchet = one.path / "scripts" / "ratchet.py"
    r = subprocess.run(
        [sys.executable, "-I", str(base_ratchet), "--root", str(tree), "--baseline", str(one.path / ".claude" / "baseline.json")],
        capture_output=True, text=True, cwd=tree, env=one.env(),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "+1 more than baseline (1 -> 2)" in r.stdout


# ---------------------------------------------------------------------------------------------
# baseline_diff: a PR may not add to its own baseline (REC-43, REC-116)
# ---------------------------------------------------------------------------------------------

def _on_branch(repo, counts: dict, version: int = 2, keys: list | None = None) -> None:
    """Commit a baseline on main, then branch and leave the tree for the test to change."""
    repo.git("switch", "-q", "main")
    _write_baseline(repo, counts, version=version, keys=keys)
    repo.commit("chore: baseline")
    repo.git("switch", "-q", "-c", "feat/pr")


def _diff(repo, *args: str):
    return repo.run_script("baseline_diff.py", "--root", str(repo.path), *args)


def test_diff_help_and_missing_base_ref(repo):
    assert _diff(repo, "--help").rc == 0
    assert _diff(repo).rc == 2


def test_diff_unchanged_baseline_passes(repo):
    _on_branch(repo, {DUP: 1})
    r = _diff(repo, "main")
    assert r.rc == 0 and "did not grow" in r.out


def test_diff_a_new_key_fails(repo):
    _on_branch(repo, {DUP: 1})
    _write_baseline(repo, {DUP: 1, "route-auth|GET /new": 1})
    r = _diff(repo, "main")
    assert r.rc == 1
    assert "+ route-auth|GET /new  (new, 1)" in r.out
    assert "1 key(s) added, 0 count(s) raised" in r.out


def test_diff_a_raised_count_fails(repo):
    _on_branch(repo, {DUP: 1})
    _write_baseline(repo, {DUP: 2})
    r = _diff(repo, "--base-ref", "main")
    assert r.rc == 1
    assert f"^ {DUP}  (1 -> 2)" in r.out


def test_diff_a_shrunk_baseline_passes(repo):
    _on_branch(repo, {DUP: 3, "route-auth|GET /gone": 1})
    _write_baseline(repo, {DUP: 1})
    r = _diff(repo, "main")
    assert r.rc == 0, r.text
    assert "1 key(s) dropped, 1 count(s) lowered" in r.out


def test_diff_allow_growth_exits_zero_but_still_prints_the_growth(repo):
    _on_branch(repo, {DUP: 1})
    _write_baseline(repo, {DUP: 4, "route-auth|GET /new": 2})
    r = _diff(repo, "main", "--allow-growth")
    assert r.rc == 0
    assert "growth allowed (baseline-approved)" in r.out
    assert f"^ {DUP}  (1 -> 4)" in r.out and "+ route-auth|GET /new" in r.out


def test_diff_reads_v1_on_either_side(repo):
    _on_branch(repo, {}, version=1, keys=[DUP])
    _write_baseline(repo, {DUP: 1})
    assert _diff(repo, "main").rc == 0  # v1 key counts as 1
    _write_baseline(repo, {DUP: 2})
    assert _diff(repo, "main").rc == 1
    _write_baseline(repo, version=1, keys=[DUP, "route-auth|GET /new"])
    assert _diff(repo, "main").rc == 1


def test_diff_without_a_base_baseline_is_the_first_one(repo):
    if repo.exists(".claude/baseline.json"):  # the kit commits one at install; main must not have it here
        repo.git("switch", "-q", "main")
        repo.git("rm", "-q", "-f", ".claude/baseline.json")
        repo.commit("chore: drop the baseline")
        repo.git("switch", "-q", "feat/work")
        repo.git("merge", "-q", "main")
    r = _diff(repo, "main")  # main has no baseline.json
    assert r.rc == 0 and "no baseline" in r.out
    _write_baseline(repo, {DUP: 1})
    r = _diff(repo, "main")
    assert r.rc == 0 and "no baseline on main" in r.out


def test_diff_without_a_baseline_in_this_tree_has_nothing_to_compare(repo):
    _on_branch(repo, {DUP: 1})
    (repo.path / ".claude" / "baseline.json").unlink()
    r = _diff(repo, "main")
    assert r.rc == 0 and "nothing to compare" in r.out


def test_diff_unknown_flag_is_misuse(repo):
    assert _diff(repo, "main", "--wat").rc == 2


# ---- oxlint over a Foldkit frontend: lint|<file>|foldkit/<rule> keys, skipped with a note when missing ----

def _foldkit_frontend(repo) -> None:
    repo.write("package.json", json.dumps({"dependencies": {"foldkit": "0.170.0"}}) + "\n")
    repo.write(".oxlintrc.json", '{"extends": ["./node_modules/@foldkit/oxlint-plugin/recommended.json"]}\n')
    repo.write("src/programs/home/update.ts", "let count = 0\nexport const bump = () => (count += 1)\n")
    if repo.form == "plugin":  # the plugin reads the frontend block init writes; the kit finds the package.json
        repo.set_config(frontend={"framework": "foldkit", "programs": ["src/programs/**"],
                                  "api_table": "src/lib/api/endpoints.ts", "oxlint": True})


def _path_with(tmp_path, oxlint_json: str | None) -> str:
    """PATH = a bin dir with git (and a fake oxlint printing oxlint_json) + this python's dir."""
    import os
    import shutil
    import stat

    bin_dir = tmp_path / ("oxbin" if oxlint_json is not None else "nooxbin")
    bin_dir.mkdir()
    git = shutil.which("git")
    if git:
        (bin_dir / "git").symlink_to(git)
    if oxlint_json is not None:
        exe = bin_dir / "oxlint"
        exe.write_text(f"#!{sys.executable}\nimport sys\nprint({oxlint_json!r})\nsys.exit(1)\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return f"{bin_dir}{os.pathsep}{os.path.dirname(sys.executable)}"


def test_oxlint_findings_of_a_foldkit_frontend_are_ratcheted_like_eslint(repo, tmp_path):
    _foldkit_frontend(repo)
    out = json.dumps({"diagnostics": [{
        "message": "Module-level let/var holds state outside the Model.", "code": "foldkit(no-module-level-mutable-state)",
        "severity": "error", "filename": "src/programs/home/update.ts",
        "labels": [{"span": {"offset": 0, "length": 15, "line": 1, "column": 1}}]}]})
    r = repo.run_script("ratchet.py", "--root", str(repo.path), "--all", env=repo.env(PATH=_path_with(tmp_path, out)))
    assert "NEW    src/programs/home/update.ts:1: foldkit/no-module-level-mutable-state Module-level" in r.out, r.text
    assert r.rc == 1


def test_a_missing_oxlint_is_a_note_and_its_baseline_keys_are_not_stale(repo, tmp_path):
    _foldkit_frontend(repo)
    env = repo.env(PATH=_path_with(tmp_path, None))
    assert repo.run_script("ratchet.py", "--root", str(repo.path), "--update", env=env).rc == 0
    data = json.loads(repo.read(".claude/baseline.json"))
    data["counts"]["lint|src/programs/home/update.ts|foldkit/no-module-level-mutable-state"] = 1
    repo.write(".claude/baseline.json", json.dumps(data))
    r = repo.run_script("ratchet.py", "--root", str(repo.path), env=env)
    assert r.rc == 0, r.text
    assert "oxlint is not installed" in r.err and "Foldkit lint rules not ratcheted" in r.err
    assert "foldkit/no-module-level-mutable-state" not in r.out  # not listed as stale
