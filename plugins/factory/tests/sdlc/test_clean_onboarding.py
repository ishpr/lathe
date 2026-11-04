"""Init writes complete files (no placeholder or conditional marker left), a gate that passes its own pin check,
dependabot entries for the manifests that exist, and the maturity assessment judges only what is committed."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[2]
INIT = PACK / "scripts" / "init_project.py"
MATURITY = PACK / "scripts" / "maturity.py"
SHA = "c" * 40
LEFTOVER = re.compile(r"\{(?:stack|test_command|tier_high_paths)\}|__[A-Z][A-Z_]*__|sdlc:if|REC-\d")


def _mod():
    spec = importlib.util.spec_from_file_location("init_project_clean", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x"}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _fixture(tmp_path: Path, *, node_deps: bool = False) -> Path:
    root = tmp_path / "client-app"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "alembic").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='c'\nversion='0'\n")
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (root / "tests" / "test_main.py").write_text("def test_x():\n    assert 1\n    assert 2\n")
    ui = root / "ui"
    ui.mkdir()
    (ui / "package.json").write_text(json.dumps({"name": "ui", "dependencies": {"left-pad": "1"}} if node_deps else {"name": "ui", "scripts": {}}))
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: initial")
    return root


def _init(root: Path, *args: str):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PACK), "SDLC_INIT_NO_MIGRATE": "1"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, "-I", str(INIT), "--root", str(root), "--graph", "off", "--allow-partial-baseline", "--marketplace-ref", SHA, *args],
        capture_output=True, text=True, env=env,
    )


def test_generated_files_have_no_placeholder_or_conditional_marker(tmp_path):
    root = _fixture(tmp_path)
    r = _init(root)
    assert r.returncode == 0, r.stdout + r.stderr
    bad = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".mypy_cache", ".ruff_cache", "app", "tests", "alembic", "ui")]
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if rel in ("pyproject.toml", ".claude/baseline.json") or rel.endswith(".json"):
                continue
            text = Path(root, rel).read_text(errors="replace")
            if rel == "CLAUDE.md":  # the one marker pair init uses to find its own block
                assert text.count("<!-- sdlc:start -->") == 1 and text.count("<!-- sdlc:end -->") == 1
                text = text.replace("<!-- sdlc:start -->", "").replace("<!-- sdlc:end -->", "")
            bad += [(rel, m.group(0)) for m in LEFTOVER.finditer(text)]
    assert not bad, bad
    claude = (root / "CLAUDE.md").read_text()
    assert "`app/auth/**`" in claude and "No module-level mutable state" in claude  # tier paths filled, routes kept
    assert "alembic downgrade" in (root / "docs" / "day-one.md").read_text()  # migrate block kept (alembic/ exists)


def test_conditional_blocks_are_dropped_when_the_repo_has_no_routes_or_migrations(tmp_path):
    root = tmp_path / "lib-only"
    (root / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='l'\nversion='0'\n")
    (root / "pkg" / "__init__.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: initial")
    r = _init(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No module-level mutable state" not in (root / "CLAUDE.md").read_text()
    assert "alembic downgrade" not in (root / "docs" / "day-one.md").read_text()
    assert "Public routes unchanged" not in (root / ".github" / "pull_request_template.md").read_text()
    assert "sits under CODEOWNERS" not in (root / "CLAUDE.md").read_text()


def test_dependabot_writes_pip_at_root_and_npm_only_with_dependencies(tmp_path):
    root = _fixture(tmp_path / "a")
    assert _init(root).returncode == 0
    text = (root / ".github" / "dependabot.yml").read_text()
    assert '"pip"' in text and 'directory: "/"' in text and '"npm"' not in text and "__DEPENDABOT_ENTRIES__" not in text
    assert re.findall(r'package-ecosystem: "(\w[\w-]*)"', text) == ["pip", "github-actions"]

    other = _fixture(tmp_path / "b", node_deps=True)
    assert _init(other).returncode == 0
    eco = re.findall(r'package-ecosystem: "([\w-]+)"\n    directory: "([^"]+)"', (other / ".github" / "dependabot.yml").read_text())
    assert ("pip", "/") in eco and ("npm", "/ui") in eco


def _pin_step(workflow: Path) -> str:
    m = re.search(r"- name: Marketplace ref is a commit SHA\n\s+run: \|\n((?:\s{10}.*\n)+)", workflow.read_text())
    assert m, "no pin step"
    return m.group(1)


def test_the_generated_gate_passes_its_own_pin_check(tmp_path):
    root = _fixture(tmp_path)
    assert _init(root).returncode == 0
    gate = root / ".github" / "workflows" / "sdlc-gate.yml"
    ran = subprocess.run(["bash", "-c", _pin_step(gate)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert f"ref: {SHA}" in gate.read_text() and "__MARKETPLACE_REF__" not in gate.read_text()


def test_a_branch_default_resolves_to_the_installed_commit_and_an_unresolvable_ref_warns(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "installed_commit", lambda plugin: SHA)
    monkeypatch.setattr(mod, "remote_sha", lambda repo, ref: "d" * 40 if ref == "v1.0.0" else "")
    assert mod.resolve_pin("/p", "o/r", None) == (SHA, "")
    assert mod.resolve_pin("/p", "o/r", "v1.0.0") == ("d" * 40, "")
    assert mod.resolve_pin("/p", "o/r", "e" * 40) == ("e" * 40, "")
    ref, warn = mod.resolve_pin("/p", "o/r", "nope")
    assert ref == "nope" and "40-character" in warn  # negative: the failure is loud, not a silent bad pin


def _mat(root: Path) -> dict:
    p = subprocess.run([sys.executable, "-I", str(MATURITY), "--root", str(root), "--json", "--no-write"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


def test_maturity_ignores_files_the_kit_just_created_and_counts_committed_ones(tmp_path):
    root = _fixture(tmp_path)
    before = _mat(root)
    assert before["metrics"]["ci.present"]["value"] is False
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: ci\non: [push, pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python3 -m pytest\n")
    (root / "CLAUDE.md").write_text("# Working agreement\nRun the tests before you commit.\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{}\n")
    untracked = _mat(root)
    assert untracked["metrics"]["ci.present"]["value"] is False  # negative: untracked files are not the team's
    assert {d["key"]: d["level"] for d in untracked["dimensions"]} == {d["key"]: d["level"] for d in before["dimensions"]}
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ci: add the workflow and the agreement")
    committed = _mat(root)
    assert committed["metrics"]["ci.present"]["value"] is True
