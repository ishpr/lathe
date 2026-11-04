"""epic.decompose: proposals from the epic file, walking skeleton first, nothing written."""

from __future__ import annotations

import pytest

from sdlc_graph.ledger import decompose, nodes

EPIC = """# Epic: Self-serve order status

## Features
1. **Measure deflection** (enabler) - log every view - moves: readable ticket share - size S
2. **Tell me when it changes** - email on shipped - moves ticket share - size M
3. **See my order's status** - a status page - moves views - size S
4. **Delays explained** - show the new estimate - size M, to be sliced when scheduled

## Dependencies and risks
- Depends on: OMS webhook
"""


def test_parse_and_order_puts_thinnest_vertical_feature_first():
    props = decompose.propose(decompose.parse_features(EPIC))
    assert [p["title"] for p in props] == ["See my order's status", "Measure deflection", "Tell me when it changes", "Delays explained"]
    assert props[0]["depends_on"] == [] and "walking skeleton" in props[0]["reason"] and "moved ahead" in props[0]["reason"]
    assert props[1]["flags"] == ["not_vertical"] and props[1]["depends_on"] == []
    assert props[2]["depends_on"] == [props[0]["slug"]]
    assert props[3]["flags"] == ["needs_slicing"]
    assert props[0]["slug"] == "feature-see-my-order-s-status" and [p["order"] for p in props] == [1, 2, 3, 4]


def test_all_enablers_flags_no_skeleton():
    props = decompose.propose(decompose.parse_features("## Features\n- **Set up CI** (enabler) - size S\n"))
    assert "no_vertical_skeleton" in props[0]["flags"]


def test_decompose_reads_the_epic_file_and_writes_nothing(clean_conn, tmp_path):
    c = clean_conn
    (tmp_path / "docs" / "epics").mkdir(parents=True)
    (tmp_path / "docs" / "epics" / "status.md").write_text(EPIC)
    nodes.create(c, kind="epic", slug="epic-status", title="Status", repo="r", created_by="@pm", path="docs/epics/status.md")
    nodes.create(c, kind="feature", slug="feature-measure-deflection", title="x", repo="r", created_by="@pm")
    before = c.execute("SELECT count(*) AS n FROM node").fetchone()["n"]
    out = decompose.decompose(c, "epic-status", root=tmp_path)
    assert out["committed"] is False and len(out["proposals"]) == 4
    assert "exists" in next(p for p in out["proposals"] if p["slug"] == "feature-measure-deflection")["flags"]
    assert c.execute("SELECT count(*) AS n FROM node").fetchone()["n"] == before


def test_decompose_refusals(clean_conn, tmp_path):
    c = clean_conn
    nodes.create(c, kind="epic", slug="e-nopath", title="t", repo="r", created_by="@pm")
    nodes.create(c, kind="epic", slug="e-out", title="t", repo="r", created_by="@pm", path="../secret.md")
    nodes.create(c, kind="epic", slug="e-gone", title="t", repo="r", created_by="@pm", path="docs/none.md")
    nodes.create(c, kind="feature", slug="f-1", title="t", repo="r", created_by="@pm")
    for slug, exc in (("e-nopath", ValueError), ("e-out", ValueError), ("e-gone", LookupError), ("f-1", ValueError), ("zzz", LookupError)):
        with pytest.raises(exc):
            decompose.decompose(c, slug, root=tmp_path)


def test_epic_decompose_mcp_tool_is_registered_read_only(conn_factory):
    from sdlc_graph import mcp_server

    assert "epic_decompose" in mcp_server.READ_ONLY_TOOLS
    tool = mcp_server.build_server(conn_factory)._tool_manager.get_tool("epic_decompose")
    assert tool is not None and tool.annotations.readOnlyHint is True
