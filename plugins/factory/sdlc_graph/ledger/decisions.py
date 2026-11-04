"""Decisions a human makes first. Agents may PROPOSE; only humans (``@handle``) RESOLVE.

Fable budget raises are decisions too: a resolved decision on an EPIC node whose question is
exactly ``"fable_budget_increase"`` and whose choice is an integer string adds that many planner
runs to the epic's budget (``runs.budget``).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Decision

FABLE_BUDGET_QUESTION = "fable_budget_increase"


def _option_values(option: Any) -> list[str]:
    if isinstance(option, dict):
        return [str(option[k]) for k in ("id", "label", "value") if k in option and option[k] is not None]
    return [str(option)]


def _among(choice: str, options: list[Any]) -> bool:
    return any(choice in _option_values(o) for o in options)


def _budget_raise(choice: str) -> int:
    try:
        n = int(str(choice).strip())
    except ValueError:
        raise ValueError(f"{FABLE_BUDGET_QUESTION}: choice must be a positive integer, not {choice!r}") from None
    if n <= 0:
        raise ValueError(f"{FABLE_BUDGET_QUESTION}: choice must be a positive integer, not {choice!r}")
    return n


def propose(conn: psycopg.Connection, slug: str, question: str, options: list[Any], *, actor: str,
            recommendation: str | None = None, due: dt.date | str | None = None,
            key: str | None = None) -> dict[str, Any]:
    """Record an open decision on the node. ``key`` (``meta.key``) names a decision a guard looks for
    (``decision:<key>``: ``triage``, ``dismissal``, ``risk_acceptance``); ``risk_acceptance`` needs ``due``.

    Returns:
        ``Decision.to_dict()``.

    Raises:
        LookupError: unknown slug. ValueError: empty question, bad actor, a recommendation not
        among non-empty options, a malformed key, or a ``risk_acceptance`` with no ``due``.
    """
    from .nodes import node_id
    from .transitions import actor_class

    actor_class(actor)
    if not isinstance(question, str) or not question.strip():
        raise ValueError("decision question must be a non-empty string")
    options = list(options or [])
    if recommendation is not None and options and not _among(str(recommendation), options):
        raise ValueError(f"recommendation {recommendation!r} is not among the options")
    if isinstance(due, str):
        try:
            due = dt.date.fromisoformat(due)
        except ValueError:
            raise ValueError(f"due must be an ISO date (YYYY-MM-DD), not {due!r}") from None
    if key is not None and not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise ValueError(f"decision key must be lower_snake_case, not {key!r}")
    if key == "risk_acceptance" and due is None:
        raise ValueError("a risk_acceptance decision needs a due date (when the risk is reviewed again)")
    nid = node_id(conn, slug)
    row = conn.execute(
        """INSERT INTO decision (node_id, question, options, recommendation, proposed_by, due, meta)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (nid, question.strip(), Jsonb(options), recommendation, actor, due, Jsonb({"key": key} if key else {})),
    ).fetchone()
    return Decision.from_row(row).to_dict()


def resolve(conn: psycopg.Connection, decision_id: int, choice: str, *, actor: str) -> dict[str, Any]:
    """Resolve an open decision (sets decided_by, choice, resolved_at).

    A ``fable_budget_increase`` decision needs a positive integer choice and must sit on an epic.

    Raises:
        PermissionError: actor is not human (``agent:*`` and ``ci:*`` both refused).
        LookupError: unknown id. ValueError: already resolved, or choice not among non-empty
        options (options may be strings or ``{"id"|"label": ...}`` dicts; either matches).
    """
    from .transitions import actor_class

    cls = actor_class(actor)
    if cls != "human":
        raise PermissionError(f"only a human (@handle) may resolve a decision; {actor!r} is {cls}")
    if choice is None or not str(choice).strip():
        raise ValueError("choice must be a non-empty string")
    choice = str(choice).strip()
    with conn.transaction():
        row = conn.execute(
            "SELECT d.*, n.kind AS node_kind FROM decision d JOIN node n ON n.id = d.node_id WHERE d.id = %s FOR UPDATE OF d",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"unknown decision id: {decision_id}")
        if row["resolved_at"] is not None:
            raise ValueError(f"decision {decision_id} is already resolved (choice {row['choice']!r} by {row['decided_by']})")
        options = row["options"] or []
        if options and not _among(choice, options):
            raise ValueError(f"choice {choice!r} is not among the options of decision {decision_id}")
        if row["question"] == FABLE_BUDGET_QUESTION:
            if row["node_kind"] != "epic":
                raise ValueError(f"{FABLE_BUDGET_QUESTION} decisions must be on an epic node")
            _budget_raise(choice)
        out = conn.execute(
            "UPDATE decision SET choice = %s, decided_by = %s, resolved_at = now() WHERE id = %s RETURNING *",
            (choice, actor, decision_id),
        ).fetchone()
    return Decision.from_row(out).to_dict()


def open_for(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    """Unresolved decisions of a node, oldest first. Raises LookupError for an unknown slug."""
    from .nodes import node_id

    nid = node_id(conn, slug)
    rows = conn.execute(
        "SELECT * FROM decision WHERE node_id = %s AND resolved_at IS NULL ORDER BY created_at, id", (nid,)
    ).fetchall()
    return [Decision.from_row(r).to_dict() for r in rows]
