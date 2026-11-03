"""``metrics.snapshot``: the playbook's per-stage metrics computed from the ledger.

All values are computed from rows, never stored. A metric with no data is ``None``.
Refused transitions come from ``node.attrs["refused_transitions"]`` (see transitions.py), since
the ledger has no refusal table.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

import psycopg


def _since(since: dt.datetime | str | None) -> dt.datetime | None:
    if since is None or since == "":
        return None
    if isinstance(since, str):
        try:
            since = dt.datetime.fromisoformat(since)
        except ValueError:
            raise ValueError(f"since must be an ISO date or date-time, not {since!r}") from None
    if isinstance(since, dt.date) and not isinstance(since, dt.datetime):
        since = dt.datetime.combine(since, dt.time())
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    return since


def _hours(a: dt.datetime, b: dt.datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def _mean(xs: list[float]) -> float | None:
    return _r(statistics.fmean(xs)) if xs else None


def _median(xs: list[float]) -> float | None:
    return _r(statistics.median(xs)) if xs else None


def snapshot(conn: psycopg.Connection, repo: str | None = None, since: dt.datetime | str | None = None) -> dict[str, Any]:
    """Metrics over nodes (optionally one repo) touched since ``since`` (ISO string or datetime).

    Returns:
        ``{"repo", "since", "nodes": {kind: {state: count}}, "contracts": {state: count},
        "median_hours_drafted_to_verified": float|None,
        "transitions": {"recorded": int, "refused": int, "refused_by_target": {state: int}},
        "runs_by_tier": {tier: {"runs", "refused", "policy_violations", "tokens_in", "tokens_out", "cost_usd"}},
        "runs_by_executor": {"local"|"runner": count}, "runs_by_auth_source": {source: count} (orchestrated runs only),
        "model_drift": int (runs whose resolved_model's tier differs from requested_model's),
        "fable_calls_per_epic": {epic_slug: int},
        "cost_per_merged_contract": float|None,
        "first_pass_merge_rate": float|None (share of merged contracts with exactly one verify_stamp),
        "rework_cycles": float|None (mean of verify_stamp rows minus 1 per merged contract; the
            contract machine has no backward transitions, so re-verification is the rework signal),
        "gate_wait_hours": {"<from>-><to>": float|None} (mean hours spent in <from> before <to>),
        "intent_to_spec_hours": float|None (feature created -> first decomposed contract ready),
        "prediction": {"count", "precision", "recall"} (mean over prediction_outcome rows)}``.
    """
    from . import runs as runs_mod
    from .transitions import machine

    since_dt = _since(since)
    policy = runs_mod.load_policy()
    nodes = conn.execute(
        """SELECT id, kind, slug, state, repo, attrs, created_at, updated_at FROM node
           WHERE (%s::text IS NULL OR repo = %s) AND (%s::timestamptz IS NULL OR updated_at >= %s)""",
        (repo, repo, since_dt, since_dt),
    ).fetchall()
    by_id = {r["id"]: r for r in nodes}
    ids = list(by_id)

    by_kind: dict[str, dict[str, int]] = {}
    for r in nodes:
        by_kind.setdefault(r["kind"], {})
        by_kind[r["kind"]][r["state"]] = by_kind[r["kind"]].get(r["state"], 0) + 1
    contract_states = {s: 0 for s in (machine("contract") or {"states": []})["states"]}
    contract_states.update(by_kind.get("contract", {}))

    trs = conn.execute(
        "SELECT node_id, from_state, to_state, at FROM transition WHERE node_id = ANY(%s) ORDER BY node_id, at, id", (ids,)
    ).fetchall()
    per_node: dict[Any, list[dict[str, Any]]] = {}
    for t in trs:
        per_node.setdefault(t["node_id"], []).append(t)

    # drafted -> verified, and time spent in each state before leaving it
    to_verified: list[float] = []
    waits: dict[str, list[float]] = {}
    first_ready: dict[Any, dt.datetime] = {}
    for nid, rows in per_node.items():
        if by_id[nid]["kind"] != "contract":
            continue
        created = next((t["at"] for t in rows if t["from_state"] is None), by_id[nid]["created_at"])
        verified = next((t["at"] for t in rows if t["to_state"] == "verified"), None)
        if verified is not None:
            to_verified.append(_hours(created, verified))
        ready = next((t["at"] for t in rows if t["to_state"] == "ready"), None)
        if ready is not None:
            first_ready[nid] = ready
        for prev, cur in zip(rows, rows[1:]):
            if cur["from_state"] is not None:
                waits.setdefault(f"{cur['from_state']}->{cur['to_state']}", []).append(_hours(prev["at"], cur["at"]))
    gate_wait = {}
    for t in (machine("contract") or {"transitions": []})["transitions"]:
        key = f"{t['from']}->{t['to']}"
        gate_wait[key] = _mean(waits.get(key, []))

    refused_by: dict[str, int] = {}
    refused_total = 0
    for r in nodes:
        attrs = r["attrs"] or {}
        for entry in attrs.get("refused_transitions") or []:
            at = entry.get("at")
            if since_dt is not None and isinstance(at, str):
                try:
                    if dt.datetime.fromisoformat(at) < since_dt:
                        continue
                except ValueError:
                    pass
            refused_total += 1
            refused_by[entry.get("to", "?")] = refused_by.get(entry.get("to", "?"), 0) + 1

    run_rows = conn.execute(
        """SELECT r.* FROM run r LEFT JOIN node n ON n.id = r.node_id
           WHERE (%s::text IS NULL OR n.repo = %s)
             AND (%s::timestamptz IS NULL OR r.started_at >= %s)""",
        (repo, repo, since_dt, since_dt),
    ).fetchall()
    tiers: dict[str, dict[str, Any]] = {}
    drift = 0
    for r in run_rows:
        t = tiers.setdefault(r["model_tier"], {"runs": 0, "refused": 0, "policy_violations": 0, "tokens_in": 0,
                                               "tokens_out": 0, "cost_usd": 0.0})
        t["runs"] += 1
        if r["outcome"] == runs_mod.REFUSED:
            t["refused"] += 1
        stage_tier = policy["stages"].get(r["stage"]) if r["stage"] else None
        if stage_tier is not None and stage_tier != r["model_tier"]:
            t["policy_violations"] += 1
        t["tokens_in"] += int(r["tokens_in"] or 0)
        t["tokens_out"] += int(r["tokens_out"] or 0)
        t["cost_usd"] = round(t["cost_usd"] + float(r["cost_usd"] or 0), 6)
        if r["resolved_model"] and runs_mod.tier_of_model(r["resolved_model"], policy) != runs_mod.tier_of_model(r["requested_model"], policy):
            drift += 1

    by_executor: dict[str, int] = {}
    by_auth: dict[str, int] = {}
    for r in run_rows:
        if r.get("executor"):
            by_executor[r["executor"]] = by_executor.get(r["executor"], 0) + 1
        if r.get("auth_source"):
            by_auth[r["auth_source"]] = by_auth.get(r["auth_source"], 0) + 1

    fable = {}
    for r in nodes:
        if r["kind"] == "epic":
            fable[r["slug"]] = runs_mod.budget(conn, r["slug"], policy)["used"]

    merged = [r["id"] for r in nodes if r["kind"] == "contract" and r["state"] == "merged"]
    stamps: dict[Any, int] = {nid: 0 for nid in merged}
    for r in conn.execute(
        "SELECT node_id, count(*) AS n FROM evidence WHERE kind = 'verify_stamp' AND node_id = ANY(%s) GROUP BY node_id", (merged,)
    ).fetchall():
        stamps[r["node_id"]] = int(r["n"])
    cost_merged = sum(float(r["cost_usd"] or 0) for r in run_rows if r["node_id"] in set(merged))
    first_pass = (sum(1 for n in stamps.values() if n == 1) / len(merged)) if merged else None
    rework = _mean([max(n - 1, 0) for n in stamps.values()]) if merged else None

    spec: list[float] = []
    for r in nodes:
        if r["kind"] != "feature":
            continue
        kids = conn.execute(
            "SELECT e.dst FROM edge e JOIN node n ON n.id = e.dst WHERE e.src = %s AND e.rel = 'decomposes_into' AND n.kind = 'contract'",
            (r["id"],),
        ).fetchall()
        readies = [first_ready[k["dst"]] for k in kids if k["dst"] in first_ready]
        if readies:
            spec.append(_hours(r["created_at"], min(readies)))

    pred = conn.execute(
        """SELECT count(*) AS n, avg(o.precision) AS p, avg(o.recall) AS r
           FROM prediction_outcome o JOIN prediction p ON p.id = o.prediction_id
           WHERE (%s::text IS NULL OR p.node_id = ANY(%s)) AND (%s::timestamptz IS NULL OR o.recorded_at >= %s)""",
        (repo, ids, since_dt, since_dt),
    ).fetchone()

    return {
        "repo": repo,
        "since": since_dt.isoformat() if since_dt else None,
        "nodes": by_kind,
        "contracts": contract_states,
        "median_hours_drafted_to_verified": _median(to_verified),
        "transitions": {"recorded": sum(1 for t in trs if t["from_state"] is not None), "refused": refused_total,
                        "refused_by_target": refused_by},
        "runs_by_tier": tiers,
        "model_drift": drift,
        "runs_by_executor": by_executor,
        "runs_by_auth_source": by_auth,
        "fable_calls_per_epic": fable,
        "cost_per_merged_contract": _r(cost_merged / len(merged), 6) if merged else None,
        "first_pass_merge_rate": _r(first_pass),
        "rework_cycles": rework,
        "gate_wait_hours": gate_wait,
        "intent_to_spec_hours": _mean(spec),
        "prediction": {"count": int(pred["n"]), "precision": _r(pred["p"]), "recall": _r(pred["r"])},
    }
