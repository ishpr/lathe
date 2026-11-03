"""``depends_on`` between the contracts of a feature: who may be dispatched, and what a dependent branches from.

The edge points from the dependent to the dependency (``nodes.link(conn, dependent, dependency, "depends_on")``;
``neighbours(..., direction="out")`` of the dependent lists what it needs).

Rule. A contract is eligible for dispatch only when every contract it depends on is

* ``merged``, or skipped by a human (nothing to wait for), or
* with ``cfg.stack`` and it is the only one still unmerged: ``in_review`` or ``approved``. The dependent then
  branches from that contract's branch (``sdlc/<dependency>``) and its pull request targets that branch
  (stacked pull requests).

Everything else blocks the dependent, one ``waiting on <slug> (<state>)`` line per blocking contract. Two or
more unmerged dependencies block even with ``--stack``: a branch has one parent, so the dependent waits until
at most one is left. A cycle is refused by the loop with a P1 decision (:class:`DependencyCycle`).
"""

from __future__ import annotations

from typing import Any

import psycopg

from sdlc_graph.ledger import nodes

from . import gitops, store

#: Dependency states a stacked dependent may build on (the code is pushed and has a pull request).
STACKABLE: frozenset[str] = frozenset({"in_review", "approved"})


class DependencyCycle(ValueError):
    """``depends_on`` cycle among the contracts of a feature (``slugs``: the cycle and what waits behind it)."""

    def __init__(self, slugs: list[str]):
        self.slugs = sorted(slugs)
        super().__init__(f"depends_on cycle among {self.slugs}")


def dependencies(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    """The contracts ``slug`` depends on (node rows)."""
    return [n["node"] for n in nodes.neighbours(conn, slug, rel="depends_on", direction="out") if n["node"]["kind"] == "contract"]


def eligibility(conn: psycopg.Connection, cfg: Any, slug: str) -> dict[str, Any]:
    """``{"blocking": [{"slug", "state"}], "lines": ["waiting on <slug> (<state>)"], "stack_on": slug | None}``."""
    unmerged = [d for d in dependencies(conn, slug) if d["state"] != "merged" and not store.skipped(conn, d["slug"])]
    stack = bool(getattr(cfg, "stack", False))
    if not unmerged or (stack and len(unmerged) == 1 and unmerged[0]["state"] in STACKABLE):
        return {"blocking": [], "lines": [], "stack_on": unmerged[0]["slug"] if unmerged else None}
    blocking = [{"slug": d["slug"], "state": d["state"]} for d in sorted(unmerged, key=lambda d: d["slug"])]
    return {"blocking": blocking, "lines": [f"waiting on {b['slug']} ({b['state']})" for b in blocking], "stack_on": None}


def base_for(conn: psycopg.Connection, cfg: Any, slug: str, default: str) -> dict[str, Any]:
    """Where ``slug``'s worktree branches from and where its pull request goes:
    ``{"base", "pr_base", "stacked_on"}`` (the dependency's branch when stacked, else ``default`` for both)."""
    on = eligibility(conn, cfg, slug)["stack_on"]
    branch = gitops.branch_for(on) if on else default
    return {"base": branch, "pr_base": branch, "stacked_on": on}
