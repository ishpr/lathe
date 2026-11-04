"""Shared pytest fixtures.

Every test that uses a database fixture gets its own schema ``t_<random>``, created from sql/*.sql
before the test and dropped after it, so tests (and parallel pytest runs) never share rows and nothing
in the suite touches ``public``. ``SDLC_SCHEMA`` is set to that schema for the test, so code under test that
calls ``db.connect()`` with no schema (CLI handlers, the MCP server, hooks run in-process) lands in it too.
A session fixture (``public_sentinel``) puts a row in ``public`` first and fails the session if it is gone
or changed afterwards. When Postgres is unreachable, every test that uses a database fixture is SKIPPED with
the reason; pure tests still run.

Fixtures:
    public_sentinel  (session, autouse) a row in public that the suite must leave untouched
    pg_schema        (function) the test's schema name (``t_<random>``), dropped after the test
    conn_factory     (function) zero-arg callable -> new connection bound to pg_schema
    conn             (function) one connection on that schema
    clean_conn       (function) the same connection, on a schema that is empty of rows (it is brand new)
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

import psycopg
import pytest
from psycopg import sql

from sdlc_graph import db

#: The repository's sample app (plugins/factory/tests/graph -> the repository root -> examples/sample-app).
SAMPLE_APP_SOURCE = Path(__file__).resolve().parents[4] / "examples" / "sample-app"
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


TEST_DATABASE_ENV = "SDLC_TEST_DATABASE_URL"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def safe_test_database_url() -> str:
    """The URL the suite may run DDL/DML against, or a RuntimeError.

    ``SDLC_TEST_DATABASE_URL`` is the explicit opt-in (any host) and is exported as ``DATABASE_URL`` so code under
    test and subprocesses use it. Without it the resolved ``DATABASE_URL`` must point at localhost: the suite
    creates and drops schemas and a table in ``public``, which must never happen on a shared or remote database.
    """
    from urllib.parse import urlsplit

    explicit = os.environ.get(TEST_DATABASE_ENV, "").strip()
    if explicit:
        os.environ["DATABASE_URL"] = explicit
        return explicit
    url = db.database_url()
    host = (urlsplit(url).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(f"refusing to run the test suite against host {host!r}: it creates and drops schemas. "
                           f"Point DATABASE_URL at localhost, or set {TEST_DATABASE_ENV} to a database that is "
                           "yours to wreck")
    return url


def require_test_schema(name: str) -> str:
    """The name when it is a test's own ``t_<random>`` schema; otherwise RuntimeError (never ``public``)."""
    if name == "public" or not name.startswith("t_"):
        raise RuntimeError(f"refusing schema {name!r}: only a test's own t_<random> schema may be reset or truncated")
    return name


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The checked database URL; subprocess environments take this, never a hard-coded default."""
    return safe_test_database_url()


def _unreachable(exc: Exception) -> None:
    pytest.skip(f"Postgres unavailable at {db.redact_secrets(db.database_url())} ({type(exc).__name__}: {exc}); run 'make db-up'")


@pytest.fixture(scope="session", autouse=True)
def public_sentinel():
    """A row in the ``public`` schema that the suite must leave alone.

    Created before the first test in its own table (``public._suite_sentinel_<hex>``), checked after the last one
    and deleted. A test that resets, truncates or drops in ``public`` (the shared default schema other agents and
    the console may be using) takes the row with it, and this fixture fails the session at teardown. With no
    database reachable it does nothing.
    """
    safe_test_database_url()  # raises before any DDL when the target is not local or explicitly opted in
    table = f"_suite_sentinel_{secrets.token_hex(4)}"
    token = secrets.token_hex(8)
    try:
        with db.connect(schema="public") as c:
            c.execute(sql.SQL("CREATE TABLE public.{} (token text PRIMARY KEY)").format(sql.Identifier(table)))
            c.execute(sql.SQL("INSERT INTO public.{} VALUES (%s)").format(sql.Identifier(table)), (token,))
    except Exception:  # no database: the database tests skip on their own
        yield None
        return
    handle = {"table": table, "token": token}
    try:
        yield handle
        with db.connect(schema="public") as c:
            try:
                rows = c.execute(sql.SQL("SELECT token FROM public.{}").format(sql.Identifier(table))).fetchall()
            except psycopg.errors.UndefinedTable:
                rows = []
        assert [r["token"] for r in rows] == [token], (
            f"the suite touched the public schema: the sentinel row in public.{table} is {'gone' if not rows else 'changed'}")
    finally:
        with db.connect(schema="public") as c:
            c.execute(sql.SQL("DROP TABLE IF EXISTS public.{}").format(sql.Identifier(table)))


@pytest.fixture
def pg_schema() -> str:
    """The test's own schema ``t_<random>``: created from sql/*.sql before the test, dropped after it.

    ``SDLC_SCHEMA`` points at it for the test, so code that calls ``db.connect()`` with no schema (CLI handlers,
    the MCP server, hooks and the orchestrator run in-process) lands in it too. Never ``public``.
    """
    safe_test_database_url()
    name = f"t_{secrets.token_hex(5)}"
    try:
        db.init(name)
    except Exception as exc:  # psycopg.OperationalError and friends
        _unreachable(exc)
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


@pytest.fixture
def conn_factory(pg_schema: str) -> Callable:
    """Zero-arg callable returning a new connection bound to the test's schema. Caller closes it."""
    return db.connection_factory(schema=pg_schema)


@pytest.fixture
def conn(conn_factory):
    """A connection on the test's schema."""
    c = conn_factory()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def clean_conn(conn_factory, pg_schema):
    """A connection on the test's own, empty schema. Nothing is reset or truncated: the schema is new, and a
    reset of ``public`` or of any schema that is not this test's is refused."""
    require_test_schema(pg_schema)
    c = conn_factory()
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


@pytest.fixture
def emulator_deadline():
    """A per-test limit for tests that talk to the Spanner emulator (a hung gRPC call must fail in
    ``EMULATOR_TEST_SECONDS``, not stall the suite for the 300 s a subprocess allows)."""
    import signal

    def _expired(_signum, _frame):
        raise TimeoutError(f"emulator test exceeded {EMULATOR_TEST_SECONDS} s")

    old = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(EMULATOR_TEST_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


EMULATOR_TEST_SECONDS = 90
