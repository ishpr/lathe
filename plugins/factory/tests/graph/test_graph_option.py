"""The graph option against a real Postgres: status reachable, unreachable and disabled (the library, the CLI and
``scripts/graph.py``), the MCP tools when the graph is off, the orchestrator's refusal, the ``graph_provisioned``
evidence on the repo node, local provisioning for real (Docker, a scratch compose project on a free port, so the
shared ``sdlc-graph-db`` container is never touched), the CI path (a reachable database: ``db init`` only), and
the Spanner mirror loader on the emulator.

The neon, aws and spanner procedures against fake CLIs are in tests/sdlc/test_graph_option.py.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from sdlc_graph import cli, db, provisioned, status

PLUGIN = Path(__file__).resolve().parents[2]
GRAPH_PY = PLUGIN / "scripts" / "graph.py"
CLOSED = "postgresql://sdlc:sdlc@127.0.0.1:9/sdlc"


def _project(tmp_path: Path, graph: dict | None = None, local_url: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    cfg: dict = {"version": 1, "stack": "python"}
    if graph is not None:
        cfg["graph"] = graph
    (root / ".claude" / "sdlc.json").write_text(json.dumps(cfg, indent=2))
    if local_url:
        (root / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"DATABASE_URL": local_url}}))
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    return root


def _env(root: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(CLAUDE_PLUGIN_ROOT=str(PLUGIN), PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL=os.devnull)
    env.update(extra)
    return env


def _script(root: Path, *args: str, env: dict | None = None, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-I", str(GRAPH_PY), "--root", str(root), *args], capture_output=True, text=True,
                       cwd=root, env=env or _env(root), timeout=timeout)
    return p.returncode, p.stdout + p.stderr


# --------------------------------------------------------------------------- status


def test_status_reachable(conn_factory, tmp_path):
    root = _project(tmp_path, {"enabled": True, "provider": "local"})
    out = status.graph_status(conn_factory, project_dir=root)
    assert out["configured"] is True and out["enabled"] is True and out["reachable"] is True
    assert out["schema_version"] == "011" and out["provider"] == "local" and out["url_env"] == "DATABASE_URL"
    rc, text = _script(root, "status")
    assert rc == 0, text
    assert "graph: on, provider local" in text and "reachable: yes, schema 011" in text
    assert "sdlc:sdlc@" not in text  # the URL is redacted even when it is the public local default


def test_status_unreachable_from_the_local_env_file(tmp_path, monkeypatch):
    root = _project(tmp_path, {"enabled": True, "provider": "neon"}, local_url=CLOSED)
    monkeypatch.setenv("DATABASE_URL", db.DEFAULT_DATABASE_URL)  # what .mcp.json passes: counts as unset
    out = status.graph_status(project_dir=root)
    assert out["configured"] is False and out["reachable"] is False and out["enabled"] is True
    assert out["url_source"] == ".claude/settings.local.json" and out["url"] == "postgresql://***@127.0.0.1:9/sdlc"
    assert out["hint"].startswith("graph on but not reachable: run /factory:graph provision neon or set DATABASE_URL")
    rc, text = _script(root, "status")
    assert rc == 1, text
    assert "graph on but not reachable: run /factory:graph provision neon or set DATABASE_URL" in text
    assert "(from .claude/settings.local.json)" in text


def test_status_disabled_opens_nothing(tmp_path):
    root = _project(tmp_path, {"enabled": False, "provider": "local"}, local_url=CLOSED)

    def never():
        raise AssertionError("graph off must not open a connection")

    out = status.graph_status(never, project_dir=root)
    assert out["enabled"] is False and out["configured"] is False and out["reachable"] is False
    assert out["hint"] == "graph off: every graph step is skipped (/factory:graph on turns it back on)"
    rc, text = _script(root, "status")
    assert rc == 0 and text.strip() == out["hint"]
    rc, text = _script(root, "on")
    assert rc == 0 and "graph on" in text
    assert json.loads((root / ".claude" / "sdlc.json").read_text())["graph"]["enabled"] is True


def test_sdlc_graph_status_exits_1_when_off(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, {"enabled": False})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert cli.main(["status", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["enabled"] is False


def test_mcp_tools_answer_graph_off(tmp_path, monkeypatch):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    from sdlc_graph import mcp_server

    root = _project(tmp_path, {"enabled": False})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))

    def never():
        raise AssertionError("graph off must not open a connection")

    server = mcp_server.build_server(conn_factory=never)

    async def go(tool: str) -> dict:
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            res = await client.call_tool(tool, {"paths": ["app/x.py"]} if tool == "graph_impact" else {})
            payload = res.structuredContent
            if payload is None and res.content:
                payload = json.loads(res.content[0].text)
            assert not res.isError
            return payload

    off = anyio.run(go, "graph_impact")
    assert off == {"ok": False, "error": "graph off",
                   "hint": "graph off: every graph step is skipped (/factory:graph on turns it back on)"}
    st = anyio.run(go, "graph_status")
    assert st["enabled"] is False and st["configured"] is False


def test_orchestrator_refuses_when_the_graph_is_off(tmp_path, capsys):
    root = _project(tmp_path, {"enabled": False})
    rc = cli.main(["orchestrate", "feat", "--repo", str(root), "--once", "--executor-cmd", "true", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 5 and out["error"] == "graph_off"
    assert out["message"].startswith("the orchestrator needs the ledger; run /factory:graph on")


# --------------------------------------------------------------------------- evidence


def test_graph_provisioned_is_recorded_once_on_the_repo_node(conn):
    first = provisioned.record(conn, repo="acme/app", provider="local", where="local localhost:5435/sdlc",
                               schema="010", actor="agent:claude")
    again = provisioned.record(conn, repo="acme/app", provider="local", where="local localhost:5435/sdlc",
                               schema="010", actor="agent:claude")
    assert first["status"] == "recorded" and again["status"] == "already_there"
    assert first["slug"] == "repo-acme-app" and again["evidence_id"] == first["evidence_id"]
    node = conn.execute("SELECT kind, state FROM node WHERE slug = 'repo-acme-app'").fetchone()
    assert node == {"kind": "repo", "state": "active"}
    rows = conn.execute("SELECT payload FROM evidence WHERE kind = 'graph_provisioned'").fetchall()
    assert [r["payload"] for r in rows] == [{"provider": "local", "where": "local localhost:5435/sdlc", "schema": "010"}]


# --------------------------------------------------------------------------- local provisioning


def test_provision_local_on_a_reachable_database_runs_db_init_only(pg_schema, tmp_path):
    """CI's graph job: the service container is already up, so Docker is never called."""
    root = _project(tmp_path, {"enabled": True, "provider": "local"})
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "docker").write_text("#!/bin/sh\necho called >> \"$DOCKER_LOG\"\nexit 1\n")
    (fake / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    env = _env(root, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", DOCKER_LOG=str(log), SDLC_SCHEMA=pg_schema)
    rc, text = _script(root, "provision", "local", env=env)
    assert rc == 0, text
    assert "Docker not needed, db init only" in text and not log.exists()
    assert "evidence: graph_provisioned recorded on repo-proj" in text
    assert "DATABASE_URL written in .claude/settings.local.json (value not shown)" in text
    rc, text = _script(root, "provision", "local", env=env)
    assert rc == 0 and "evidence: graph_provisioned already there" in text and "already there: local" in text, text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="CI's graph job uses the service container (db init only, above)")
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is not installed")
def test_provision_local_for_real_with_docker(tmp_path):
    port = _free_port()
    project = f"factorytest{secrets.token_hex(3)}"
    compose = ["docker", "compose", "-p", project, "-f", str(PLUGIN / "docker-compose.yml")]
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("the Docker daemon is not running")
    url = f"postgresql://sdlc:sdlc@localhost:{port}/sdlc"
    root = _project(tmp_path, {"enabled": True, "provider": "local"}, local_url=url)
    env = _env(root)
    env.pop("SDLC_SCHEMA", None)
    env.pop("DATABASE_URL", None)
    try:
        rc, text = _script(root, "provision", "local", "--compose-project", project, "--timeout", "120", env=env, timeout=600)
        assert rc == 0, text
        assert f"docker compose up -d db: {project} on 127.0.0.1:{port}" in text
        assert "ready: pg_isready answered and a connection opened" in text
        assert f"db init: {len(db.sql_files())} DDL file(s) applied to schema public" in text
        assert "status: configured, provider local, schema 011" in text
        assert f"provisioned: local localhost:{port}/sdlc" in text
        ps = subprocess.run(["docker", "ps", "--filter", f"name={project}-db", "--format", "{{.Names}}"],
                            capture_output=True, text=True).stdout.split()
        assert ps == [f"{project}-db"]
        rc, text = _script(root, "provision", "local", "--compose-project", project, env=env, timeout=600)
        assert rc == 0 and "Docker not needed, db init only" in text and f"already there: local localhost:{port}/sdlc" in text, text
        rc, text = _script(root, "status", env=env)
        assert rc == 0 and "reachable: yes, schema 011, snapshots 0, nodes 1" in text, text
    finally:
        subprocess.run([*compose, "down", "-v"], capture_output=True, env={**os.environ, "SDLC_PG_PORT": str(port),
                                                                             "SDLC_PG_CONTAINER": f"{project}-db"})


# --------------------------------------------------------------------------- spanner mirror


_MIRROR_CHILD = r"""
import json, secrets
from google.auth.credentials import AnonymousCredentials
from google.cloud import spanner
from sdlc_graph import db
from sdlc_graph.spanner import ddl, emulator_smoke, export

host = emulator_smoke.emulator_host()
inst = export._open_instance(host, emulator_smoke.DEFAULT_PROJECT, emulator_smoke.DEFAULT_INSTANCE)
name = "mirror_" + secrets.token_hex(3)
database = inst.database(name, ddl_statements=ddl.ddl_statements())
database.create().result(120)
out = {}
try:
    client = spanner.Client(project=emulator_smoke.DEFAULT_PROJECT, credentials=AnonymousCredentials())
    kw = dict(project=emulator_smoke.DEFAULT_PROJECT, instance=emulator_smoke.DEFAULT_INSTANCE, database=name, client=client)
    with db.connect() as conn:
        out["first"] = export.mirror(conn, **kw)["rows_loaded"]
        out["second"] = export.mirror(conn, **kw)["rows_loaded"]
        with database.snapshot() as snap:
            out["nodes"] = [list(r) for r in snap.execute_sql("SELECT kind, slug FROM node")]
        try:
            export.mirror(conn, **{**kw, "database": "missing_db"})
        except LookupError as exc:
            out["missing"] = str(exc)
finally:
    database.drop()
print(json.dumps(out))
"""


def test_spanner_mirror_replaces_rows_and_keeps_the_schema(conn):
    """In a child process: the Spanner client loads grpc, whose fork handlers would print into later tests' hooks."""
    from sdlc_graph.spanner import emulator_smoke

    host = emulator_smoke.emulator_host()
    if not emulator_smoke._reachable(host):
        pytest.skip(f"no Spanner emulator at {host}")
    provisioned.record(conn, repo="acme/app", provider="local", where="local x:1/sdlc", schema="010", actor="agent:claude")
    p = subprocess.run([sys.executable, "-c", _MIRROR_CHILD], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "SPANNER_EMULATOR_HOST": host})
    assert p.returncode == 0, p.stderr[-2000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["first"] == out["second"] and out["first"]["node"] == 1
    assert out["nodes"] == [["repo", "repo-acme-app"]]
    assert "does not exist" in out["missing"]


# --------------------------------------------------------------------------- pending migrations


@pytest.fixture
def behind_schema(pg_schema):
    """A schema that holds every DDL file but the newest, as a database left by an older plugin is."""
    from sdlc_graph import db

    name = f"{pg_schema}_old"
    with db.connect(schema="public") as admin:
        admin.execute(f"CREATE SCHEMA {name}")
    try:
        with db.connect(schema=name) as c:
            db.run_sql_files(c, db.sql_files()[:-1])
        yield name
    finally:
        db.drop_schema(name)


def test_status_reports_how_many_migrations_the_database_is_behind(behind_schema, tmp_path):
    from sdlc_graph import db

    root = _project(tmp_path, {"enabled": True, "provider": "local"})
    out = status.graph_status(db.connection_factory(schema=behind_schema), project_dir=root)
    assert out["configured"] is True and out["schema_version"] == "010"
    assert out["latest_version"] == "011" and out["pending_migrations"] == 1
    assert "behind by 1 migration" in out["hint"]
    rc, text = _script(root, "status", env=_env(root, SDLC_SCHEMA=behind_schema))
    assert rc == 0 and "behind by 1 migration (schema 010, this plugin ships 011)" in text, text


def test_migrate_applies_the_pending_files_and_status_is_current(behind_schema, tmp_path):
    from sdlc_graph import db

    root = _project(tmp_path, {"enabled": True, "provider": "local"})
    env = _env(root, SDLC_SCHEMA=behind_schema)
    rc, text = _script(root, "migrate", env=env)
    assert rc == 0 and "migrated: schema 010 -> 011" in text, text
    out = status.graph_status(db.connection_factory(schema=behind_schema), project_dir=root)
    assert out["schema_version"] == "011" and out["pending_migrations"] == 0 and out["hint"] is None
    rc, text = _script(root, "migrate", env=env)
    assert rc == 0 and "is current: nothing to migrate" in text, text
