"""Spanner Graph export: DDL content (always) and the emulator smoke test (when docker can supply the image)."""

from __future__ import annotations

import os
import subprocess

import pytest

from graph_fakes import _emulator_running
from sdlc_graph.cli import main
from sdlc_graph.spanner import ddl, emulator_smoke


def test_ddl_has_all_tables_and_property_graph():
    text = ddl.render()
    for t in ddl.TABLES:
        assert f"CREATE TABLE {t} (" in text
    assert text.count("CREATE PROPERTY GRAPH SdlcGraph") == 1
    assert "NODE TABLES" in text and "EDGE TABLES" in text
    assert "SOURCE KEY (src) REFERENCES node (id)" in text
    assert "DESTINATION KEY (dst) REFERENCES node (id)" in text
    assert "LABEL Node" in text and "LABEL Symbol" in text and "LABEL Rel" in text
    for typ in ("STRING(MAX)", "JSON", "TIMESTAMP", "INT64", "ARRAY<STRING(MAX)>"):
        assert typ in text
    assert "vector" not in text.lower()
    assert text.rstrip().endswith(";")


def test_ddl_statements_are_single_and_unterminated():
    stmts = ddl.ddl_statements()
    assert stmts[-1].startswith("CREATE PROPERTY GRAPH")
    assert all(";" not in s for s in stmts)


def test_sample_queries_file():
    q = ddl.sample_queries()
    assert "MATCH (c:Node {kind: 'contract'})-[:Rel {rel: 'depends_on'}]->(d:Node)" in q
    assert "GRAPH SdlcGraph" in q


def test_cli_ddl_out(tmp_path, capsys):
    out = tmp_path / "s.sql"
    assert main(["spanner", "ddl", "--out", str(out), "--json"]) == 0
    assert "CREATE PROPERTY GRAPH SdlcGraph" in out.read_text()
    assert main(["spanner", "ddl"]) == 0
    assert "CREATE PROPERTY GRAPH SdlcGraph" in capsys.readouterr().out


def _emulator_available() -> str | None:
    """Return a skip reason, or None when an emulator is reachable or the image can be pulled in 60 s."""
    if emulator_smoke._reachable(emulator_smoke.emulator_host()):
        return None
    try:
        subprocess.run(["docker", "pull", emulator_smoke.EMULATOR_IMAGE], capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"docker cannot supply the emulator image within 60 s: {type(exc).__name__}"
    return None


@pytest.mark.usefixtures("emulator_deadline")
def test_emulator_smoke_returns_two_rows():
    reason = _emulator_available()
    if reason:
        pytest.skip(reason)
    old = os.environ.get("SPANNER_EMULATOR_HOST")
    try:
        res = emulator_smoke.smoke(instance="pytest", database="pytest")
    finally:
        if old is None:
            os.environ.pop("SPANNER_EMULATOR_HOST", None)
        else:
            os.environ["SPANNER_EMULATOR_HOST"] = old
    assert res["ok"], res["error"]
    assert len(res["gql"]["rows"]) == 2
    assert res["rows_loaded"] == {"node": 3, "edge": 2}


# --------------------------------------------------------------------------- export (integrator)

from sdlc_graph.spanner import export as spx  # noqa: E402


def test_render_query_substitutes_and_escapes():
    gql = spx.render_query("contract_routes", slug="it's")
    assert "{slug: 'it\\'s'}" in gql and "{slug}" not in gql
    assert spx.render_query("GRAPH SdlcGraph MATCH (n:Node) RETURN n.slug") == "GRAPH SdlcGraph MATCH (n:Node) RETURN n.slug"
    with pytest.raises(ValueError):
        spx.render_query("tests_reaching")


def test_export_tables_match_spanner_ddl():
    """Every exported column exists in the Spanner table it is loaded into."""
    text = ddl.render()
    for table, (_sql, cols, _kinds) in spx._TABLES.items():
        body = text.split(f"CREATE TABLE {table} (")[1].split(") PRIMARY KEY")[0]
        for col in cols:
            assert f"  {col} " in body or f"  `{col}` " in body, (table, col)


@pytest.mark.usefixtures("emulator_deadline")
def test_export_loads_ledger_and_snapshot_and_runs_gql(clean_conn, synthetic_repo):
    """Integration: Postgres ledger + one materialised snapshot -> emulator -> GQL over both graphs."""
    reason = _emulator_running()
    if reason:
        pytest.skip(reason)
    from sdlc_graph.codegraph import snapshot
    from sdlc_graph.ledger import intake, nodes

    snapshot.index(clean_conn, synthetic_repo, name="synthetic")
    nodes.create(clean_conn, kind="epic", slug="ep", title="Epic", repo="synthetic", created_by="@ish")
    nodes.create(clean_conn, kind="feature", slug="ft", title="Feature", repo="synthetic", created_by="@ish")
    nodes.link(clean_conn, "ep", "ft", "decomposes_into")
    res = intake.contract_draft(clean_conn, slug="ct", title="Items contract", repo="synthetic",
                                ticket_text="List items faster.", actor="@ish", feature="ft",
                                touched_symbols=["function:app.routes.a.make_item@app/routes/a.py"])
    assert res["status"] == "created", res
    out = spx.export(clean_conn, database="pytest_export", instance="pytest",
                     queries={"chain": {}, "tests_reaching": {"symbol": "app.routes.a.make_item"},
                              "contract_routes": {"slug": "ct"}})
    assert out["ok"] and out["rows_loaded"]["node"] == 3 and out["rows_loaded"]["sym"] > 0
    chain = {tuple(r[1:3]) for r in out["queries"]["chain"]["rows"]}
    assert chain == {("feature", "ft"), ("contract", "ct")}
    tests = {r[1] for r in out["queries"]["tests_reaching"]["rows"]}
    assert "test_list_items" in tests
    # parity: the GQL answer walks the same rels to the same depth as the Postgres reverse walk, so it is a
    # subset of affected(); Postgres adds tests of reached test modules, conftest, closure and mirror paths.
    from sdlc_graph.impact import affected

    pg = affected.affected(clean_conn, snapshot.resolve_snapshot(clean_conn, "synthetic"),
                           symbols=["function:app.routes.a.make_item@app/routes/a.py"])
    assert tests <= {t.rsplit("::", 1)[-1] for t in pg["tests"]}
    routes = {r[2] for r in out["queries"]["contract_routes"]["rows"]}
    assert "GET /items" in routes
    again = spx.query("chain", database="pytest_export", instance="pytest")
    assert again["rows"] == out["queries"]["chain"]["rows"]


def test_a_listener_that_is_not_an_emulator_is_not_reachable_and_the_probe_is_quick():
    """Found in the sandbox: a bare TCP connect passed on any listener, then every gRPC call hung. A real
    handshake with a short deadline does not."""
    import socket
    import threading
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    stop = threading.Event()
    held: list[socket.socket] = []

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])  # accepts and says nothing: not HTTP/2
            except OSError:
                pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    try:
        t0 = time.monotonic()
        assert emulator_smoke._reachable(f"127.0.0.1:{srv.getsockname()[1]}") is False
        assert time.monotonic() - t0 < emulator_smoke.PROBE_TIMEOUT + 3
    finally:
        stop.set()
        t.join()
        for c in held:
            c.close()
        srv.close()


def test_skip_proxy_for_localhost_extends_no_proxy():
    env = {"no_proxy": "example.com"}
    emulator_smoke.skip_proxy_for_localhost(env)
    assert env["no_proxy"] == "example.com,localhost,127.0.0.1,::1" and "localhost" in env["no_grpc_proxy"]
