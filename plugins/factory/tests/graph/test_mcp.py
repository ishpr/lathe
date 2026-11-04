"""MCP server tests: an in-memory client session against the server object.

The library modules are implemented by other owners; where one is still a stub the test
monkeypatches the function and asserts the wrapper's shape, otherwise it uses the real code.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from sdlc_graph import mcp_server
from sdlc_graph.status import latest_version
from sdlc_graph.codegraph import queries, snapshot
from sdlc_graph.ledger import nodes, transitions


def _call(server: Any, tool: str, args: dict | None = None) -> tuple[bool, Any]:
    async def go() -> tuple[bool, Any]:
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            res = await client.call_tool(tool, args or {})
            payload = res.structuredContent
            if payload is None and res.content:
                payload = json.loads(res.content[0].text)
            return bool(res.isError), payload

    return anyio.run(go)


def _implemented(fn: Any, *args: Any, **kw: Any) -> bool:
    try:
        fn(*args, **kw)
    except NotImplementedError:
        return False
    except Exception:
        return True
    return True


@pytest.fixture()
def server(conn_factory):
    return mcp_server.build_server(conn_factory)


def test_lists_all_twenty_three_tools_with_schemas(server):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(go)
    assert len(mcp_server.TOOL_NAMES) == 23
    assert {t.name for t in tools} == set(mcp_server.TOOL_NAMES)
    for t in tools:
        assert t.description, t.name
        assert t.inputSchema.get("type") == "object", t.name
    by = {t.name: t.inputSchema for t in tools}
    assert by["contract_transition"]["required"] == ["slug", "to_state"]
    assert by["graph_deps"]["required"] == ["symbol"]
    assert set(by["contract_draft"]["properties"]) >= {"slug", "title", "repo", "ticket_text", "touched_symbols",
                                                       "contract_md", "strict"}
    assert by["contract_draft"]["properties"]["strict"].get("default") is True
    assert by["contract_update"]["required"] == ["slug", "contract_md"]
    assert set(by["contract_update"]["properties"]) == {"slug", "contract_md"}
    for name in mcp_server.TOOL_NAMES:  # identity is the server's, never a tool argument
        assert "actor" not in by[name]["properties"], name
    assert set(by["evidence_add"]["properties"]) == {"slug", "kind", "payload"}


def test_resources_are_registered(server):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return [t.uriTemplate for t in (await client.list_resource_templates()).resourceTemplates]

    templates = anyio.run(go)
    assert "contract://{slug}" in templates and "epic://{slug}" in templates
    assert any(t.startswith("rules://") for t in templates)


def test_graph_snapshot_wrapper(server, monkeypatch):
    rows = [{"snapshot_id": 1, "repo": "demo", "commit_sha": "abc123", "storage": "full"}]
    seen = {}

    def fake(conn, repo=None):
        seen["repo"] = repo
        return rows

    monkeypatch.setattr(snapshot, "list_snapshots", fake)
    is_error, payload = _call(server, "graph_snapshot", {"repo": "demo"})
    assert not is_error and payload == {"ok": True, "result": rows} and seen == {"repo": "demo"}


def test_graph_snapshot_real_if_implemented(server, clean_conn):
    if not _implemented(snapshot.list_snapshots, clean_conn):
        pytest.skip("codegraph.snapshot not implemented yet")
    is_error, payload = _call(server, "graph_snapshot")
    assert not is_error and payload == {"ok": True, "result": []}


def test_graph_tools_default_to_latest_snapshot(server, monkeypatch):
    calls = {}
    monkeypatch.setattr(snapshot, "resolve_snapshot", lambda conn, repo=None, sha=None: calls.setdefault("snap", 7))
    monkeypatch.setattr(queries, "deps", lambda conn, sid, symbol, **kw: {"snapshot_id": sid, "root": symbol, "kw": kw})
    is_error, payload = _call(server, "graph_deps", {"symbol": "app.models.Item", "depth": 2})
    assert not is_error
    assert payload["snapshot_id"] == 7 and payload["kw"] == {"depth": 2, "direction": "out", "rels": None}


def test_tool_errors_are_dicts_not_raises(server, monkeypatch):
    def boom(conn, slug):
        raise LookupError(f"no node {slug}")

    monkeypatch.setattr(nodes, "get", boom)
    is_error, payload = _call(server, "node_get", {"slug": "nope"})
    assert not is_error
    assert payload["ok"] is False and payload["error"]["type"] == "not_found" and "nope" in payload["error"]["message"]


def test_unimplemented_library_is_a_structured_error(server, monkeypatch):
    def todo(conn, *a, **k):
        raise NotImplementedError("metrics")

    monkeypatch.setattr("sdlc_graph.ledger.metrics.snapshot", todo)
    _, payload = _call(server, "metrics_snapshot", {})
    assert payload["ok"] is False and payload["error"]["type"] == "not_implemented"


def test_node_get_wrapper_shape(server, monkeypatch):
    view = {"node": {"slug": "c1", "kind": "contract", "state": "drafted"}, "open_decisions": 0,
            "evidence": {}, "edges": {"out": [], "in": []}, "transitions": []}
    monkeypatch.setattr(nodes, "get", lambda conn, slug: view)
    is_error, payload = _call(server, "node_get", {"slug": "c1"})
    assert not is_error and payload == view


def test_node_get_real_node_created_through_ledger(server, clean_conn):
    if not _implemented(nodes.get, clean_conn, "x"):
        pytest.skip("ledger.nodes not implemented yet")
    nodes.create(clean_conn, kind="contract", slug="contract-mcp-demo", title="MCP demo", repo="demo",
                 created_by="@alice", capabilities={"paths": ["app/**"]})
    is_error, payload = _call(server, "node_get", {"slug": "contract-mcp-demo"})
    assert not is_error and payload["node"]["slug"] == "contract-mcp-demo"
    assert payload["node"]["state"] == "drafted"
    # an unknown slug is a structured error
    _, missing = _call(server, "node_get", {"slug": "nope"})
    assert missing["ok"] is False and missing["error"]["type"] == "not_found"


def test_contract_resource_renders_node_json(server, monkeypatch):
    view = {"node": {"slug": "c1", "kind": "contract"}, "open_decisions": 0}
    monkeypatch.setattr(nodes, "get", lambda conn, slug: view)

    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.read_resource("contract://c1")

    res = anyio.run(go)
    assert json.loads(res.contents[0].text) == view


@pytest.mark.parametrize("to_state", sorted(mcp_server.PROTECTED_STATES))
def test_agent_actor_refused_into_protected_states(server, monkeypatch, to_state):
    # The wrapper refuses before the ledger is reached; prove the ledger is never called.
    monkeypatch.setattr(transitions, "transition", lambda *a, **k: pytest.fail("ledger reached"))
    is_error, payload = _call(server, "contract_transition",
                              {"slug": "c1", "to_state": to_state})
    assert not is_error
    assert payload["ok"] is False and "actor:not_agent" in payload["missing"]


def test_agent_refused_end_to_end_leaves_state(server, clean_conn):
    if not _implemented(nodes.get, clean_conn, "x"):
        pytest.skip("ledger not implemented yet")
    nodes.create(clean_conn, kind="contract", slug="contract-mcp-guard", title="Guard", repo="demo", created_by="@alice")
    _, payload = _call(server, "contract_transition",
                       {"slug": "contract-mcp-guard", "to_state": "approved"})
    assert payload["ok"] is False
    assert nodes.get(clean_conn, "contract-mcp-guard")["node"]["state"] == "drafted"


def test_contract_transition_passes_non_protected_through(server, monkeypatch):
    seen = {}

    def fake(conn, slug, to_state, actor, *, evidence_ids=None, reason=None):
        seen.update(slug=slug, to=to_state, actor=actor, ev=evidence_ids, reason=reason)
        return {"ok": True, "slug": slug, "state": to_state}

    monkeypatch.setattr(transitions, "transition", fake)
    _, payload = _call(server, "contract_transition",
                       {"slug": "c1", "to_state": "ready", "reason": "x"})
    assert payload["ok"] is True
    assert seen == {"slug": "c1", "to": "ready", "actor": "agent:mcp", "ev": None, "reason": "x"}


def test_decision_resolve_refuses_agents(server, monkeypatch):
    monkeypatch.setattr("sdlc_graph.ledger.decisions.resolve", lambda *a, **k: pytest.fail("ledger reached"))
    _, payload = _call(server, "decision_resolve", {"decision_id": "1", "choice": "a"})
    assert payload["ok"] is False and payload["error"]["type"] == "forbidden"


def test_read_only_tools_do_not_write_ledger_functions():
    assert set(mcp_server.READ_ONLY_TOOLS).isdisjoint(mcp_server.WRITE_TOOLS)
    assert len(mcp_server.READ_ONLY_TOOLS) == 14 and len(mcp_server.WRITE_TOOLS) == 9


def test_cli_registers_mcp():
    from sdlc_graph import cli

    args = cli.build_parser().parse_args(["mcp", "--http", "--port", "9000"])
    assert args.http is True and args.port == 9000


def test_rules_resource_filters_by_glob(server, conn_factory, clean_conn):
    clean_conn.execute("INSERT INTO node (kind, slug, title, repo, path, state, created_by) VALUES "
                       "('rule','rule-routes','Routes need auth','demo','app/routes/a.py','active','@alice'),"
                       "('rule','rule-docs','Docs rule','demo','docs/x.md','active','@alice'),"
                       "('rule','rule-global','Global','demo',NULL,'active','@alice')")

    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.read_resource("rules://demo/app%2Froutes%2F*")

    body = json.loads(anyio.run(go).contents[0].text)
    assert [r["slug"] for r in body["rules"]] == ["rule-global", "rule-routes"]
    assert body["glob"] == "app/routes/*"


def test_tool_annotations_mark_read_only_and_writes(server):
    """Integration: clients see readOnlyHint on the ten read-only tools and not on the eight writes."""
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return (await client.list_tools()).tools

    hints = {t.name: (t.annotations.readOnlyHint if t.annotations else None) for t in anyio.run(go)}
    assert {n for n, ro in hints.items() if ro} == set(mcp_server.READ_ONLY_TOOLS)
    assert {n for n, ro in hints.items() if ro is False} == set(mcp_server.WRITE_TOOLS)


def test_contract_transition_with_integer_evidence_ids_end_to_end(server, clean_conn):
    """Integration: evidence ids are bigints in the ledger, so the tool schema advertises integers
    (it said strings), and the real transition cites exactly the rows it was given."""
    from sdlc_graph.ledger import evidence

    nodes.create(clean_conn, kind="contract", slug="contract-mcp-ev", title="Ev", repo="demo", created_by="@alice",
                 acceptance=[{"id": "AC1", "text": "x"}], capabilities={"paths": ["app/**"]})
    from sdlc_graph.orchestrator import contract_blocks

    contract_blocks.set_blocks(clean_conn, "contract-mcp-ev", actor="@alice",
                               defaults=["Make the smallest change that satisfies the acceptance criteria.",
                                         "Follow the nearest existing pattern in the code being changed.",
                                         "Prefer the option the Decisions above imply.",
                                         "When none applies, choose the reversible option."],
                               capabilities={"commands": [], "egress": [], "data": []})
    for kind, payload in (("tests_failing_first", {"tests": ["tests/t.py::test_x"], "commit": "a" * 40}),
                          ("tests_frozen", {"files": {"tests/t.py": "0" * 64}, "commit": "a" * 40})):
        evidence.add(clean_conn, "contract-mcp-ev", kind, payload)
    ids = [e["id"] for e in evidence.list_for(clean_conn, "contract-mcp-ev")]
    is_error, out = _call(server, "contract_transition", {"slug": "contract-mcp-ev", "to_state": "ready"})
    assert not is_error and out["ok"] is True, out
    is_error, out = _call(server, "contract_transition", {"slug": "contract-mcp-ev", "to_state": "tests_frozen",
                                                          "evidence_ids": ids})
    assert not is_error and out["ok"] is True, out
    assert sorted(out["evidence_ids"]) == sorted(ids)


def test_server_identity_comes_from_configuration(monkeypatch):
    monkeypatch.delenv("SDLC_ACTOR", raising=False)
    assert mcp_server.server_actor() == "agent:mcp"
    monkeypatch.setenv("SDLC_ACTOR", "agent:sonnet")
    assert mcp_server.server_actor() == "agent:sonnet"
    for bad in ("@ish", "ci:github", "root"):
        with pytest.raises(ValueError):
            mcp_server.server_actor(bad)
        monkeypatch.setenv("SDLC_ACTOR", bad)
        with pytest.raises(ValueError):
            mcp_server.build_server(lambda: None)


def test_forged_actor_and_ci_evidence_cannot_reach_approved_or_merged(server, clean_conn):
    """Review finding: with actor, source and signed_by as tool arguments an agent walked a node to
    merged. Now a forged `actor` is ignored and evidence_add cannot write binding rows."""
    from sdlc_graph.ledger import evidence

    nodes.create(clean_conn, kind="contract", slug="contract-forge", title="Forge", repo="demo", created_by="@alice",
                 state="in_review")
    forged = {"slug": "contract-forge", "source": "ci:github", "signed_by": "anything", "actor": "@ish"}
    _, out = _call(server, "evidence_add", {**forged, "kind": "ci_run",
                                            "payload": {"url": "u", "conclusion": "success"}})
    rows = evidence.list_for(clean_conn, "contract-forge")
    assert rows and all(r["source"] == "local" and not r["signed_by"] for r in rows), rows
    _call(server, "evidence_add", {**forged, "kind": "review_approval",
                                   "payload": {"reviewer": "@bob", "author": "@alice", "url": "u"}})
    for to in ("approved", "merged"):
        _, out = _call(server, "contract_transition", {"slug": "contract-forge", "to_state": to, "actor": "@bob"})
        assert out["ok"] is False and "actor:not_agent" in out["missing"], out
    assert nodes.get(clean_conn, "contract-forge")["node"]["state"] == "in_review"
    _, out = _call(server, "decision_resolve", {"decision_id": "1", "choice": "a", "actor": "@ish"})
    assert out["ok"] is False and out["error"]["type"] == "forbidden"


# --------------------------------------------------------------------------- no database: "graph not configured"

#: A port nothing listens on: the server must still start and answer every tool.
CLOSED_URL = "postgresql://nobody:pw-not-shown@127.0.0.1:9/sdlc"

#: One valid argument set per tool (values are never reached: the connection fails first).
_ARGS: dict[str, dict] = {
    "graph_deps": {"symbol": "x"}, "graph_impact": {"paths": ["app/main.py"]}, "graph_owners": {"paths": ["a"], "repo": "r"},
    "graph_search": {"text": "x"}, "graph_snapshot": {}, "node_get": {"slug": "s"}, "node_search": {"text": "x"},
    "incident_similar": {"text": "x"}, "intake_context": {"repo": "r", "title": "t"},
    "node_neighbours": {"slug": "s"}, "workflow_next": {"slug": "s"}, "metrics_snapshot": {}, "epic_decompose": {"slug": "s"},
    "contract_draft": {"slug": "s", "title": "t", "repo": "r", "ticket_text": "x"},
    "contract_update": {"slug": "s", "contract_md": "x"}, "contract_transition": {"slug": "s", "to_state": "ready"},
    "evidence_add": {"slug": "s", "kind": "pr_url", "payload": {"url": "u"}},
    "decision_propose": {"slug": "s", "question": "q", "options": ["a", "b"]},
    "decision_resolve": {"decision_id": "1", "choice": "a"},
    "run_start": {"stage": "implement", "model": "sonnet"}, "run_finish": {"run_id": "1", "outcome": "ok"},
    "release_request": {"contract_slugs": ["s"]},
}


def test_without_a_database_every_tool_answers_graph_not_configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", CLOSED_URL)
    server = mcp_server.build_server()  # starts: nothing connects until a tool is called
    assert set(_ARGS) | {"graph_status"} == set(mcp_server.TOOL_NAMES)
    refused_before_connecting = {"decision_resolve"}  # an agent may never resolve: refused without a connection
    for tool, args in _ARGS.items():
        is_error, out = _call(server, tool, args)
        assert not is_error, tool
        if tool in refused_before_connecting:
            assert out["ok"] is False and out["error"]["type"] == "forbidden", out
            continue
        assert out["ok"] is False and out["error"] == "graph not configured", (tool, out)
        assert "DATABASE_URL" in out["hint"] and "pw-not-shown" not in json.dumps(out), (tool, out)
    is_error, status = _call(server, "graph_status")
    assert not is_error
    assert {k: status[k] for k in ("configured", "provider", "reachable", "schema_version", "snapshots", "nodes")} == {
        "configured": False, "provider": "local", "reachable": False, "schema_version": None, "snapshots": None, "nodes": None}
    assert "DATABASE_URL" in status["hint"] and "pw-not-shown" not in json.dumps(status)


def test_graph_status_on_an_initialised_schema_reports_counts(server, clean_conn):
    is_error, status = _call(server, "graph_status")
    assert not is_error
    assert status["configured"] is True and status["reachable"] is True
    assert status["schema_version"] == latest_version() and status["snapshots"] == 0 and status["nodes"] == 0
    assert status["hint"] is None


def test_graph_status_on_an_empty_schema_says_db_init(conn_factory, pg_schema):
    import psycopg

    from sdlc_graph import db, status

    empty = "e_" + pg_schema[2:]
    with db.connect(schema="public") as admin:
        admin.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(empty)))
    try:
        out = status.graph_status(db.connection_factory(schema=empty))
        assert out["reachable"] is True and out["configured"] is False and out["schema_version"] is None
        assert "db init" in out["hint"]
    finally:
        db.drop_schema(empty)

    class NoTables:  # reachable, but `db init` never ran: every query names a table that is not there
        def execute(self, *a, **k):
            raise psycopg.errors.UndefinedTable('relation "snapshot" does not exist')

        def close(self):
            pass

    server = mcp_server.build_server(lambda: NoTables())
    _, res = _call(server, "graph_snapshot")
    assert res["error"] == "graph not configured" and "db init" in res["hint"], res


@pytest.mark.parametrize("url, provider", [
    ("postgresql://u@localhost:5435/x", "local"), ("postgresql://u@ep-cool-1.us-east-2.aws.neon.tech/x", "neon"),
    ("postgresql://u@db.abc.us-east-1.rds.amazonaws.com/x", "aws"), ("postgresql://u@pg.example.com/x", "postgres")])
def test_graph_status_names_the_provider_from_the_url(url, provider):
    from sdlc_graph import status

    assert status.provider_from_url(url) == provider


def test_graph_status_takes_the_provider_from_sdlc_json(tmp_path):
    from sdlc_graph import status

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "sdlc.json").write_text(json.dumps({"graph": {"enabled": True, "provider": "neon"}}))
    assert status.configured_provider(tmp_path) == "neon"
    assert status.configured_provider(tmp_path / "nowhere") is None


def test_cli_status_exits_1_when_not_configured(monkeypatch, capsys):
    from sdlc_graph import cli

    monkeypatch.setenv("DATABASE_URL", CLOSED_URL)
    assert cli.main(["status", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["configured"] is False and out["reachable"] is False and "pw-not-shown" not in json.dumps(out)



def test_graph_impact_tool_caps_and_counts_only(server, graph_snapshot_for_impact):
    """#24: graph_impact takes max_items and counts_only."""
    ok, res = _call(server, "graph_impact", {"paths": ["app/routes/a.py"], "max_items": 1})
    assert not ok and len(res["tests"]) == 1 and res["truncated"]["tests"] >= 1
    ok, res = _call(server, "graph_impact", {"paths": ["app/routes/a.py"], "counts_only": True})
    assert not ok and "tests" not in res and res["counts"]["tests"] >= 2


@pytest.fixture()
def graph_snapshot_for_impact(conn):
    from psycopg.types.json import Jsonb

    sid = conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('r', %s, 't') RETURNING id",
                       ("b" * 40,)).fetchone()["id"]

    def sym(kind, qual, path, attrs=None):
        return conn.execute("INSERT INTO sym (snapshot_id, kind, name, qualname, path, attrs) VALUES (%s,%s,%s,%s,%s,%s) "
                            "RETURNING id", (sid, kind, qual.rsplit(".", 1)[-1], qual, path, Jsonb(attrs or {}))).fetchone()["id"]

    mod = sym("module", "app.routes.a", "app/routes/a.py")
    for n in ("x", "y"):
        t = sym("test", f"tests.test_a.test_{n}", "tests/test_a.py", {"nodeid": f"tests/test_a.py::test_{n}"})
        conn.execute("INSERT INTO sym_edge (snapshot_id, src, dst, rel) VALUES (%s,%s,%s,'tests')", (sid, t, mod))
    conn.commit()
    return sid
