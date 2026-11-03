"""Chunking and storage for the retrieval index (docs/DESIGN.md 2.2).

Two sources, both read from tables that already exist:

* **Ledger nodes**, one chunk per *section*, not per file: ``title``, ``acceptance``, ``decisions``,
  ``scope`` (touched symbols, rule path) and one section per free-text ``attrs`` key
  (``riskiest_assumption``, ``guardrail_metric``, ...). The intake scan record (``attrs.intake``)
  is not text people search for and is skipped.
* **Code-graph symbols**: one short summary per symbol of the repo's latest snapshot (kind, qualname,
  path and the scalar ``attrs``). Source code itself is never embedded: grep and the AST answer code
  questions exactly.

:func:`build` embeds only chunks whose text or embedding model changed, repoints unchanged symbol
chunks at the newest snapshot, and deletes chunks whose node or symbol no longer exists. It writes
nothing when no provider is configured.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .embed import Provider, available

MAX_TEXT = 1500           # characters kept per chunk
_SKIP_ATTRS = {"intake"}  # node.attrs keys that are bookkeeping, not searchable text
_SECTION_RE = re.compile(r"[^a-z0-9_]+")


@dataclass
class ChunkSpec:
    """One chunk before it is embedded. ``(ref, section)`` is its identity."""

    source: str                       # 'node' | 'sym'
    ref: str
    repo: str
    kind: str
    section: str
    text: str
    slug: str | None = None
    qualname: str | None = None
    path: str | None = None
    node_id: str | None = None
    snapshot_id: int | None = None
    sym_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.ref, self.section)

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- text helpers


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = re.sub(r"[ \t]+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _scalar(value: Any) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip():
        return _clip(value, 200)
    return None


def _attr_text(value: Any) -> str | None:
    """Flatten a node.attrs value into text, or None when it carries nothing searchable."""
    s = _scalar(value)
    if s is not None:
        return s
    if isinstance(value, list):
        parts = [p for p in (_attr_text(v) for v in value) if p]
        return "; ".join(parts) or None
    if isinstance(value, dict):
        parts = [f"{k}: {t}" for k, v in value.items() if (t := _attr_text(v))]
        return "; ".join(parts) or None
    return None


def _section_name(key: str) -> str:
    return _SECTION_RE.sub("_", key.lower()).strip("_")[:40] or "attr"


# --------------------------------------------------------------------------- nodes


def node_chunks(conn: psycopg.Connection, repo: str | None = None) -> list[ChunkSpec]:
    """Section chunks for every ledger node (of ``repo`` when given), in a stable order."""
    nodes = conn.execute("SELECT * FROM node WHERE (%(r)s::text IS NULL OR repo = %(r)s) ORDER BY slug",
                         {"r": repo}).fetchall()
    decisions: dict[Any, list[dict[str, Any]]] = {}
    for d in conn.execute("SELECT * FROM decision ORDER BY node_id, id").fetchall():
        decisions.setdefault(d["node_id"], []).append(d)
    out: list[ChunkSpec] = []
    for n in nodes:
        base = dict(source="node", ref=f"node:{n['slug']}", repo=n["repo"], kind=n["kind"], slug=n["slug"],
                    path=n["path"], node_id=str(n["id"]), meta={"title": n["title"], "state": n["state"],
                                                                "tier": n["tier"]})

        def add(section: str, text: str | None) -> None:
            if text and text.strip():
                out.append(ChunkSpec(section=section, text=_clip(text), **base))

        add("title", f"{n['kind']} {n['slug']}: {n['title']} (state {n['state']}"
                     + (f", tier {n['tier']}" if n["tier"] else "") + ")")
        acceptance = []
        for a in n["acceptance"] or []:
            if isinstance(a, dict):
                line = " ".join(p for p in (str(a.get("id") or ""), str(a.get("text") or "")) if p)
                if a.get("test"):
                    line += f" (test {a['test']})"
                acceptance.append(line)
            elif isinstance(a, str):
                acceptance.append(a)
        add("acceptance", "Acceptance. " + " | ".join(acceptance) if acceptance else None)
        dec = []
        for d in decisions.get(n["id"], []):
            opts = ", ".join(str(o.get("label") if isinstance(o, dict) else o) for o in (d["options"] or []))
            line = f"Question: {d['question']}"
            if opts:
                line += f" Options: {opts}."
            if d["recommendation"]:
                line += f" Recommended: {d['recommendation']}."
            if d["choice"]:
                line += f" Chosen: {d['choice']}."
            dec.append(line)
        add("decisions", "Decisions. " + " ".join(dec) if dec else None)
        scope = []
        if n["path"]:
            scope.append(f"path {n['path']}")
        if n["touched_symbols"]:
            scope.append("touches " + ", ".join(n["touched_symbols"]))
        caps = n["capabilities"] or {}
        if isinstance(caps, dict) and caps.get("paths"):
            scope.append("allowed paths " + ", ".join(map(str, caps["paths"])))
        add("scope", "Scope. " + "; ".join(scope) if scope else None)
        for key, value in sorted((n["attrs"] or {}).items()):
            if key in _SKIP_ATTRS:
                continue
            text = _attr_text(value)
            if text:
                add(_section_name(key), f"{key.replace('_', ' ').capitalize()}. {text}")
    return out


# --------------------------------------------------------------------------- symbols


def symbol_summary(sym: dict[str, Any]) -> str:
    """One short text per symbol: kind, qualname, path, and its scalar attrs (``key: value``)."""
    lines = [f"{sym['kind']} {sym['qualname']}", f"file {sym['path']}"]
    attrs = sym.get("attrs") or {}
    if sym["kind"] == "route" and attrs.get("method") and attrs.get("path"):
        lines.insert(1, f"route {attrs['method']} {attrs['path']}")
    facts = []
    for key in sorted(attrs):
        value = attrs[key]
        if key == "confidence" and value != "low":
            continue
        text = _attr_text(value)
        if text:
            facts.append(f"{key}: {_clip(text, 120)}")
    if facts:
        lines.append("; ".join(facts))
    return _clip("\n".join(lines))


def symbol_chunks(conn: psycopg.Connection, snapshot_id: int) -> list[ChunkSpec]:
    """Summary chunks for every symbol of ``snapshot_id`` (delta snapshots are materialised first)."""
    from sdlc_graph.codegraph import snapshot

    snapshot.materialise(conn, snapshot_id)
    repo = conn.execute("SELECT repo FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()
    if repo is None:
        raise LookupError(f"no snapshot {snapshot_id}")
    repo = repo["repo"]
    rows = conn.execute("SELECT id, kind, name, qualname, path, line_start, line_end, attrs FROM sym "
                        "WHERE snapshot_id = %s ORDER BY kind, qualname, path", (snapshot_id,)).fetchall()
    return [ChunkSpec(source="sym", ref=f"sym:{repo}:{r['kind']}:{r['qualname']}@{r['path']}", repo=repo,
                      kind=r["kind"], section="summary", text=symbol_summary(r), qualname=r["qualname"],
                      path=r["path"], snapshot_id=snapshot_id, sym_id=r["id"],
                      meta={"name": r["name"], "line_start": r["line_start"], "line_end": r["line_end"]})
            for r in rows]


# --------------------------------------------------------------------------- store


def vector_literal(vec: list[float]) -> str:
    """pgvector text form of a vector: ``[0.1,0.2,...]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def has_table(conn: psycopg.Connection) -> bool:
    """True when the ``chunk`` table exists in the connection's schema (sql/005 applied)."""
    return conn.execute("SELECT to_regclass('chunk') AS t").fetchone()["t"] is not None


def plan(conn: psycopg.Connection, specs: list[ChunkSpec], model: str, *, repos: list[str],
         node_repo: str | None) -> dict[str, Any]:
    """Compare ``specs`` with what is stored: which to embed, which to repoint, which to delete."""
    existing = {(r["ref"], r["section"]): r for r in conn.execute(
        "SELECT ref, section, text_hash, embed_model, snapshot_id, sym_id FROM chunk "
        "WHERE (source = 'node' AND (%(nr)s::text IS NULL OR repo = %(nr)s)) "
        "   OR (source = 'sym' AND repo = ANY(%(repos)s))", {"nr": node_repo, "repos": repos}).fetchall()}
    embed, repoint = [], []
    for s in specs:
        old = existing.get(s.key)
        if old is None or old["text_hash"] != s.text_hash or old["embed_model"] != model:
            embed.append(s)
        elif s.source == "sym" and (old["snapshot_id"] != s.snapshot_id or old["sym_id"] != s.sym_id):
            repoint.append(s)
    wanted = {s.key for s in specs}
    stale = [k for k in existing if k not in wanted]
    return {"embed": embed, "repoint": repoint, "stale": stale, "unchanged": len(specs) - len(embed) - len(repoint)}


def build(conn: psycopg.Connection, provider: Provider, *, repo: str | None = None, dry_run: bool = False,
          max_chunks: int | None = None) -> dict[str, Any]:
    """Chunk every ledger node and the latest snapshot's symbols, and embed what changed.

    Args:
        conn: autocommit connection (each batch commits on its own, so a failed run resumes).
        provider: the embedding provider. With the ``none`` provider and ``dry_run=False`` this raises
            :class:`~sdlc_graph.index.embed.ProviderUnavailable` before anything is written.
        repo: repo name; limits nodes to that repo and symbols to its latest snapshot. Default: all
            nodes, and the latest snapshot of every repo that has one.
        dry_run: count what would be embedded; call no provider and write nothing.
        max_chunks: refuse (ValueError) when more than this many chunks would be embedded.

    Returns:
        ``{"provider", "model", "repos", "chunks", "embedded", "repointed", "unchanged", "deleted",
        "dry_run", "chars"}``; ``embedded`` is the number sent to the provider (the number that would
        be, when ``dry_run``).
    """
    if not has_table(conn):
        raise LookupError("the chunk table does not exist; run 'sdlc-graph db init' to apply sql/005_index.sql")
    if not dry_run and not available(provider):
        provider.embed([])  # raises ProviderUnavailable with the instruction; writes nothing
    from sdlc_graph.codegraph import snapshot

    repos = [repo] if repo else [r["repo"] for r in conn.execute("SELECT DISTINCT repo FROM snapshot ORDER BY 1").fetchall()]
    specs = node_chunks(conn, repo)
    snapshots: dict[str, int] = {}
    for r in repos:
        try:
            snapshots[r] = snapshot.resolve_snapshot(conn, r)
        except LookupError:
            continue
        specs.extend(symbol_chunks(conn, snapshots[r]))
    model = provider.model
    p = plan(conn, specs, model, repos=repos, node_repo=repo)
    result = {"provider": provider.name, "model": model, "repos": sorted(snapshots), "chunks": len(specs),
              "embedded": len(p["embed"]), "repointed": len(p["repoint"]), "unchanged": p["unchanged"],
              "deleted": len(p["stale"]), "dry_run": dry_run, "chars": sum(len(s.text) for s in p["embed"])}
    if dry_run:
        return result
    if max_chunks is not None and len(p["embed"]) > max_chunks:
        raise ValueError(f"{len(p['embed'])} chunks need embedding, more than --max-chunks {max_chunks}")
    for i in range(0, len(p["embed"]), provider.batch_size):
        batch = p["embed"][i:i + provider.batch_size]
        vectors = provider.embed([s.text for s in batch])
        with conn.transaction():
            for s, vec in zip(batch, vectors):
                conn.execute(
                    "INSERT INTO chunk (source, ref, repo, kind, slug, qualname, path, node_id, snapshot_id, sym_id,"
                    " section, text, text_hash, embedding, embed_model, meta) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::public.vector,%s,%s) "
                    "ON CONFLICT (ref, section) DO UPDATE SET repo = EXCLUDED.repo, kind = EXCLUDED.kind,"
                    " slug = EXCLUDED.slug, qualname = EXCLUDED.qualname, path = EXCLUDED.path,"
                    " node_id = EXCLUDED.node_id, snapshot_id = EXCLUDED.snapshot_id, sym_id = EXCLUDED.sym_id,"
                    " text = EXCLUDED.text, text_hash = EXCLUDED.text_hash, embedding = EXCLUDED.embedding,"
                    " embed_model = EXCLUDED.embed_model, meta = EXCLUDED.meta, at = now()",
                    (s.source, s.ref, s.repo, s.kind, s.slug, s.qualname, s.path, s.node_id, s.snapshot_id, s.sym_id,
                     s.section, s.text, s.text_hash, vector_literal(vec), model, Jsonb(s.meta)))
    with conn.transaction():
        for s in p["repoint"]:
            conn.execute("UPDATE chunk SET snapshot_id = %s, sym_id = %s, meta = %s WHERE ref = %s AND section = %s",
                         (s.snapshot_id, s.sym_id, Jsonb(s.meta), s.ref, s.section))
        for ref, section in p["stale"]:
            conn.execute("DELETE FROM chunk WHERE ref = %s AND section = %s", (ref, section))
    return result


def stats(conn: psycopg.Connection) -> dict[str, Any]:
    """Chunk counts by source and embed_model (for ``index status``-style output and tests)."""
    if not has_table(conn):
        return {"table": False, "chunks": 0, "by": []}
    rows = conn.execute("SELECT source, embed_model, count(*) AS n FROM chunk GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    return {"table": True, "chunks": sum(r["n"] for r in rows), "by": rows}
