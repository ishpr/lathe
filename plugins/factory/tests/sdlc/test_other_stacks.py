"""A second stack below the root (a backend with a frontend/ beside it) is reported, never silently dropped.

Found in the FDE panel prep: cadence is Python at the root with a React frontend/, and init said nothing about it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

INIT = Path(__file__).resolve().parents[2] / "scripts" / "init_project.py"


def _mod():
    spec = importlib.util.spec_from_file_location("init_project_other_stacks", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path, *, frontend: dict | None) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='r'\nversion='0'\n")
    if frontend is not None:
        (root / "frontend").mkdir()
        (root / "frontend" / "package.json").write_text(json.dumps(frontend))
    return root


def test_a_frontend_without_a_test_script_is_reported_as_audited_not_gated(tmp_path):
    root = _repo(tmp_path, frontend={"name": "fe", "version": "0.1.0"})
    assert _mod().other_stacks(str(root), "python") == [
        {"stack": "node", "path": "frontend", "gated": False, "why": "no test command (package.json has no test script)"}
    ]


def test_a_frontend_with_a_test_script_still_is_not_gated_by_the_python_config(tmp_path):
    root = _repo(tmp_path, frontend={"name": "fe", "scripts": {"test": "vitest run"}})
    [entry] = _mod().other_stacks(str(root), "python")
    assert entry["path"] == "frontend" and entry["gated"] is False and "one config governs one stack" in entry["why"]


def test_a_single_stack_repo_reports_no_other_stacks(tmp_path):
    # negative case: nothing to report, so nothing is written
    assert _mod().other_stacks(str(_repo(tmp_path, frontend=None)), "python") == []
