"""Database access shared by every sdlc-graph module.

One rule: every module gets its connection from :func:`connect`, never from psycopg directly,
so schema scoping works everywhere. Tables are created and queried *unqualified*; the schema is
chosen by ``search_path``, which :func:`connect` sets to ``<schema>, public`` where ``<schema>``
is the ``schema`` argument, else the ``SDLC_SCHEMA`` environment variable, else ``public``.
Tests run in their own ``test_<random>`` schema this way (see ``tests/conftest.py``); the
``vector`` and ``pg_trgm`` extensions always live in ``public`` and are shared.

Connection conventions (callers rely on them):

* ``autocommit=True`` by default. Wrap multi-statement writes in ``with conn.transaction():``.
* ``row_factory=psycopg.rows.dict_row``: every fetch returns ``dict`` rows keyed by column name.
* jsonb columns come back as Python ``dict``/``list``; pass ``psycopg.types.json.Jsonb(obj)``
  when writing them. ``text[]`` columns map to/from Python ``list[str]``.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import Callable, Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

#: Default URL. Port 5435, not 5433: 5433 is taken on the build machine (see docker-compose.yml).
DEFAULT_DATABASE_URL = "postgresql://sdlc:sdlc@localhost:5435/sdlc"

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


_URI_USERINFO = re.compile(r"""([A-Za-z][A-Za-z0-9+.-]*://)[^\s/"']*@""")
_KV_PASSWORD = re.compile(r"(?i)(\bpassword\s*=\s*)('[^']*'|\S+)")


def redact_secrets(text: str) -> str:
    """Hide credentials in text about to be printed or stored: the user:password part of any URI and
    ``password=...`` pairs. libpq echoes a malformed connection URI, password included, in its error."""
    return _KV_PASSWORD.sub(r"\1***", _URI_USERINFO.sub(r"\1***@", str(text)))


def database_url() -> str:
    """Return ``DATABASE_URL`` from the environment, or :data:`DEFAULT_DATABASE_URL`."""
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def schema_name(schema: str | None = None) -> str:
    """Resolve the schema to use: the argument, else ``SDLC_SCHEMA``, else ``public``.

    Raises:
        ValueError: the name is not a plain lowercase SQL identifier (``[a-z_][a-z0-9_]*``).
    """
    name = schema or os.environ.get("SDLC_SCHEMA") or "public"
    if not _SCHEMA_RE.match(name):
        raise ValueError(f"invalid schema name {name!r}")
    return name


def sql_dir() -> Path:
    """Directory holding the ordered ``NNN_*.sql`` DDL files.

    Looks for ``<repo>/sql`` (editable install, the normal case) and falls back to the copy a
    wheel build places at ``sdlc_graph/_sql``.

    Raises:
        FileNotFoundError: neither location exists.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "sql", here / "_sql"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("sql/ directory not found next to the sdlc_graph package")


def sql_files() -> list[Path]:
    """The DDL files in apply order (lexicographic by file name: 001, 002, 003)."""
    return sorted(sql_dir().glob("[0-9][0-9][0-9]_*.sql"))


def connect(url: str | None = None, *, schema: str | None = None, autocommit: bool = True) -> psycopg.Connection:
    """Open a connection scoped to one schema.

    Args:
        url: libpq URL; default :func:`database_url`.
        schema: schema to scope to; default :func:`schema_name` (``SDLC_SCHEMA`` or ``public``).
            The schema is NOT created here; see :func:`init`.
        autocommit: psycopg autocommit mode (default True).

    Returns:
        A ``psycopg.Connection`` with ``dict_row`` rows and ``search_path = <schema>, public``.

    Raises:
        psycopg.OperationalError: the server is unreachable or rejects the login.
        ValueError: invalid schema name.
    """
    name = schema_name(schema)
    conn = psycopg.connect(url or database_url(), autocommit=autocommit, row_factory=dict_row)
    if name == "public":
        conn.execute("SET search_path TO public")
    else:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(name)))
    if not autocommit:
        conn.commit()
    return conn


def connection_factory(url: str | None = None, *, schema: str | None = None) -> Callable[[], psycopg.Connection]:
    """Return a zero-argument callable that opens a new :func:`connect` connection each call.

    The MCP server and the CLI take a factory rather than a connection so each request gets its
    own connection (and tests can inject one bound to their schema).
    """
    resolved = schema_name(schema)

    def factory() -> psycopg.Connection:
        return connect(url, schema=resolved)

    return factory


def _ensure_extensions(conn: psycopg.Connection) -> None:
    """Create the shared extensions in ``public``, tolerating a concurrent creator."""
    for ext in ("vector", "pg_trgm"):
        try:
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {} WITH SCHEMA public").format(sql.Identifier(ext)))
        except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject):
            pass  # another session created it between the check and the insert


def run_sql_files(conn: psycopg.Connection, paths: list[Path] | None = None) -> list[str]:
    """Apply DDL files in order on ``conn`` (whose search_path picks the target schema).

    Each file runs in its own transaction. Files must be idempotent.

    Args:
        conn: a connection from :func:`connect` (autocommit mode).
        paths: files to apply; default :func:`sql_files`.

    Returns:
        The file names applied, in order.

    Raises:
        psycopg.Error: a statement failed; that file's transaction is rolled back.
    """
    _ensure_extensions(conn)
    applied = []
    for path in paths or sql_files():
        with conn.transaction():
            conn.execute(path.read_text())
        applied.append(path.name)
    return applied


def init(schema: str | None = None, url: str | None = None) -> dict:
    """Create the schema if needed and apply every DDL file to it.

    Returns:
        ``{"schema": str, "applied": [file names], "tables": [table names in the schema]}``.

    Raises:
        psycopg.Error: connection or DDL failure.
    """
    name = schema_name(schema)
    with connect(url, schema="public") as admin:
        admin.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(name)))
    with connect(url, schema=name) as conn:
        applied = run_sql_files(conn)
        tables = list_tables(conn, name)
    return {"schema": name, "applied": applied, "tables": tables}


def list_tables(conn: psycopg.Connection, schema: str | None = None) -> list[str]:
    """Base tables in ``schema`` (default: the resolved schema), sorted."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY 1",
        (schema_name(schema),),
    ).fetchall()
    return [r["table_name"] for r in rows]


def drop_schema(schema: str, url: str | None = None) -> None:
    """``DROP SCHEMA ... CASCADE``. Refuses ``public`` (use :func:`reset`).

    Raises:
        ValueError: schema is ``public`` or invalid.
    """
    name = schema_name(schema)
    if name == "public":
        raise ValueError("refusing to drop schema public; use reset()")
    with connect(url, schema="public") as admin:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def reset(schema: str | None = None, url: str | None = None) -> dict:
    """Drop everything sdlc-graph created in the schema, then :func:`init` it again.

    For a non-public schema the schema itself is dropped. For ``public`` only the tables in it
    are dropped (the shared extensions stay).

    Returns:
        The :func:`init` result plus ``"dropped": [table names]``.
    """
    name = schema_name(schema)
    if name == "public":
        with connect(url, schema="public") as conn:
            dropped = list_tables(conn, "public")
            if dropped:
                conn.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.SQL(", ").join(sql.Identifier("public", t) for t in dropped)
                    )
                )
    else:
        with connect(url, schema="public") as conn:
            dropped = list_tables(conn, name)
        drop_schema(name, url)
    result = init(name, url)
    result["dropped"] = dropped
    return result


def truncate_all(conn: psycopg.Connection, schema: str | None = None) -> None:
    """Empty every table in the schema (``TRUNCATE ... RESTART IDENTITY CASCADE``). Test helper."""
    name = schema_name(schema)
    tables = list_tables(conn, name)
    if tables:
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(name, t) for t in tables)
            )
        )


@contextlib.contextmanager
def use_schema(schema: str) -> Iterator[str]:
    """Temporarily set ``SDLC_SCHEMA`` so code that calls :func:`connect` with no schema
    (the CLI, the MCP server, hooks run in-process) lands in ``schema``. Restores the old value.
    """
    name = schema_name(schema)
    old = os.environ.get("SDLC_SCHEMA")
    os.environ["SDLC_SCHEMA"] = name
    try:
        yield name
    finally:
        if old is None:
            os.environ.pop("SDLC_SCHEMA", None)
        else:
            os.environ["SDLC_SCHEMA"] = old
