"""Ledger access the orchestrator needs beyond the ledger package: the orchestrated run columns,
the per-feature lock row and escalation decisions with a severity.

Everything here goes through ``ledger.runs`` / ``ledger.decisions`` for the row itself and only
adds the columns ``sql/004_orchestrator.sql`` introduced.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import sys
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.ledger import decisions, runs
from sdlc_graph.ledger.nodes import node_id

from . import ACTOR

RUN_COLUMNS = ("session_id", "pid", "start_commit", "log_path", "summary", "last_assistant_message", "step", "attempt")
SEVERITIES = ("P0", "P1", "P2")


def _json(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            v = v.isoformat()
        elif hasattr(v, "is_finite"):  # Decimal
            v = float(v)
        out[k] = v
    return out


# --------------------------------------------------------------------------- runs


def start_run(conn: psycopg.Connection, slug: str, *, step: str, stage: str | None, model: str, attempt: int,
              meta: dict[str, Any] | None = None, executor: str | None = None, auth_source: str | None = None,
              **cols: Any) -> dict[str, Any]:
    """``runs.start`` plus the orchestrator columns (``executor`` and ``auth_source`` are recorded on the row
    itself). Returns the full run row."""
    r = runs.start(conn, stage=stage, requested_model=model, slug=slug, agent_type="orchestrator", meta=meta,
                   executor=executor, auth_source=auth_source)
    update_run(conn, r["id"], step=step, attempt=attempt, **cols)
    return get_run(conn, r["id"])


def update_run(conn: psycopg.Connection, run_id: int, *, meta: dict[str, Any] | None = None, **cols: Any) -> None:
    bad = set(cols) - set(RUN_COLUMNS)
    if bad:
        raise ValueError(f"unknown run columns {sorted(bad)}")
    sets = [f"{c} = %s" for c in cols]
    vals: list[Any] = list(cols.values())
    if meta:
        sets.append("meta = meta || %s")
        vals.append(Jsonb(meta))
    if sets:
        conn.execute(f"UPDATE run SET {', '.join(sets)} WHERE id = %s", (*vals, run_id))


def finish_run(conn: psycopg.Connection, run_id: int, outcome: str, *, usage: dict[str, Any] | None = None,
               summary: str | None = None, last_message: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Close a run once. A run that is already finished is returned untouched, so two writers (a daemon that
    lost its lock and the one that took over) cannot overwrite each other's outcome."""
    with conn.transaction():
        cur = conn.execute("SELECT finished_at FROM run WHERE id = %s FOR UPDATE", (run_id,)).fetchone()
        if cur is None:
            raise LookupError(f"no run {run_id}")
        if cur["finished_at"] is not None:
            return get_run(conn, run_id)
        runs.finish(conn, run_id, outcome=outcome, usage={k: v for k, v in (usage or {}).items() if v is not None})
        update_run(conn, run_id, meta=meta, **{k: v for k, v in (("summary", summary), ("last_assistant_message", last_message))
                                               if v is not None})
    return get_run(conn, run_id)


def get_run(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT r.*, n.slug FROM run r LEFT JOIN node n ON n.id = r.node_id WHERE r.id = %s", (run_id,)).fetchone()
    if row is None:
        raise LookupError(f"no run {run_id}")
    return _json(row)  # type: ignore[return-value]


def open_runs(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    """Orchestrated runs on the node that have not finished (oldest first)."""
    rows = conn.execute(
        """SELECT r.*, n.slug FROM run r JOIN node n ON n.id = r.node_id
           WHERE n.slug = %s AND r.step IS NOT NULL AND r.finished_at IS NULL AND r.agent_type = 'orchestrator'
           ORDER BY r.id""", (slug,)).fetchall()
    return [_json(r) for r in rows]  # type: ignore[misc]


def open_run_count(conn: psycopg.Connection, slugs: list[str]) -> int:
    if not slugs:
        return 0
    row = conn.execute(
        """SELECT count(*) AS n FROM run r JOIN node n ON n.id = r.node_id
           WHERE n.slug = ANY(%s) AND r.step IN ('tests','implement','verify','ship')
             AND r.finished_at IS NULL AND r.agent_type = 'orchestrator'""", (slugs,)).fetchone()
    return int(row["n"])


def state_entered_at(conn: psycopg.Connection, slug: str) -> str | None:
    """When the node entered its current state (newest transition), ISO; None if never moved."""
    row = conn.execute(
        """SELECT max(t.at) AS at FROM transition t JOIN node n ON n.id = t.node_id
           WHERE n.slug = %s AND t.to_state = n.state""", (slug,)).fetchone()
    return row["at"].isoformat() if row and row["at"] else None


def step_runs(conn: psycopg.Connection, slug: str, step: str, *, since: str | None = None) -> list[dict[str, Any]]:
    """Finished and open orchestrated runs of ``step`` on the node since ``since`` (oldest first)."""
    rows = conn.execute(
        """SELECT r.*, n.slug FROM run r JOIN node n ON n.id = r.node_id
           WHERE n.slug = %s AND r.step = %s AND r.agent_type = 'orchestrator'
             AND (%s::timestamptz IS NULL OR r.started_at >= %s::timestamptz)
           ORDER BY r.id""", (slug, step, since, since)).fetchall()
    return [_json(r) for r in rows]  # type: ignore[misc]


def last_run(conn: psycopg.Connection, slug: str) -> dict[str, Any] | None:
    """The newest finished orchestrated run on the node (the seance source)."""
    row = conn.execute(
        """SELECT r.*, n.slug FROM run r JOIN node n ON n.id = r.node_id
           WHERE n.slug = %s AND r.step IN ('tests','implement','verify','ship') AND r.finished_at IS NOT NULL
           ORDER BY r.finished_at DESC, r.id DESC LIMIT 1""", (slug,)).fetchone()
    return _json(row)


def feature_cost(conn: psycopg.Connection, slugs: list[str]) -> float:
    if not slugs:
        return 0.0
    row = conn.execute(
        """SELECT coalesce(sum(r.cost_usd), 0) AS c FROM run r JOIN node n ON n.id = r.node_id
           WHERE n.slug = ANY(%s) AND r.agent_type = 'orchestrator'""", (slugs,)).fetchone()
    return float(row["c"])


def progress_at(conn: psycopg.Connection, slug: str) -> dt.datetime | None:
    """Newest evidence or transition on the node (the watchdog's progress signal)."""
    row = conn.execute(
        """SELECT greatest((SELECT max(e.at) FROM evidence e WHERE e.node_id = n.id),
                           (SELECT max(t.at) FROM transition t WHERE t.node_id = n.id)) AS at
           FROM node n WHERE n.slug = %s""", (slug,)).fetchone()
    return row["at"] if row else None


def db_now(conn: psycopg.Connection) -> dt.datetime:
    return conn.execute("SELECT now() AS t").fetchone()["t"]


# --------------------------------------------------------------------------- budget decisions


def budget_limit(conn: psycopg.Connection, feature: str, base: float | None) -> float | None:
    """The feature's effective cost limit: the configured one, or the highest limit a human raised it to
    (a resolved 'raise' answer on a budget decision carries ``raise_to`` in its meta). None = unlimited."""
    if base is None:
        return None
    row = conn.execute(
        """SELECT max((d.meta->>'raise_to')::numeric) AS m FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'source' = 'orchestrator'
             AND d.meta->>'step' = 'budget' AND lower(d.choice) = 'raise'""", (feature,)).fetchone()
    return max(base, float(row["m"])) if row and row["m"] is not None else base


def budget_stopped(conn: psycopg.Connection, feature: str, limit: float) -> bool:
    """A human answered 'stop' to the budget question asked at this limit (raising the limit, with 'raise' or a
    larger --max-cost, lifts it)."""
    row = conn.execute(
        """SELECT 1 FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'source' = 'orchestrator'
             AND d.meta->>'step' = 'budget' AND lower(d.choice) = 'stop'
             AND (d.meta->>'limit')::numeric = %s LIMIT 1""", (feature, limit)).fetchone()
    return row is not None


def answered_questions(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    """Resolved ``needs_human`` questions on the node: what the session asked and the human's answer."""
    rows = conn.execute(
        """SELECT d.question, d.choice, d.decided_by FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'source' = 'orchestrator'
             AND d.meta->>'kind' = 'question' ORDER BY d.id""", (slug,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- lock


class LockHeld(RuntimeError):
    """Another daemon holds a fresh lock on the feature."""

    def __init__(self, holder: dict[str, Any]):
        self.holder = holder
        super().__init__(f"feature is orchestrated by {holder.get('owner')} (heartbeat {holder.get('heartbeat_at')}); "
                         f"stop it with 'sdlc-graph orchestrate <feature> --stop' or wait 3 ticks for it to go stale")


class LockLost(RuntimeError):
    """The lock row was released (``--stop``) or taken over after going stale."""


def new_owner() -> str:
    return f"{socket.gethostname().split('.')[0]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire_lock(conn: psycopg.Connection, feature: str, owner: str, tick_seconds: float,
                 meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Take the feature's lock row, or take over a stale one (heartbeat older than 3 of the
    holder's ticks). Raises LockHeld when another daemon's lock is fresh."""
    fid = node_id(conn, feature)
    row = conn.execute(
        """INSERT INTO orchestrator_lock AS l (feature_id, owner, tick_seconds, meta)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (feature_id) DO UPDATE
             SET owner = EXCLUDED.owner, heartbeat_at = now(), acquired_at = now(),
                 tick_seconds = EXCLUDED.tick_seconds, meta = EXCLUDED.meta
             WHERE l.owner = EXCLUDED.owner
                OR l.heartbeat_at < now() - make_interval(secs => 3 * l.tick_seconds::double precision)
           RETURNING *""",
        (fid, owner, tick_seconds, Jsonb(meta or {}))).fetchone()
    if row is None:
        raise LockHeld(lock_status(conn, feature) or {})
    return _json(row)  # type: ignore[return-value]


def heartbeat(conn: psycopg.Connection, feature: str, owner: str) -> None:
    """Refresh the lock; raises LockLost when the row is gone or someone else holds it."""
    row = conn.execute(
        """UPDATE orchestrator_lock SET heartbeat_at = now()
           WHERE feature_id = (SELECT id FROM node WHERE slug = %s) AND owner = %s RETURNING owner""",
        (feature, owner)).fetchone()
    if row is None:
        raise LockLost(f"lost the lock on {feature!r} (released with --stop, or taken over after going stale)")


def release_lock(conn: psycopg.Connection, feature: str, owner: str | None = None) -> bool:
    """Delete the lock row (only ours when ``owner`` is given). Returns whether a row went."""
    if owner is None:
        cur = conn.execute("DELETE FROM orchestrator_lock WHERE feature_id = (SELECT id FROM node WHERE slug = %s)", (feature,))
    else:
        cur = conn.execute("DELETE FROM orchestrator_lock WHERE feature_id = (SELECT id FROM node WHERE slug = %s) AND owner = %s",
                           (feature, owner))
    return cur.rowcount > 0


def lock_status(conn: psycopg.Connection, feature: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT l.*, now() - l.heartbeat_at > make_interval(secs => 3 * l.tick_seconds::double precision) AS stale
           FROM orchestrator_lock l JOIN node n ON n.id = l.feature_id WHERE n.slug = %s""", (feature,)).fetchone()
    return _json(row)


# --------------------------------------------------------------------------- escalations


def notify(decision: dict[str, Any]) -> None:
    """The notify adapter: stdout now (``ORCHESTRATOR_NOTIFY=none`` silences it), Slack later."""
    if os.environ.get("ORCHESTRATOR_NOTIFY", "stdout") == "none":
        return
    print(f"[escalation {decision.get('severity') or '-'}] {decision.get('slug') or ''} #{decision.get('id')}: "
          f"{decision.get('question')}", file=sys.stderr, flush=True)


def escalate(conn: psycopg.Connection, slug: str, *, key: str, question: str, severity: str = "P1",
             options: list[str] | None = None, recommendation: str | None = None,
             meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ensure one open decision with this dedupe ``key`` on the node; returns it (new or existing)."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    existing = conn.execute(
        """SELECT d.*, n.slug FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NULL AND d.meta->>'key' = %s ORDER BY d.id LIMIT 1""",
        (slug, key)).fetchone()
    if existing:
        return _json(existing)  # type: ignore[return-value]
    options = ["retry", "skip"] if options is None else options   # [] = a free-text answer
    d = decisions.propose(conn, slug, question, options, actor=ACTOR,
                          recommendation=recommendation if recommendation in options else None)
    conn.execute("UPDATE decision SET severity = %s, meta = %s WHERE id = %s",
                 (severity, Jsonb({**(meta or {}), "key": key, "source": "orchestrator"}), d["id"]))
    row = _json(conn.execute("SELECT d.*, n.slug FROM decision d JOIN node n ON n.id = d.node_id WHERE d.id = %s",
                             (d["id"],)).fetchone())
    notify(row)  # type: ignore[arg-type]
    return row  # type: ignore[return-value]


def open_escalations(conn: psycopg.Connection, slugs: list[str] | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT d.*, n.slug, n.kind FROM decision d JOIN node n ON n.id = d.node_id
           WHERE d.resolved_at IS NULL AND (%s::text[] IS NULL OR n.slug = ANY(%s::text[]))
           ORDER BY CASE d.severity WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, d.created_at""",
        (slugs, slugs)).fetchall()
    return [_json(r) for r in rows]  # type: ignore[misc]


def resolved_escalations(conn: psycopg.Connection, slug: str, step: str) -> list[dict[str, Any]]:
    """Resolved orchestrator escalations for (node, step), newest first."""
    rows = conn.execute(
        """SELECT d.*, n.slug FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'source' = 'orchestrator'
             AND d.meta->>'step' = %s ORDER BY d.resolved_at DESC""", (slug, step)).fetchall()
    return [_json(r) for r in rows]  # type: ignore[misc]


def escalation_resolved(conn: psycopg.Connection, slug: str, key: str) -> bool:
    """Whether an escalation with this dedupe key was already answered on the node."""
    row = conn.execute(
        """SELECT 1 FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'key' = %s LIMIT 1""", (slug, key)).fetchone()
    return row is not None


def skipped(conn: psycopg.Connection, slug: str) -> bool:
    """A human explicitly skipped the contract (resolved an orchestrator escalation with 'skip')."""
    row = conn.execute(
        """SELECT 1 FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL AND d.meta->>'source' = 'orchestrator'
             AND lower(d.choice) = 'skip' LIMIT 1""", (slug,)).fetchone()
    return row is not None
