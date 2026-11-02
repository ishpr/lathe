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


def test_lists_all_seventeen_tools_with_schemas(server):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(go)
    assert len(mcp_server.TOOL_NAMES) == 17
    assert {t.name for t in tools} == set(mcp_server.TOOL_NAMES)
    for t in tools:
        assert t.description, t.name
        assert t.inputSchema.get("type") == "object", t.name
    by = {t.name: t.inputSchema for t in tools}
    assert by["contract_transition"]["required"] == ["slug", "to_state"]
    assert by["graph_deps"]["required"] == ["symbol"]
    assert set(by["contract_draft"]["properties"]) >= {"slug", "title", "repo", "ticket_text", "touched_symbols"}
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
    assert len(mcp_server.READ_ONLY_TOOLS) == 10 and len(mcp_server.WRITE_TOOLS) == 7


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
    """Integration: clients see readOnlyHint on the ten read-only tools and not on the seven writes."""
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
