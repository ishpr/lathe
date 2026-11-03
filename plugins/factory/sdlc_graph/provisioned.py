"""The ``graph_provisioned`` evidence row on the repository's ``repo`` node (``/factory:graph provision``).

One ``repo`` node per repository (slug ``repo-<owner>-<name>``, created on first use). :func:`record` appends
``graph_provisioned {provider, where, schema}`` unless a row with the same three values is already there, so a second
provision run reports ``already_there`` and the evidence stays one row per database. Evidence is local and advisory.
"""

from __future__ import annotations

import re
from typing import Any

from sdlc_graph.ledger import evidence, nodes

KIND = "graph_provisioned"


def repo_slug(repo: str) -> str:
    """``acme/sample-app`` -> ``repo-acme-sample-app``."""
    return "repo-" + (re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-") or "unnamed")


def ensure_repo_node(conn: Any, repo: str, actor: str) -> str:
    """The slug of the repo node for ``repo``, created when missing."""
    slug = repo_slug(repo)
    if conn.execute("SELECT 1 FROM node WHERE slug = %s", (slug,)).fetchone() is None:
        nodes.create(conn, kind="repo", slug=slug, title=repo, repo=repo, created_by=actor)
    return slug


def record(conn: Any, *, repo: str, provider: str, where: str, schema: str, actor: str,
           extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{"status": "recorded"|"already_there", "slug", "evidence_id", "payload"}``."""
    slug = ensure_repo_node(conn, repo, actor)
    payload = {"provider": provider, "where": where, "schema": schema, **(extra or {})}
    for row in evidence.list_for(conn, slug, KIND):
        p = row.get("payload") or {}
        if (p.get("provider"), p.get("where"), p.get("schema")) == (provider, where, schema):
            return {"status": "already_there", "slug": slug, "evidence_id": row.get("id"), "payload": p}
    row = evidence.add(conn, slug, KIND, payload)
    return {"status": "recorded", "slug": slug, "evidence_id": row.get("id"), "payload": payload}
