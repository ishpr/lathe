"""``sdlc-graph feed`` (Gas Town's ``gt feed``) and ``sdlc-graph escalations``.

The feed is the ledger read as a timeline: transitions, evidence, runs and decisions, newest
first, optionally narrowed to a feature (the feature and its contracts) or a start time. An
``assumptions`` row shows how many defaults the session applied and the first of them, so a reviewer
sees what was assumed without opening the evidence.
"""

from __future__ import annotations

from typing import Any

import psycopg

from sdlc_graph.ledger import nodes

from . import store


def scope(conn: psycopg.Connection, feature: str | None) -> list[str] | None:
    if not feature:
        return None
    nodes.get_row(conn, feature)  # LookupError for an unknown slug
    kids = [n["node"]["slug"] for n in nodes.neighbours(conn, feature, rel="decomposes_into", direction="out")]
    return [feature, *kids]


def feed(conn: psycopg.Connection, *, feature: str | None = None, since: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    slugs = scope(conn, feature)
    rows = conn.execute(
        """WITH s AS (SELECT id, slug FROM node WHERE %(slugs)s::text[] IS NULL OR slug = ANY(%(slugs)s::text[]))
           SELECT * FROM (
             SELECT t.at, 'transition' AS kind, s.slug, coalesce(t.from_state, '(new)') || ' -> ' || t.to_state || ' by ' || t.actor
                    || coalesce(' (' || t.reason || ')', '') AS what
               FROM transition t JOIN s ON s.id = t.node_id
             UNION ALL
             SELECT e.at, 'evidence', s.slug, e.kind || ' #' || e.id || ' from ' || e.source
                    || CASE WHEN e.kind = 'assumptions' AND jsonb_typeof(e.payload->'assumptions') = 'array'
                            THEN ': ' || jsonb_array_length(e.payload->'assumptions') || ' default(s) applied'
                                 || coalesce(' (' || left(e.payload->'assumptions'->0->>'default_applied', 120)
                                    || coalesce(' in ' || left(e.payload->'assumptions'->0->>'where', 80), '')
                                    || CASE WHEN jsonb_array_length(e.payload->'assumptions') > 1 THEN '; ...' ELSE '' END
                                    || ')', '')
                            WHEN e.kind = 'conformance' AND e.payload->>'source' = 'assumptions'
                            THEN ': assumptions failed conformance' ELSE '' END
               FROM evidence e JOIN s ON s.id = e.node_id
             UNION ALL
             SELECT r.started_at, 'run', s.slug, coalesce(r.step, r.stage, '?') || coalesce(' attempt ' || r.attempt, '')
                    || ' #' || r.id || ' ' || r.requested_model || ' started'
                    || coalesce(' [' || r.executor || '/' || r.auth_source || ']', '') FROM run r JOIN s ON s.id = r.node_id
             UNION ALL
             SELECT r.finished_at, 'run', s.slug, coalesce(r.step, r.stage, '?') || ' #' || r.id || ' ' || coalesce(r.outcome, '')
                    || coalesce(': ' || left(r.summary, 200), '')
                    || coalesce(' [' || r.executor || '/' || r.auth_source || ']', '') FROM run r JOIN s ON s.id = r.node_id WHERE r.finished_at IS NOT NULL
             UNION ALL
             SELECT d.created_at, 'decision', s.slug, coalesce(d.severity || ' ', '') || '#' || d.id || ' opened: ' || left(d.question, 200)
               FROM decision d JOIN s ON s.id = d.node_id
             UNION ALL
             SELECT d.resolved_at, 'decision', s.slug, '#' || d.id || ' resolved by ' || coalesce(d.decided_by, '?') || ': ' || coalesce(d.choice, '')
               FROM decision d JOIN s ON s.id = d.node_id WHERE d.resolved_at IS NOT NULL
           ) f WHERE %(since)s::timestamptz IS NULL OR f.at >= %(since)s::timestamptz
           ORDER BY f.at DESC LIMIT %(limit)s""",
        {"slugs": slugs, "since": since, "limit": limit}).fetchall()
    return [{"at": r["at"].isoformat(), "kind": r["kind"], "slug": r["slug"], "what": r["what"] or ""} for r in rows]


def escalations(conn: psycopg.Connection, *, feature: str | None = None, all_open: bool = False) -> list[dict[str, Any]]:
    """Open decisions with a severity (orchestrator escalations), P0 first; ``all_open`` adds the
    unranked ones too."""
    rows = store.open_escalations(conn, scope(conn, feature))
    keep = rows if all_open else [r for r in rows if r.get("severity")]
    return [{"id": r["id"], "severity": r.get("severity"), "slug": r["slug"], "question": r["question"],
             "options": r.get("options"), "created_at": r.get("created_at"),
             "worktree": (r.get("meta") or {}).get("worktree"),
             "resolve": f"sdlc-graph decision resolve {r['id']} --choice <{'|'.join(map(str, r.get('options') or ['choice']))}> --actor @you"}
            for r in keep]
