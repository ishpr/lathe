"""Read-only queries over one code-graph snapshot. Nothing here writes (except the cache that
``snapshot.materialise`` fills for delta snapshots).

Symbol arguments accept any of: a key string ``"kind:qualname@path"``, a qualname
(``"app.routes.a.list_items"``), or a repo-relative path (``"app/routes/a.py"`` means that
file's module symbol). An ambiguous qualname raises LookupError listing the candidates.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg

from sdlc_graph.codegraph import snapshot as _snapshot
from sdlc_graph.shapes import SYM_RELS, AffectedSet, Sym, key_dict, key_str, parse_key

_SYM_COLS = "id, snapshot_id, kind, name, qualname, path, line_start, line_end, attrs"
#: edge rels walked in reverse by ``impact``
IMPACT_RELS = ("imports", "calls", "tests", "handles", "calls_api", "emits", "renders", "routes_to")


def _ready(conn: psycopg.Connection, snapshot_id: int) -> dict[str, Any]:
    """Materialise a delta snapshot if needed; return the snapshot row (LookupError if unknown)."""
    snap = _snapshot._get_snapshot(conn, snapshot_id)
    _snapshot.materialise(conn, snapshot_id)
    return snap


def _sym_by_id(conn: psycopg.Connection, ids: list[int]) -> dict[int, Sym]:
    if not ids:
        return {}
    rows = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE id = ANY(%s)", (list(ids),)).fetchall()
    return {r["id"]: r for r in rows}


def find_symbol(conn: psycopg.Connection, snapshot_id: int, symbol: str) -> Sym:
    """Resolve one symbol argument (see module docstring) to its Sym row (with ``id``).

    Raises:
        LookupError: not found, or ambiguous (message lists up to 10 candidate keys).
    """
    _ready(conn, snapshot_id)
    try:
        key = parse_key(symbol)
    except ValueError:
        key = None
    if key is not None:
        row = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE snapshot_id = %s AND kind = %s AND qualname = %s "
                           "AND path = %s", (snapshot_id, key["kind"], key["qualname"], key["path"])).fetchone()
        if row is None:
            raise LookupError(f"no symbol {symbol!r} in snapshot {snapshot_id}")
        return row
    rows = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE snapshot_id = %s AND kind = 'module' AND path = %s",
                        (snapshot_id, symbol)).fetchall()
    if not rows:
        rows = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE snapshot_id = %s AND qualname = %s ORDER BY kind",
                            (snapshot_id, symbol)).fetchall()
    if not rows:
        raise LookupError(f"no symbol {symbol!r} in snapshot {snapshot_id}")
    if len(rows) > 1:
        cands = ", ".join(key_str(r) for r in rows[:10])
        raise LookupError(f"{symbol!r} is ambiguous in snapshot {snapshot_id}; use one of: {cands}")
    return rows[0]


def _edge_dict(row: dict[str, Any], syms: dict[int, Sym]) -> dict[str, Any]:
    return {"src": key_dict(syms[row["src"]]), "dst": key_dict(syms[row["dst"]]), "rel": row["rel"],
            "attrs": row["attrs"], "src_id": row["src"], "dst_id": row["dst"]}


def deps(conn: psycopg.Connection, snapshot_id: int, symbol: str, *, depth: int = 1,
         direction: str = "out", rels: list[str] | None = None) -> dict[str, Any]:
    """Dependency neighbourhood of a symbol.

    Args:
        direction: ``"out"`` (what it depends on: follow src->dst), ``"in"`` (what depends on
            it: dst->src) or ``"both"``.
        depth: hops, 1..6. rels: restrict to these edge rels (default all).

    Returns:
        ``{"snapshot_id", "root": Sym, "nodes": [Sym + {"depth": int}], "edges": [Edge with
        src/dst SymKeys and src_id/dst_id]}``; nodes exclude the root, sorted by (depth, key).

    Raises:
        LookupError: unknown symbol. ValueError: bad direction/depth/rel.
    """
    if direction not in ("out", "in", "both"):
        raise ValueError(f"direction must be out, in or both, not {direction!r}")
    if not isinstance(depth, int) or not 1 <= depth <= 6:
        raise ValueError(f"depth must be 1..6, not {depth!r}")
    bad = [r for r in (rels or []) if r not in SYM_RELS]
    if bad:
        raise ValueError(f"unknown rel(s) {bad}; valid: {', '.join(SYM_RELS)}")
    root = find_symbol(conn, snapshot_id, symbol)
    rel_list = list(rels) if rels else list(SYM_RELS)
    seen = {root["id"]: 0}
    edges: dict[tuple[int, int, str], dict[str, Any]] = {}
    frontier = [root["id"]]
    for level in range(1, depth + 1):
        if not frontier:
            break
        rows: list[dict[str, Any]] = []
        if direction in ("out", "both"):
            rows += conn.execute("SELECT src, dst, rel, attrs FROM sym_edge WHERE snapshot_id = %s AND src = ANY(%s) "
                                 "AND rel = ANY(%s)", (snapshot_id, frontier, rel_list)).fetchall()
        if direction in ("in", "both"):
            rows += conn.execute("SELECT src, dst, rel, attrs FROM sym_edge WHERE snapshot_id = %s AND dst = ANY(%s) "
                                 "AND rel = ANY(%s)", (snapshot_id, frontier, rel_list)).fetchall()
        nxt: list[int] = []
        for r in rows:
            edges[(r["src"], r["dst"], r["rel"])] = r
            for end in (r["src"], r["dst"]):
                if end not in seen:
                    seen[end] = level
                    nxt.append(end)
        frontier = nxt
    syms = _sym_by_id(conn, list(seen))
    nodes = [{**syms[i], "depth": d} for i, d in seen.items() if i != root["id"]]
    nodes.sort(key=lambda s: (s["depth"], key_str(s)))
    edge_list = sorted((_edge_dict(r, syms) for r in edges.values()),
                       key=lambda e: (key_str(e["src"]), key_str(e["dst"]), e["rel"]))
    return {"snapshot_id": snapshot_id, "root": syms[root["id"]], "nodes": nodes, "edges": edge_list}


def _path_symbols(conn: psycopg.Connection, snapshot_id: int, paths: list[str]) -> list[Sym]:
    found: dict[int, Sym] = {}
    for p in paths:
        p = p.lstrip("./") if p.startswith("./") else p
        if p.endswith("/"):
            like = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE snapshot_id = %s AND path LIKE %s",
                                (snapshot_id, like)).fetchall()
        else:
            rows = conn.execute(f"SELECT {_SYM_COLS} FROM sym WHERE snapshot_id = %s AND path = %s",
                                (snapshot_id, p)).fetchall()
        found.update({r["id"]: r for r in rows})
    return list(found.values())


#: ``depth`` given to symbols found by the low-confidence module-level closure of a touched function,
#: method or class (depth = CLOSURE_DEPTH + hops). They sort after every directly reached test.
CLOSURE_DEPTH = 100


def _is_test_module_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if not path.endswith(".py") or name in ("conftest.py", "__init__.py"):
        return False
    return "tests" in parts[:-1] or "test" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py")


def impact(conn: psycopg.Connection, snapshot_id: int, *, paths: list[str] | None = None,
           symbols: list[str] | None = None, depth: int = 4) -> AffectedSet:
    """Static blast radius: every symbol reachable by walking ``imports``, ``calls`` and ``tests``
    edges in REVERSE (dst->src) from the touched symbols, up to ``depth`` hops, plus the routes
    exposed by affected handlers (``exposes_route``) or declared in an affected module, the test symbols among them (as pytest node
    ids from ``attrs.nodeid``; a reached test MODULE contributes every test it defines) and the
    owners of every affected path.

    Beyond the plain reverse walk (each addition is a real way code is reached):

    * a reached ``conftest.py`` affects every test module beneath its directory (pytest imports it
      for all of them), at one hop past the conftest;
    * a test that sends a literal HTTP request to a route (``tests`` edge test -> route, made by the
      Python extractor's ``link``) is affected when the route's handler is, one hop past the handler;
    * for a touched function, method or class (a symbol argument), the module-level closure of its
      defining module is added at low confidence with ``depth >= CLOSURE_DEPTH``: calls through
      instances and over HTTP are not all resolved, so this keeps recall close to the path-level
      query, ordered after every directly reached test.

    ``paths`` select every symbol whose path equals the path (or lies under it, for a directory
    ending in ``/``). At least one of paths/symbols is required.

    Returns:
        AffectedSet without ``reasons`` (``impact.affected.affected`` adds them).

    Raises:
        ValueError: neither paths nor symbols. LookupError: an unknown symbol argument.
    """
    if not paths and not symbols:
        raise ValueError("impact needs at least one path or symbol")
    if not isinstance(depth, int) or depth < 0:
        raise ValueError(f"depth must be >= 0, not {depth!r}")
    snap = _ready(conn, snapshot_id)
    touched: dict[int, Sym] = {}
    for sym in _path_symbols(conn, snapshot_id, list(paths or [])):
        touched[sym["id"]] = sym
    explicit: list[Sym] = []
    for text in symbols or []:
        sym = find_symbol(conn, snapshot_id, text)
        touched[sym["id"]] = sym
        explicit.append(sym)
    reached: dict[int, int] = {i: 0 for i in touched}

    def walk(seeds: dict[int, int], limit: int) -> None:
        """Reverse-walk from ``seeds`` ({id: starting depth}), adding unreached sources at +1 per hop."""
        for i, d in seeds.items():
            if i not in reached or d < reached[i]:
                reached[i] = d
        frontier = dict(seeds)
        for _ in range(limit):
            if not frontier:
                break
            rows = conn.execute("SELECT src, dst FROM sym_edge WHERE snapshot_id = %s AND dst = ANY(%s) "
                                "AND rel = ANY(%s)", (snapshot_id, list(frontier), list(IMPACT_RELS))).fetchall()
            nxt: dict[int, int] = {}
            for r in rows:
                if r["src"] not in reached:
                    reached[r["src"]] = frontier[r["dst"]] + 1
                    nxt[r["src"]] = reached[r["src"]]
            frontier = nxt

    walk({i: 0 for i in touched}, depth)

    # low-confidence closure: a touched function/method/class also touches its module's importers
    module_by_path: dict[str, int] = {}
    closure_paths = sorted({s["path"] for s in explicit if s["kind"] in ("function", "method", "class", "test")})
    if closure_paths:
        for r in conn.execute("SELECT id, path FROM sym WHERE snapshot_id = %s AND kind = 'module' AND path = ANY(%s)",
                              (snapshot_id, closure_paths)):
            module_by_path[r["path"]] = r["id"]
        seeds = {mid: CLOSURE_DEPTH for mid in module_by_path.values() if mid not in reached}
        if seeds:
            walk(seeds, depth)

    # Foldkit programs (TypeScript extractor): an update module that changed handles its Message tags, so the
    # story tests of those tags are affected; and a program is one unit, so a reached file of a program affects the
    # program's views (and, through the walk, the scene tests that test them) at low confidence.
    handled = conn.execute("SELECT e.dst, e.src FROM sym_edge e WHERE e.snapshot_id = %s AND e.rel = 'handles' "
                           "AND e.src = ANY(%s)", (snapshot_id, list(reached))).fetchall()
    seeds = {r["dst"]: reached[r["src"]] + 1 for r in handled if r["dst"] not in reached}
    if seeds:
        walk(seeds, depth)
    programs = sorted({(s.get("attrs") or {}).get("program") for s in _sym_by_id(conn, list(reached)).values()
                       if (s.get("attrs") or {}).get("program")})
    if programs:
        seeds = {r["id"]: CLOSURE_DEPTH for r in conn.execute(
            "SELECT id FROM sym WHERE snapshot_id = %s AND kind = 'view' AND attrs->>'program' = ANY(%s)",
            (snapshot_id, programs)) if r["id"] not in reached}
        if seeds:
            walk(seeds, depth)

    # a reached conftest.py is imported by every test beneath it
    conftests = conn.execute(
        "SELECT id, path FROM sym WHERE snapshot_id = %s AND kind = 'module' AND id = ANY(%s) "
        "AND (path = 'conftest.py' OR path LIKE '%%/conftest.py')", (snapshot_id, list(reached))).fetchall()
    if conftests:
        test_modules = conn.execute("SELECT id, path FROM sym WHERE snapshot_id = %s AND kind = 'module'",
                                    (snapshot_id,)).fetchall()
        for c in conftests:
            base = c["path"][: -len("conftest.py")]
            for m in test_modules:
                if m["path"].startswith(base) and _is_test_module_path(m["path"]) and m["id"] not in reached:
                    reached[m["id"]] = reached[c["id"]] + 1

    # routes exposed by reached handlers, and the tests that call those routes over HTTP
    route_handlers: dict[int, int] = {}  # route sym id -> depth of its handler
    routes: set[str] = set()
    for r in conn.execute("SELECT e.src, d.id AS rid, d.qualname FROM sym_edge e JOIN sym d ON d.id = e.dst "
                          "WHERE e.snapshot_id = %s AND e.src = ANY(%s) AND e.rel = 'exposes_route'",
                          (snapshot_id, list(reached))):
        routes.add(r["qualname"])
        route_handlers[r["rid"]] = min(reached[r["src"]], route_handlers.get(r["rid"], reached[r["src"]]))
    if route_handlers:
        for r in conn.execute("SELECT src, dst FROM sym_edge WHERE snapshot_id = %s AND rel = 'tests' "
                              "AND dst = ANY(%s)", (snapshot_id, list(route_handlers))):
            d = route_handlers[r["dst"]] + 1
            if r["src"] not in reached or d < reached[r["src"]]:
                reached[r["src"]] = d
    syms = _sym_by_id(conn, list(reached))

    tests: set[str] = set()
    test_syms = [s for s in syms.values() if s["kind"] == "test"]
    module_ids = [s["id"] for s in syms.values() if s["kind"] == "module"]
    module_paths = sorted({s["path"] for s in syms.values() if s["kind"] == "module"})
    if module_paths:  # an affected module re-runs at import: every route declared in it is affected
        routes.update(r["qualname"] for r in conn.execute(
            "SELECT qualname FROM sym WHERE snapshot_id = %s AND kind = 'route' AND path = ANY(%s)",
            (snapshot_id, module_paths)))
    if module_ids:
        test_syms += conn.execute(
            f"SELECT {', '.join('t.' + c for c in _SYM_COLS.split(', '))} FROM sym_edge e JOIN sym t ON t.id = e.dst "
            "WHERE e.snapshot_id = %s AND e.src = ANY(%s) AND e.rel = 'defines' AND t.kind = 'test'",
            (snapshot_id, module_ids)).fetchall()
    for t in test_syms:
        tests.add((t["attrs"] or {}).get("nodeid") or f"{t['path']}::{t['name']}")

    out_syms = [{**s, "depth": reached[i]} for i, s in syms.items()]
    out_syms.sort(key=lambda s: (s["depth"], key_str(s)))
    affected_paths = sorted({s["path"] for s in out_syms})
    return {"snapshot_id": snapshot_id, "symbols": out_syms, "routes": sorted(routes), "tests": sorted(tests),
            "owners": owners(conn, snap["repo"], affected_paths, snapshot_id=snapshot_id)}


# --------------------------------------------------------------------------- owners


def _glob_regex(glob: str) -> re.Pattern[str] | None:
    """CODEOWNERS glob to regex (gitignore-style: a leading or inner ``/`` anchors at the root, a
    trailing ``/`` means a directory, ``*`` stays within a segment, ``**`` crosses segments).
    Plain ``fnmatch`` is not used because its ``*`` crosses ``/``."""
    pat = glob.strip()
    if not pat:
        return None
    dir_only = pat.endswith("/")
    pat = pat.rstrip("/")
    anchored = pat.startswith("/") or "/" in pat
    pat = pat.lstrip("/")
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out.append("/.*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    prefix = "^" if anchored else "^(?:.*/)?"
    suffix = "/.*$" if dir_only else "(?:/.*)?$"
    return re.compile(prefix + body + suffix)


def owners(conn: psycopg.Connection, repo: str, paths: list[str], *, snapshot_id: int | None = None,
           sha: str | None = None) -> dict[str, list[str]]:
    """``{path: [handles]}`` from the CODEOWNERS rules stored WITH a snapshot (last matching rule
    wins, GitHub semantics). Paths with no owner map to ``[]``.

    The snapshot is ``snapshot_id``, else the one of commit ``sha`` (prefix ok), else the latest
    snapshot of ``repo`` (newest commit). A repo with no snapshot has no rules (every path unowned);
    an explicit unknown ``sha`` raises LookupError.
    """
    if snapshot_id is None:
        try:
            snapshot_id = _snapshot.resolve_snapshot(conn, repo, sha)
        except LookupError:
            if sha:
                raise
            snapshot_id = -1  # nothing indexed for the repo: no rules, every path unowned
    rules: dict[tuple[int, str], list[str]] = {}
    for r in conn.execute("SELECT ord, path_glob, handle FROM owner WHERE snapshot_id = %s ORDER BY ord, handle",
                          (snapshot_id,)):
        handles = rules.setdefault((r["ord"], r["path_glob"]), [])
        if r["handle"]:  # '' marks a rule that un-owns
            handles.append(r["handle"])
    compiled = [(rx, handles) for (_, glob), handles in sorted(rules.items())
                if (rx := _glob_regex(glob)) is not None]
    out: dict[str, list[str]] = {}
    for path in paths:
        norm = path[2:] if path.startswith("./") else path.lstrip("/")
        winner: list[str] = []
        for rx, handles in compiled:
            if rx.match(norm):
                winner = handles
        out[path] = list(winner)
    return out


# --------------------------------------------------------------------------- search


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(conn: psycopg.Connection, snapshot_id: int, text: str, *, kind: str | None = None,
           limit: int = 20) -> list[dict[str, Any]]:
    """Name/path search: ILIKE on name, qualname and path, full-text over ``sym.tsv`` and (when the
    pg_trgm extension is installed) trigram similarity on name and qualname.

    Returns:
        Sym dicts plus ``"score": float`` (descending), at most ``limit``.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("search text is empty")
    if kind is not None and kind not in _snapshot_kinds():
        raise ValueError(f"unknown kind {kind!r}; valid: {', '.join(_snapshot_kinds())}")
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be >= 1, not {limit!r}")
    _ready(conn, snapshot_id)
    has_trgm = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone() is not None
    contains = f"%{_like(text)}%"
    prefix = f"{_like(text)}%"
    trgm_score = "GREATEST(similarity(name, %(t)s), similarity(qualname, %(t)s))" if has_trgm else "0"
    trgm_cond = "OR name %% %(t)s OR qualname %% %(t)s" if has_trgm else ""
    sql = f"""
        SELECT {_SYM_COLS}, GREATEST(
            CASE WHEN lower(name) = lower(%(t)s) OR lower(qualname) = lower(%(t)s) THEN 1.0
                 WHEN name ILIKE %(prefix)s THEN 0.9
                 WHEN name ILIKE %(contains)s THEN 0.8
                 WHEN qualname ILIKE %(contains)s THEN 0.7
                 WHEN path ILIKE %(contains)s THEN 0.6 ELSE 0 END,
            -- fuzzy-only matches (trigram, full-text) never outrank a literal substring match
            LEAST(GREATEST({trgm_score}, ts_rank(tsv, plainto_tsquery('simple', %(t)s))), 0.5)) AS score
        FROM sym
        WHERE snapshot_id = %(snap)s AND (%(kind)s::text IS NULL OR kind = %(kind)s)
          AND (name ILIKE %(contains)s OR qualname ILIKE %(contains)s OR path ILIKE %(contains)s
               OR tsv @@ plainto_tsquery('simple', %(t)s) {trgm_cond})
        ORDER BY score DESC, kind, qualname, path LIMIT %(limit)s"""
    rows = conn.execute(sql, {"t": text, "prefix": prefix, "contains": contains, "snap": snapshot_id,
                              "kind": kind, "limit": limit}).fetchall()
    for r in rows:
        r["score"] = round(float(r["score"]), 4)
    return rows


def _snapshot_kinds() -> tuple[str, ...]:
    from sdlc_graph.shapes import SYM_KINDS

    return SYM_KINDS


def snapshots(conn: psycopg.Connection, repo: str | None = None) -> list[dict[str, Any]]:
    """Snapshot listing (alias of ``snapshot.list_snapshots``), newest first."""
    return _snapshot.list_snapshots(conn, repo)


# --------------------------------------------------------------------------- structural checks


def unmatched_fetches(conn: psycopg.Connection, snapshot_id: int) -> list[Sym]:
    """frontend_call symbols with no outgoing ``fetches`` edge (frontend calls with no route)."""
    _ready(conn, snapshot_id)
    return conn.execute(
        f"SELECT {_SYM_COLS} FROM sym s WHERE s.snapshot_id = %s AND s.kind = 'frontend_call' AND NOT EXISTS "
        "(SELECT 1 FROM sym_edge e WHERE e.snapshot_id = s.snapshot_id AND e.src = s.id AND e.rel = 'fetches') "
        "ORDER BY qualname, path", (snapshot_id,)).fetchall()


def unmounted_routers(conn: psycopg.Connection, snapshot_id: int) -> list[Sym]:
    """Modules that define routes with ``attrs.mounted = false`` and have no incoming
    ``includes_router`` edge, as module Sym dicts."""
    _ready(conn, snapshot_id)
    cols = ", ".join("m." + c for c in _SYM_COLS.split(", "))
    return conn.execute(
        f"SELECT {cols} FROM sym m WHERE m.snapshot_id = %s AND m.kind = 'module' "
        "AND EXISTS (SELECT 1 FROM sym r WHERE r.snapshot_id = m.snapshot_id AND r.kind = 'route' AND r.path = m.path "
        "            AND r.attrs->>'mounted' = 'false') "
        "AND NOT EXISTS (SELECT 1 FROM sym_edge e WHERE e.snapshot_id = m.snapshot_id AND e.dst = m.id "
        "                AND e.rel = 'includes_router') ORDER BY m.qualname", (snapshot_id,)).fetchall()
