"""demo/run_demo.sh deletes DEMO_WORK: the guard must refuse anything but a scratch path (review finding)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "demo" / "run_demo.sh"


def run(work: Path | str, fixture: Path | str, tmp: Path):
    env = {**os.environ, "DEMO_WORK": str(work), "DEMO_FIXTURE": str(fixture), "TMPDIR": str(tmp)}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def tree(tmp_path):
    fixture = tmp_path / "scratch" / "fixture"
    (fixture / ".git").mkdir(parents=True)
    (tmp_path / "scratch" / "sentinel.txt").write_text("keep me")
    (fixture / "app.py").write_text("keep me too")
    return tmp_path, fixture


@pytest.mark.parametrize("which", ["ancestor_of_fixture", "inside_fixture", "fixture_itself", "home", "repo_root", "outside_scratch"])
def test_dangerous_demo_work_is_refused_and_nothing_is_deleted(tree, which):
    tmp, fixture = tree
    work = {"ancestor_of_fixture": fixture.parent, "inside_fixture": fixture / "sub", "fixture_itself": fixture,
            "home": Path.home(), "repo_root": ROOT, "outside_scratch": Path.home() / "Documents"}[which]
    r = run(work, fixture, tmp)
    assert r.returncode == 2 and "refusing DEMO_WORK" in r.stderr, (r.stdout, r.stderr)
    assert (fixture.parent / "sentinel.txt").read_text() == "keep me"
    assert (fixture / "app.py").exists()
