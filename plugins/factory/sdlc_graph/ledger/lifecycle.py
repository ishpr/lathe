"""The lifecycle view (DESIGN 16.1, 16.9): seven stages, computed from the ledger, never stored.

``lifecycle.json`` maps each ``(kind, state)`` of every machine to a stage (``map``), lists the killed exits
(``killed``: parked, rejected, split, superseded count as Done, flagged killed), the states whose stage comes from
their children (``derived``) and the kinds that sit outside the funnel (``outside``). :func:`stage_of` reads it;
:func:`tree` walks ``decomposes_into`` and ``promoted_to`` below a node with a recursive CTE (depth at most
:data:`MAX_DEPTH`, a path array as the cycle guard); :func:`lifecycle_view` puts the two together.

The same traversal is ``QUERIES["lifecycle_tree"]`` in ``sdlc_graph/spanner/export.py`` over the Spanner property
graph; tests/graph/test_lifecycle_parity.py runs both on one fixture.

Also here: :func:`backfill` (touched_symbols for stories and accepted records, dry run unless ``apply``) and
:func:`resolve` (re-resolve the symbol keys of the ledger against a snapshot and write ``symbols_resolved`` evidence).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

import psycopg

from . import LIFECYCLE_PATH

#: The edge relations a lifecycle tree follows.
TREE_RELS: tuple[str, ...] = ("decomposes_into", "promoted_to")
MAX_DEPTH = 6


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(Path(LIFECYCLE_PATH).read_text(encoding="utf-8"))


def stages() -> tuple[str, ...]:
    return tuple(_data()["stages"])


#: The seven stages in order (tests/graph/test_registry_conformance.py holds the board to this list).
STAGES: tuple[str, ...] = stages()


def stage_of(kind: str, state: str) -> str | None:
    """The stage of a ``(kind, state)`` pair, or None for a kind outside the funnel and for a derived state (a
    parent's stage comes from its children: :func:`parent_stage`)."""
    return _data()["map"].get(kind, {}).get(state)


def is_killed(kind: str, state: str) -> bool:
    """True for a killed exit (parked, rejected, split, superseded): a successful exit that counts as Done."""
    return state in _data()["killed"].get(kind, [])


def is_derived(kind: str, state: str) -> bool:
    return state in _data()["derived"].get(kind, [])


class ChildStage(TypedDict):
    slug: str
    kind: str
    state: str
    stage: str | None
    killed: bool


def parent_stage(children: list[ChildStage]) -> tuple[str, dict[str, Any]]:
    """TODO(product): the stage a parent (epic in accepted/in_progress, feature) shows on the board, and its
    progress, e.g. ("build", {"done": 3, "total": 5, "label": "3/5 contracts merged", "straggler": "ct-x"}).
    DESIGN 16.1 proposes: the least advanced OPEN child's stage plus a count; killed children count as done.
    Decide: (1) least-advanced vs median child (one straggler parks an epic in Design for weeks);
    (2) killed children in the denominator or not ("3/5" vs "3/3 of what remains"); (3) immediate children
    or leaves (an epic over features over stories over contracts: whose count is the label?); (4) no children:
    the parent's own state, or "brief"; (5) all children killed: "done" flagged killed vs shipped, which the
    funnel must tell apart; (6) whether an escalated (P0/P1) child pins the parent regardless of stage."""
    raise NotImplementedError("parent_stage is a product rule; see docstring")


# --------------------------------------------------------------------------- the tree

_TREE_SQL = """
WITH RECURSIVE t AS (
  SELECT n.id, n.slug, n.kind, n.state, 0 AS depth, ARRAY[n.id] AS path, NULL::text AS parent
    FROM node n WHERE n.slug = %(slug)s
  UNION ALL
  SELECT c.id, c.slug, c.kind, c.state, t.depth + 1, t.path || c.id, t.slug
    FROM t
    JOIN edge e ON e.src = t.id AND e.rel = ANY(%(rels)s)
    JOIN node c ON c.id = e.dst
   WHERE t.depth < %(max_depth)s AND NOT c.id = ANY(t.path)
)
SELECT slug, kind, state, depth, parent FROM t WHERE depth > 0 ORDER BY depth, parent, slug
"""


def tree(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    """Every node below ``slug`` over ``decomposes_into`` and ``promoted_to``, at most :data:`MAX_DEPTH` hops down, as
    ``[{slug, kind, state, depth, parent}]`` (the root is not a row). A node reachable by two paths appears once per
    path; a cycle is cut where it would revisit a node on its own path.

    Raises:
        LookupError: unknown slug.
    """
    if conn.execute("SELECT 1 FROM node WHERE slug = %s", (slug,)).fetchone() is None:
        raise LookupError(f"unknown node slug: {slug!r}")
    return [dict(r) for r in conn.execute(_TREE_SQL, {"slug": slug, "rels": list(TREE_RELS), "max_depth": MAX_DEPTH}).fetchall()]


def _child_stage(row: dict[str, Any]) -> ChildStage:
    return {"slug": row["slug"], "kind": row["kind"], "state": row["state"],
            "stage": stage_of(row["kind"], row["state"]), "killed": is_killed(row["kind"], row["state"])}


def _stage_and_progress(kind: str, state: str, kids: list[ChildStage]) -> tuple[str | None, dict[str, Any]]:
    if is_derived(kind, state):
        try:
            return parent_stage(kids)
        except NotImplementedError:
            return None, {"todo": "parent_stage"}
    by_stage: dict[str, int] = {}
    for k in kids:
        if k["stage"]:
            by_stage[k["stage"]] = by_stage.get(k["stage"], 0) + 1
    progress: dict[str, Any] = {"children": len(kids), "killed": sum(1 for k in kids if k["killed"]), "by_stage": by_stage}
    return stage_of(kind, state), progress


def lifecycle_view(conn: psycopg.Connection, slug: str) -> dict[str, Any]:
    """``{slug, kind, state, stage, killed, progress, children}`` for a node and everything below it.

    ``children`` nests (each child has the same keys without ``progress``'s parent rule applied twice: a child that
    is a parent has its own ``children``). While :func:`parent_stage` is not decided, a derived parent answers
    ``stage: None`` and ``progress: {"todo": "parent_stage"}`` and the view still works.

    Raises:
        LookupError: unknown slug.
    """
    root = conn.execute("SELECT slug, kind, state FROM node WHERE slug = %s", (slug,)).fetchone()
    if root is None:
        raise LookupError(f"unknown node slug: {slug!r}")
    rows = tree(conn, slug)
    by_parent: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if (r["parent"], r["slug"]) not in seen:
            seen.add((r["parent"], r["slug"]))
            by_parent.setdefault(r["parent"], []).append(r)

    def build(node: dict[str, Any], trail: frozenset[str]) -> dict[str, Any]:
        below = [r for r in by_parent.get(node["slug"], []) if r["slug"] not in trail]
        kids = [build(r, trail | {node["slug"]}) for r in below]
        stage, progress = _stage_and_progress(node["kind"], node["state"], [_child_stage(r) for r in below])
        return {"slug": node["slug"], "kind": node["kind"], "state": node["state"], "stage": stage,
                "killed": is_killed(node["kind"], node["state"]), "progress": progress, "children": kids}

    return build(dict(root), frozenset())


# --------------------------------------------------------------------------- backfill and re-resolution


def backfill(conn: psycopg.Connection, repo: str, *, apply: bool = False, root: str | Path = ".") -> dict[str, Any]:
    """touched_symbols for the nodes that predate them. A dry run unless ``apply``; running it again changes nothing.

    * a story gets the union of its contracts' keys (``decomposes_into`` children of kind contract), kept in the
      order the contracts list them;
    * an accepted decision record whose file (``node.path`` under ``root``) has a front-matter ``paths:`` list gets
      those globs resolved against the repo's newest snapshot: each glob becomes the files it matches there (bare
      paths, which ``nodes.validate_touched`` accepts); a glob that matches nothing is reported, not stored.

    Findings carry their symbols from their adapter when they are posted, so they need no backfill.

    Returns:
        ``{"repo", "apply", "stories": [{slug, added}], "records": [{slug, added, unmatched}], "changed": n}``.
    """
    from . import nodes

    out: dict[str, Any] = {"repo": repo, "apply": apply, "stories": [], "records": [], "changed": 0}

    def merge(row: dict[str, Any], add: list[str]) -> list[str]:
        have = list(row["touched_symbols"] or [])
        return nodes.validate_touched(have + [a for a in add if a not in have])

    for s in conn.execute("SELECT id, slug, touched_symbols FROM node WHERE kind = 'story' AND repo = %s ORDER BY slug", (repo,)).fetchall():
        keys: list[str] = []
        for c in conn.execute(
                "SELECT n.touched_symbols FROM edge e JOIN node n ON n.id = e.dst WHERE e.src = %s AND e.rel = 'decomposes_into' "
                "AND n.kind = 'contract' ORDER BY n.slug", (s["id"],)).fetchall():
            keys += [k for k in c["touched_symbols"] or [] if k not in keys]
        merged = merge(s, keys)
        added = [k for k in merged if k not in (s["touched_symbols"] or [])]
        if added:
            out["stories"].append({"slug": s["slug"], "added": added})
            if apply:
                conn.execute("UPDATE node SET touched_symbols = %s, updated_at = now() WHERE id = %s", (merged, s["id"]))

    snap = conn.execute(
        "SELECT id FROM snapshot WHERE repo = %s ORDER BY committed_at DESC NULLS LAST, ancestors DESC NULLS LAST, "
        "indexed_at DESC, id DESC LIMIT 1", (repo,)).fetchone()
    files: list[str] = []
    if snap is not None:
        from sdlc_graph.codegraph import snapshot as snapmod

        snapmod.materialise(conn, snap["id"])
        files = sorted({r["path"] for r in conn.execute("SELECT DISTINCT path FROM sym WHERE snapshot_id = %s", (snap["id"],)).fetchall()})
    for r in conn.execute("SELECT id, slug, path, touched_symbols FROM node WHERE kind = 'decision' AND state = 'accepted' "
                          "AND repo = %s AND path IS NOT NULL ORDER BY slug", (repo,)).fetchall():
        file = Path(root) / r["path"].split("#", 1)[0]
        if not file.is_file():
            continue
        from sdlc_graph.codegraph.queries import _glob_regex
        from sdlc_graph.index.chunk import parse_rule

        globs, _ = parse_rule(file.read_text(encoding="utf-8"))
        found: list[str] = []
        unmatched: list[str] = []
        for g in globs:
            rx = _glob_regex(g)
            hits = [f for f in files if rx is not None and rx.match(f)]
            (found if hits else unmatched).extend(hits or [g])
        merged = merge(r, found)
        added = [k for k in merged if k not in (r["touched_symbols"] or [])]
        if added or unmatched:
            out["records"].append({"slug": r["slug"], "added": added, "unmatched": unmatched})
        if added and apply:
            conn.execute("UPDATE node SET touched_symbols = %s, updated_at = now() WHERE id = %s", (merged, r["id"]))
    out["changed"] = sum(1 for x in out["stories"] if x["added"]) + sum(1 for x in out["records"] if x["added"])
    return out


def resolve(conn: psycopg.Connection, repo: str, sha: str, *, actor: str = "ci:lifecycle-resolve") -> dict[str, Any]:
    """Re-resolve every symbol key of the repo's nodes against the snapshot of ``sha`` (DESIGN 16.9 rule 4) and write a
    ``symbols_resolved`` evidence row on each node where a key moved or vanished. A key that exists at the snapshot is
    unchanged; one whose kind and qualname exist at exactly one other path is followed there (``resolved``); anything
    else is ``vanished`` (a bare path vanishes when no symbol sits under it). A node whose newest ``symbols_resolved``
    row for this sha says the same thing gets no second row, so it can run after every ``snapshot.index``.

    Returns:
        ``{"repo", "sha", "snapshot_id", "nodes": [{slug, resolved, vanished}]}``.

    Raises:
        LookupError: no snapshot for the repo and sha.
    """
    from sdlc_graph.codegraph import snapshot as snapmod
    from sdlc_graph.shapes import parse_key

    from . import evidence

    sid = snapmod.resolve_snapshot(conn, repo, sha)
    snapmod.materialise(conn, sid)
    full = conn.execute("SELECT commit_sha FROM snapshot WHERE id = %s", (sid,)).fetchone()["commit_sha"]
    present = {(r["kind"], r["qualname"], r["path"]) for r in conn.execute(
        "SELECT kind, qualname, path FROM sym WHERE snapshot_id = %s", (sid,)).fetchall()}
    where: dict[tuple[str, str], list[str]] = {}
    for kind, qual, path in present:
        where.setdefault((kind, qual), []).append(path)
    paths = {p for _k, _q, p in present}
    out: dict[str, Any] = {"repo": repo, "sha": full, "snapshot_id": sid, "nodes": []}
    for n in conn.execute("SELECT id, slug, touched_symbols FROM node WHERE repo = %s AND cardinality(touched_symbols) > 0 "
                          "ORDER BY slug", (repo,)).fetchall():
        resolved: dict[str, str] = {}
        vanished: list[str] = []
        for entry in n["touched_symbols"]:
            try:
                k = parse_key(entry)
            except ValueError:
                bare = entry.rstrip("/")
                if not any(p == bare or p.startswith(bare + "/") for p in paths):
                    vanished.append(entry)
                continue
            if (k["kind"], k["qualname"], k["path"]) in present:
                continue
            moved = where.get((k["kind"], k["qualname"]), [])
            if len(moved) == 1:
                resolved[entry] = f"{k['kind']}:{k['qualname']}@{moved[0]}"
            else:
                vanished.append(entry)
        if not resolved and not vanished:
            continue
        payload = {"sha": full, "resolved": resolved, "vanished": sorted(vanished)}
        last = [e for e in evidence.list_for(conn, n["slug"], "symbols_resolved")
                if (e["payload"] or {}).get("sha") == full]
        if last and last[-1]["payload"] == payload:
            continue
        evidence.add(conn, n["slug"], "symbols_resolved", payload, source="local", actor=actor)
        out["nodes"].append({"slug": n["slug"], "resolved": resolved, "vanished": sorted(vanished)})
    return out
