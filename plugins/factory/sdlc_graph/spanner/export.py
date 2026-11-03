"""Copy the Postgres ledger and one code-graph snapshot into Spanner (the local emulator) and run
the ``SdlcGraph`` GQL queries over them.

This is the integrator's ``spanner export``: the smoke test (``emulator_smoke.smoke``) proves the
DDL and one MATCH on three sample rows; this loads the real rows the demo produced (every ledger
table, and ``sym``/``sym_edge`` of the chosen snapshot, materialised first when it is a delta) and
keeps the database so it can be queried live (``sdlc-graph spanner query``).

Postgres stays the source of truth. Spanner is a read model rebuilt wholesale on every export
(drop and recreate), so nothing is ever written to it except by this loader.

Only one snapshot is exported: sym ids are unique across snapshots, but mixing snapshots in one
graph would make every MATCH need a ``snapshot_id`` filter.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sdlc_graph.spanner import ddl, emulator_smoke

#: Named GQL queries for the demo. ``{slug}`` and ``{symbol}`` are substituted as GQL string
#: literals by :func:`run_query` (quotes escaped); everything else is fixed text.
QUERIES: dict[str, str] = {
    # Ledger: the epic -> feature -> contract chain, any depth up to three.
    "chain": (
        "GRAPH SdlcGraph "
        "MATCH (e:Node {kind: 'epic'})-[:Rel {rel: 'decomposes_into'}]->{1,3}(n:Node) "
        "RETURN DISTINCT e.slug AS epic, n.kind AS kind, n.slug AS slug, n.state AS state "
        "ORDER BY kind, slug"
    ),
    # Code graph: tests that reach a symbol through tests/calls/imports edges, up to four hops. This is
    # STRUCTURAL REACHABILITY over exactly the rels the Postgres impact walk reverses (imports, calls,
    # tests), so the two answers differ only by what Postgres adds on top: tests defined in a reached
    # test module, conftest expansion, the module-level closure and mirror paths. Not a second oracle.
    "tests_reaching": (
        "GRAPH SdlcGraph "
        "MATCH (t:Symbol {kind: 'test'})-[e:SymRel WHERE e.rel IN ('imports', 'calls', 'tests')]->{1,4}"
        "(s:Symbol {qualname: {symbol}}) "
        "RETURN DISTINCT t.path AS test_file, t.name AS test "
        "ORDER BY test_file, test"
    ),
    # Both graphs in one statement: a contract's touched symbols (Node.touched_symbols holds
    # 'kind:qualname@path' keys) and the routes whose handler reaches them within three hops.
    "contract_routes": (
        "GRAPH SdlcGraph "
        "MATCH (c:Node {slug: {slug}}), (s:Symbol) "
        "WHERE CONCAT(s.kind, ':', s.qualname, '@', s.path) IN UNNEST(c.touched_symbols) "
        "MATCH (h:Symbol)-[:SymRel]->{1,3}(s), (h)-[:SymRel {rel: 'exposes_route'}]->(r:Symbol {kind: 'route'}) "
        "RETURN DISTINCT c.slug AS contract, s.qualname AS touched, r.qualname AS route "
        "ORDER BY route"
    ),
}


def _literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_query(name_or_gql: str, *, slug: str | None = None, symbol: str | None = None) -> str:
    """A named query from :data:`QUERIES` with its parameters substituted, or raw GQL unchanged."""
    gql = QUERIES.get(name_or_gql, name_or_gql)
    if "{slug}" in gql:
        if not slug:
            raise ValueError(f"query {name_or_gql!r} needs --slug")
        gql = gql.replace("{slug}", _literal(slug))
    if "{symbol}" in gql:
        if not symbol:
            raise ValueError(f"query {name_or_gql!r} needs --symbol (a qualname)")
        gql = gql.replace("{symbol}", _literal(symbol))
    return gql


# --------------------------------------------------------------------------- value conversion


def _jsonv(value: Any) -> Any:
    from google.cloud.spanner_v1.data_types import JsonObject

    if value is None:
        return None
    return JsonObject(value) if isinstance(value, (dict, list)) else JsonObject(json.loads(json.dumps(value)))


def _conv(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "json":
        return _jsonv(value)
    if kind == "str":
        return str(value)
    if kind == "num":
        return Decimal(str(value))
    return value


#: (Postgres SELECT, Spanner columns, per-column conversion) per exported table.
_TABLES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "node": ("SELECT id, kind, slug, title, repo, path, commit_sha, state, tier, external_id, created_by, created_at, "
             "updated_at, touched_symbols, capabilities, acceptance, attrs FROM node",
             ("id", "kind", "slug", "title", "repo", "path", "commit_sha", "state", "tier", "external_id", "created_by",
              "created_at", "updated_at", "touched_symbols", "capabilities", "acceptance", "attrs"),
             ("str",) + ("raw",) * 13 + ("json",) * 3),
    "edge": ("SELECT src, dst, rel FROM edge", ("src", "dst", "rel"), ("str", "str", "raw")),
    "evidence": ("SELECT id, node_id, kind, source, signed_by, payload, at FROM evidence",
                 ("id", "node_id", "kind", "source", "signed_by", "payload", "at"),
                 ("raw", "str", "raw", "raw", "raw", "json", "raw")),
    "transition": ("SELECT id, node_id, from_state, to_state, actor, evidence_ids, reason, at FROM transition",
                   ("id", "node_id", "from_state", "to_state", "actor", "evidence_ids", "reason", "at"),
                   ("raw", "str", "raw", "raw", "raw", "raw", "raw", "raw")),
    "decision": ("SELECT id, node_id, question, options, recommendation, proposed_by, decided_by, choice, due, "
                 "created_at, resolved_at FROM decision",
                 ("id", "node_id", "question", "options", "recommendation", "proposed_by", "decided_by", "choice",
                  "due", "created_at", "resolved_at"),
                 ("raw", "str", "raw", "json") + ("raw",) * 7),
    "run": ("SELECT id, node_id, stage, requested_model, resolved_model, model_tier, tokens_in, tokens_out, cost_usd, "
            "turns, started_at, finished_at, outcome, tool_use_id, agent_type, meta FROM run",
            ("id", "node_id", "stage", "requested_model", "resolved_model", "model_tier", "tokens_in", "tokens_out",
             "cost_usd", "turns", "started_at", "finished_at", "outcome", "tool_use_id", "agent_type", "meta"),
            ("raw", "str") + ("raw",) * 6 + ("num",) + ("raw",) * 6 + ("json",)),
}


assert all(len(cols) == len(kinds) for _sql, cols, kinds in _TABLES.values())


def pg_rows(conn: Any, snapshot_id: int | None = None) -> dict[str, tuple[tuple[str, ...], list[tuple]]]:
    """Read everything to export from Postgres: ``{table: (columns, rows)}`` in load order.

    ``snapshot_id`` defaults to the newest snapshot; a delta snapshot is materialised first. With
    no snapshot at all, only the ledger tables are returned.
    """
    from sdlc_graph.codegraph import snapshot as snap

    out: dict[str, tuple[tuple[str, ...], list[tuple]]] = {}
    if snapshot_id is None:
        row = conn.execute("SELECT id FROM snapshot ORDER BY id DESC LIMIT 1").fetchone()
        snapshot_id = row["id"] if row else None
    if snapshot_id is not None:
        snap.materialise(conn, snapshot_id)
        s = conn.execute("SELECT id, repo, commit_sha, parent_sha, indexed_at, extractor_version, storage, "
                         "base_snapshot_id FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()
        if s is None:
            raise LookupError(f"unknown snapshot {snapshot_id}")
        # base_snapshot_id is a plain column in Spanner (no FK), so the base row is not needed.
        out["snapshot"] = (tuple(s.keys()), [tuple(s.values())])
        syms = conn.execute("SELECT id, snapshot_id, kind, name, qualname, path, line_start, line_end, attrs "
                            "FROM sym WHERE snapshot_id = %s ORDER BY id", (snapshot_id,)).fetchall()
        out["sym"] = (("id", "snapshot_id", "kind", "name", "qualname", "path", "line_start", "line_end", "attrs"),
                      [(*tuple(r.values())[:8], _jsonv(r["attrs"])) for r in syms])
        edges = conn.execute("SELECT snapshot_id, src, dst, rel, attrs FROM sym_edge WHERE snapshot_id = %s",
                             (snapshot_id,)).fetchall()
        out["sym_edge"] = (("snapshot_id", "src", "dst", "rel", "attrs"),
                           [(*tuple(r.values())[:4], _jsonv(r["attrs"])) for r in edges])
    for table, (sql, cols, kinds) in _TABLES.items():
        rows = conn.execute(sql).fetchall()
        out[table] = (cols, [tuple(_conv(v, k) for v, k in zip(r.values(), kinds)) for r in rows])
    return out


def _batched_insert(database: Any, table: str, cols: tuple[str, ...], rows: list[tuple], size: int = 500) -> None:
    for i in range(0, len(rows), size):
        with database.batch() as b:
            b.insert(table, columns=cols, values=rows[i:i + size])


def _open_instance(host: str, project: str, instance: str) -> Any:
    os.environ["SPANNER_EMULATOR_HOST"] = host
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import spanner

    client = spanner.Client(project=project, credentials=AnonymousCredentials())
    inst = client.instance(instance, configuration_name=f"projects/{project}/instanceConfigs/emulator-config",
                           display_name=instance, node_count=1)
    last: Exception | None = None
    for _ in range(10):  # the first admin call can race the emulator start
        try:
            if not inst.exists():
                inst.create().result(60)
            return inst
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1)
    raise RuntimeError(f"emulator at {host} did not accept the instance: {last}")


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _execute(database: Any, gql: str, *, sort: bool = False) -> dict[str, Any]:
    with database.snapshot() as snap:
        res = snap.execute_sql(gql)
        rows = _plain([list(r) for r in res])
        cols = [f.name for f in res.fields] if res.fields else []
    if sort:
        # The emulator did not honour ORDER BY after RETURN DISTINCT in these queries (observed on
        # the local emulator image), so named queries are sorted here for a stable display.
        rows.sort(key=lambda r: [("" if v is None else str(v)) for v in r])
    return {"columns": cols, "rows": rows}


def export(conn: Any, *, snapshot_id: int | None = None, host: str | None = None,
           project: str = emulator_smoke.DEFAULT_PROJECT, instance: str = emulator_smoke.DEFAULT_INSTANCE,
           database: str = "sdlc_export", queries: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Drop and recreate ``database`` on the emulator, load Postgres into it and run ``queries``.

    ``queries`` maps a name from :data:`QUERIES` to its parameters (``{"slug": ..., "symbol": ...}``).
    The database is kept afterwards for :func:`query`.

    Returns:
        ``{"ok", "host", "instance", "database", "statements", "rows_loaded": {table: n},
        "snapshot_id", "queries": {name: {"gql", "columns", "rows"}}}``.

    Raises:
        RuntimeError: no emulator reachable. LookupError: unknown snapshot.
    """
    host = host or emulator_smoke.emulator_host()
    if not emulator_smoke._reachable(host):
        raise RuntimeError(f"no Spanner emulator at {host}: run 'make spanner-up' "
                           "(docker compose --profile spanner up -d spanner)")
    data = pg_rows(conn, snapshot_id)
    inst = _open_instance(host, project, instance)
    db = inst.database(database)
    if db.exists():
        db.drop()
    stmts = ddl.ddl_statements()
    db = inst.database(database, ddl_statements=stmts)
    db.create().result(120)
    loaded = _load_rows(db, data)
    out: dict[str, Any] = {"ok": True, "host": host, "instance": instance, "database": database,
                           "statements": len(stmts), "rows_loaded": loaded,
                           "snapshot_id": data["snapshot"][1][0][0] if "snapshot" in data else None, "queries": {}}
    for name, params in (queries or {}).items():
        gql = render_query(name, **params)
        out["queries"][name] = {"gql": gql, **_execute(db, gql, sort=True)}
    return out


def _load_rows(db: Any, data: dict[str, tuple[tuple[str, ...], list[tuple]]]) -> dict[str, int]:
    loaded: dict[str, int] = {}
    for table in ddl.TABLES:  # dependency order (FKs)
        if table in data:
            cols, rows = data[table]
            _batched_insert(db, table, cols, rows)
            loaded[table] = len(rows)
    return loaded


def mirror_into(db: Any, data: dict[str, tuple[tuple[str, ...], list[tuple]]]) -> dict[str, int]:
    """Replace every row of an existing database whose schema is :func:`ddl.ddl_statements`: delete the tables
    in reverse dependency order, then load ``data``. The schema is never changed (no DDL rights needed)."""
    for table in reversed(ddl.TABLES):
        db.execute_partitioned_dml(f"DELETE FROM {table} WHERE true")
    return _load_rows(db, data)


def mirror(conn: Any, *, project: str, instance: str, database: str, snapshot_id: int | None = None,
           queries: dict[str, dict[str, str]] | None = None, client: Any = None) -> dict[str, Any]:
    """Mirror the Postgres ledger and one snapshot into a REAL Spanner database (``/factory:graph provision
    spanner``): gcloud created the instance and the database from ``sdlc-graph spanner ddl``; this replaces the rows.
    Credentials are Application Default Credentials (``gcloud auth application-default login``). Postgres stays the
    ledger; Spanner is the graph mirror.

    Raises:
        LookupError: the database does not exist (create it with gcloud first) or the snapshot is unknown.
    """
    if client is None:
        from google.cloud import spanner

        client = spanner.Client(project=project)
    db = client.instance(instance).database(database)
    if not db.exists():
        raise LookupError(f"database {database} does not exist on projects/{project}/instances/{instance}: "
                          "create it with gcloud spanner databases create --ddl-file (from sdlc-graph spanner ddl)")
    data = pg_rows(conn, snapshot_id)
    loaded = mirror_into(db, data)
    out: dict[str, Any] = {"ok": True, "project": project, "instance": instance, "database": database,
                           "rows_loaded": loaded,
                           "snapshot_id": data["snapshot"][1][0][0] if data.get("snapshot") and data["snapshot"][1] else None,
                           "queries": {}}
    for name, params in (queries or {}).items():
        gql = render_query(name, **params)
        out["queries"][name] = {"gql": gql, **_execute(db, gql, sort=True)}
    return out


def query(gql_or_name: str, *, slug: str | None = None, symbol: str | None = None, host: str | None = None,
          project: str = emulator_smoke.DEFAULT_PROJECT, instance: str = emulator_smoke.DEFAULT_INSTANCE,
          database: str = "sdlc_export") -> dict[str, Any]:
    """Run one named or raw GQL/SQL statement against an exported database."""
    host = host or emulator_smoke.emulator_host()
    if not emulator_smoke._reachable(host):
        raise RuntimeError(f"no Spanner emulator at {host}: run 'make spanner-up'")
    inst = _open_instance(host, project, instance)
    db = inst.database(database)
    if not db.exists():
        raise LookupError(f"database {database} does not exist on {host}: run 'sdlc-graph spanner export' first")
    gql = render_query(gql_or_name, slug=slug, symbol=symbol)
    return {"gql": gql, **_execute(db, gql, sort=gql_or_name in QUERIES)}
