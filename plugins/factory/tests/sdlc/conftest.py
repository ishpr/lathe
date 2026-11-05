"""Fixtures for the sdlc hook, ratchet and script suite (REC-41).

Standard library plus pytest only, so the same tests run in CI (marketplace test.yml) and on a
laptop. Every test works on a throwaway git repo built here: a tiny FastAPI-style app, an initial
commit on `main`, then a feature branch. The repo is described to the plugin by a hand-written
`.claude/sdlc.json`; nothing depends on init_project.py.

The kit ships the same suite (repo/tests/hooks) with a conftest that copies the kit's `.claude/` and
`scripts/` into the temp repo instead; the tests only talk to the `repo` object below, so one
test file serves both forms. `repo.form` is "plugin" or "kit" for the few places that differ.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FORM = "plugin"
PACK = Path(__file__).resolve().parents[2]  # the plugin root


@pytest.fixture(autouse=True)
def _laptop_environment(monkeypatch):
    """In-process script calls behave as on a laptop. GitHub's runners set CI=1, under which a
    missing tool is refused by design; tests that want CI behaviour set it themselves, and
    subprocess tests pass their own environment."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("SDLC_INIT_NO_MIGRATE", "1")  # init does not reach for a database from the suite

APP_MAIN = '''\
from fastapi import APIRouter, Depends, FastAPI

from app.auth import api_key_auth
from app.config import settings

app = FastAPI(title=settings.name)
router = APIRouter(dependencies=[Depends(api_key_auth)])


@router.get("/runs")
def list_runs():
    return []


app.include_router(router)
'''
APP_AUTH = '''\
def api_key_auth():
    return True
'''
APP_CONFIG = '''\
import os


class Settings:
    name = "app"
    database_url = os.environ.get("DATABASE_URL", "sqlite://")


settings = Settings()
'''
TEST_X = '''\
def test_x():
    assert 1 + 1 == 2
'''
GITIGNORE = '''\
__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.claude/work/last_verify.json
.claude/work/blocks.jsonl
.claude/work/.stop_nudged
'''
# lint-free, mypy-free: the kit's ratchet runs real ruff when it is installed
PLUGIN_CONFIG = {
    "version": 1,
    "stack": "python",
    "checkers": ["route_auth", "module_state"],
    "commands": {"test": "true", "lint_report": "", "lint_fix": "", "format": "", "typecheck": ""},
}


class Res(SimpleNamespace):
    """rc, out, err of one subprocess; `text` is both streams."""

    @property
    def text(self) -> str:
        return (self.out or "") + (self.err or "")


class Repo:
    form = FORM

    def __init__(self, path: Path) -> None:
        self.path = path
        self.home = path.parent / (path.name + "-home")
        self.home.mkdir(exist_ok=True)

    # ---- environment ----
    def env(self, **extra: str) -> dict[str, str]:
        # CI and GITHUB_ACTIONS are dropped so the suite behaves the same on a laptop and on a
        # runner; the ratchet's CI mode (refuse an unconfigured repo) is tested by setting CI=1
        # explicitly through `extra`.
        e = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("CLAUDE_", "GIT_", "VERIFY_", "DATABASE_URL", "REDIS_URL"))
            and k not in ("CI", "GITHUB_ACTIONS", "BASE_REF")  # BASE_REF: the CI gate job exports it; a test sets it through extra
        }
        e.update(
            HOME=str(self.home),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="t@example.com",
            CLAUDE_PROJECT_DIR=str(self.path),
            PYTHONDONTWRITEBYTECODE="1",
        )
        e.update(self.pack_env())
        e.update(extra)
        return e

    def pack_env(self) -> dict[str, str]:
        return {"CLAUDE_PLUGIN_ROOT": str(PACK)}

    # ---- files and git ----
    def write(self, rel: str, text: str = "", mode: int | None = None) -> Path:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mode:
            p.chmod(mode)
        return p

    def read(self, rel: str) -> str:
        return (self.path / rel).read_text(encoding="utf-8")

    def exists(self, rel: str) -> bool:
        return (self.path / rel).exists()

    def git(self, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, env=self.env())
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
        return p.stdout

    def commit(self, msg: str = "chore: snapshot") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "--short", "HEAD").strip()

    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    # ---- contract and block log ----
    def contract(self, **fields) -> dict:
        data = {"slug": "rl", "allowed_paths": ["app/**", "tests/**"], "tier": "low", "require_tests": True}
        data.update(fields)
        data = {k: v for k, v in data.items() if v is not None}
        self.write(".claude/work/current.json", json.dumps(data, indent=2) + "\n")
        self.write(f".claude/work/{data['slug']}/contract.md", "# rate limit\n\n## Decisions\nNone\n")
        return data

    def current(self) -> dict:
        return json.loads(self.read(".claude/work/current.json"))

    def blocks(self) -> list[dict]:
        p = self.path / ".claude" / "work" / "blocks.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def stamp(self) -> dict | None:
        p = self.path / ".claude" / "work" / "last_verify.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # ---- hooks ----
    def hooks_dir(self) -> Path:
        return PACK / "hooks"

    def registration_path(self) -> Path:
        return PACK / "hooks" / "hooks.json"

    def hook(self, name: str, payload: dict | None = None, raw: str | None = None, env: dict | None = None) -> Res:
        data = raw if raw is not None else json.dumps(payload if payload is not None else {})
        p = subprocess.run(
            [sys.executable, "-I", str(self.hooks_dir() / name)],
            input=data, capture_output=True, text=True, cwd=self.path, env=env or self.env(), timeout=120,
        )
        return Res(rc=p.returncode, out=p.stdout, err=p.stderr)

    def bash(self, cmd: str) -> Res:
        return self.hook("guard_bash.py", {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.path)})

    def edit(self, rel: str, tool: str = "Edit", old: str = "a", new: str = "b", content: str | None = None, edits: list | None = None) -> Res:
        path = rel if os.path.isabs(rel) else str(self.path / rel)
        if tool == "Write":
            ti = {"file_path": path, "content": content if content is not None else new}
        elif tool == "MultiEdit":
            ti = {"file_path": path, "edits": edits or [{"old_string": old, "new_string": new}]}
        else:
            ti = {"file_path": path, "old_string": old, "new_string": new}
        return self.hook("guard_edit.py", {"tool_name": tool, "tool_input": ti, "cwd": str(self.path)})

    # ---- scripts ----
    def script_path(self, name: str) -> Path:
        return PACK / "scripts" / name

    def run_script(self, name: str, *args: str, stdin: str | None = None, env: dict | None = None, cwd: Path | None = None, timeout: int = 180) -> Res:
        path = self.script_path(name)
        cmd = ["bash", str(path), *args] if name.endswith(".sh") else [sys.executable, "-I", str(path), *args]
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd or self.path, env=env or self.env(), timeout=timeout)
        return Res(rc=p.returncode, out=p.stdout, err=p.stderr)

    def checker(self, name: str, *args: str) -> Res:
        """Run one python checker ('stubs', 'dead_code', 'api_contract', 'route_auth', ...)."""
        path = PACK / "scripts" / "checkers" / "python" / f"{name}.py"
        cmd = [sys.executable, "-I", str(path), "--root", str(self.path), "--config", str(self.path / ".claude" / "sdlc.json"), *args]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=self.path, env=self.env(), timeout=120)
        return Res(rc=p.returncode, out=p.stdout, err=p.stderr)

    def ratchet(self, *args: str) -> Res:
        return self.run_script("ratchet.py", "--root", str(self.path), *args)

    def verify(self, *args: str, env: dict | None = None) -> Res:
        return self.run_script("verify.py", "--root", str(self.path), *args, env=env)

    def fingerprint(self) -> str:
        return self.run_script("worktree_fingerprint.py", str(self.path)).out.strip()

    def set_config(self, **fields) -> None:
        """Merge fields into .claude/sdlc.json (plugin only; the kit hard-codes its settings)."""
        cfg = json.loads(self.read(".claude/sdlc.json"))
        cfg.update(fields)
        self.write(".claude/sdlc.json", json.dumps(cfg, indent=1) + "\n")

    def install_config(self) -> None:
        self.write(".claude/sdlc.json", json.dumps(PLUGIN_CONFIG, indent=1) + "\n")


def build_repo(path: Path, repo_cls=Repo) -> Repo:
    path.mkdir(parents=True)
    repo = repo_cls(path)
    repo.git("init", "-q", "-b", "main")
    repo.write("app/__init__.py", "")
    repo.write("app/main.py", APP_MAIN)
    repo.write("app/auth.py", APP_AUTH)
    repo.write("app/config.py", APP_CONFIG)
    repo.write("tests/test_x.py", TEST_X)
    repo.write(".gitignore", GITIGNORE)
    repo.install_config()
    repo.commit("chore: initial")
    repo.git("switch", "-q", "-c", "feat/work")
    return repo


@pytest.fixture
def repo(tmp_path) -> Repo:
    """A fresh repo on branch feat/work with one commit on main."""
    return build_repo(tmp_path / "proj")


@pytest.fixture(scope="session")
def shared_repo(tmp_path_factory) -> Repo:
    """One repo for read-only guard cases (shell analysis); tests must not change its files."""
    return build_repo(tmp_path_factory.mktemp("shared") / "proj")


@pytest.fixture
def uninit(tmp_path) -> Repo:
    """A git repo with no .claude directory and no config: the guards must not create one."""
    path = tmp_path / "bare"
    path.mkdir()
    r = Repo(path)
    r.git("init", "-q", "-b", "main")
    r.write("README.md", "hi\n")
    r.commit("chore: initial")
    r.git("switch", "-q", "-c", "feat/work")
    return r


@pytest.fixture
def form(repo) -> str:
    return repo.form
