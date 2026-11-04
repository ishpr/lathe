"""Affected set for a change: ``queries.impact`` (reverse ``imports``/``calls``/``tests`` walk up to
depth 4) plus tests whose path MIRRORS a touched module, with a reason for every test and route.

Mirror rule: for a touched module ``a/b/c.py`` the tests ``tests/test_c.py``,
``tests/b/test_c.py``, ``tests/a/b/test_c.py`` and every test under ``tests/b/`` / ``tests/a/b/``
that exist in the snapshot are included (reason ``"mirror path of a/b/c.py"``), even with no
import edge. For a package ``a/b/__init__.py`` the package name ``b`` plays the role of ``c``.

Ordering (``tests`` and ``order``, closest first, so ``/verify`` can fail fast):

1. direct tests: a test the graph reaches at depth 1 (it imports or calls a touched symbol);
2. transitive tests: reached at depth 2 or more, by (depth, id);
3. module-closure tests: reached only through the low-confidence module-level closure of a touched
   function, method or class (``queries.CLOSURE_DEPTH``), by (depth, id);
4. mirror-only tests: found by path mirroring alone, by id.

``conftest.py`` and ``__init__.py`` are never test ids (``pytest conftest.py`` collects nothing); a
reached conftest contributes the tests beneath it instead (see ``queries.impact``). Entries that do not
resolve in the snapshot (a deleted or renamed path, a renamed symbol) are reported in ``unresolved``
(``[{"entry", "reason": "absent_in_snapshot"}]``) instead of silently predicting nothing or aborting.

``order`` is the same list as ``tests`` and is what to feed ``pytest`` (node ids, closest first).
Every other list is sorted. Nothing here writes: predictions are stored by ``impact.record``.
"""

from __future__ import annotations

import fnmatch
from typing import Any

import psycopg

from sdlc_graph.codegraph import queries
from sdlc_graph.shapes import AffectedSet, parse_key

_GLOB_CHARS = "*?["
_FAR = 10_000  # depth used for a graph test whose depth is unknown (sorts after known depths)


# --------------------------------------------------------------------------- mirror rule (pure)


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if name in ("conftest.py", "__init__.py"):
        return False
    return "tests" in parts[:-1] or "test" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py")


def mirror_tests(module_path: str, test_paths: list[str]) -> list[str]:
    """Test file paths (from ``test_paths``) that mirror ``module_path`` under the rule above. Pure.

    A path that is itself a test file mirrors nothing. Only ``.py`` modules are mirrored.
    """
    if not module_path.endswith(".py") or _is_test_path(module_path):
        return []
    parts = module_path[:-3].split("/")
    names: set[str] = set()
    dirs: list[str] = []
    if parts[-1] == "__init__":
        parts = parts[:-1]  # a package a/b/__init__.py owns tests/test_b.py, tests/b/ and tests/a/b/
        if not parts:
            return []
        names.add(f"tests/test_{parts[-1]}.py")
        dirs += [f"tests/{parts[-1]}/", f"tests/{'/'.join(parts)}/"]
    else:
        stem, pkg = parts[-1], parts[:-1]
        names.add(f"tests/test_{stem}.py")
        if pkg:
            names.add(f"tests/{pkg[-1]}/test_{stem}.py")
            names.add(f"tests/{'/'.join(pkg)}/test_{stem}.py")
            dirs += [f"tests/{pkg[-1]}/", f"tests/{'/'.join(pkg)}/"]
    out = []
    for tp in test_paths:
        if tp in names or any(tp.startswith(d) for d in dirs):
            out.append(tp)
    return sorted(set(out))


# --------------------------------------------------------------------------- snapshot helpers


def latest_snapshot_id(conn: psycopg.Connection, repo: str | None = None, sha: str | None = None) -> int:
    """Snapshot of the newest COMMIT (``committed_at``, not the latest indexing), optionally of
    ``repo`` and/or whose commit starts with ``sha``.

    Raises:
        LookupError: no matching snapshot.
    """
    sql = "SELECT id FROM snapshot WHERE true"
    params: list[Any] = []
    if repo:
        sql += " AND repo = %s"
        params.append(repo)
    if sha:
        sql += " AND commit_sha LIKE %s"
        params.append(sha + "%")
    row = conn.execute(sql + " ORDER BY committed_at DESC NULLS LAST, ancestors DESC NULLS LAST, indexed_at DESC, id DESC LIMIT 1", params).fetchone()
    if row is None:
        raise LookupError(f"no snapshot for repo={repo!r} sha={sha!r}; run 'sdlc-graph index' first")
    return int(row["id"])


def _snapshot_row(conn: psycopg.Connection, snapshot_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT id, repo, storage FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown snapshot {snapshot_id}")
    return row


def _ensure_materialised(conn: psycopg.Connection, snap: dict[str, Any]) -> None:
    """Delta snapshots keep their content as a diff; the sym table is only filled on demand."""
    if snap["storage"] == "delta":
        from sdlc_graph.codegraph import snapshot

        snapshot.materialise(conn, snap["id"])


def _test_index(conn: psycopg.Connection, snapshot_id: int) -> dict[str, list[str]]:
    """``{test file path: [pytest node ids defined in it]}`` for the snapshot (a test file with no
    test symbol maps to ``[]``, meaning the bare path)."""
    index: dict[str, list[str]] = {}
    rows = conn.execute(
        "SELECT kind, path, qualname, attrs FROM sym WHERE snapshot_id = %s AND kind IN ('test','module')",
        (snapshot_id,),
    ).fetchall()
    for r in rows:
        if r["kind"] == "module":
            if r["path"].endswith(".py") and _is_test_path(r["path"]):
                index.setdefault(r["path"], [])
        else:
            nodeid = (r["attrs"] or {}).get("nodeid") or f"{r['path']}::{r['qualname'].rsplit('.', 1)[-1]}"
            index.setdefault(r["path"], []).append(nodeid)
    for ids in index.values():
        ids.sort()
    return index


# --------------------------------------------------------------------------- affected


def _route_reasons(conn: psycopg.Connection, snapshot_id: int, routes: list[str]) -> dict[str, str]:
    if not routes:
        return {}
    rows = conn.execute(
        """SELECT r.qualname AS route, h.qualname AS handler
             FROM sym_edge e JOIN sym r ON r.id = e.dst JOIN sym h ON h.id = e.src
            WHERE e.snapshot_id = %s AND e.rel = 'exposes_route' AND r.kind = 'route' AND r.qualname = ANY(%s)""",
        (snapshot_id, routes),
    ).fetchall()
    return {r["route"]: f"exposed by {r['handler']}" for r in rows}


def affected(conn: psycopg.Connection, snapshot_id: int, *, paths: list[str] | None = None,
             symbols: list[str] | None = None, depth: int = 4) -> AffectedSet:
    """Full affected set with ``reasons`` and an ``order`` list.

    Args:
        paths / symbols: what the change touches (symbols in any form ``queries.find_symbol``
            accepts). At least one is required.
        depth: reverse-walk depth (default 4).

    Returns:
        AffectedSet (symbols, routes, tests, owners, reasons, snapshot_id) plus ``order``: the
        test node ids closest first (identical to ``tests``).

    Raises:
        ValueError: neither paths nor symbols. LookupError: unknown symbol or snapshot.
    """
    if not paths and not symbols:
        raise ValueError("affected needs at least one of paths or symbols")
    snap = _snapshot_row(conn, snapshot_id)
    _ensure_materialised(conn, snap)

    # Entries absent from the snapshot are reported, not guessed at and not fatal.
    unresolved: list[dict[str, str]] = []
    live_paths: list[str] = []
    for p in paths or []:
        q = p.removeprefix("./")
        if q.endswith("/"):
            like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            found = conn.execute("SELECT 1 FROM sym WHERE snapshot_id = %s AND path LIKE %s LIMIT 1",
                                 (snapshot_id, like)).fetchone()
        else:
            found = conn.execute("SELECT 1 FROM sym WHERE snapshot_id = %s AND path = %s LIMIT 1",
                                 (snapshot_id, q)).fetchone()
        (live_paths if found else unresolved).append(q if found else {"entry": p, "reason": "absent_in_snapshot"})
    live_symbols: list[str] = []
    for text in symbols or []:
        try:
            queries.find_symbol(conn, snapshot_id, text)
        except LookupError as exc:
            reason = "ambiguous" if "ambiguous" in str(exc) else "absent_in_snapshot"
            unresolved.append({"entry": text, "reason": reason})
        else:
            live_symbols.append(text)
    if not live_paths and not live_symbols:
        return {"snapshot_id": snapshot_id, "symbols": [], "routes": [], "tests": [], "owners": {}, "reasons": {},
                "order": [], "unresolved": unresolved}  # type: ignore[return-value]
    paths, symbols = live_paths, live_symbols

    base = queries.impact(conn, snapshot_id, paths=paths, symbols=symbols, depth=depth)
    syms = list(base.get("symbols", []))

    # touched module paths drive the mirror rule: the requested paths and the roots of the walk
    touched: set[str] = {p for p in (paths or []) if not p.endswith("/")}
    for s in syms:
        if s.get("depth", 0) == 0:
            touched.add(s["path"])
    for sym in symbols or []:
        touched.add(queries.find_symbol(conn, snapshot_id, sym)["path"])

    # depth of every test the graph reached
    id_depth: dict[str, int] = {}
    file_depth: dict[str, int] = {}
    for s in syms:
        d = int(s.get("depth", _FAR))
        if s["kind"] == "test":
            nodeid = (s.get("attrs") or {}).get("nodeid")
            if nodeid:
                id_depth[nodeid] = min(d, id_depth.get(nodeid, _FAR))
        elif s["kind"] == "module" and _is_test_path(s["path"]):
            file_depth[s["path"]] = min(d, file_depth.get(s["path"], _FAR))

    index = _test_index(conn, snapshot_id)
    graph_tests: dict[str, int] = {}
    for tid in base.get("tests", []):
        fpath = tid.split("::", 1)[0]
        if fpath.rsplit("/", 1)[-1] in ("conftest.py", "__init__.py"):
            continue
        graph_tests[tid] = id_depth.get(tid, file_depth.get(fpath, _FAR))

    mirror: dict[str, str] = {}
    for module_path in sorted(touched):
        for tfile in mirror_tests(module_path, sorted(index)):
            for tid in index[tfile] or [tfile]:
                mirror.setdefault(tid, f"mirror path of {module_path}")

    reasons: dict[str, str] = dict(base.get("reasons", {}))
    ranked: list[tuple[int, int, str]] = []
    for tid, d in graph_tests.items():
        closure = queries.CLOSURE_DEPTH <= d < _FAR
        group = 0 if d <= 1 else (2 if closure else 1)
        ranked.append((group, d, tid))
        if closure:
            reasons.setdefault(tid, f"module-level closure (low confidence): reached {d - queries.CLOSURE_DEPTH} "
                                    "hop(s) from the touched symbol's module")
        else:
            label = "direct" if group == 0 else "transitive"
            reasons.setdefault(tid, f"{label} test: reverse walk reaches it at depth {d}" if d < _FAR
                               else "reverse walk reaches it")
    for tid, why in mirror.items():
        if tid not in graph_tests and tid.split("::", 1)[0].rsplit("/", 1)[-1] not in ("conftest.py", "__init__.py"):
            ranked.append((3, 0, tid))
            reasons.setdefault(tid, why)
    ranked.sort()
    ordered = [tid for _, _, tid in ranked]

    routes = sorted(set(base.get("routes", [])))
    for route, why in _route_reasons(conn, snapshot_id, routes).items():
        reasons.setdefault(route, why)

    owners: dict[str, list[str]] = {k: list(v) for k, v in base.get("owners", {}).items()}
    test_files = sorted({tid.split("::", 1)[0] for tid in ordered} - set(owners))
    if test_files:
        owners.update(queries.owners(conn, snap["repo"], test_files, snapshot_id=snapshot_id))

    return {
        "snapshot_id": snapshot_id,
        "symbols": syms,
        "routes": routes,
        "tests": ordered,
        "owners": dict(sorted(owners.items())),
        "reasons": reasons,
        "order": list(ordered),
        "unresolved": unresolved,
    }  # type: ignore[return-value]


# --------------------------------------------------------------------------- result shaping

DEFAULT_MAX_ITEMS = 200  # per list; 0 means no cap
_WHOLE_SUITE_SHARE = 0.9
_WHOLE_SUITE_MIN_FILES = 5


def shape_result(conn: psycopg.Connection, snapshot_id: int, result: dict[str, Any], *,
                 max_items: int | None = DEFAULT_MAX_ITEMS, counts_only: bool = False) -> dict[str, Any]:
    """Cap an affected/impact result so a shared fixture cannot return the whole graph.

    ``counts_only`` returns just ``counts`` (symbols by kind, routes, tests, owners). Otherwise
    ``symbols`` (nearest first), ``routes``, ``tests`` (already closest first) and ``owners`` are cut
    to ``max_items`` each (``0``/``None`` = no cap), ``reasons``/``order`` follow the kept entries and
    ``truncated`` says how many were left out per list. Either way ``whole_suite`` is a message when
    the tests reach at least 90% of the snapshot's test files (and there are at least 5), so a path
    that touches everything says so instead of listing everything.
    """
    syms = result.get("symbols", [])
    tests = result.get("tests", [])
    by_kind: dict[str, int] = {}
    for sym in syms:
        by_kind[sym["kind"]] = by_kind.get(sym["kind"], 0) + 1
    counts = {"symbols": len(syms), "symbols_by_kind": dict(sorted(by_kind.items())),
              "routes": len(result.get("routes", [])), "tests": len(tests), "owners": len(result.get("owners", {}))}
    suite = _test_index(conn, snapshot_id)
    reached = {t.split("::", 1)[0] for t in tests} & set(suite)
    out: dict[str, Any] = {"snapshot_id": result.get("snapshot_id", snapshot_id), "counts": counts}
    if len(suite) >= _WHOLE_SUITE_MIN_FILES and len(reached) >= _WHOLE_SUITE_SHARE * len(suite):
        out["whole_suite"] = (f"reaches the whole test suite: {len(reached)} of {len(suite)} test files; "
                              "narrow the path or run everything")
    if result.get("unresolved"):
        out["unresolved"] = result["unresolved"]
    if counts_only:
        return out
    cap = max_items or None

    def cut(items: list) -> list:
        return items[:cap] if cap else list(items)

    kept_syms = cut(sorted(syms, key=lambda x: (x.get("depth", _FAR), x["path"], x["qualname"])))
    kept_tests = cut(tests)
    kept_routes = cut(result.get("routes", []))
    owners = result.get("owners", {})
    kept_owners = dict(list(owners.items())[:cap]) if cap else dict(owners)
    keep = set(kept_tests) | set(kept_routes)
    out.update({"symbols": kept_syms, "routes": kept_routes, "tests": kept_tests, "owners": kept_owners,
                "reasons": {k: v for k, v in result.get("reasons", {}).items() if k in keep},
                "order": list(kept_tests)})
    omitted = {"symbols": len(syms) - len(kept_syms), "routes": counts["routes"] - len(kept_routes),
               "tests": len(tests) - len(kept_tests), "owners": len(owners) - len(kept_owners)}
    omitted = {k: v for k, v in omitted.items() if v}
    if omitted:
        out["truncated"] = omitted
    return out


# --------------------------------------------------------------------------- contract wrapper


def _expand_paths(conn: psycopg.Connection, snapshot_id: int, entries: list[str]) -> list[str]:
    """Literal files/dirs pass through; glob entries expand against the snapshot's symbol paths."""
    out: list[str] = []
    all_paths: list[str] | None = None
    for entry in entries:
        entry = entry.removeprefix("./")
        if not any(c in entry for c in _GLOB_CHARS):
            out.append(entry)
            continue
        if all_paths is None:
            all_paths = [r["path"] for r in conn.execute(
                "SELECT DISTINCT path FROM sym WHERE snapshot_id = %s", (snapshot_id,)).fetchall()]
        pattern = entry.replace("**", "*")
        out += [p for p in all_paths if fnmatch.fnmatchcase(p, pattern)]
    return sorted(set(out))


def affected_for_node(conn: psycopg.Connection, slug: str, snapshot_id: int | None = None) -> AffectedSet:
    """``affected`` over a contract's ``touched_symbols`` (bare paths go to ``paths``), on
    ``snapshot_id`` or the latest snapshot of the node's repo. ``capabilities.paths`` is used only
    when ``touched_symbols`` is empty: it says where the contract may write (often ``tests/**``
    or ``app/**``), not what it changes, so unioning it in predicts nearly the whole suite.

    Raises:
        LookupError: unknown slug or no snapshot for the repo. ValueError: nothing touched.
    """
    row = conn.execute("SELECT repo, touched_symbols, capabilities FROM node WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"unknown node {slug!r}")
    if snapshot_id is None:
        snapshot_id = latest_snapshot_id(conn, row["repo"])
    sym_args: list[str] = []
    path_args: list[str] = []
    for entry in row["touched_symbols"] or []:
        try:
            parse_key(entry)
            sym_args.append(entry)
        except ValueError:
            path_args.append(entry)
    if not row["touched_symbols"]:
        path_args += list((row["capabilities"] or {}).get("paths") or [])
    path_args = _expand_paths(conn, snapshot_id, path_args)
    if not sym_args and not path_args:
        raise ValueError(f"node {slug!r} touches nothing: set touched_symbols or capabilities.paths")
    return affected(conn, snapshot_id, paths=path_args or None, symbols=sym_args or None)

