"""Finding #56: a mypy found on PATH or through uvx runs in its own interpreter, so every new module that imports a
project package read as a new `import-not-found` key and turned the ratchet red. The ratchet points such a mypy at
the project's Python; a mypy from the project's own environment is left alone."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ratchet.py"


def _mod():
    spec = importlib.util.spec_from_file_location("ratchet_mypy_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _venv(root: Path) -> Path:
    py = root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    return py


def test_a_mypy_from_path_is_pointed_at_the_projects_python(tmp_path, monkeypatch):
    mod = _mod()
    py = _venv(tmp_path)
    monkeypatch.setattr(mod.cl, "resolve_tool", lambda tool, root: (["mypy"], "2.4.1", "PATH"))
    cmd, problem = mod.prepare_command("mypy app", str(tmp_path), {}, {})
    assert problem is None
    assert cmd == f"mypy --python-executable {py} app"


def test_a_project_mypy_and_an_explicit_interpreter_are_left_alone(tmp_path, monkeypatch):
    # negative cases: the project's own mypy already sees its packages, and a configured flag wins
    mod = _mod()
    _venv(tmp_path)
    exe = str(tmp_path / ".venv" / "bin" / "mypy")
    monkeypatch.setattr(mod.cl, "resolve_tool", lambda tool, root: ([exe], "2.4.1", "the project environment (.venv)"))
    assert mod.prepare_command("mypy app", str(tmp_path), {}, {})[0] == f"{exe} app"
    monkeypatch.setattr(mod.cl, "resolve_tool", lambda tool, root: (["uvx", "mypy"], "2.4.1", "uvx"))
    cmd = mod.prepare_command("mypy --python-executable /opt/py app", str(tmp_path), {}, {})[0]
    assert cmd == "uvx mypy --python-executable /opt/py app"


def test_without_a_project_environment_the_python3_on_path_is_used(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert mod.project_python(str(tmp_path)) == "/usr/bin/python3"
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    assert not mod.project_python(str(tmp_path))
