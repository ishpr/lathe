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


# --------------------------------------------------------------------------- the index's uses at intake (docs/DESIGN.md 2.2)

#: Cosine distance (1 - similarity) under which an open contract's chunk counts as the same ticket again.
DEDUPE_DISTANCE = 0.15
PRECEDENT_LIMIT = 3
RULE_LIMIT = 5
_GLOB_CHARS = "*?[{"
_EXT = re.compile(r"\.([A-Za-z0-9]+)$")


def similar_open(conn: psycopg.Connection, texts: Sequence[str], *, repo: str, kind: str = "contract",
                 closed_states: Sequence[str] = (), exclude_slugs: Sequence[str] = (),
                 max_distance: float = DEDUPE_DISTANCE, provider: Provider | None = None,
                 limit: int = 5) -> list[dict[str, Any]]:
    """Open nodes of ``kind`` in ``repo`` with a chunk within ``max_distance`` (cosine distance) of any of
    ``texts``, nearest first: ``{slug, title, state, distance, section}``. Only chunks of the provider's own
    embed model are compared. Returns ``[]`` (never raises) with no table, no chunks, no usable provider or
    a provider failure, so the caller falls back to its lexical check."""
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts or not has_table(conn):
        return []
    provider = provider if provider is not None else get_provider()
    if not available(provider):
        return []
    try:
        vecs = provider.embed(texts, query=True)
    except (ProviderUnavailable, ProviderError):
        return []
    best: dict[str, dict[str, Any]] = {}
    for v in vecs:
        rows = conn.execute(
            "SELECT c.slug, c.section, n.title, n.state, c.embedding <=> %(v)s::public.vector AS dist FROM chunk c "
            "JOIN node n ON n.id = c.node_id WHERE c.source = 'node' AND c.kind = %(kind)s AND c.repo = %(repo)s "
            "AND c.embed_model = %(model)s AND NOT (n.state = ANY(%(closed)s)) AND NOT (n.slug = ANY(%(skip)s)) "
            "AND c.embedding <=> %(v)s::public.vector <= %(max)s ORDER BY dist, c.slug LIMIT %(lim)s",
            {"v": vector_literal(v), "kind": kind, "repo": repo, "model": provider.model, "closed": list(closed_states),
             "skip": list(exclude_slugs), "max": max_distance, "lim": limit * 4}).fetchall()
        for r in rows:
            d = float(r["dist"])
            if r["slug"] not in best or d < best[r["slug"]]["distance"]:
                best[r["slug"]] = {"slug": r["slug"], "title": r["title"], "state": r["state"],
                                   "distance": round(d, 4), "section": r["section"]}
    return sorted(best.values(), key=lambda h: (h["distance"], h["slug"]))[:limit]


def precedent(conn: psycopg.Connection, repo: str, touched: Sequence[str], *, exclude_slug: str | None = None,
              limit: int = PRECEDENT_LIMIT) -> list[dict[str, Any]]:
    """The last ``limit`` contracts of ``repo`` (most recently updated first) whose touched paths or symbols
    overlap ``touched``: ``{slug, title, state, overlap, decisions: [{question, choice}], tests, findings}``.

    ``decisions`` are the resolved ones (the ``decision`` table, else the contract.md table on the node),
    ``tests`` the acceptance test names, ``findings`` whatever reviewer findings the node's evidence carries
    (a ``findings`` list in a ``review_approval`` or other payload). Reads only the ledger, so it works
    with the index off; ``[]`` for no touched paths."""
    from sdlc_graph.ledger import nodes

    if not touched:
        return []
    rows = conn.execute(
        "SELECT id, slug, title, state, touched_symbols, acceptance, attrs FROM node WHERE kind = 'contract' "
        "AND repo = %s AND (%s::text IS NULL OR slug <> %s) AND cardinality(touched_symbols) > 0 "
        "ORDER BY updated_at DESC, slug LIMIT 500", (repo, exclude_slug, exclude_slug)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        hit = nodes.symbols_overlap(list(touched), list(r["touched_symbols"] or []))
        if not hit:
            continue
        decided = [{"question": d["question"], "choice": d["choice"]} for d in conn.execute(
            "SELECT question, choice FROM decision WHERE node_id = %s AND choice IS NOT NULL ORDER BY id",
            (r["id"],)).fetchall()]
        if not decided:
            decided = [{"question": d.get("question"), "choice": d.get("value")}
                       for d in (r["attrs"] or {}).get("decisions") or [] if isinstance(d, dict) and d.get("value")]
        tests = [str(a.get("test") or a.get("name")) for a in r["acceptance"] or []
                 if isinstance(a, dict) and (a.get("test") or a.get("name"))]
        findings: list[Any] = []
        for e in conn.execute("SELECT payload FROM evidence WHERE node_id = %s AND payload ? 'findings' "
                              "ORDER BY at, id", (r["id"],)).fetchall():
            f = e["payload"].get("findings")
            findings += f if isinstance(f, list) else [f]
        out.append({"slug": r["slug"], "title": r["title"], "state": r["state"], "overlap": hit,
                    "decisions": decided, "tests": tests, "findings": findings})
        if len(out) >= limit:
            break
    return out


def _literal_dir(glob: str) -> str:
    """The literal directory prefix of a glob (``app/auth/**`` -> ``app/auth/``; ``**/*.py`` -> ``''``)."""
    cut = min((glob.find(c) for c in _GLOB_CHARS if c in glob), default=len(glob))
    head = glob[:cut]
    return head if cut == len(glob) and head.endswith("/") else head[: head.rfind("/") + 1]


def globs_match(rule_globs: Sequence[str], allowed: Sequence[str]) -> bool:
    """Whether a rule's ``paths:`` globs reach any of the contract's allowed paths. A rule with no globs
    applies everywhere. Two globs are compared by their literal directory prefixes (one contains the other)
    and, when both name a file extension, by that extension; this is a screen, not a path matcher."""
    import fnmatch

    if not rule_globs:
        return True
    for rg in rule_globs:
        rg = rg.strip().lstrip("./")
        rd, rext = _literal_dir(rg), _EXT.search(rg)
        for a in allowed:
            a = str(a).strip().lstrip("./")
            if "@" in a:  # a symbol key: kind:qualname@path
                a = a.rsplit("@", 1)[1]
            ad, aext = _literal_dir(a), _EXT.search(a)
            if fnmatch.fnmatch(a, rg) or (not any(c in rg for c in _GLOB_CHARS) and a.startswith(rg.rstrip("/") + "/")):
                return True
            if (ad.startswith(rd) or rd.startswith(ad)) and not (
                    rext and aext and not any(c in rext.group(1) for c in _GLOB_CHARS) and rext.group(1) != aext.group(1)):
                return True
    return False


def rule_globs(row: dict[str, Any]) -> list[str]:
    """A rule node's path globs: ``attrs.paths`` (frontmatter ``paths:``), else its ``path`` column."""
    attrs = row.get("attrs") or {}
    if "paths" in attrs:  # a synced rule file: its path column is the file, and an empty list means everywhere
        return [str(p) for p in attrs["paths"] or []]
    return [row["path"]] if row.get("path") else []


def applicable_rules(conn: psycopg.Connection, text: str, allowed_paths: Sequence[str], *, repo: str,
                     limit: int = RULE_LIMIT, provider: Provider | None = None) -> list[dict[str, Any]]:
    """Rule nodes of ``repo`` whose ``paths:`` globs match ``allowed_paths``, ranked by hybrid search against
    ``text`` (the ticket); rules the search does not reach follow, by slug. Each is ``{slug, title, file,
    paths, body, score, mode}``; ``mode`` is ``hybrid``, ``lexical`` or ``paths`` (the rule matched on its
    globs only: no chunks, so no ranking). Works with the index off."""
    rows = conn.execute("SELECT slug, title, path, attrs FROM node WHERE kind = 'rule' AND repo = %s ORDER BY slug",
                        (repo,)).fetchall()
    matched = {r["slug"]: r for r in rows if globs_match(rule_globs(r), allowed_paths)}
    if not matched:
        return []
    ranked: dict[str, dict[str, Any]] = {}
    if text.strip() and has_chunks(conn, source="node", repo=repo):
        for h in search(conn, text, provider=provider, scope="node", kind="rule", repo=repo, limit=100):
            if h["slug"] in matched and h["slug"] not in ranked:
                ranked[h["slug"]] = h
    order = list(ranked) + [s for s in matched if s not in ranked]
    out = []
    for slug in order[:limit]:
        r, h = matched[slug], ranked.get(slug)
        attrs = r["attrs"] or {}
        out.append({"slug": slug, "title": r["title"], "file": attrs.get("source_file") or r["path"],
                    "paths": rule_globs(r), "body": snippet(str(attrs.get("body") or r["title"]), 600),
                    "score": h["score"] if h else None, "mode": h["mode"] if h else "paths"})
    return out


def intake_context(conn: psycopg.Connection, *, repo: str, title: str, ticket_text: str = "",
                   paths: Sequence[str] = (), exclude_slugs: Sequence[str] = (),
                   provider: Provider | None = None) -> dict[str, Any]:
    """What the index says about a ticket before a contract exists (``/factory:ticket``, and what
    ``contract_draft`` runs itself): ``{duplicates, precedent, rules, mode}``.

    ``duplicates`` are open contracts within :data:`DEDUPE_DISTANCE` of the title or ticket text (embedding
    only; ``contract_draft`` adds the title-trigram and external-id checks), ``precedent`` up to 3 prior
    contracts on the same ``paths`` and ``rules`` the rule files whose ``paths:`` match them, ranked against the
    ticket. ``mode`` is ``hybrid`` when an embedding provider answered, else ``lexical`` (precedent and rules
    still work; duplicates is then empty). Reads only; each part is ``[]`` when the index or ledger has nothing.
    """
    from sdlc_graph.ledger.contract_md import touched_from_paths
    from sdlc_graph.ledger.transitions import terminal_states

    text = f"{title}\n{ticket_text or ''}".strip()
    provider = provider if provider is not None else get_provider()
    touched = touched_from_paths(list(paths))[0] if paths else []  # globs reduce to their directory
    return {"duplicates": similar_open(conn, [title, text[:1500]], repo=repo, closed_states=sorted(terminal_states("contract")),
                                      exclude_slugs=list(exclude_slugs), provider=provider),
            "precedent": precedent(conn, repo, touched, exclude_slug=(list(exclude_slugs) or [None])[0]),
            "rules": applicable_rules(conn, text, list(paths), repo=repo, provider=provider),
            "mode": "hybrid" if available(provider) else "lexical"}


_CLOSING_RELS = ("caused_by", "governed_by", "supersedes")


def similar_incidents(conn: psycopg.Connection, text: str, k: int = 3, *, repo: str | None = None,
                      provider: Provider | None = None) -> list[dict[str, Any]]:
    """Past incidents nearest ``text`` and what closed each (an incident is the *intent* a new one is
    triaged against: if a rule or test already covers it, the ticket is "the rule did not fire").

    Incidents are ranked by hybrid search over their chunks when the index holds any, else by trigram
    similarity of title (then ILIKE) so it works with no provider. ``closed_by`` lists the nodes linked to
    the incident by ``caused_by``, ``governed_by`` or ``supersedes`` edges, either direction:
    ``{slug, kind, title, state, rel, direction, tests}`` (``tests`` the acceptance test names of a
    contract). Each hit is ``{slug, title, state, repo, score, mode, closed_by}``.

    Raises:
        ValueError: empty text or ``k`` < 1.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("search text is empty")
    if not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be >= 1, not {k!r}")
    if has_chunks(conn, source="node", repo=repo):
        hits = [{"slug": h["slug"], "score": h["score"], "mode": h["mode"]} for h in
                search(conn, text, provider=provider, scope="node", kind="incident", repo=repo, limit=k)]
    else:
        hits = []
    if not hits:  # no index, or none of its chunks is an incident: trigram / substring over the title
        hits = [{"slug": h["slug"], "score": h["score"], "mode": h["mode"]}
                for h in node_search(conn, text, kind="incident", repo=repo, limit=k)]
    out = []
    for h in hits:
        n = conn.execute("SELECT id, slug, title, state, repo FROM node WHERE slug = %s", (h["slug"],)).fetchone()
        if n is None:
            continue
        linked = conn.execute(
            "SELECT m.slug, m.kind, m.title, m.state, m.acceptance, e.rel, 'out' AS direction FROM edge e "
            "JOIN node m ON m.id = e.dst WHERE e.src = %(id)s AND e.rel = ANY(%(rels)s) UNION ALL "
            "SELECT m.slug, m.kind, m.title, m.state, m.acceptance, e.rel, 'in' FROM edge e "
            "JOIN node m ON m.id = e.src WHERE e.dst = %(id)s AND e.rel = ANY(%(rels)s) ORDER BY 6, 1",
            {"id": n["id"], "rels": list(_CLOSING_RELS)}).fetchall()
        closed_by = [{"slug": m["slug"], "kind": m["kind"], "title": m["title"], "state": m["state"], "rel": m["rel"],
                      "direction": m["direction"],
                      "tests": [str(a.get("test") or a.get("name")) for a in m["acceptance"] or []
                                if isinstance(a, dict) and (a.get("test") or a.get("name"))]} for m in linked]
        out.append({"slug": n["slug"], "title": n["title"], "state": n["state"], "repo": n["repo"],
                    "score": h["score"], "mode": h["mode"], "closed_by": closed_by})
    return out
