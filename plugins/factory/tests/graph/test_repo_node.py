"""Indexing a repository gives it a ledger repo node, so repo-level evidence always has somewhere to go.

Found in the FDE panel rehearsal (1.0.0): init posted review_pack on the repo, the ledger had no nodes (the database
was already up, so /factory:graph provision never created one), and both evidence_add calls failed.
"""

from __future__ import annotations

import json

from sdlc_graph import cli, provisioned
from sdlc_graph.ledger import evidence


def test_index_creates_the_repo_node_and_reports_its_slug(clean_conn, synthetic_repo, capsys):
    assert cli.main(["index", "--repo", str(synthetic_repo), "--sha", "HEAD", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    slug = out["repo_node"]
    assert slug == provisioned.repo_slug(out["repo"])
    row = clean_conn.execute("SELECT kind FROM node WHERE slug = %s", (slug,)).fetchone()
    assert row is not None and row["kind"] == "repo"


def test_review_pack_evidence_can_be_posted_on_the_indexed_repo_node(clean_conn, synthetic_repo, capsys):
    assert cli.main(["index", "--repo", str(synthetic_repo), "--sha", "HEAD", "--json"]) == 0
    slug = json.loads(capsys.readouterr().out)["repo_node"]
    payload = {"pack": "onboarding", "path": "docs/reviews/2026-11-04-onboarding.html", "sha256": "a" * 64,
               "shown_for": {"transition": "active"}}
    evidence.add(clean_conn, slug, "review_pack", payload)
    assert len(evidence.list_for(clean_conn, slug, "review_pack")) == 1


def test_indexing_twice_keeps_one_repo_node(clean_conn, synthetic_repo, capsys):
    for _ in range(2):
        assert cli.main(["index", "--repo", str(synthetic_repo), "--sha", "HEAD", "--json"]) == 0
    count = clean_conn.execute("SELECT count(*) AS n FROM node WHERE kind = 'repo'").fetchone()["n"]
    assert count == 1  # negative case: a re-index must not create a second node
