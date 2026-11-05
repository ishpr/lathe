"""Init never stops on a file the sandbox protects (it stages it and carries on), and a repo whose code lives below the
root gets one test command for every project and high-risk paths under each project (findings #51 to #53)."""

from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path

INIT = Path(__file__).resolve().parents[2] / "scripts" / "init_project.py"
ROUTES = ".claude/public_routes.txt"


def _mod():
    spec = importlib.util.spec_from_file_location("init_project_sandboxed", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='c'\nversion='0'\n")
    (root / "app" / "main.py").write_text("x = 1\n")
    return root


def _init(root: Path, *extra: str) -> int:
    return _mod().main(
        [
            "--root",
            str(root),
            "--stack",
            "python",
            "--no-ci",
            "--graph",
            "off",
            "--allow-partial-baseline",
            *extra,
        ]
    )


def _deny(monkeypatch, *suffixes: str) -> None:
    real = builtins.open

    def guarded(file, mode="r", *a, **k):
        if "w" in str(mode) or "a" in str(mode):
            path = str(file).replace("\\", "/")
            if ".sdlc-stage/" not in path and path.endswith(suffixes):
                raise PermissionError(1, "Operation not permitted", str(file))
        return real(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded)


def test_a_refused_write_is_staged_with_its_cp_line_and_init_continues(
    tmp_path, monkeypatch, capsys
):
    root = _repo(tmp_path)
    _deny(monkeypatch, ROUTES)
    assert _init(root) == 0
    out = capsys.readouterr().out
    assert not (root / ROUTES).exists()
    assert (root / ".sdlc-stage" / ROUTES).is_file()
    line = f"staged: {ROUTES} is not writable here (the sandbox protects it); the init skill writes .sdlc-stage/{ROUTES} to {ROUTES} with the Write tool (one approval prompt)"
    assert line in out
    assert "a human runs: cp" not in out
    assert f"  .sdlc-stage/{ROUTES} -> {ROUTES}" in out, (
        "the final summary lists the staged file and its target"
    )
    # later files were still written
    assert (root / ".claude" / "sdlc.json").is_file()
    assert (root / ".claude" / "KIT_VERSION").is_file()
    assert (
        "sdlc:start" in (root / "CLAUDE.md").read_text()
        or (root / "CLAUDE.md").is_file()
    )
    assert ".sdlc-stage/" in (root / ".gitignore").read_text()


def test_a_writable_file_is_written_directly_and_never_staged(tmp_path, capsys):
    root = _repo(tmp_path)
    assert _init(root) == 0
    out = capsys.readouterr().out
    assert (root / ROUTES).is_file()
    assert not (root / ".sdlc-stage").exists()
    assert "staged:" not in out


def test_several_refused_files_are_all_staged(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    _deny(monkeypatch, ROUTES, ".claude/KIT_VERSION", "CLAUDE.md")
    assert _init(root) == 0
    out = capsys.readouterr().out
    for rel in (ROUTES, ".claude/KIT_VERSION", "CLAUDE.md"):
        assert (root / ".sdlc-stage" / rel).is_file()
        assert f".sdlc-stage/{rel} -> {rel}" in out
    assert (root / ".claude" / "sdlc.json").is_file()


def _monorepo(tmp_path: Path, names=("alpha", "beta")) -> Path:
    root = tmp_path / "mono"
    for n in names:
        (root / "examples" / n / "app" / "auth").mkdir(parents=True)
        (root / "examples" / n / "tests").mkdir()
        (root / "examples" / n / "pyproject.toml").write_text(
            "[project]\nname='x'\nversion='0'\n"
        )
        (root / "examples" / n / "uv.lock").write_text("")
    return root


def test_with_several_projects_the_test_command_runs_each_one(tmp_path):
    root = _monorepo(tmp_path)
    assert _init(root) == 0
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    test = cfg["commands"]["test"]
    assert (
        "uv run --project examples/alpha" in test
        and "uv run --project examples/beta" in test
    )
    assert test.count(" && ") == 1


def test_with_one_project_the_test_command_names_its_tests(tmp_path):
    root = _monorepo(tmp_path, names=("alpha",))
    assert _init(root) == 0
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    assert (
        cfg["commands"]["test"]
        == "uv run --project examples/alpha pytest -q examples/alpha/tests"
    )
    assert "&&" not in cfg["commands"]["test"]


def test_one_picked_project_keeps_a_single_command(tmp_path):
    root = _monorepo(tmp_path)
    assert _init(root, "--project", "examples/beta") == 0
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    assert (
        cfg["commands"]["test"]
        == "uv run --project examples/beta pytest -q examples/beta/tests"
    )


def test_high_risk_paths_are_prefixed_per_project_and_repo_wide_ones_stay(tmp_path):
    root = _monorepo(tmp_path)
    # defaults that match no file are dropped, so the files the globs name must exist
    for n in ("alpha", "beta"):
        (root / "examples" / n / "app" / "auth" / "login.py").write_text("x = 1\n")
        (root / "examples" / n / "alembic").mkdir()
        (root / "examples" / n / "alembic" / "env.py").write_text("x = 1\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    assert _init(root) == 0
    high = json.loads((root / ".claude" / "sdlc.json").read_text())["tier_high_paths"]
    for n in ("alpha", "beta"):
        assert f"examples/{n}/app/auth/**" in high
        assert f"examples/{n}/alembic/**" in high
    for wide in (".github/**", ".claude/**", ".mcp.json", "docker-compose*.yml"):
        assert wide in high
    assert "app/auth/**" not in high and "alembic/**" not in high


def test_a_single_root_project_keeps_root_level_high_risk_paths(tmp_path):
    root = _repo(tmp_path)
    (root / "app" / "auth").mkdir(
        parents=True
    )  # a default that matches no file is dropped
    (root / "app" / "auth" / "login.py").write_text("x = 1\n")
    assert _init(root) == 0
    high = json.loads((root / ".claude" / "sdlc.json").read_text())["tier_high_paths"]
    assert "app/auth/**" in high and not any(g.startswith("examples/") for g in high)


def test_a_project_pytest_command_names_the_projects_tests(tmp_path):
    import importlib.util
    from pathlib import Path as _P

    spec = importlib.util.spec_from_file_location(
        "init_scope", _P(__file__).resolve().parents[2] / "scripts" / "init_project.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "plugins" / "factory" / "tests").mkdir(parents=True)
    assert (
        mod.scope_to_project(
            "uv run --project plugins/factory pytest -q",
            str(tmp_path),
            "plugins/factory",
        )
        == "uv run --project plugins/factory pytest -q plugins/factory/tests"
    )
    # negative cases: already scoped, or not pytest, stays as it is
    assert (
        mod.scope_to_project(
            "uv run --project plugins/factory pytest -q plugins/factory/tests",
            str(tmp_path),
            "plugins/factory",
        ).count("plugins/factory/tests")
        == 1
    )
    assert (
        mod.scope_to_project("cd web && npm test", str(tmp_path), "web")
        == "cd web && npm test"
    )
