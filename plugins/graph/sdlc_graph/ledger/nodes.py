"""Node and edge CRUD, the node view returned by ``node_get``, and symbol-overlap detection.

Node view shape (``get``), used by ``node_get`` and ``sdlc-graph node get``::

    {"node": Node.to_dict(),
     "open_decisions": [Decision.to_dict(), ...],
     "evidence": {kind: {"count": int, "latest_id": int, "latest_at": iso, "binding": bool}},
     "edges": {"out": [{"rel", "slug", "kind", "state"}], "in": [{"rel", "slug", "kind", "state"}]},
     "transitions": [Transition.to_dict(), ...]}      # oldest first
"""

from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.shapes import NODE_KINDS, NODE_RELS, parse_key

from .models import Decision, Evidence, Node, Transition

TIERS: tuple[str, ...] = ("trivial", "low", "medium", "high")
_BARE_PATH = re.compile(r"^[A-Za-z0-9_.\-+/()\[\]@]+$")


# --------------------------------------------------------------------------- helpers


def _norm_path(path: str) -> str:
    """``./a//b/./c.py`` -> ``a/b/c.py`` (a trailing ``/`` is kept: it marks a directory)."""
    trailing = path.endswith("/")
    parts = [seg for seg in path.split("/") if seg not in ("", ".")]
    out = "/".join(parts)
    return out + "/" if trailing and out else out


def normalise_entry(entry: str) -> str:
    """Canonical spelling of a touched_symbols entry: path segments cleaned of ``./`` and ``//``
    (the path of a symbol key too). Anything that is not a key or a bare path is returned unchanged
    for :func:`validate_touched` to reject."""
    try:
        k = parse_key(entry)
    except ValueError:
        return _norm_path(entry) if _BARE_PATH.match(entry) else entry
    return f"{k['kind']}:{k['qualname']}@{_norm_path(k['path'])}"


def validate_touched(entries: list[str] | None) -> list[str]:
    """Check every ``touched_symbols`` entry is a symbol key (``kind:qualname@path``) or a bare
    repo-relative path (a file, or a directory with or without a trailing ``/``). Entries are
    normalised (``./`` and ``//`` removed) before they are stored, and returned de-duplicated in
    input order.

    Raises:
        ValueError: an entry is neither (absolute path, ``..`` segment, whitespace, bad key).
    """
    out: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"touched_symbols entry must be a non-empty string: {entry!r}")
        if entry != entry.strip():
            raise ValueError(f"touched_symbols entry has surrounding whitespace: {entry!r}")
        raw = entry
        if not raw.startswith("/"):
            entry = normalise_entry(raw)
        try:
            parse_key(entry)
        except ValueError:
            if not _BARE_PATH.match(entry) or entry.startswith("/") or ".." in entry.split("/") or not entry:
                raise ValueError(
                    f"touched_symbols entry is neither a symbol key (kind:qualname@path) nor a repo-relative path: {raw!r}"
                ) from None
        if entry not in out:
            out.append(entry)
    return out


def _entry_path(entry: str) -> tuple[str, bool]:
    """``(path, is_bare)`` of a touched_symbols entry."""
    try:
        return parse_key(entry)["path"], False
    except ValueError:
        return entry, True


def _contains(bare: str, path: str) -> bool:
    """A bare entry is a file or a directory (with or without a trailing ``/``); compared
    case-insensitively so ``App/a.py`` and ``app/a.py`` cannot slip past on a case-folding checkout."""
    bare, path = _norm_path(bare).lower().rstrip("/"), _norm_path(path).lower().rstrip("/")
    return bool(bare) and (bare == path or path.startswith(bare + "/"))


def _key_parts(entry: str) -> tuple[str, str, str] | None:
    try:
        k = parse_key(normalise_entry(entry))
    except ValueError:
        return None
    return k["kind"], k["qualname"], _norm_path(k["path"]).lower()


def _keys_overlap(x: str, y: str) -> bool:
    """Two symbol keys of the same file overlap when equal, when either is the ``module`` key of that
    file (the module is the whole file), or when they are the same qualname as ``class`` and ``model``."""
    kx, ky = _key_parts(x), _key_parts(y)
    if kx is None or ky is None or kx[2] != ky[2]:
        return False
    if kx == ky or "module" in (kx[0], ky[0]):
        return True
    return kx[1] == ky[1] and {kx[0], ky[0]} == {"class", "model"}


def _check_actor(actor: str) -> str:
    from .transitions import actor_class

    return actor_class(actor)


def node_id(conn: psycopg.Connection, slug: str) -> str:
    """Node id (str) for a slug. Raises LookupError."""
    row = conn.execute("SELECT id FROM node WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"unknown node slug: {slug!r}")
    return str(row["id"])


# --------------------------------------------------------------------------- CRUD


def create(conn: psycopg.Connection, *, kind: str, slug: str, title: str, repo: str, created_by: str,
           state: str | None = None, path: str | None = None, commit_sha: str | None = None,
           tier: str | None = None, external_id: str | None = None, touched_symbols: list[str] | None = None,
           capabilities: dict[str, Any] | None = None, acceptance: list[dict[str, Any]] | None = None,
           attrs: dict[str, Any] | None = None, contract_md: str | None = None) -> dict[str, Any]:
    """Insert a node in its machine's initial state (``states[0]``; ``"active"`` for kinds
    without a machine) and record a ``transition`` row (from_state null) by ``created_by``.

    Contracts are created through ``intake.contract_draft`` (which runs the overlap rule), not
    directly; ``create`` itself does not check overlap. ``contract_md`` is the contract.md text the node was
    drafted from (stored as is, in ``node.contract_md``).

    Returns:
        ``Node.to_dict()``.

    Raises:
        ValueError: unknown kind, bad actor string, state not valid for the kind.
        psycopg.errors.UniqueViolation: slug exists.
    """
    from . import transitions

    if kind not in NODE_KINDS:
        raise ValueError(f"unknown node kind {kind!r}; expected one of {', '.join(NODE_KINDS)}")
    _check_actor(created_by)
    for name, value in (("slug", slug), ("title", title), ("repo", repo)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if tier is not None and tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    m = transitions.machine(kind)
    valid = m["states"] if m else [transitions.STATELESS]
    state = state or transitions.initial_state(kind)
    if state not in valid:
        raise ValueError(f"state {state!r} is not a state of {kind}; expected one of {', '.join(valid)}")
    touched = validate_touched(touched_symbols)
    if capabilities is not None and not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    if acceptance is not None and not isinstance(acceptance, list):
        raise ValueError("acceptance must be a list")
    with conn.transaction():
        row = conn.execute(
            """INSERT INTO node (kind, slug, title, repo, path, commit_sha, state, tier, external_id, created_by,
                                 touched_symbols, capabilities, acceptance, attrs, contract_md)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (kind, slug, title, repo, path, commit_sha, state, tier, external_id, created_by, touched,
             Jsonb(capabilities or {}), Jsonb(acceptance or []), Jsonb(attrs or {}), contract_md),
        ).fetchone()
        conn.execute(
            "INSERT INTO transition (node_id, from_state, to_state, actor, reason) VALUES (%s, NULL, %s, %s, 'created')",
            (row["id"], state, created_by),
        )
    return Node.from_row(row).to_dict()


def get_row(conn: psycopg.Connection, slug: str) -> dict[str, Any]:
    """The bare ``node`` row as a dict (no view extras). Raises LookupError."""
    row = conn.execute("SELECT * FROM node WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"unknown node slug: {slug!r}")
    return row


def _edge_items(conn: psycopg.Connection, nid: Any, direction: str) -> list[dict[str, Any]]:
    if direction == "out":
        sql = """SELECT e.rel, n.slug, n.kind, n.state FROM edge e JOIN node n ON n.id = e.dst
                 WHERE e.src = %s ORDER BY e.rel, n.slug"""
    else:
        sql = """SELECT e.rel, n.slug, n.kind, n.state FROM edge e JOIN node n ON n.id = e.src
                 WHERE e.dst = %s ORDER BY e.rel, n.slug"""
    return [dict(r) for r in conn.execute(sql, (nid,)).fetchall()]


def get(conn: psycopg.Connection, slug: str) -> dict[str, Any]:
    """The node view (module docstring).

    Raises:
        LookupError: unknown slug.
    """
    from . import evidence as evidence_mod

    row = get_row(conn, slug)
    nid = row["id"]
    decisions = conn.execute(
        "SELECT * FROM decision WHERE node_id = %s AND resolved_at IS NULL ORDER BY created_at, id", (nid,)
    ).fetchall()
    ev = conn.execute("SELECT * FROM evidence WHERE node_id = %s ORDER BY at, id", (nid,)).fetchall()
    trs = conn.execute("SELECT * FROM transition WHERE node_id = %s ORDER BY at, id", (nid,)).fetchall()
    return {
        "node": Node.from_row(row).to_dict(),
        "open_decisions": [Decision.from_row(d).to_dict() for d in decisions],
        "evidence": evidence_mod.summary([Evidence.from_row(e).to_dict() for e in ev]),
        "edges": {"out": _edge_items(conn, nid, "out"), "in": _edge_items(conn, nid, "in")},
        "transitions": [Transition.from_row(t).to_dict() for t in trs],
    }


def neighbours(conn: psycopg.Connection, slug: str, rel: str | None = None, direction: str = "both") -> list[dict[str, Any]]:
    """Adjacent nodes: ``[{"direction": "out"|"in", "rel", "node": Node.to_dict()}]`` sorted by
    (direction, rel, slug).

    Raises:
        LookupError: unknown slug. ValueError: unknown rel or direction.
    """
    if direction not in ("out", "in", "both"):
        raise ValueError(f"direction must be out, in or both, not {direction!r}")
    if rel is not None and rel not in NODE_RELS:
        raise ValueError(f"unknown rel {rel!r}; expected one of {', '.join(NODE_RELS)}")
    nid = get_row(conn, slug)["id"]
    out: list[dict[str, Any]] = []
    for d, (here, there) in (("out", ("src", "dst")), ("in", ("dst", "src"))):
        if direction not in (d, "both"):
            continue
        rows = conn.execute(
            f"""SELECT e.rel, n.* FROM edge e JOIN node n ON n.id = e.{there}
                WHERE e.{here} = %s AND (%s::text IS NULL OR e.rel = %s)""",
            (nid, rel, rel),
        ).fetchall()
        out.extend({"direction": d, "rel": r["rel"], "node": Node.from_row(r).to_dict()} for r in rows)
    out.sort(key=lambda x: (x["direction"], x["rel"], x["node"]["slug"]))
    return out


def link(conn: psycopg.Connection, src_slug: str, dst_slug: str, rel: str) -> dict[str, Any]:
    """Insert ``src -[rel]-> dst`` (idempotent). Returns ``{"src", "dst", "rel"}`` as slugs.

    Raises:
        LookupError: unknown slug. ValueError: unknown rel, or a self-loop.
    """
    if rel not in NODE_RELS:
        raise ValueError(f"unknown rel {rel!r}; expected one of {', '.join(NODE_RELS)}")
    if src_slug == dst_slug:
        raise ValueError(f"a node cannot link to itself ({src_slug!r} -[{rel}]-> {dst_slug!r})")
    src, dst = node_id(conn, src_slug), node_id(conn, dst_slug)
    conn.execute("INSERT INTO edge (src, dst, rel) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (src, dst, rel))
    return {"src": src_slug, "dst": dst_slug, "rel": rel}


# --------------------------------------------------------------------------- overlap


def symbols_overlap(a: list[str], b: list[str]) -> list[str]:
    """Overlapping entries of two ``touched_symbols`` lists (pure).

    Entries are symbol keys ``"kind:qualname@path"`` or bare repo paths (a whole file, or a
    directory, with or without a trailing ``/``). Two entries overlap when equal after
    normalisation, when one is a bare path that equals or contains the other's path (case
    -insensitive), or when two keys of one file are a ``module`` key against any key, or a ``class``
    and ``model`` of the same qualname. Returns the sorted overlapping entries of ``a``.
    """
    parsed_b = [(y, *_entry_path(normalise_entry(y))) for y in b]
    hits: set[str] = set()
    for x in a:
        px, bare_x = _entry_path(normalise_entry(x))
        for y, py, bare_y in parsed_b:
            if normalise_entry(x) == normalise_entry(y) or (bare_x and _contains(px, py)) \
                    or (bare_y and _contains(py, px)) or (not bare_x and not bare_y and _keys_overlap(x, y)):
                hits.add(x)
                break
    return sorted(hits)


def overlaps(conn: psycopg.Connection, repo: str, touched_symbols: list[str], *, exclude_slug: str | None = None) -> list[dict[str, Any]]:
    """Open contracts in ``repo`` (state not terminal) whose touched_symbols overlap.

    Returns:
        ``[{"slug", "state", "overlap": [entries]}]``. The caller decides whether a
        ``depends_on`` edge excuses each one.
    """
    from .transitions import terminal_states

    if not touched_symbols:
        return []
    closed = sorted(terminal_states("contract"))
    rows = conn.execute(
        """SELECT slug, state, touched_symbols FROM node
           WHERE kind = 'contract' AND repo = %s AND NOT (state = ANY(%s)) AND (%s::text IS NULL OR slug <> %s)
           ORDER BY slug""",
        (repo, closed, exclude_slug, exclude_slug),
    ).fetchall()
    out = []
    for r in rows:
        hit = symbols_overlap(list(touched_symbols), list(r["touched_symbols"] or []))
        if hit:
            out.append({"slug": r["slug"], "state": r["state"], "overlap": hit})
    return out
