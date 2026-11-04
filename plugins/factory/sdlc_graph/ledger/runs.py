"""Agent runs and the routing policy (``routing_policy.json``).

Model aliases map to tiers through the policy's ``tiers`` table (planner=fable,
orchestrator=opus, executor=sonnet, utility=haiku). A full model ID maps by the family name it
contains (``claude-sonnet-5-5`` -> executor); anything else is tier ``"unknown"``.

Fable budget: per epic, ``fable_budget_per_epic`` planner runs plus raises recorded as
``fable_budget_increase`` decisions (see decisions.py). A planner run counts against the epic
of its node: the node itself if it is an epic, else the nearest ancestor epic reached by
walking ``decomposes_into`` edges backwards (dst -> src). Runs with no node count against no
epic. Refused runs (``outcome = "refused"``) never count.

``start`` enforces the budget: a planner-tier run whose epic has no budget left is recorded with
``outcome = "refused"`` (so the refusal is visible in the ledger) and then refused with
``PermissionError``. Pass ``enforce_budget=False`` to record without the check (a PostToolUse
recorder for a spawn that already ran).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Run

TIERS: tuple[str, ...] = ("planner", "orchestrator", "executor", "utility")
UNKNOWN_TIER = "unknown"
REFUSED = "refused"
_USAGE_COLUMNS = {"resolved_model": "resolved_model", "resolvedModel": "resolved_model",
                  "tokens_in": "tokens_in", "input_tokens": "tokens_in",
                  "tokens_out": "tokens_out", "output_tokens": "tokens_out",
                  "cost_usd": "cost_usd", "turns": "turns"}


def _validate_policy(data: Any, where: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("tiers"), dict) or not isinstance(data.get("stages"), dict):
        raise ValueError(f"{where}: policy needs 'tiers' and 'stages' objects")
    for stage, tier in data["stages"].items():
        if tier not in data["tiers"]:
            raise ValueError(f"{where}: stage {stage!r} maps to unknown tier {tier!r}")
    b = data.get("fable_budget_per_epic")
    if not isinstance(b, int) or isinstance(b, bool) or b < 0:
        raise ValueError(f"{where}: fable_budget_per_epic must be a non-negative integer")
    return data


@lru_cache(maxsize=1)
def _default_policy() -> dict[str, Any]:
    from . import ROUTING_POLICY_PATH

    return _validate_policy(json.loads(ROUTING_POLICY_PATH.read_text(encoding="utf-8")), str(ROUTING_POLICY_PATH))


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load ``routing_policy.json`` (default ``ledger.ROUTING_POLICY_PATH``).

    Raises:
        ValueError: a stage maps to an unknown tier, or the budget is not a non-negative int.
    """
    if path is None:
        return _default_policy()
    return _validate_policy(json.loads(Path(path).read_text(encoding="utf-8")), str(path))


def tier_for_stage(stage: str, policy: dict[str, Any] | None = None) -> str:
    """Tier for a stage. Raises KeyError for a stage the policy does not list."""
    policy = policy or load_policy()
    if stage not in policy["stages"]:
        raise KeyError(f"stage {stage!r} is not in the routing policy")
    return policy["stages"][stage]


def model_for_tier(tier: str, policy: dict[str, Any] | None = None) -> str:
    """Model alias for a tier (``"executor" -> "sonnet"``). Raises KeyError."""
    policy = policy or load_policy()
    if tier not in policy["tiers"]:
        raise KeyError(f"tier {tier!r} is not in the routing policy")
    return policy["tiers"][tier]


def tier_of_model(model: str | None, policy: dict[str, Any] | None = None) -> str:
    """Tier of a model alias or full ID; ``"unknown"`` when it matches no tier (or is None)."""
    if not model:
        return UNKNOWN_TIER
    policy = policy or load_policy()
    m = str(model).strip().lower()
    for tier, alias in policy["tiers"].items():
        if m == str(alias).lower():
            return tier
    for tier, alias in policy["tiers"].items():
        if str(alias).lower() in m:
            return tier
    return UNKNOWN_TIER


def epic_of(conn: psycopg.Connection, slug: str) -> str | None:
    """Slug of the epic a node belongs to (itself, or nearest ancestor via ``decomposes_into``).
    Raises LookupError for an unknown slug."""
    from .nodes import node_id

    nid = node_id(conn, slug)
    row = conn.execute(
        """WITH RECURSIVE up(id, depth) AS (
               SELECT %s::uuid, 0
               UNION
               SELECT e.src, up.depth + 1 FROM edge e JOIN up ON e.dst = up.id
               WHERE e.rel = 'decomposes_into' AND up.depth < 32)
           SELECT n.slug FROM up JOIN node n ON n.id = up.id WHERE n.kind = 'epic'
           ORDER BY up.depth, n.slug LIMIT 1""",
        (nid,),
    ).fetchone()
    return row["slug"] if row else None


def _epic_members(conn: psycopg.Connection, epic_id: Any) -> list[Any]:
    """Node ids whose nearest epic is ``epic_id``: the epic and its decomposes_into descendants,
    not descending through another epic."""
    rows = conn.execute(
        """WITH RECURSIVE down(id, depth) AS (
               SELECT %s::uuid, 0
               UNION
               SELECT e.dst, down.depth + 1 FROM edge e JOIN down ON e.src = down.id
               JOIN node n ON n.id = e.dst
               WHERE e.rel = 'decomposes_into' AND n.kind <> 'epic' AND down.depth < 32)
           SELECT DISTINCT id FROM down""",
        (epic_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _counts_as_planner(run: dict[str, Any], policy: dict[str, Any]) -> bool:
    if run.get("outcome") == REFUSED:
        return False
    return run.get("model_tier") == "planner" or tier_of_model(run.get("resolved_model"), policy) == "planner"


def _raises(conn: psycopg.Connection, epic_id: Any) -> int:
    from .decisions import FABLE_BUDGET_QUESTION

    total = 0
    for r in conn.execute(
        "SELECT choice FROM decision WHERE node_id = %s AND question = %s AND resolved_at IS NOT NULL",
        (epic_id, FABLE_BUDGET_QUESTION),
    ).fetchall():
        try:
            total += max(int(str(r["choice"]).strip()), 0)
        except ValueError:
            continue
    return total


def budget(conn: psycopg.Connection, slug: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fable budget of the node's epic.

    Returns:
        ``{"epic": slug|None, "budget": int, "used": int, "remaining": int}``; with no epic,
        ``{"epic": None, "budget": base, "used": 0, "remaining": base}``. ``budget`` is the base
        plus resolved ``fable_budget_increase`` raises.
    """
    policy = policy or load_policy()
    base = int(policy["fable_budget_per_epic"])
    epic = epic_of(conn, slug)
    if epic is None:
        return {"epic": None, "budget": base, "used": 0, "remaining": base}
    eid = conn.execute("SELECT id FROM node WHERE slug = %s", (epic,)).fetchone()["id"]
    members = _epic_members(conn, eid)
    rows = conn.execute(
        "SELECT model_tier, resolved_model, outcome FROM run WHERE node_id = ANY(%s)", (members,)
    ).fetchall()
    used = sum(1 for r in rows if _counts_as_planner(r, policy))
    total = base + _raises(conn, eid)
    return {"epic": epic, "budget": total, "used": used, "remaining": max(total - used, 0)}


def start(conn: psycopg.Connection, *, stage: str | None, requested_model: str, slug: str | None = None,
          tool_use_id: str | None = None, agent_type: str | None = None, model_tier: str | None = None,
          meta: dict[str, Any] | None = None, enforce_budget: bool = True,
          executor: str | None = None, auth_source: str | None = None, actor: str | None = None) -> dict[str, Any]:
    """Insert a run row (``started_at = now()``). ``model_tier`` defaults to
    ``tier_of_model(requested_model)``. ``executor`` ('local'|'runner') and ``auth_source``
    ('subscription'|'api_key'|'oauth_token'|'claude_platform_aws'|'bedrock') are recorded by the orchestrator
    (sql/006_executor.sql); other callers leave them NULL. ``actor`` is the identity that opened the run (the MCP
    server passes its caller; sql/012); :func:`finish` with an ``actor`` refuses a run opened by another one.

    Returns:
        ``Run.to_dict()`` (``id`` is the run id the finish call needs).

    Raises:
        LookupError: unknown slug. ValueError: empty model or unknown model_tier.
        PermissionError: a planner run on an epic with no Fable budget left (the refused run is
        still recorded, ``outcome = "refused"``). psycopg.errors.UniqueViolation: tool_use_id reused.
    """
    from .nodes import node_id

    if not isinstance(requested_model, str) or not requested_model.strip():
        raise ValueError("requested_model must be a non-empty model alias or ID")
    policy = load_policy()
    tier = model_tier or tier_of_model(requested_model, policy)
    if tier not in TIERS + (UNKNOWN_TIER,):
        raise ValueError(f"unknown model_tier {tier!r}; expected one of {', '.join(TIERS + (UNKNOWN_TIER,))}")
    nid = node_id(conn, slug) if slug else None
    meta = dict(meta or {})
    if stage and stage in policy["stages"]:
        meta.setdefault("policy_tier", policy["stages"][stage])
    refusal: str | None = None
    # The budget check and the insert share one transaction and an advisory lock per epic, so
    # parallel planner spawns cannot both pass the check for the last slot.
    with conn.transaction():
        if enforce_budget and tier == "planner" and slug:
            epic = epic_of(conn, slug)
            if epic is not None:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"fable_budget:{epic}",))
            b = budget(conn, slug, policy)
            if b["epic"] is not None and b["remaining"] <= 0:
                refusal = (f"Fable budget for epic {b['epic']!r} is spent ({b['used']}/{b['budget']} planner runs); "
                           f"a human raises it by resolving a 'fable_budget_increase' decision on the epic")
                meta["refusal"] = refusal
                meta["fable_budget"] = b
        row = conn.execute(
            """INSERT INTO run (node_id, stage, requested_model, model_tier, tool_use_id, agent_type, meta, outcome,
                                executor, auth_source, actor, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END) RETURNING *""",
            (nid, stage, requested_model.strip(), tier, tool_use_id, agent_type, Jsonb(meta),
             REFUSED if refusal else None, executor, auth_source, actor, refusal is not None),
        ).fetchone()
    if refusal:
        raise PermissionError(f"{refusal} (run {row['id']} recorded as refused)")
    return Run.from_row(row).to_dict()


def finish(conn: psycopg.Connection, run_id: int | None = None, *, tool_use_id: str | None = None,
           outcome: str, usage: dict[str, Any] | None = None, actor: str | None = None) -> dict[str, Any]:
    """Close a run found by ``run_id`` or ``tool_use_id``.

    When ``actor`` is given it must equal the actor that opened the run (a run with no recorded actor was not
    opened by that caller); otherwise PermissionError and the run is left open.

    Args:
        usage: ``{"resolved_model", "tokens_in", "tokens_out", "cost_usd", "turns",
            "models_used", "total_duration_ms"}`` (all optional; unknown keys go to ``meta``).

    Returns:
        ``Run.to_dict()``.

    Raises:
        ValueError: neither id given, or empty outcome. LookupError: no such run.
    """
    if run_id is None and not tool_use_id:
        raise ValueError("finish needs run_id or tool_use_id")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ValueError("outcome must be a non-empty string ('ok', 'error', ...)")
    cols: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in (usage or {}).items():
        col = _USAGE_COLUMNS.get(key)
        if col is None:
            extra[key] = value
        elif value is not None:
            cols[col] = value
    for col in ("tokens_in", "tokens_out", "turns"):
        if col in cols:
            try:
                cols[col] = int(cols[col])
            except (TypeError, ValueError):
                raise ValueError(f"usage.{col} must be an integer") from None
    if "cost_usd" in cols:
        try:
            cols["cost_usd"] = float(cols["cost_usd"])
        except (TypeError, ValueError):
            raise ValueError("usage.cost_usd must be a number") from None
    where, key = ("id = %s", run_id) if run_id is not None else ("tool_use_id = %s", tool_use_id)
    with conn.transaction():
        row = conn.execute(f"SELECT * FROM run WHERE {where} FOR UPDATE", (key,)).fetchone()
        if row is None:
            raise LookupError(f"no run with {'id' if run_id is not None else 'tool_use_id'} {key!r}")
        if actor is not None and row.get("actor") != actor:
            raise PermissionError(f"run {row['id']} was opened by {row.get('actor') or 'another actor'}, "
                                  f"not {actor}; only the actor that opened a run may finish it")
        meta = {**(row["meta"] or {}), **extra}
        sets = ", ".join(f"{c} = %s" for c in cols)
        out = conn.execute(
            f"UPDATE run SET outcome = %s, finished_at = now(), meta = %s{', ' + sets if sets else ''} WHERE id = %s RETURNING *",
            (outcome.strip(), Jsonb(meta), *cols.values(), row["id"]),
        ).fetchone()
    return Run.from_row(out).to_dict()
