"""Shared pytest fixtures.

Database fixtures run in a private schema ``test_<random>`` created for the session (so parallel
pytest runs never collide) and dropped at the end. ``SDLC_SCHEMA`` is set to that schema for the
whole session, so code under test that calls ``db.connect()`` with no schema (CLI handlers, the
MCP server, hooks run in-process) lands in it too. When Postgres is unreachable, every test that
uses a database fixture is SKIPPED with the reason; pure tests still run.

Fixtures:
    pg_schema        (session) the schema name
    conn_factory     (session) zero-arg callable -> new connection bound to pg_schema
    conn             (function) one connection, shared session state (no cleanup)
    clean_conn       (function) one connection after TRUNCATE of every table (isolated test)
    sample_app       (session) Path of a git checkout of the repository's examples/sample-app (built once
                     per session in a temp directory; read-only by convention; skip if the example is missing)
    sample_app_copy  (function) a private mutable copy of it (with .git) under tmp_path
    sample_repo      (session) Path of the optional private sample repository named by ``SAMPLE_REPO``
                     (any git checkout; skip when unset or not a checkout). Tests on it assert only what
                     holds for any repository; the sample app's planted facts are asserted on sample_app.
    synthetic_repo   (session) Path of a tiny git repo with planted structure (read-only)
    synthetic_facts  (session) dict of the facts planted in synthetic_repo
    make_synthetic_repo (function) factory(dest=None) -> Path: a fresh mutable synthetic repo
    git_commit       (function) helper(repo, files: {path: text|None}, message) -> sha
                     (None deletes the file)
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from sdlc_graph import db

#: The repository's sample app (plugins/graph/tests -> the repository root -> examples/sample-app).
SAMPLE_APP_SOURCE = Path(__file__).resolve().parents[3] / "examples" / "sample-app"
#: Optional: a private sample repository (any git checkout); the tests that use it skip when unset.
SAMPLE_REPO = os.environ.get("SAMPLE_REPO", "")
SAMPLE_REPO_PATH = Path(SAMPLE_REPO) if SAMPLE_REPO else None
#: Never copied into a sample-app checkout (build output and local state).
_SAMPLE_IGNORE = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "node_modules", "*.db", ".git")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


# --------------------------------------------------------------------------- database


@pytest.fixture(scope="session")
def pg_schema() -> str:
    """Create ``test_<hex>``, apply sql/*.sql to it, set SDLC_SCHEMA; drop it at session end."""
    name = f"test_{secrets.token_hex(4)}"
    try:
        db.init(name)
    except Exception as exc:  # psycopg.OperationalError and friends
        pytest.skip(f"Postgres unavailable at {db.database_url()} ({type(exc).__name__}: {exc}); run 'make db-up'")
    old = os.environ.get("SDLC_SCHEMA")
    os.environ["SDLC_SCHEMA"] = name
    try:
        yield name
    finally:
        if old is None:
            os.environ.pop("SDLC_SCHEMA", None)
        else:
            os.environ["SDLC_SCHEMA"] = old
        db.drop_schema(name)


@pytest.fixture(scope="session")
def conn_factory(pg_schema: str) -> Callable:
    """Zero-arg callable returning a new connection bound to the test schema. Caller closes it."""
    return db.connection_factory(schema=pg_schema)


@pytest.fixture
def conn(conn_factory):
    """A connection on the session schema; state persists across tests (no cleanup)."""
    c = conn_factory()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def clean_conn(conn_factory, pg_schema):
    """A connection on the session schema after truncating every table."""
    c = conn_factory()
    db.truncate_all(c, pg_schema)
    try:
        yield c
    finally:
        c.close()


# --------------------------------------------------------------------------- sample app and SAMPLE_REPO


@pytest.fixture(scope="session")
def sample_app(tmp_path_factory) -> Path:
    """examples/sample-app as a one-commit git checkout (the extractors and snapshots read commits).
    Tests only read it; copy it with ``sample_app_copy`` to mutate."""
    if not (SAMPLE_APP_SOURCE / "app" / "main.py").is_file():
        pytest.skip(f"the sample app is not at {SAMPLE_APP_SOURCE} (run the tests from the repository checkout)")
    dest = tmp_path_factory.mktemp("sample") / "sample-app"
    shutil.copytree(SAMPLE_APP_SOURCE, dest, ignore=_SAMPLE_IGNORE)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "sample-app")
    return dest


@pytest.fixture
def sample_app_copy(sample_app: Path, tmp_path: Path) -> Path:
    """A private copy of the sample app checkout (with .git) that a test may mutate."""
    dest = tmp_path / "sample-app"
    shutil.copytree(sample_app, dest, symlinks=True)
    return dest


@pytest.fixture(scope="session")
def sample_repo() -> Path:
    """The private sample repository named by SAMPLE_REPO (only read)."""
    if SAMPLE_REPO_PATH is None:
        pytest.skip("SAMPLE_REPO is not set; point it at a git checkout to run the tests on a second repository")
    if not (SAMPLE_REPO_PATH / ".git").exists():
        pytest.skip(f"SAMPLE_REPO={SAMPLE_REPO_PATH} is not a git checkout")
    return SAMPLE_REPO_PATH


# --------------------------------------------------------------------------- synthetic repo

SYNTHETIC_FILES: dict[str, str] = {
    "app/__init__.py": "",
    "app/main.py": '''\
from fastapi import FastAPI

from app.config import DATABASE_URL
from app.routes import a

app = FastAPI(title="synthetic")
app.include_router(a.router)


@app.get("/health")
def health():
    return {"ok": True, "db": bool(DATABASE_URL)}
''',
    "app/config.py": '''\
import os

DATABASE_URL = os.environ["DATABASE_URL"]
DEBUG = os.getenv("DEBUG", "0") == "1"
''',
    "app/auth.py": '''\
from fastapi import Header, HTTPException


def require_user(authorization: str = Header(default="")):
    if not authorization:
        raise HTTPException(status_code=401)
    return authorization
''',
    "app/models.py": '''\
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()

    def label(self) -> str:
        return f"item:{self.name}"
''',
    "app/routes/__init__.py": "",
    "app/routes/a.py": '''\
from fastapi import APIRouter, Depends

from app.auth import require_user
from app.models import Item

router = APIRouter(prefix="/items")


def make_item(name: str) -> Item:
    return Item(name=name)


@router.get("")
def list_items():
    return [make_item("x").label()]


@router.post("", dependencies=[Depends(require_user)])
def create_item(name: str):
    return make_item(name).label()
''',
    "app/routes/b.py": '''\
import os

from fastapi import APIRouter

router = APIRouter(prefix="/b")


@router.get("/orphan")
def orphan():
    return {"token": os.environ.get("B_TOKEN")}
''',
    "tests/__init__.py": "",
    "tests/test_a.py": '''\
from app.routes.a import list_items


def test_list_items():
    assert list_items() == ["item:x"]
''',
    "frontend/src/api.ts": '''\
export async function loadMissing() {
  const res = await fetch('/api/missing');
  return res.json();
}

export async function loadItems() {
  const res = await fetch('/items');
  return res.json();
}
''',
    "requirements.txt": "fastapi==0.115.0\nSQLAlchemy>=2.0\npytest\n",
    "CODEOWNERS": "* @team-core\n/app/routes/ @alice\n/frontend/ @bob\n",
}

#: What the synthetic repo plants, for assertions. Route qualnames are "METHOD /full/path".
SYNTHETIC_FACTS: dict = {
    "modules": ["app", "app.auth", "app.config", "app.main", "app.models", "app.routes", "app.routes.a",
                "app.routes.b", "tests", "tests.test_a"],
    "routes": {
        "GET /health": {"path": "app/main.py", "handler": "app.main.health", "mounted": True, "auth": False},
        "GET /items": {"path": "app/routes/a.py", "handler": "app.routes.a.list_items", "mounted": True, "auth": False},
        "POST /items": {"path": "app/routes/a.py", "handler": "app.routes.a.create_item", "mounted": True, "auth": True},
        "GET /b/orphan": {"path": "app/routes/b.py", "handler": "app.routes.b.orphan", "mounted": False, "auth": False},
    },
    "unmounted_router_modules": ["app.routes.b"],
    "includes_router": [("app.main", "app.routes.a")],
    "env_vars": {"DATABASE_URL": ["app/config.py"], "DEBUG": ["app/config.py"], "B_TOKEN": ["app/routes/b.py"]},
    "env_reads_outside_config": ["B_TOKEN"],
    "models": {"app.models.Item": {"table": "items"}},
    "frontend_calls": {"GET /api/missing": {"matched": False}, "GET /items": {"matched": True, "route": "GET /items"}},
    "tests": {"tests/test_a.py::test_list_items": {"imports": ["app.routes.a.list_items"]}},
    "packages": ["fastapi", "pytest", "sqlalchemy"],
    "owners": {"app/routes/a.py": ["@alice"], "frontend/src/api.ts": ["@bob"], "app/main.py": ["@team-core"]},
    # changing app/models.py must reach tests/test_a.py via app.routes.a (imports) within depth 4
    "impact": {"app/models.py": {"tests": ["tests/test_a.py::test_list_items"], "routes_include": ["GET /items", "POST /items"]}},
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def write_files(repo: Path, files: dict[str, str | None]) -> None:
    """Write (or delete, for None) files under ``repo``."""
    for rel, text in files.items():
        target = repo / rel
        if text is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def commit_files(repo: Path, files: dict[str, str | None], message: str) -> str:
    """Write/delete files, ``git add -A``, commit with fixed identity and dates; return the SHA."""
    write_files(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def build_synthetic_repo(dest: Path) -> Path:
    """Create the synthetic repo at ``dest`` (a new git repo, branch main, one commit)."""
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    commit_files(dest, SYNTHETIC_FILES, "synthetic: initial structure")
    return dest


@pytest.fixture(scope="session")
def synthetic_repo(tmp_path_factory) -> Path:
    """Session-wide synthetic repo; treat as read-only (use make_synthetic_repo to mutate)."""
    return build_synthetic_repo(tmp_path_factory.mktemp("synthetic") / "repo")


@pytest.fixture(scope="session")
def synthetic_facts() -> dict:
    return SYNTHETIC_FACTS


@pytest.fixture
def make_synthetic_repo(tmp_path: Path) -> Callable[..., Path]:
    """Factory: ``make_synthetic_repo(dest=None) -> Path`` builds a fresh mutable copy."""
    counter = {"n": 0}

    def factory(dest: Path | None = None) -> Path:
        counter["n"] += 1
        return build_synthetic_repo(dest or tmp_path / f"synthetic{counter['n']}")

    return factory


@pytest.fixture
def git_commit() -> Callable[[Path, dict, str], str]:
    """``git_commit(repo, {path: text | None}, message) -> sha``."""
    return commit_files
