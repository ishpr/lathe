"""One traversal, two dialects (DESIGN 16.9 rule 5): ``lifecycle.tree`` in Postgres and ``QUERIES["lifecycle_tree"]`` in
Spanner GQL answer the same question on the same fixture. The query text is also held to ``queries.gql``."""

from __future__ import annotations

import re

import pytest
from graph_fakes import _emulator_running

from sdlc_graph.ledger import lifecycle, nodes
from sdlc_graph.spanner import ddl, export

REPO = "o/parity"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_the_named_query_is_the_text_in_queries_gql():
    gql = ddl.sample_queries()
    block = gql.split("-- 4.", 1)[1]
    body = "\n".join(line for line in block.splitlines()[1:] if not line.startswith("--")).strip().rstrip(";")
    assert _flat(body) == _flat(export.QUERIES["lifecycle_tree"].replace("GRAPH SdlcGraph ", "GRAPH SdlcGraph\n", 1))
    assert "{1,6}" in export.QUERIES["lifecycle_tree"] and lifecycle.MAX_DEPTH == 6
    assert set(lifecycle.TREE_RELS) == {"decomposes_into", "promoted_to"}
    for rel in lifecycle.TREE_RELS:
        assert f"'{rel}'" in export.QUERIES["lifecycle_tree"]
    assert "{slug: 'it\\'s'}" in export.render_query("lifecycle_tree", slug="it's")


def _fixture(c) -> None:
    for kind, slug, state in (("idea", "idea-1", "promoted"), ("epic", "epic-1", "accepted"), ("feature", "feat-1", "building"),
                              ("feature", "feat-2", "ready"), ("story", "story-1", "building"), ("contract", "ct-1", "implementing"),
                              ("contract", "ct-2", "merged"), ("finding", "find-1", "open"), ("feature", "other-root", "proposed"),
                              ("feature", "deep-0", "proposed"), ("feature", "deep-1", "proposed"), ("feature", "deep-2", "proposed"),
                              ("feature", "deep-3", "proposed"), ("feature", "deep-4", "proposed"), ("feature", "deep-5", "proposed"),
                              ("feature", "deep-6", "proposed"), ("feature", "deep-7", "proposed")):
        nodes.create(c, kind=kind, slug=slug, title=slug, repo=REPO, created_by="@pm", state=state)
    for a, b, rel in (("idea-1", "epic-1", "promoted_to"), ("epic-1", "feat-1", "decomposes_into"),
                      ("epic-1", "feat-2", "decomposes_into"), ("feat-1", "story-1", "decomposes_into"),
                      ("story-1", "ct-1", "decomposes_into"), ("story-1", "ct-2", "decomposes_into"),
                      ("feat-2", "ct-2", "decomposes_into"),  # a diamond onto ct-2
                      ("epic-1", "find-1", "depends_on"),  # not a lifecycle rel: neither dialect follows it
                      ("ct-2", "feat-1", "decomposes_into"),  # a cycle back up
                      ("other-root", "ct-1", "decomposes_into")):
        nodes.link(c, a, b, rel)
    for i in range(7):
        nodes.link(c, f"deep-{i}", f"deep-{i + 1}", "decomposes_into")


def _postgres(c, slug: str) -> set[tuple[str, str, str]]:
    return {(r["slug"], r["kind"], r["state"]) for r in lifecycle.tree(c, slug)}


def test_the_postgres_side_of_the_fixture(clean_conn):
    c = clean_conn
    _fixture(c)
    assert _postgres(c, "idea-1") == {("epic-1", "epic", "accepted"), ("feat-1", "feature", "building"), ("feat-2", "feature", "ready"),
                                      ("story-1", "story", "building"), ("ct-1", "contract", "implementing"),
                                      ("ct-2", "contract", "merged")}
    assert {s for s, _k, _st in _postgres(c, "deep-0")} == {f"deep-{i}" for i in range(1, 7)}  # six hops, no further


@pytest.mark.spanner
@pytest.mark.parametrize("root", ["idea-1", "feat-2", "deep-0", "ct-1"])
def test_lifecycle_tree_is_the_same_in_postgres_and_spanner(clean_conn, root):
    reason = _emulator_running()
    if reason:
        pytest.skip(reason)
    c = clean_conn
    _fixture(c)
    out = export.export(c, database="pytest_lifecycle", instance="pytest", queries={"lifecycle_tree": {"slug": root}})
    got = {tuple(r) for r in out["queries"]["lifecycle_tree"]["rows"]}
    assert {(s, k, st) for s, k, st in got} == _postgres(c, root)
