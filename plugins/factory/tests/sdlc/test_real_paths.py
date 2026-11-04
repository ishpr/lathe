"""Init writes only default test globs that match a directory (cadence rehearsal: `test/**` matched nothing)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

INIT = Path(__file__).resolve().parents[2] / "scripts" / "init_project.py"


def _mod():
    spec = importlib.util.spec_from_file_location("init_project_real_paths", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_default_test_glob_with_no_directory_is_dropped(tmp_path):
    (tmp_path / "tests").mkdir()
    assert _mod().existing_globs(str(tmp_path), ["tests/**", "test/**"]) == ["tests/**"]


def test_a_repo_with_no_test_directory_keeps_the_stack_convention(tmp_path):
    # negative case: nothing exists yet, so the defaults stay rather than an empty list
    assert _mod().existing_globs(str(tmp_path), ["tests/**", "test/**"]) == ["tests/**", "test/**"]
