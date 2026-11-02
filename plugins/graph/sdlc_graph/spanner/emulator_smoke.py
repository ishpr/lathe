"""Smoke test against the Cloud Spanner emulator: create instance + database with
``ddl.ddl_statements()``, load a small ledger slice (3 nodes, 2 depends_on edges; with ``from_pg``
the ledger is copied from Postgres instead) and run the GQL MATCH over ``SdlcGraph``.

Uses ``google-cloud-spanner`` with ``SPANNER_EMULATOR_HOST`` set (no credentials needed). If the
host is not set and nothing answers on the default compose port, a throwaway emulator container is
started with docker and removed afterwards.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from typing import Any

from sdlc_graph.spanner import ddl

DEFAULT_HOST = "localhost:9110"
DEFAULT_PROJECT = "sdlc-graph"
DEFAULT_INSTANCE = "sdlc"
DEFAULT_DATABASE = "sdlc"
EMULATOR_IMAGE = "gcr.io/cloud-spanner-emulator/emulator:latest"
_CONTAINER = "sdlc-graph-spanner-smoke"
FROM_PG_GQL = ("GRAPH SdlcGraph MATCH (a:Node)-[r:Rel]->(b:Node) "
               "RETURN a.slug AS src, r.rel AS rel, b.slug AS dst ORDER BY src, rel, dst")


def emulator_host() -> str:
    """``SPANNER_EMULATOR_HOST`` or :data:`DEFAULT_HOST`."""
    return os.environ.get("SPANNER_EMULATOR_HOST") or DEFAULT_HOST


def _reachable(host: str) -> bool:
    h, _, p = host.rpartition(":")
    try:
        with socket.create_connection((h or "localhost", int(p)), timeout=1):
            return True
    except OSError:
        return False


def start_container(grpc_port: int = 9110, rest_port: int = 9120) -> str:
    """Start a throwaway emulator container (removed by :func:`stop_container`); returns the host."""
    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", _CONTAINER, "-p", f"127.0.0.1:{grpc_port}:9010",
         "-p", f"127.0.0.1:{rest_port}:9020", EMULATOR_IMAGE],
        check=True, capture_output=True, text=True,
    )
    host = f"localhost:{grpc_port}"
    for _ in range(30):
        if _reachable(host):
            return host
        time.sleep(0.5)
    raise RuntimeError("emulator container did not open its port")


def stop_container() -> None:
    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)


def _sample_rows() -> dict[str, list[tuple]]:
    ids = [str(uuid.uuid4()) for _ in range(3)]
    return {
        "node": [
            (ids[0], "contract", "c-auth", "Auth contract", "demo", "implementing", "medium"),
            (ids[1], "contract", "c-session", "Session contract", "demo", "ready", "low"),
            (ids[2], "contract", "c-audit", "Audit contract", "demo", "drafted", "low"),
        ],
        "edge": [(ids[0], ids[1], "depends_on"), (ids[0], ids[2], "depends_on")],
    }


def _pg_rows() -> dict[str, list[tuple]]:
    from sdlc_graph import db

    conn = db.connect()
    nodes = conn.execute("SELECT id::text AS id, kind, slug, title, repo, state, tier FROM node").fetchall()
    edges = conn.execute("SELECT src::text AS src, dst::text AS dst, rel FROM edge").fetchall()
    return {
        "node": [(n["id"], n["kind"], n["slug"], n["title"], n["repo"], n["state"], n["tier"]) for n in nodes],
        "edge": [(e["src"], e["dst"], e["rel"]) for e in edges],
    }


def _load(database: Any, rows: dict[str, list[tuple]]) -> dict[str, int]:
    from google.cloud import spanner

    ts = spanner.COMMIT_TIMESTAMP
    with database.batch() as b:
        b.insert(
            "node",
            columns=("id", "kind", "slug", "title", "repo", "state", "tier", "created_by", "created_at", "updated_at"),
            values=[(*r, "@smoke", ts, ts) for r in rows["node"]],
        )
        b.insert("edge", columns=("src", "dst", "rel"), values=rows["edge"])
    return {k: len(v) for k, v in rows.items()}


def smoke(*, host: str | None = None, project: str = DEFAULT_PROJECT, instance: str = DEFAULT_INSTANCE,
          database: str = DEFAULT_DATABASE, from_pg: bool = False, recreate: bool = True) -> dict[str, Any]:
    """Run the smoke test.

    Returns:
        ``{"ok": bool, "host", "project", "instance", "database", "statements": int,
        "rows_loaded": {table: int}, "gql": {"query": str, "rows": [...]}, "error": str|None}``.

    Errors:
        Failures return ``ok: False`` with ``error`` set (the CLI exits 1); they do not raise.
    """
    host = host or emulator_host()
    # The sample rows are built for SAMPLE_GQL (two depends_on edges); a real ledger need not have
    # any depends_on edge, so --from-pg matches every ledger edge instead.
    gql = FROM_PG_GQL if from_pg else ddl.SAMPLE_GQL
    out: dict[str, Any] = {"ok": False, "host": host, "project": project, "instance": instance,
                           "database": database, "statements": 0, "rows_loaded": {},
                           "gql": {"query": gql, "rows": []}, "error": None}
    started = False
    try:
        if not _reachable(host):
            if host != DEFAULT_HOST and not os.environ.get("SPANNER_EMULATOR_HOST"):
                raise RuntimeError(f"no emulator at {host}")
            host = start_container()
            started = True
            out["host"] = host
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
                last = None
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1)
        if last:
            raise last
        stmts = ddl.ddl_statements()
        db = inst.database(database)
        if db.exists():
            if not recreate:
                raise RuntimeError(f"database {database} already exists (recreate=False)")
            db.drop()
        db = inst.database(database, ddl_statements=stmts)
        db.create().result(120)
        out["statements"] = len(stmts)
        out["rows_loaded"] = _load(db, _pg_rows() if from_pg else _sample_rows())
        with db.snapshot() as snap:
            res = snap.execute_sql(gql)
            out["gql"]["rows"] = [list(r) for r in res]
        out["ok"] = len(out["gql"]["rows"]) > 0
        if not out["ok"]:
            out["error"] = "GQL returned no rows"
        try:
            db.drop()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if started:
            stop_container()
    return out
