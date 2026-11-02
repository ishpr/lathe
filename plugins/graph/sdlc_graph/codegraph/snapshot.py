"""Snapshot store: index a commit into the code graph, store later commits as deltas, rebuild any
snapshot exactly, and verify a stored snapshot against a fresh extraction.

Git is used READ-ONLY: ``git rev-parse``, ``git rev-list --parents``, ``git archive <sha> | tar -x``
into a temp directory (never a checkout in the target repo, never a write to it).

Storage rules:
* The first snapshot of a repo is ``storage='full'``: symbols and edges are rows in sym/sym_edge.
* ``delta`` stores the later SHA as ``storage='delta'`` with ``base_snapshot_id`` = the from-SHA's
  snapshot and the differences as sym_delta rows (shapes in sql/001_codegraph.sql).
* ``reconstruct`` walks the base chain to the full snapshot and replays deltas in order.
* Queries need sym rows; a delta snapshot is materialised into sym/sym_edge on first query
  (``materialise``) without changing its ``storage`` value, so reconstruct stays the source of truth
  and ``verify`` checks both.

Canonical forms used for every comparison and every stored delta:
* symbol identity is ``(kind, qualname, path)``; its payload is ``name, line_start, line_end, attrs``;
* edge identity is ``(src key, dst key, rel)``; its payload is ``attrs``. An edge whose attrs changed
  is stored as a ``remove`` followed by an ``add``;
* ``reconstruct`` and ``extract_all`` both return symbols sorted by key and edges sorted by
  (src key, dst key, rel).
"""

from __future__ import annotations

import importlib
import json
import re
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.shapes import Edge, ExtractResult, Sym, key_dict, sym_key

#: Bump when extractor output changes; stored on every snapshot row.
EXTRACTOR_VERSION = "0.2.0"

_SYM_FIELDS = ("name", "line_start", "line_end", "attrs")
_EdgeId = tuple[tuple[str, str, str], tuple[str, str, str], str]


# --------------------------------------------------------------------------- git (read-only)


def _git(repo_path: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    repo = Path(repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"not a directory: {repo}")
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:  # git binary missing
        raise FileNotFoundError("git is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"git {' '.join(args)} failed: {exc.stderr.strip() or exc.stdout.strip()}") from exc


def repo_name(repo_path: str | Path) -> str:
    """The ``repo`` string stored on snapshots, owners and ledger nodes for a local checkout:
    ``owner/name`` parsed from ``remote.origin.url`` when present, else the directory basename.
    An origin of ``https://github.com/acme/sample-app.git`` gives ``acme/sample-app``;
    pass ``name`` (CLI ``--name``) to store something shorter, e.g. ``sample-app``."""
    proc = _git(repo_path, "config", "--get", "remote.origin.url", check=False)
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    if url:
        m = re.search(r"([^/:]+)/([^/:]+?)(?:\.git)?/?$", url)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return Path(repo_path).resolve().name


def resolve_sha(repo_path: str | Path, ref: str = "HEAD") -> str:
    """Full 40-character SHA of ``ref`` (``git rev-parse --verify <ref>^{commit}``).

    Raises:
        ValueError: ``ref`` does not name a commit. FileNotFoundError: not a git repository.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"not a directory: {repo}")
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if proc.returncode != 0:
        probe = _git(repo, "rev-parse", "--git-dir", check=False)
        if probe.returncode != 0:
            raise FileNotFoundError(f"not a git repository: {repo}")
        raise ValueError(f"unknown commit: {ref!r}")
    return proc.stdout.strip()


def _parent_sha(repo_path: str | Path, sha: str) -> str | None:
    out = _git(repo_path, "rev-list", "--parents", "-n", "1", sha).stdout.split()
    return out[1] if len(out) > 1 else None


def export_tree(repo_path: str | Path, sha: str, dest: Path) -> Path:
    """Write the tree of ``sha`` into ``dest`` with ``git archive`` (read-only). Returns ``dest``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(["git", "-C", str(repo_path), "archive", "--format=tar", sha],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert archive.stdout is not None and archive.stderr is not None
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, capture_output=True)
    archive.stdout.close()
    err = archive.stderr.read().decode(errors="replace").strip()
    archive.stderr.close()
    if archive.wait() != 0:
        raise ValueError(f"git archive {sha} failed: {err}")
    if untar.returncode != 0:
        raise ValueError(f"tar failed: {untar.stderr.decode(errors='replace').strip()}")
    drop_symlinks(dest)
    return dest


def drop_symlinks(root: Path) -> list[str]:
    """Delete every symlink under ``root`` (relative paths returned). ``git archive`` recreates
    committed symlinks and the extractors would follow them out of the exported tree, reading files
    that are not part of the commit (``requirements.txt -> ~/.env``) into the graph, which MCP tools
    then serve. A link is removed, never followed."""
    dropped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                p.unlink()
                dropped.append(str(p.relative_to(root)))
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
    return sorted(dropped)


# --------------------------------------------------------------------------- extraction


def _plain(attrs: Any) -> dict[str, Any]:
    """JSON round-trip so tuples/sets compare equal to what jsonb returns."""
    return json.loads(json.dumps(attrs or {}, sort_keys=True, default=list))


def _norm_sym(sym: dict[str, Any]) -> Sym:
    return {  # type: ignore[return-value]
        "kind": sym["kind"], "name": sym["name"], "qualname": sym["qualname"], "path": sym["path"],
        "line_start": sym.get("line_start"), "line_end": sym.get("line_end"),
        "attrs": _plain(sym.get("attrs")),
    }


def _norm_edge(edge: dict[str, Any]) -> Edge:
    return {"src": key_dict(edge["src"]), "dst": key_dict(edge["dst"]), "rel": edge["rel"],
            "attrs": _plain(edge.get("attrs"))}


def _edge_id(edge: dict[str, Any]) -> _EdgeId:
    return (sym_key(edge["src"]), sym_key(edge["dst"]), edge["rel"])


def _sorted(symbols: dict[tuple[str, str, str], Sym], edges: dict[_EdgeId, Edge]) -> ExtractResult:
    return {"symbols": [symbols[k] for k in sorted(symbols)], "edges": [edges[k] for k in sorted(edges)]}


def _merge(parts: list[ExtractResult]) -> tuple[dict, dict]:
    symbols: dict[tuple[str, str, str], Sym] = {}
    edges: dict[_EdgeId, Edge] = {}
    for part in parts:
        for raw in part["symbols"]:
            sym = _norm_sym(raw)
            key = sym_key(sym)
            if key in symbols and symbols[key] != sym:
                raise ValueError(f"two extractors emitted {key} with different fields")
            symbols[key] = sym
        for raw in part["edges"]:
            edge = _norm_edge(raw)
            edges[_edge_id(edge)] = edge
    return symbols, edges


def extract_all(repo_root: Path) -> ExtractResult:
    """Run every extractor in ``codegraph.EXTRACTORS`` order on a tree, merge, then append each
    extractor's ``link(merged)`` edges. Pure: no database, no git.

    Errors:
        Raises ValueError when two extractors emit the same SymKey with different fields, or an
        edge references a SymKey no extractor emitted (an extractor bug, never silently dropped).
    """
    from sdlc_graph.codegraph import EXTRACTORS

    modules = [importlib.import_module(name) for name in EXTRACTORS]
    symbols, edges = _merge([m.extract(Path(repo_root)) for m in modules])
    merged = _sorted(symbols, edges)
    links = [e for m in modules for e in m.link(merged)]
    symbols, edges = _merge([merged, {"symbols": [], "edges": links}])
    for eid, edge in edges.items():
        for end in (eid[0], eid[1]):
            if end not in symbols:
                raise ValueError(f"edge {edge['rel']} references a symbol no extractor emitted: {end}")
    return _sorted(symbols, edges)


def _owner_rules(root: Path) -> list[dict[str, Any]]:
    from sdlc_graph.codegraph import extract_owners

    return list(extract_owners.parse(root))


def _extract_commit(repo_path: str | Path, sha: str) -> tuple[ExtractResult, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="sdlc-graph-") as tmp:
        root = export_tree(repo_path, sha, Path(tmp) / "tree")
        return extract_all(root), _owner_rules(root)


# --------------------------------------------------------------------------- database helpers


def _snapshot_row(conn: psycopg.Connection, repo: str, sha: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM snapshot WHERE repo = %s AND commit_sha = %s", (repo, sha)).fetchone()


def _insert_content(conn: psycopg.Connection, snapshot_id: int, content: ExtractResult) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sym (snapshot_id, kind, name, qualname, path, line_start, line_end, attrs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(snapshot_id, s["kind"], s["name"], s["qualname"], s["path"], s["line_start"], s["line_end"],
              Jsonb(s["attrs"])) for s in content["symbols"]])
    ids = {(r["kind"], r["qualname"], r["path"]): r["id"] for r in conn.execute(
        "SELECT id, kind, qualname, path FROM sym WHERE snapshot_id = %s", (snapshot_id,))}
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sym_edge (snapshot_id, src, dst, rel, attrs) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(snapshot_id, ids[sym_key(e["src"])], ids[sym_key(e["dst"])], e["rel"], Jsonb(e["attrs"]))
             for e in content["edges"]])


def _owner_rows(rules: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """``(path_glob, handle, ord)`` rows for CODEOWNERS rules; a rule with no handles is stored with
    handle '' (it un-owns)."""
    return [(rule["path_glob"], handle, rule["ord"]) for rule in rules for handle in (rule["handles"] or [""])]


def _store_owners(conn: psycopg.Connection, snapshot_id: int, repo: str, rules: list[dict[str, Any]]) -> int:
    """Store the CODEOWNERS rules of ONE snapshot (they are versioned per commit, never rewritten)."""
    rows = [(snapshot_id, repo, g, h, o) for g, h, o in _owner_rows(rules)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO owner (snapshot_id, repo, path_glob, handle, ord) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (snapshot_id, path_glob, handle) DO UPDATE SET ord = EXCLUDED.ord", rows)
    return len(rows)


def _committed_at(repo_path: str | Path, sha: str) -> tuple[str | None, int | None]:
    """``(committer date ISO 8601, number of commits reachable from sha)``; None where git cannot say."""
    proc = _git(repo_path, "show", "-s", "--format=%cI", sha, check=False)
    when = (proc.stdout.strip() if proc.returncode == 0 else "") or None
    count = _git(repo_path, "rev-list", "--count", sha, check=False)
    return when, int(count.stdout.strip()) if count.returncode == 0 and count.stdout.strip().isdigit() else None


def _read_full(conn: psycopg.Connection, snapshot_id: int) -> tuple[dict, dict]:
    symbols: dict[tuple[str, str, str], Sym] = {}
    for r in conn.execute("SELECT kind, name, qualname, path, line_start, line_end, attrs FROM sym "
                          "WHERE snapshot_id = %s", (snapshot_id,)):
        sym = _norm_sym(r)
        symbols[sym_key(sym)] = sym
    edges: dict[_EdgeId, Edge] = {}
    for r in conn.execute(
            "SELECT s.kind AS sk, s.qualname AS sq, s.path AS sp, d.kind AS dk, d.qualname AS dq, d.path AS dp, "
            "e.rel, e.attrs FROM sym_edge e JOIN sym s ON s.id = e.src JOIN sym d ON d.id = e.dst "
            "WHERE e.snapshot_id = %s", (snapshot_id,)):
        edge = {"src": {"kind": r["sk"], "qualname": r["sq"], "path": r["sp"]},
                "dst": {"kind": r["dk"], "qualname": r["dq"], "path": r["dp"]},
                "rel": r["rel"], "attrs": r["attrs"]}
        edges[_edge_id(edge)] = _norm_edge(edge)
    return symbols, edges


def _get_snapshot(conn: psycopg.Connection, snapshot_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()
    if row is None:
        raise LookupError(f"no snapshot with id {snapshot_id}")
    return row


def _load(conn: psycopg.Connection, snapshot_id: int, _seen: frozenset[int] = frozenset()) -> tuple[dict, dict]:
    snap = _get_snapshot(conn, snapshot_id)
    if snap["storage"] == "full":
        return _read_full(conn, snapshot_id)
    if snapshot_id in _seen or snap["base_snapshot_id"] is None:
        raise ValueError(f"broken delta chain at snapshot {snapshot_id}")
    symbols, edges = _load(conn, snap["base_snapshot_id"], _seen | {snapshot_id})
    rows = conn.execute("SELECT op, table_name, row FROM sym_delta WHERE snapshot_id = %s ORDER BY id",
                        (snapshot_id,)).fetchall()
    # removals first so a remove+add of the same identity ends as the add
    for r in rows:
        if r["op"] == "remove":
            (symbols if r["table_name"] == "sym" else edges).pop(
                sym_key(r["row"]) if r["table_name"] == "sym" else _edge_id(r["row"]), None)
    for r in rows:
        if r["op"] == "remove":
            continue
        if r["table_name"] == "sym":
            sym = _norm_sym(r["row"])
            if r["op"] == "change" and sym_key(sym) not in symbols:
                raise ValueError(f"broken delta at snapshot {snapshot_id}: change of unknown {sym_key(sym)}")
            symbols[sym_key(sym)] = sym
        else:
            edge = _norm_edge(r["row"])
            edges[_edge_id(edge)] = edge
    return symbols, edges


# --------------------------------------------------------------------------- public API


def index(conn: psycopg.Connection, repo_path: str | Path, sha: str = "HEAD", *, name: str | None = None) -> dict[str, Any]:
    """Index one commit as a FULL snapshot (and store that commit's CODEOWNERS rules with it).

    Idempotent: if ``(repo, commit_sha)`` exists, returns it with ``"created": False``.

    Returns:
        ``{"snapshot_id", "repo", "commit_sha", "parent_sha", "storage": "full", "symbols": int,
        "edges": int, "owners": int, "created": bool}``.

    Raises:
        ValueError: unknown ref. psycopg.Error: database failure (the whole index is one transaction).
    """
    full_sha = resolve_sha(repo_path, sha)
    repo = name or repo_name(repo_path)
    existing = _snapshot_row(conn, repo, full_sha)
    if existing is not None:
        return _summary(conn, existing, created=False)
    content, rules = _extract_commit(repo_path, full_sha)
    parent = _parent_sha(repo_path, full_sha)
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO snapshot (repo, commit_sha, parent_sha, extractor_version, storage, committed_at, ancestors) "
            "VALUES (%s, %s, %s, %s, 'full', %s, %s) RETURNING *",
            (repo, full_sha, parent, EXTRACTOR_VERSION, *_committed_at(repo_path, full_sha))).fetchone()
        _insert_content(conn, row["id"], content)
        owners = _store_owners(conn, row["id"], repo, rules)
    out = _summary(conn, row, created=True)
    out["owners"] = owners
    return out


def _summary(conn: psycopg.Connection, snap: dict[str, Any], *, created: bool) -> dict[str, Any]:
    symbols, edges = _load(conn, snap["id"])
    owners = conn.execute("SELECT count(*) AS n FROM owner WHERE snapshot_id = %s", (snap["id"],)).fetchone()["n"]
    return {"snapshot_id": snap["id"], "repo": snap["repo"], "commit_sha": snap["commit_sha"],
            "parent_sha": snap["parent_sha"], "storage": snap["storage"], "symbols": len(symbols),
            "edges": len(edges), "owners": owners, "created": created}


def _diff(old: tuple[dict, dict], new: tuple[dict, dict]) -> dict[str, list]:
    osyms, oedges = old
    nsyms, nedges = new
    return {
        "add": [nsyms[k] for k in sorted(nsyms.keys() - osyms.keys())],
        "remove": [osyms[k] for k in sorted(osyms.keys() - nsyms.keys())],
        "change": [nsyms[k] for k in sorted(nsyms.keys() & osyms.keys())
                   if any(nsyms[k][f] != osyms[k][f] for f in _SYM_FIELDS)],
        "edge_add": [nedges[k] for k in sorted(nedges.keys() - oedges.keys())]
                    + [nedges[k] for k in sorted(nedges.keys() & oedges.keys()) if nedges[k]["attrs"] != oedges[k]["attrs"]],
        "edge_remove": [oedges[k] for k in sorted(oedges.keys() - nedges.keys())]
                       + [oedges[k] for k in sorted(nedges.keys() & oedges.keys()) if nedges[k]["attrs"] != oedges[k]["attrs"]],
    }


def delta(conn: psycopg.Connection, repo_path: str | Path, from_sha: str, to_sha: str, *, name: str | None = None) -> dict[str, Any]:
    """Store ``to_sha`` as a DELTA snapshot against ``from_sha``'s snapshot (indexing ``from_sha``
    first, as full, if it is not stored yet).

    The difference is taken against the STORED base content (``reconstruct`` of the from-snapshot),
    so replaying the stored deltas reproduces exactly what was extracted for ``to_sha``.

    Returns:
        ``{"snapshot_id", "base_snapshot_id", "repo", "commit_sha", "storage": "delta",
        "added": int, "removed": int, "changed": int, "edges_added": int, "edges_removed": int,
        "created": bool}``.
    """
    from_full = resolve_sha(repo_path, from_sha)
    to_full = resolve_sha(repo_path, to_sha)
    repo = name or repo_name(repo_path)
    base = index(conn, repo_path, from_full, name=repo)
    existing = _snapshot_row(conn, repo, to_full)
    if existing is not None:
        return _delta_summary(conn, existing, created=False)
    content, rules = _extract_commit(repo_path, to_full)
    parent = _parent_sha(repo_path, to_full)
    d = _diff(_load(conn, base["snapshot_id"]), _merge([content]))
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO snapshot (repo, commit_sha, parent_sha, extractor_version, storage, base_snapshot_id, "
            "committed_at, ancestors) VALUES (%s, %s, %s, %s, 'delta', %s, %s, %s) RETURNING *",
            (repo, to_full, parent, EXTRACTOR_VERSION, base["snapshot_id"], *_committed_at(repo_path, to_full))).fetchone()
        rows = ([(row["id"], "add", "sym", Jsonb(s)) for s in d["add"]]
                + [(row["id"], "remove", "sym", Jsonb(s)) for s in d["remove"]]
                + [(row["id"], "change", "sym", Jsonb(s)) for s in d["change"]]
                + [(row["id"], "remove", "sym_edge", Jsonb(e)) for e in d["edge_remove"]]
                + [(row["id"], "add", "sym_edge", Jsonb(e)) for e in d["edge_add"]])
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO sym_delta (snapshot_id, op, table_name, row) VALUES (%s, %s, %s, %s)", rows)
        _store_owners(conn, row["id"], repo, rules)
    return _delta_summary(conn, row, created=True)


def _delta_summary(conn: psycopg.Connection, snap: dict[str, Any], *, created: bool) -> dict[str, Any]:
    counts = {(r["table_name"], r["op"]): r["n"] for r in conn.execute(
        "SELECT table_name, op, count(*) AS n FROM sym_delta WHERE snapshot_id = %s GROUP BY 1, 2", (snap["id"],))}
    return {"snapshot_id": snap["id"], "base_snapshot_id": snap["base_snapshot_id"], "repo": snap["repo"],
            "commit_sha": snap["commit_sha"], "storage": snap["storage"],
            "added": counts.get(("sym", "add"), 0), "removed": counts.get(("sym", "remove"), 0),
            "changed": counts.get(("sym", "change"), 0),
            "edges_added": counts.get(("sym_edge", "add"), 0), "edges_removed": counts.get(("sym_edge", "remove"), 0),
            "created": created}


def reconstruct(conn: psycopg.Connection, snapshot_id: int) -> ExtractResult:
    """The full symbol and edge set of any snapshot, without ids, sorted as extractors sort.

    Raises:
        LookupError: no such snapshot. ValueError: a broken delta chain.
    """
    symbols, edges = _load(conn, snapshot_id)
    return _sorted(symbols, edges)


def materialise(conn: psycopg.Connection, snapshot_id: int) -> int:
    """Ensure sym/sym_edge rows exist for ``snapshot_id`` (no-op for full snapshots).
    Returns the number of sym rows. Called by queries before reading a delta snapshot."""
    snap = _get_snapshot(conn, snapshot_id)
    count = conn.execute("SELECT count(*) AS n FROM sym WHERE snapshot_id = %s", (snapshot_id,)).fetchone()["n"]
    if snap["storage"] == "full" or count:
        return count
    content = reconstruct(conn, snapshot_id)
    with conn.transaction():
        _insert_content(conn, snapshot_id, content)
    return len(content["symbols"])


def verify(conn: psycopg.Connection, repo_path: str | Path, sha: str = "HEAD", *, name: str | None = None) -> dict[str, Any]:
    """Re-extract ``sha`` and diff it against the stored snapshot's ``reconstruct``.

    ``missing`` are items a fresh extraction produces that the stored snapshot lacks; ``extra`` are
    stored items a fresh extraction no longer produces; ``changed`` are symbols present in both
    whose fields differ. Edge entries are ``{"src", "dst", "rel"}`` (an edge whose attrs differ
    appears in both lists).

    Returns:
        ``{"ok": bool, "snapshot_id": int, "missing": [SymKey...], "extra": [SymKey...],
        "changed": [SymKey...], "edges_missing": [...], "edges_extra": [...], "owners_missing": [...],
        "owners_extra": [...]}``; ok means all empty (CODEOWNERS rules are checked too).

    Raises:
        LookupError: ``sha`` was never indexed.
    """
    full_sha = resolve_sha(repo_path, sha)
    repo = name or repo_name(repo_path)
    snap = _snapshot_row(conn, repo, full_sha)
    if snap is None:
        raise LookupError(f"{full_sha} is not indexed for repo {repo!r}; run 'sdlc-graph index' first")
    stored = _load(conn, snap["id"])
    fresh_content, fresh_rules = _extract_commit(repo_path, full_sha)
    d = _diff(stored, _merge([fresh_content]))
    brief = lambda e: {"src": e["src"], "dst": e["dst"], "rel": e["rel"]}  # noqa: E731
    out = {
        "snapshot_id": snap["id"],
        "missing": [key_dict(s) for s in d["add"]],
        "extra": [key_dict(s) for s in d["remove"]],
        "changed": [key_dict(s) for s in d["change"]],
        "edges_missing": [brief(e) for e in d["edge_add"]],
        "edges_extra": [brief(e) for e in d["edge_remove"]],
    }
    stored_owners = {(r["path_glob"], r["handle"], r["ord"]) for r in conn.execute(
        "SELECT path_glob, handle, ord FROM owner WHERE snapshot_id = %s", (snap["id"],))}
    fresh_owners = set(_owner_rows(fresh_rules))
    out["owners_missing"] = [{"path_glob": g, "handle": h, "ord": o} for g, h, o in sorted(fresh_owners - stored_owners)]
    out["owners_extra"] = [{"path_glob": g, "handle": h, "ord": o} for g, h, o in sorted(stored_owners - fresh_owners)]
    out["ok"] = not any(out[k] for k in ("missing", "extra", "changed", "edges_missing", "edges_extra",
                                          "owners_missing", "owners_extra"))
    # the materialised rows of a delta snapshot must agree with reconstruct as well
    if snap["storage"] == "delta":
        has_rows = conn.execute("SELECT 1 FROM sym WHERE snapshot_id = %s LIMIT 1", (snap["id"],)).fetchone()
        if has_rows and _read_full(conn, snap["id"]) != stored:
            out["ok"] = False
            out["changed"].append({"kind": "snapshot", "qualname": "materialised rows differ from reconstruct", "path": ""})
    return {"ok": out.pop("ok"), **out}


def list_snapshots(conn: psycopg.Connection, repo: str | None = None) -> list[dict[str, Any]]:
    """Snapshot rows (id, repo, commit_sha, parent_sha, indexed_at, extractor_version, storage,
    base_snapshot_id), newest first, optionally for one repo."""
    rows = conn.execute(
        "SELECT id, repo, commit_sha, parent_sha, committed_at, indexed_at, extractor_version, storage, base_snapshot_id "
        "FROM snapshot WHERE (%s::text IS NULL OR repo = %s) "
        "ORDER BY committed_at DESC NULLS LAST, ancestors DESC NULLS LAST, indexed_at DESC, id DESC", (repo, repo)).fetchall()
    for r in rows:
        r["indexed_at"] = r["indexed_at"].isoformat()
        r["committed_at"] = r["committed_at"].isoformat() if r["committed_at"] else None
    return rows


def resolve_snapshot(conn: psycopg.Connection, repo: str | None = None, sha: str | None = None) -> int:
    """Snapshot id for (repo, sha); sha may be a unique prefix; with no sha, the latest snapshot
    of ``repo``, meaning the one of the NEWEST COMMIT (``committed_at``; indexing an old commit later
    does not make it "latest"); with neither, the latest snapshot overall.

    Raises:
        LookupError: nothing matches, or a sha prefix is ambiguous.
    """
    if sha:
        like = sha.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute("SELECT id, commit_sha FROM snapshot WHERE (%s::text IS NULL OR repo = %s) "
                            "AND commit_sha LIKE %s ORDER BY id", (repo, repo, like)).fetchall()
        if not rows:
            raise LookupError(f"no snapshot for sha {sha!r}" + (f" in repo {repo!r}" if repo else ""))
        if len({r["commit_sha"] for r in rows}) > 1:
            raise LookupError(f"sha prefix {sha!r} is ambiguous: " + ", ".join(r["commit_sha"][:12] for r in rows[:10]))
        return rows[0]["id"]
    row = conn.execute("SELECT id FROM snapshot WHERE (%s::text IS NULL OR repo = %s) "
                       "ORDER BY committed_at DESC NULLS LAST, ancestors DESC NULLS LAST, indexed_at DESC, id DESC LIMIT 1", (repo, repo)).fetchone()
    if row is None:
        raise LookupError("no snapshots" + (f" for repo {repo!r}" if repo else "") + "; run 'sdlc-graph index' first")
    return row["id"]
