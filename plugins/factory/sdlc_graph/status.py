"""Is the graph configured? One read-only answer for the MCP tool ``graph_status`` and ``sdlc-graph status``.

The plugin ships the graph's MCP server to every session, including a team's that only wants the
hooks and has no database. So the server never fails to start: each tool opens its connection per
call, and when there is no reachable, initialised database every tool answers :func:`not_configured`
instead of an error trace, and ``graph_status`` says why.

``graph_status`` returns::

    {"configured": bool,          # enabled, reachable and the ledger's schema is there
     "enabled": bool,             # .claude/sdlc.json graph.enabled (default true); false: nothing is opened
     "provider": str,             # .claude/sdlc.json graph.provider, else guessed from the URL host
     "url_env": str, "url_source": str,  # the variable named by graph.url_env, and where the URL came from
     "reachable": bool,           # a connection opened
     "schema_version": str|None,  # the newest DDL file whose objects exist ("013"), None without tables
     "latest_version": str|None,  # the newest DDL file this plugin ships
     "pending_migrations": int,   # DDL files newer than schema_version; `sdlc-graph db init` applies them
     "snapshots": int|None, "nodes": int|None,
     "url": str,                  # the URL with its credentials redacted
     "hint": str|None, "error": str|None}

It never writes, and the connection string's password is never part of the answer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

NOT_CONFIGURED = "graph not configured"
GRAPH_OFF = "graph off"
HINT_OFF = "graph off: every graph step is skipped (/factory:graph on turns it back on)"
HINT_NO_SCHEMA = "the database is reachable but has no ledger schema: run `sdlc-graph db init` (/factory:graph provision does it)"


HINT_BEHIND = ("the database schema is behind by {n} migration{s}: run `sdlc-graph db init` (idempotent; "
               "/factory:graph migrate and /factory:init apply it)")


def hint_unreachable(provider: str = "local", url_env: str = "DATABASE_URL") -> str:
    """The one line a skill prints when the graph is on but its database cannot be reached."""
    return (f"graph on but not reachable: run /factory:graph provision {provider} or set {url_env} "
            "(the local default is postgresql://sdlc:sdlc@localhost:5435/sdlc, which /factory:graph provision local "
            "starts with Docker); until then the graph steps are skipped")


HINT_UNREACHABLE = hint_unreachable()

#: Newest first: (DDL file prefix, marker). The first marker present is the schema version, so each migration needs
#: one object only it creates. A marker is ``("column", table, column)``, ``("table", table)`` (a view counts) or
#: ``("constraint", name, fragment)`` (the constraint's definition contains the fragment).
#: tests/graph/test_registry_conformance.py applies the files one by one and checks every prefix is told apart.
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("013", ("constraint", "node_kind_check", "'finding'")),
    ("012", ("column", "run", "actor")),
    ("011", ("constraint", "sym_kind_check", "'submodel'")),
    ("010", ("constraint", "node_kind_check", "'repo'")),
    ("009", ("column", "node", "contract_md")),
    ("008", ("constraint", "sym_kind_check", "'route_fe'")),
    ("007", ("table", "assumption")),
    ("006", ("column", "run", "executor")),
    ("005", ("table", "chunk")),
    ("004", ("table", "orchestrator_lock")),
    ("003", ("table", "prediction")),
    ("002", ("table", "node")),
    ("001", ("table", "snapshot")),
)


def _project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def not_configured(reason: str | None = None, hint: str | None = None) -> dict[str, Any]:
    """What every graph tool answers when the graph cannot be used. ``reason`` is redacted by the caller."""
    out: dict[str, Any] = {"ok": False, "error": NOT_CONFIGURED, "hint": hint or _unreachable_hint()}
    if reason:
        out["reason"] = reason
    return out


def graph_off() -> dict[str, Any]:
    """What every graph tool answers when ``graph.enabled`` is false: nothing is opened."""
    return {"ok": False, "error": GRAPH_OFF, "hint": HINT_OFF}


def _unreachable_hint(project_dir: Path | None = None) -> str:
    from sdlc_graph import graphcfg

    graph, _ = graphcfg.load(project_dir)
    return hint_unreachable(graph["provider"], graph["url_env"])


def enabled(project_dir: Path | None = None) -> bool:
    """``graph.enabled`` of the project (true when there is no file or no block)."""
    from sdlc_graph import graphcfg

    return bool(graphcfg.load(project_dir)[0]["enabled"])


def configured_provider(project_dir: Path | None = None) -> str | None:
    """``graph.provider`` from ``.claude/sdlc.json`` when the project names one; else None."""
    path = (project_dir or _project_dir()) / ".claude" / "sdlc.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    graph = data.get("graph") if isinstance(data, dict) else None
    provider = graph.get("provider") if isinstance(graph, dict) else None
    return provider if isinstance(provider, str) and provider else None


def provider_from_url(url: str) -> str:
    """``local``, ``neon``, ``aws`` or ``postgres`` (any other host), from the URL's host name."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return "postgres"
    if host in ("", "localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
        return "local"
    if host.endswith(".neon.tech"):
        return "neon"
    if host.endswith(".amazonaws.com"):
        return "aws"
    return "postgres"


def schema_version(conn: Any, schema: str) -> str | None:
    """The newest DDL file whose objects exist in ``schema``, or None when the ledger has no tables there."""
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s", (schema,)
    ).fetchall()
    tables = {r["table_name"] for r in rows}
    columns = {(r["table_name"], r["column_name"]) for r in rows}
    defs: dict[str, str] = {}
    for name in {m[1] for _v, m in _MARKERS if m[0] == "constraint"}:
        row = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = %s AND c.conname = %s", (schema, name)).fetchone()
        defs[name] = (row["d"] or "") if row else ""
    for version, marker in _MARKERS:
        if marker[0] == "column":
            present = (marker[1], marker[2]) in columns
        elif marker[0] == "table":
            present = marker[1] in tables
        else:
            present = marker[2] in defs[marker[1]]
        if present:
            return version
    return None


def latest_version() -> str:
    """The newest DDL file prefix this plugin ships ("013")."""
    from sdlc_graph import db

    return max(f.name[:3] for f in db.sql_files())


def pending_migrations(version: str | None) -> list[str]:
    """DDL file prefixes newer than ``version``, oldest first (empty when up to date or the ledger has no tables)."""
    from sdlc_graph import db

    if version is None:
        return []
    return sorted({f.name[:3] for f in db.sql_files() if f.name[:3] > version})


def graph_status(conn_factory: Callable[[], Any] | None = None, project_dir: Path | None = None) -> dict[str, Any]:
    """Read-only: is the graph on, reachable and initialised, which provider, how much is stored."""
    from sdlc_graph import db, graphcfg

    graph, problems = graphcfg.load(project_dir)
    url, source = graphcfg.resolve_url(project_dir)
    out: dict[str, Any] = {"configured": False, "enabled": graph["enabled"],
                           "provider": configured_provider(project_dir) or provider_from_url(url),
                           "url_env": graph["url_env"], "url_source": source,
                           "reachable": False, "schema_version": None, "latest_version": None,
                           "pending_migrations": 0, "snapshots": None, "nodes": None,
                           "url": db.redact_secrets(url), "hint": None, "error": None}
    if problems:
        out["config_problems"] = problems
    if not graph["enabled"]:
        out["hint"] = HINT_OFF
        return out
    try:
        conn = (conn_factory or db.connection_factory(url))()
    except Exception as exc:  # noqa: BLE001 - unreachable, bad URL, refused login: all "not configured"
        out.update(hint=hint_unreachable(out["provider"] if out["provider"] in graphcfg.PROVIDERS else graph["provider"],
                                         graph["url_env"]),
                   error=db.redact_secrets(f"{type(exc).__name__}: {exc}".strip())[:300])
        return out
    try:
        out["reachable"] = True
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"] or "public"
        out["schema_version"] = schema_version(conn, schema)
        if out["schema_version"] is None:
            out["hint"] = HINT_NO_SCHEMA
            return out
        out["latest_version"] = latest_version()
        pending = pending_migrations(out["schema_version"])
        out["pending_migrations"] = len(pending)
        if pending:
            out["hint"] = HINT_BEHIND.format(n=len(pending), s="" if len(pending) == 1 else "s")
        out["snapshots"] = conn.execute("SELECT count(*) AS n FROM snapshot").fetchone()["n"]
        out["nodes"] = conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"]
        out["configured"] = True
        return out
    except Exception as exc:  # noqa: BLE001 - a status call never raises
        out.update(hint=HINT_NO_SCHEMA, error=db.redact_secrets(f"{type(exc).__name__}: {exc}".strip())[:300])
        return out
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
