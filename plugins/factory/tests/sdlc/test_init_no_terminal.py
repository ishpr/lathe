"""Onboarding needs no terminal (findings #57 to #65): init finds every source directory, takes the confirmed paths as
flags, drops high-risk globs that match nothing, writes a working agreement that names only real paths, prints a baseline
command the sandbox settings let through, and the commit guard puts an onboarding-only commit to the human."""

from __future__ import annotations

import builtins
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[2]
INIT = PACK / "scripts" / "init_project.py"
GUARD = PACK / "hooks" / "guard_bash.py"
PATHS_CHECK = PACK / "scripts" / "check_claude_md_paths.py"
SKILL = PACK / "skills" / "init" / "SKILL.md"

ONBOARDING_COMMIT = 'git commit -m "chore(onboarding): add factory files" -m "body"'
ONBOARDING_FILES = (
    ".claude/rules/sdlc-common.md",
    ".github/workflows/sdlc-gate.yml",
    "docs/onboarding-report.md",
)


def _git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)


def _env() -> dict:
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PACK), "SDLC_INIT_NO_MIGRATE": "1"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(INIT),
            "--root",
            str(root),
            "--stack",
            "python",
            "--graph",
            "off",
            "--allow-partial-baseline",
            "--no-ci",
            *args,
        ],
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )


def _touch(root: Path, rel: str, text: str = "x = 1\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _cfg(root: Path) -> dict:
    return json.loads((root / ".claude" / "sdlc.json").read_text(encoding="utf-8"))


def _python_project(tmp_path: Path, name: str = "proj") -> Path:
    """The plugins/factory layout: scripts, hooks and lib without __init__.py, one real package."""
    root = tmp_path / name
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _touch(root, "scripts/a.py")
    _touch(root, "hooks/b.py")
    _touch(root, "lib/c.py")
    _touch(root, "sdlc_graph/__init__.py", "")
    _git(root)
    return root


def _init_module():
    spec = importlib.util.spec_from_file_location("init_no_terminal", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 1, 2: app paths ------------------------------------------------------------------------------------------------------


def test_python_app_paths_include_every_source_dir_but_tests_docs_and_build(tmp_path):
    # AC1: scripts, hooks, lib (no __init__.py) and sdlc_graph are all app paths, in the config and in the dry run
    root = _python_project(tmp_path)
    wanted = {"scripts/**", "hooks/**", "lib/**", "sdlc_graph/**"}
    dry = _run(root, "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert not (root / ".claude" / "sdlc.json").exists(), "a dry run writes nothing"
    for glob in wanted:
        assert glob in dry.stdout, f"{glob} missing from the dry run:\n{dry.stdout}"
    real = _run(root)
    assert real.returncode == 0, real.stdout + real.stderr
    assert wanted <= set(_cfg(root)["paths"]["app"])


def test_dirs_without_python_or_named_tests_docs_fixtures_examples_templates_are_not_app_paths(
    tmp_path,
):
    # AC2 (negative): excluded names and a directory with no .py file are never app paths
    root = _python_project(tmp_path)
    for d in (
        "tests",
        "test",
        "docs",
        "fixtures",
        "examples",
        "templates",
        "build",
        "dist",
    ):
        _touch(root, f"{d}/mod.py")
    _touch(root, "data/rows.json", "{}\n")
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    app = _cfg(root)["paths"]["app"]
    assert {"scripts/**", "hooks/**", "lib/**", "sdlc_graph/**"} <= set(app)
    for d in (
        "tests",
        "test",
        "docs",
        "fixtures",
        "examples",
        "templates",
        "build",
        "dist",
        "data",
    ):
        assert f"{d}/**" not in app, f"{d}/** must not be an app path: {app}"


# 3, 4: path flags -----------------------------------------------------------------------------------------------------


def test_path_flags_set_the_config_and_scope_the_baseline(tmp_path):
    # AC3: the four flags become the config exactly, and baseline keys of the four checks stay under app/test paths
    root = tmp_path / "flags"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    stub = "def todo():\n    raise NotImplementedError\n\n\ndef orphan_helper():\n    return 1\n"
    for rel in ("lib/a.py", "hooks/b.py", "scripts/s.py", "gen/g.py"):
        _touch(root, rel, stub)
    _touch(root, "tests/test_a.py", "def test_a():\n    assert True\n")
    _git(root)
    r = _run(
        root,
        "--app-path",
        "lib/**",
        "--app-path",
        "hooks/**",
        "--test-path",
        "tests/**",
        "--protected-path",
        "gen/**",
        "--high-risk-path",
        "hooks/**",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = _cfg(root)
    assert cfg["paths"]["app"] == ["lib/**", "hooks/**"]
    assert cfg["paths"]["tests"] == ["tests/**"]
    assert "gen/**" in cfg["paths"]["protected"]
    assert cfg["tier_high_paths"] == ["hooks/**"]
    counts = json.loads((root / ".claude" / "baseline.json").read_text())["counts"]
    scoped = [
        k for k in counts if k.split("|", 1)[0] in ("dead", "stubs", "state", "tests")
    ]
    for key in scoped:
        assert re.search(r"(^|\|)(\./)?(lib|hooks|tests)/", key), (
            f"{key} is outside the given paths"
        )
        assert "scripts/" not in key and "gen/" not in key, key


def test_a_path_flag_that_matches_no_file_is_refused(tmp_path):
    # AC4 (negative): a glob that matches no file exits 2, names itself, and nothing is written
    root = _python_project(tmp_path, "refused")
    r = _run(root, "--app-path", "nope/**")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "nope/**" in r.stdout + r.stderr
    assert not (root / ".claude" / "sdlc.json").exists()
    # the same flag with a glob that matches is accepted, so the refusal is about the glob and not the flag
    ok = _python_project(tmp_path, "accepted")
    good = _run(ok, "--app-path", "lib/**")
    assert good.returncode == 0, good.stdout + good.stderr
    assert (ok / ".claude" / "sdlc.json").is_file()


# 5: high-risk defaults ------------------------------------------------------------------------------------------------


def test_default_high_risk_globs_that_match_nothing_are_dropped_and_named(tmp_path):
    # AC5: defaults matching no file leave tier_high_paths and are named on a `note:` line; matching ones stay
    root = _python_project(tmp_path)
    _touch(root, ".github/workflows/x.yml", "name: x\n")
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    high = _cfg(root)["tier_high_paths"]
    assert ".github/**" in high
    for gone in ("app/auth/**", "alembic/**", ".devcontainer/**"):
        assert gone not in high, f"{gone} matches no file and must be dropped: {high}"
    notes = [ln for ln in r.stdout.splitlines() if ln.startswith("note:")]
    assert any("app/auth/**" in ln for ln in notes), r.stdout


# 6, 7: the working agreement ------------------------------------------------------------------------------------------


def test_the_working_agreement_names_only_paths_that_exist(tmp_path):
    # AC6: the CLAUDE.md block passes the path check on a repo with no app/ dir, and init prints that check's result
    root = _python_project(tmp_path)
    assert not (root / "app").exists()
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "check_claude_md_paths:" in r.stdout
    chk = subprocess.run(
        [sys.executable, "-I", str(PATHS_CHECK), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert chk.returncode == 0, chk.stdout + chk.stderr


def _deny_writes(monkeypatch, *suffixes: str) -> None:
    real = builtins.open

    def guarded(file, mode="r", *a, **k):
        if "w" in str(mode) or "a" in str(mode):
            path = str(file).replace("\\", "/")
            if ".sdlc-stage/" not in path and path.endswith(suffixes):
                raise PermissionError(1, "Operation not permitted", str(file))
        return real(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded)


def test_the_mcp_line_follows_whether_mcp_json_exists(tmp_path, monkeypatch):
    # AC7 (negative): "empty on purpose" appears only when the root .mcp.json exists after init
    with_mcp = _python_project(tmp_path, "with_mcp")
    assert _run(with_mcp).returncode == 0
    assert (with_mcp / ".mcp.json").is_file()
    assert "empty on purpose" in (with_mcp / "CLAUDE.md").read_text(encoding="utf-8")

    without = _python_project(tmp_path, "without_mcp")
    _deny_writes(monkeypatch, ".mcp.json")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PACK))
    monkeypatch.setenv("SDLC_INIT_NO_MIGRATE", "1")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rc = _init_module().main(
        [
            "--root",
            str(without),
            "--stack",
            "python",
            "--graph",
            "off",
            "--allow-partial-baseline",
            "--no-ci",
        ]
    )
    assert rc == 0
    assert not (without / ".mcp.json").exists()
    assert "empty on purpose" not in (without / "CLAUDE.md").read_text(encoding="utf-8")


# 8: the skill ---------------------------------------------------------------------------------------------------------


def test_the_init_skill_writes_staged_files_through_the_write_tool():
    # AC8: the skill Writes each staged file with the Write tool and no longer hands the human a cp line
    text = re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8"))
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z(*`])", text)
    assert any("with the Write tool" in s and ".sdlc-stage" in s for s in sentences), (
        "no sentence tells the conversation to write a .sdlc-stage file with the Write tool"
    )
    assert not "a human runs: cp" in text


# 9: the printed baseline command --------------------------------------------------------------------------------------


def test_the_printed_baseline_command_matches_the_excluded_command(
    tmp_path, monkeypatch, capsys
):
    # AC9: the command printed after `baseline: not recorded` matches a sandbox.excludedCommands entry init wrote
    mod = _init_module()
    root = _python_project(tmp_path)
    real = mod.cl.run

    def fake(cmd, *a, **kw):
        if any(str(c).endswith("ratchet.py") for c in cmd):
            return subprocess.CompletedProcess(
                cmd, 1, "", "PermissionError: [Errno 1] Operation not permitted: 'x'\n"
            )
        return real(cmd, *a, **kw)

    monkeypatch.setattr(mod.cl, "run", fake)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PACK))
    monkeypatch.setenv("SDLC_INIT_NO_MIGRATE", "1")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rc = mod.main(
        [
            "--root",
            str(root),
            "--stack",
            "python",
            "--graph",
            "off",
            "--no-ci",
            "--allow-partial-baseline",
        ]
    )
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    at = next(i for i, ln in enumerate(out) if ln.startswith("baseline: not recorded"))
    printed = out[at + 1].strip()
    assert "ratchet.py" in printed and "--update" in printed
    excluded: list[str] = []
    for name in ("settings.json", "settings.local.json"):
        f = root / ".claude" / name
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            excluded += (data.get("sandbox") or {}).get("excludedCommands") or []
    ratchet_entries = [e for e in excluded if "ratchet.py" in e]
    assert ratchet_entries, excluded
    assert any(
        e.endswith(" *") and fnmatch.fnmatchcase(printed, e) for e in ratchet_entries
    ), (
        printed,
        ratchet_entries,
    )


# 10 to 12: the commit guard -------------------------------------------------------------------------------------------


def _commit_repo(tmp_path: Path, *staged: str) -> Path:
    root = tmp_path / "guarded"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "sdlc.json").write_text(
        json.dumps({"paths": {"app": ["app/**"], "tests": ["tests/**"]}}),
        encoding="utf-8",
    )
    _touch(root, "README.md", "# r\n")
    genv = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }

    def git(*a: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *a], check=True, capture_output=True, env=genv
        )

    git("init", "-q", "-b", "work")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: base")
    for rel in staged:
        _touch(root, rel, "x = 1\n" if rel.endswith(".py") else "text\n")
    git("add", *staged)
    return root


def _guard(root: Path, cmd: str, *, headless: bool) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    env.update(
        CLAUDE_PROJECT_DIR=str(root),
        CLAUDE_PLUGIN_ROOT=str(PACK),
        HOME=str(root.parent),
    )
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "cwd": str(root),
        "permission_mode": "dontAsk" if headless else "default",
    }
    return subprocess.run(
        [sys.executable, "-I", str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
        check=False,
    )


def _blocks(root: Path) -> list[dict]:
    p = root / ".claude" / "work" / "blocks.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_an_onboarding_only_commit_gets_an_ask_naming_unprotected_files(tmp_path):
    # AC10: interactive, an onboarding-only commit with no stamp is put to the human with every file listed
    root = _commit_repo(tmp_path, *ONBOARDING_FILES)
    r = _guard(root, ONBOARDING_COMMIT, headless=False)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask"
    reason = out["permissionDecisionReason"]
    for rel in ONBOARDING_FILES:
        assert rel in reason, f"{rel} not listed in the ask:\n{reason}"
    assert ".github/workflows/sdlc-gate.yml (not edit-protected" in reason
    assert ".claude/rules/sdlc-common.md (not edit-protected" not in reason


def test_a_commit_with_an_app_or_test_path_and_no_stamp_is_still_refused(tmp_path):
    # AC11 (negative): one app file, or one test file next to an app file, makes it a stamped commit again
    plus_app = _commit_repo(tmp_path / "a", *ONBOARDING_FILES, "app/x.py")
    r = _guard(plus_app, ONBOARDING_COMMIT, headless=False)
    assert r.returncode == 2, r.stdout + r.stderr
    assert _blocks(plus_app)[-1]["rule"] == "no-stamp"

    plus_both = _commit_repo(
        tmp_path / "b", *ONBOARDING_FILES, "tests/test_y.py", "app/x.py"
    )
    r = _guard(plus_both, ONBOARDING_COMMIT, headless=False)
    assert r.returncode == 2, r.stdout + r.stderr
    assert _blocks(plus_both)[-1]["rule"] == "no-stamp"


def test_a_headless_onboarding_commit_is_refused(tmp_path):
    # AC12 (negative): the same onboarding-only commit, run unattended, is refused and never asked
    root = _commit_repo(tmp_path, *ONBOARDING_FILES)
    r = _guard(root, ONBOARDING_COMMIT, headless=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "permissionDecision" not in r.stdout
    assert _blocks(root)[-1]["rule"] == "no-stamp"
