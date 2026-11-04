"""Dogfooding findings 4, 8, 14, 15, 16, 18, 19, 20, 31: the checkers, the ratchet, maturity and the security
assessment must not scan virtualenvs or gitignored paths, must know their entry points, treat constants as
constants, pin ruff's config, find tools without a system install, and honour test-fixture secrets."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys

from conftest import PACK

sys.path.insert(0, str(PACK / "lib"))
import sdlc as cl  # noqa: E402


def _ratchet_module():
    spec = importlib.util.spec_from_file_location("ratchet_under_test", PACK / "scripts" / "ratchet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _keys(r):
    return {line.split("\t", 1)[0] for line in r.out.splitlines() if "\t" in line}


def _widen(repo, app=("app/**", "hooks/**", "scripts/**", "pkg/**")):
    repo.set_config(paths={"app": list(app), "tests": ["tests/**"]})


# ---- #16, #31: virtualenvs and gitignored paths ----

def test_checkers_skip_a_venv_by_pyvenv_cfg_and_gitignored_paths(repo):
    repo.write("app/real.py", "CACHE = {}\n")
    repo.write("app/.demo/app-venv/pyvenv.cfg", "home = /usr/bin\n")  # gitignored, not named .venv
    repo.write("app/.demo/app-venv/lib/third.py", "STATE = {}\n")
    repo.write("app/oddenv/pyvenv.cfg", "home = /usr/bin\n")  # a venv that is not ignored, odd name
    repo.write("app/oddenv/lib/third2.py", "STATE2 = {}\n")
    repo.write("app/generated/out.py", "GEN = {}\n")  # gitignored, no venv
    repo.write(".gitignore", repo.read(".gitignore") + "app/.demo/\napp/generated/\n")
    r = repo.checker("module_state")
    keys = _keys(r)
    assert "state|app/real.py|CACHE" in keys, r.text
    assert not [k for k in keys if "third" in k or "oddenv" in k or "generated" in k], keys


def test_ratchet_drops_tool_findings_in_ignored_and_venv_paths_and_records_tool_versions(repo):
    report = [{"filename": f, "code": "F401", "message": "unused", "location": {"row": 1}}
              for f in ("app/real.py", "app/.demo/app-venv/lib/third.py", "app/oddenv/lib/x.py")]
    repo.write("lint.json", json.dumps(report))
    repo.write("app/real.py", "x = 1\n")
    repo.write("app/.demo/app-venv/lib/third.py", "")
    repo.write("app/oddenv/pyvenv.cfg", "")
    repo.write("app/oddenv/lib/x.py", "")
    repo.write(".gitignore", repo.read(".gitignore") + "app/.demo/\n")
    repo.set_config(lint_format="ruff-json", commands={"test": "true", "lint_report": "cat lint.json", "lint_fix": "", "format": "", "typecheck": ""})
    r = repo.ratchet("--update")
    assert r.rc == 0, r.text
    counts = json.loads(repo.read(".claude/baseline.json"))["counts"]
    assert counts == {"lint|app/real.py|F401": 1}, counts
    assert "ignored 2 finding(s)" in r.err


def test_large_ratchet_output_is_summarised_by_check_unless_full():
    rt = _ratchet_module()
    rows = [("NEW    ", f"lint|pkg/m{i}.py|E{i % 3}", f"pkg/m{i}.py:1: E{i % 3} message {'x' * 60}") for i in range(400)]
    short = rt.summarise(rows)
    assert short[0].startswith("summary by check")
    assert sum(len(x) + 1 for x in short) < rt.OUTPUT_LIMIT
    assert any(x.strip().startswith("lint|E0: ") for x in short)
    assert len(rt.summarise(rows, full=True)) == 400
    assert len(rt.summarise(rows[:5])) == 5  # small output is untouched


# ---- #18: dead-code entry points ----

def test_dead_code_reads_hooks_json_console_scripts_and_scripts_dir_as_entry_points(repo):
    _widen(repo)
    repo.set_config(entry_modules=["app/main.py"])
    repo.write("hooks/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": 'python3 -I "${CLAUDE_PLUGIN_ROOT}"/hooks/guard.py'}]}]}}))
    repo.write("hooks/guard.py", "import helper_mod\n")
    repo.write("hooks/helper_mod.py", "X = 1\n")
    repo.write("scripts/tool.py", "print(1)\n")
    repo.write("pyproject.toml", '[project]\nname = "p"\n[project.scripts]\ncli = "pkg.cli:main"\n')
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/cli.py", "def main():\n    pass\n")
    repo.write("app/orphan.py", "Y = 1\n")
    r = repo.checker("dead_code")
    dead = {k.split("|")[1] for k in _keys(r)}
    assert dead == {"app/orphan.py"}, r.text


# ---- #19: env reads ----

def test_env_reads_allow_a_list_of_config_modules_and_command_line_programs(repo):
    _widen(repo)
    repo.set_config(env_access_allowed=["app/config.py", "app/settings.py"])
    read = "import os\nV = os.environ.get('X')\n"
    repo.write("app/settings.py", read)
    repo.write("app/cli.py", read + "\nif __name__ == '__main__':\n    print(V)\n")
    repo.write("scripts/run.py", read)
    repo.write("app/other.py", read)
    r = repo.checker("stubs")
    flagged = {k.split("|")[1] for k in _keys(r) if k.endswith("ENV-OUTSIDE-CONFIG")}
    assert flagged == {"app/other.py"}, r.text


# ---- #20: module state ----

def test_a_module_level_set_or_dict_nothing_mutates_is_a_constant(repo):
    repo.write("app/tables.py", """\
_kinds = set(["a", "b"])
_lookup = dict(a=1, b=2)
_letters = {c for c in "abc"}
SEEN = set(["x"])
REG = {}
CACHE = {"a": 1}


def use(k):
    SEEN.add(k)
    CACHE["b"] = 2
    return k in _kinds
""")
    r = repo.checker("module_state")
    names = {k.split("|")[2] for k in _keys(r) if k.startswith("state|app/tables.py")}
    assert names == {"SEEN", "REG"}, names  # _kinds, _lookup, _letters are constants


# ---- #8, #4: ruff pinned to the project's config, tools found without a system install ----

def test_ruff_gets_the_projects_config_or_isolated(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert cl.ruff_config_args(str(bare)) == ["--isolated"]
    (bare / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    assert cl.ruff_config_args(str(bare)) == ["--config", "pyproject.toml"]
    own = tmp_path / "own"
    own.mkdir()
    (own / "ruff.toml").write_text("")
    assert cl.ruff_config_args(str(own)) == ["--config", "ruff.toml"]
    nested = tmp_path / "nested"
    (nested / "sub").mkdir(parents=True)
    (nested / "sub" / "ruff.toml").write_text("")
    assert cl.ruff_config_args(str(nested)) == []


def test_tools_come_from_the_project_environment_and_the_version_used_is_recorded(tmp_path):
    root = tmp_path / "proj"
    exe = root / ".venv" / "bin" / "ruff"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho 'ruff 9.8.7'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    argv, version, where = cl.resolve_tool("ruff", str(root))
    assert argv == [str(exe)] and version == "9.8.7" and "project environment" in where
    rt = _ratchet_module()
    used: dict[str, str] = {}
    cmd, problem = rt.prepare_command("python3 -m ruff check --output-format json .", str(root), {}, used)
    assert problem is None and used == {"ruff": "9.8.7"}
    assert cmd.startswith(str(exe) + " check --isolated "), cmd


def test_a_missing_tool_is_reported_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr(cl.shutil, "which", lambda name: None)
    monkeypatch.setattr(cl, "tool_version", lambda cmd, root: None)
    cl._TOOL_CACHE.clear()
    argv, version, why = cl.resolve_tool("mypy", str(tmp_path))
    assert argv is None and version is None and "uvx" in why
    cl._TOOL_CACHE.clear()


# ---- #15: test-fixture secrets ----

FAKE = "AKIA" + "ABCDEFGHIJKLMNOP"  # shaped like a live key, assembled so this file is not a hit itself


def test_secret_scan_honours_the_documented_example_key_markers_and_the_allow_list():
    cfg = {"paths": {"tests": []}}
    assert cl.find_secrets_scan(f"k = '{FAKE}'\n", cfg, "app/a.py")
    assert cl.find_secrets_scan("k = 'AKIAIOSFODNN7EXAMPLE'\n", cfg, "app/a.py") is None
    assert cl.find_secrets_scan(f"k = '{FAKE}'  # secret: test-fixture\n", cfg, "app/a.py") is None
    assert cl.find_secrets_scan(f"# secret: test-fixture\nk = '{FAKE}'\nj = '{FAKE}'\n\nreal = '{FAKE}'\n", cfg, "app/a.py")
    assert cl.find_secrets_scan(f"# secret: test-fixture\nk = '{FAKE}'\n\n", cfg, "app/a.py") is None
    assert cl.find_secrets_scan(f"k = '{FAKE}'\n", {"paths": {"tests": []}, "secret_allowlist": [FAKE]}, "app/a.py") is None


def test_security_assessment_and_maturity_ignore_fixture_keys(repo):
    repo.write("app/fixture.py", f"# secret: test-fixture\nKEY = '{FAKE}'\n")
    repo.write("app/leak.py", f"KEY = '{FAKE}'\n")
    repo.commit("feat: fixtures")
    r = repo.run_script("security_assess.py", "--root", str(repo.path), "--offline", "--no-graph", "--json", "--no-write")
    assert r.rc in (0, 1), r.text
    files = {f["file"] for f in json.loads(r.out)["findings"] if f["rule"] == "secret-committed"}
    assert files == {"app/leak.py"}, files
    m = repo.run_script("maturity.py", "--root", str(repo.path), "--json", "--no-write")
    assert m.rc == 0, m.text
    assert "1 secret-like string" in json.loads(m.out)["metrics"]["secrets.hits"]["line"]


# ---- #14: manifests under the app paths ----

def test_maturity_finds_manifests_under_the_configured_app_paths(repo):
    repo.set_config(paths={"app": ["services/api/src/**"], "tests": ["tests/**"]})
    repo.write("services/api/pyproject.toml", '[project]\nname = "api"\ndependencies = ["fastapi==0.1"]\n')
    repo.write("services/api/uv.lock", "")
    repo.write("services/api/src/api/__init__.py", "")
    repo.commit("feat: service")
    r = repo.run_script("maturity.py", "--root", str(repo.path), "--json", "--no-write")
    assert r.rc == 0, r.text
    m = json.loads(r.out)["metrics"]
    assert m["deps.manifest"]["value"] is True, m["deps.manifest"]
    assert "services/api/pyproject.toml" in m["deps.manifest"]["line"]
    assert m["deps.lockfile"]["value"] is True
