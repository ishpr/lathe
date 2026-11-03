"""Hybrid retrieval over the chunk table: cosine over the HNSW index plus full-text ``ts_rank``,
merged by reciprocal rank fusion (RRF). Ticket text is full of identifiers that embeddings blur, so
neither ranking is trusted alone.

``search`` is the one entry point. ``graph_search`` and ``node_search`` are what the MCP tools call:
they use the index when it holds chunks for the thing asked about, and otherwise fall back to the
existing trigram path (``queries.search`` for symbols, a title/slug match for nodes). With no
embedding provider or key at query time the index still answers, lexical only (``mode: "lexical"``).
"""

from __future__ import annotations

import re
from typing import Any, Hashable, Sequence

import psycopg

from .chunk import has_table, vector_literal
from .embed import Provider, ProviderError, ProviderUnavailable, available, get_provider

RRF_K = 60  # the constant from the original RRF paper; flattens the head so one list cannot dominate
_WORD = re.compile(r"[A-Za-z0-9]{2,}")


def rrf(rankings: Sequence[Sequence[Hashable]], *, k: int = RRF_K) -> dict[Hashable, float]:
    """Reciprocal rank fusion: each item scores ``sum(1 / (k + rank))`` over the lists it appears in
    (rank is 1-based). Pure; the ranked lists are best-first."""
    scores: dict[Hashable, float] = {}
    for ranking in rankings:
        seen: set[Hashable] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def or_query(text: str) -> str:
    """A ``to_tsquery`` string that ORs every alphanumeric word of ``text`` (empty if none).
    Only ``[A-Za-z0-9]`` survive, so nothing in a user's text can be a tsquery operator."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(text)))
    return " | ".join(words[:32])


def has_chunks(conn: psycopg.Connection, *, source: str | None = None, snapshot_id: int | None = None,
               repo: str | None = None) -> bool:
    """True when the chunk table holds at least one matching row. False when the table is missing."""
    if not has_table(conn):
        return False
    row = conn.execute(
        "SELECT 1 FROM chunk WHERE (%(s)s::text IS NULL OR source = %(s)s) "
        "AND (%(snap)s::bigint IS NULL OR snapshot_id = %(snap)s) AND (%(r)s::text IS NULL OR repo = %(r)s) LIMIT 1",
        {"s": source, "snap": snapshot_id, "r": repo}).fetchone()
    return row is not None


_FILTER = """(%(scope)s::text IS NULL OR source = %(scope)s)
  AND (%(kind)s::text IS NULL OR kind = %(kind)s)
  AND (%(repo)s::text IS NULL OR repo = %(repo)s)
  AND (source <> 'sym' OR %(snap)s::bigint IS NULL OR snapshot_id = %(snap)s)"""


def search(conn: psycopg.Connection, text: str, *, provider: Provider | None = None, scope: str | None = None,
           kind: str | None = None, repo: str | None = None, snapshot_id: int | None = None, limit: int = 20,
           pool: int = 50, k: int = RRF_K) -> list[dict[str, Any]]:
    """Hybrid search; one hit per node or symbol (its best chunk), best first.

    Args:
        text: the query.
        provider: embeds the query (default :func:`get_provider`). Without one, or when it fails, the
            result is lexical only.
        scope: ``"node"``, ``"sym"`` or None for both.
        kind: node kind or symbol kind filter. repo: repo name filter.
        snapshot_id: restrict symbol chunks to this snapshot.
        limit: hits returned. pool: candidates taken from each ranking before fusion.

    Returns:
        Dicts with ``ref, source, kind, repo, slug, qualname, path, section, text, meta, score`` (the RRF
        score), ``mode`` (``hybrid`` or ``lexical``), ``vector_rank``/``lexical_rank`` (None when absent
        from that ranking), ``cosine`` (similarity, when the vector ranking has it) and ``sections``
        (every section of that ref that ranked).

    Raises:
        ValueError: empty text, bad scope or limit.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("search text is empty")
    if scope not in (None, "node", "sym"):
        raise ValueError(f"scope must be 'node', 'sym' or omitted, not {scope!r}")
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be >= 1, not {limit!r}")
    if not has_table(conn):
        return []
    params: dict[str, Any] = {"scope": scope, "kind": kind, "repo": repo, "snap": snapshot_id, "pool": pool}

    vec_ranked: list[int] = []
    cosine: dict[int, float] = {}
    mode = "lexical"
    provider = provider if provider is not None else get_provider()
    if available(provider):
        try:
            qvec = provider.embed([text], query=True)[0]
        except (ProviderUnavailable, ProviderError):
            qvec = None
        if qvec is not None:
            mode = "hybrid"
            rows = conn.execute(
                f"SELECT id, 1 - (embedding <=> %(v)s::public.vector) AS cos FROM chunk "
                f"WHERE {_FILTER} AND embed_model = %(model)s ORDER BY embedding <=> %(v)s::public.vector, id "
                f"LIMIT %(pool)s", {**params, "v": vector_literal(qvec), "model": provider.model}).fetchall()
            vec_ranked = [r["id"] for r in rows]
            cosine = {r["id"]: float(r["cos"]) for r in rows}

    lex_ranked: list[int] = []
    q = or_query(text)
    if q:
        rows = conn.execute(
            f"SELECT id FROM chunk, to_tsquery('english', %(q)s) AS tq WHERE tsv @@ tq AND {_FILTER} "
            f"ORDER BY ts_rank(tsv, tq) DESC, id LIMIT %(pool)s", {**params, "q": q}).fetchall()
        lex_ranked = [r["id"] for r in rows]

    fused = rrf([vec_ranked, lex_ranked], k=k)
    if not fused:
        return []
    ids = sorted(fused, key=lambda i: (-fused[i], i))
    details = {r["id"]: r for r in conn.execute(
        "SELECT id, source, ref, kind, repo, slug, qualname, path, snapshot_id, sym_id, node_id::text AS node_id,"
        " section, text, meta FROM chunk WHERE id = ANY(%s)", (ids,)).fetchall()}
    vrank = {cid: i for i, cid in enumerate(vec_ranked, start=1)}
    lrank = {cid: i for i, cid in enumerate(lex_ranked, start=1)}
    hits: dict[str, dict[str, Any]] = {}
    for cid in ids:
        d = details.get(cid)
        if d is None:
            continue
        hit = hits.get(d["ref"])
        if hit is not None:
            hit["sections"].append(d["section"])
            continue
        hits[d["ref"]] = {**{key: d[key] for key in ("ref", "source", "kind", "repo", "slug", "qualname", "path",
                                                       "snapshot_id", "sym_id", "node_id", "section", "text", "meta")},
                          "score": round(fused[cid], 6), "mode": mode, "vector_rank": vrank.get(cid),
                          "lexical_rank": lrank.get(cid),
                          "cosine": round(cosine[cid], 4) if cid in cosine else None, "sections": [d["section"]]}
        if len(hits) >= limit:
            break
    return list(hits.values())


def snippet(text: str, width: int = 160) -> str:
    """First line-ish of a chunk for tables."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


# --------------------------------------------------------------------------- MCP tool bodies


def graph_search(conn: psycopg.Connection, snapshot_id: int, text: str, *, kind: str | None = None, limit: int = 20,
                 provider: Provider | None = None) -> list[dict[str, Any]]:
    """Symbol search for ``graph_search``: hybrid when the snapshot has symbol chunks, else the trigram
    path in :func:`sdlc_graph.codegraph.queries.search`. Hits keep that path's sym shape
    (``kind, name, qualname, path, line_start, line_end, score``) plus ``mode`` and ``section``."""
    from sdlc_graph.codegraph import queries

    if not has_chunks(conn, source="sym", snapshot_id=snapshot_id):
        return queries.search(conn, snapshot_id, text, kind=kind, limit=limit)
    if kind is not None:
        from sdlc_graph.shapes import SYM_KINDS

        if kind not in SYM_KINDS:
            raise ValueError(f"unknown kind {kind!r}; valid: {', '.join(SYM_KINDS)}")
    hits = search(conn, text, provider=provider, scope="sym", kind=kind, snapshot_id=snapshot_id, limit=limit)
    out = []
    for h in hits:
        meta = h["meta"] or {}
        out.append({"id": h["sym_id"], "snapshot_id": h["snapshot_id"], "kind": h["kind"],
                    "name": meta.get("name") or (h["qualname"] or "").rsplit(".", 1)[-1], "qualname": h["qualname"],
                    "path": h["path"], "line_start": meta.get("line_start"), "line_end": meta.get("line_end"),
                    "score": h["score"], "mode": h["mode"], "cosine": h["cosine"]})
    return out


def node_search(conn: psycopg.Connection, text: str, *, kind: str | None = None, repo: str | None = None,
                limit: int = 20, provider: Provider | None = None) -> list[dict[str, Any]]:
    """Ledger node search for ``node_search``: hybrid over node chunks when any exist (for ``repo``),
    else a title/slug match (ILIKE plus trigram similarity when pg_trgm is installed).

    Returns:
        Dicts ``{slug, kind, title, repo, state, score, mode, section, snippet}``; ``mode`` is ``hybrid``,
        ``lexical`` or ``trigram`` (the fallback).
    """
    from sdlc_graph.shapes import NODE_KINDS

    text = (text or "").strip()
    if not text:
        raise ValueError("search text is empty")
    if kind is not None and kind not in NODE_KINDS:
        raise ValueError(f"unknown kind {kind!r}; valid: {', '.join(NODE_KINDS)}")
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be >= 1, not {limit!r}")
    if has_chunks(conn, source="node", repo=repo):
        hits = search(conn, text, provider=provider, scope="node", kind=kind, repo=repo, limit=limit)
        return [{"slug": h["slug"], "kind": h["kind"], "title": (h["meta"] or {}).get("title"), "repo": h["repo"],
                 "state": (h["meta"] or {}).get("state"), "score": h["score"], "mode": h["mode"],
                 "section": h["section"], "snippet": snippet(h["text"])} for h in hits]
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    has_trgm = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone() is not None
    sim = "GREATEST(similarity(title, %(t)s), similarity(slug, %(t)s))" if has_trgm else "0"
    cond = "OR title %% %(t)s OR slug %% %(t)s" if has_trgm else ""
    rows = conn.execute(
        f"SELECT slug, kind, title, repo, state, GREATEST(CASE WHEN lower(slug) = lower(%(t)s) THEN 1.0 "
        f"WHEN title ILIKE %(c)s THEN 0.8 WHEN slug ILIKE %(c)s THEN 0.7 ELSE 0 END, LEAST({sim}, 0.5)) AS score "
        f"FROM node WHERE (%(kind)s::text IS NULL OR kind = %(kind)s) AND (%(repo)s::text IS NULL OR repo = %(repo)s) "
        f"AND (title ILIKE %(c)s OR slug ILIKE %(c)s {cond}) ORDER BY score DESC, slug LIMIT %(limit)s",
        {"t": text, "c": f"%{esc}%", "kind": kind, "repo": repo, "limit": limit}).fetchall()
    return [{**r, "score": round(float(r["score"]), 4), "mode": "trigram", "section": "title", "snippet": r["title"]}
            for r in rows]
