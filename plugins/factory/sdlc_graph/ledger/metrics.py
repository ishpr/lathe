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


_ACCEPTED_TO = {"epic": "accepted", "feature": "ready"}
_SURVIVED = {"epic": {"measured", "closed"}, "feature": {"released", "evaluated"}}


def _intent_survival(nodes: list[dict[str, Any]], per_node: dict[Any, list[dict[str, Any]]]) -> dict[str, Any]:
    """See the ``intent_survival`` entry of :func:`snapshot`."""
    by_kind: dict[str, dict[str, int]] = {k: {"survived": 0, "lost": 0} for k in _ACCEPTED_TO}
    for n in nodes:
        kind = n["kind"]
        if kind not in _ACCEPTED_TO:
            continue
        reached = {t["to_state"] for t in per_node.get(n["id"], [])} | {n["state"]}
        accepted = _ACCEPTED_TO[kind] in reached or n["state"] in _SURVIVED[kind] or n["state"] == "rejected"
        if not accepted:
            continue
        abandoned = bool((n["attrs"] or {}).get("abandoned"))
        if abandoned or n["state"] == "rejected" or (kind == "epic" and n["state"] == "closed" and "measured" not in reached):
            by_kind[kind]["lost"] += 1
        elif n["state"] in _SURVIVED[kind]:
            by_kind[kind]["survived"] += 1
    survived = sum(v["survived"] for v in by_kind.values())
    lost = sum(v["lost"] for v in by_kind.values())
    rate = lambda s, l: _r(s / (s + l)) if s + l else None  # noqa: E731
    return {"rate": rate(survived, lost), "survived": survived, "lost": lost,
            "by_kind": {k: {**v, "rate": rate(v["survived"], v["lost"])} for k, v in by_kind.items()}}


def _repeat_incidents(conn: psycopg.Connection, nodes: list[dict[str, Any]], repo: str | None) -> dict[str, Any]:
    """See the ``repeat_incidents`` entry of :func:`snapshot`."""
    window = [n for n in nodes if n["kind"] == "incident"]
    if not window:
        return {"incidents": 0, "repeats": 0, "rate": None}
    inc = {r["id"]: r for r in conn.execute(
        "SELECT id, repo, created_at FROM node WHERE kind = 'incident' AND (%s::text IS NULL OR repo = %s)", (repo, repo)
    ).fetchall()}
    edges = conn.execute(
        """SELECT e.src, e.dst, e.rel, s.kind AS skind, s.state AS sstate, d.kind AS dkind, d.created_at AS dcreated
           FROM edge e JOIN node s ON s.id = e.src JOIN node d ON d.id = e.dst
           WHERE e.rel IN ('caused_by', 'governed_by', 'supersedes')"""
    ).fetchall()
    rules_of: dict[Any, dict[Any, Any]] = {}  # node -> {rule id: rule created_at}
    for e in edges:
        if e["rel"] == "governed_by" and e["dkind"] == "rule":
            rules_of.setdefault(e["src"], {})[e["dst"]] = e["dcreated"]
    intents_of: dict[Any, list[dict[str, Any]]] = {}  # incident -> intent nodes caused_by it (intent -> incident)
    for e in edges:
        if e["rel"] == "caused_by" and e["dst"] in inc:
            intents_of.setdefault(e["dst"], []).append(e)
    superseded = {(e["src"], e["dst"]) for e in edges if e["rel"] == "supersedes"}

    def closers(iid: Any) -> dict[Any, Any]:
        """Closing artifacts of an incident: {rule or contract id: when it existed}."""
        out = dict(rules_of.get(iid, {}))
        for e in intents_of.get(iid, []):
            out.update(rules_of.get(e["src"], {}))
            if e["skind"] == "contract" and e["sstate"] == "merged":
                out[e["src"]] = None  # merge time is not on the edge; counts as closed
        return out

    repeats = 0
    for b in window:
        bid = b["id"]
        b_rules = set(rules_of.get(bid, {}))
        for aid, a in inc.items():
            if aid == bid or a["repo"] != b["repo"] or a["created_at"] >= b["created_at"]:
                continue
            related = ((bid, aid) in superseded or (aid, bid) in superseded
                       or bool(b_rules & set(rules_of.get(aid, {}))))
            if related and any(t is None or t < b["created_at"] for t in closers(aid).values()):
                repeats += 1
                break
    return {"incidents": len(window), "repeats": repeats, "rate": _r(repeats / len(window))}


#: Guard rule ids (the hooks' ``blocks.jsonl`` ``rule`` field, or the orchestrator's ``guard <hook>:<rule>`` and
#: ``guard: <reason>`` denial rules) grouped into the three guard trips DESIGN section 18.5 measures.
GUARD_KINDS: dict[str, tuple[str, ...]] = {
    "frozen_test": ("frozen-test", "test-config", "frozen test", "frozen_tests"),
    "scope": ("scope", "outside-root", "contract-lift", "allowed_paths", "tier"),
    "secret": ("secret", "credential-read", "credential"),
}
#: Requested-model values that are not a request for a model: the spawn named none, so the routing default applied.
NO_MODEL_REQUEST = {"", "unspecified", "inherit", "default"}


def guard_kind(rule: str) -> str:
    """``frozen_test``, ``scope``, ``secret`` or ``other`` for a guard rule id or reason text."""
    r = (rule or "").lower()
    for kind, needles in GUARD_KINDS.items():
        if any(n in r for n in needles):
            return kind
    return "other"


def _guard_trips(run_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """See the ``guard_trips`` entry of :func:`snapshot`."""
    seen: set[tuple[Any, ...]] = set()
    by_kind = {k: 0 for k in (*GUARD_KINDS, "other")}
    by_rule: dict[str, int] = {}
    runs_with = 0
    for r in run_rows:
        meta = r["meta"] or {}
        items: list[tuple[str, str]] = []
        for d in meta.get("denials") or []:  # the orchestrator's gate (autonomy.denials), on finished runs
            if isinstance(d, dict) and d.get("reason") == "guard_refused":
                rule = str(d.get("rule") or "guard")
                rule = rule.split(":", 1)[1] if rule.startswith("guard ") and ":" in rule else rule
                items.append((rule, str(d.get("target") or "")))
        for b in meta.get("guard_blocks") or []:  # hooks/record_agent_run.py: blocks.jsonl during the hand-off
            if isinstance(b, dict) and b.get("rule") != "crash":
                items.append((str(b.get("rule") or "block"), str(b.get("detail") or "")))
        counted = False
        for rule, target in items:
            # one refusal can reach the ledger twice (the orchestrator's run and the hand-off's hook record) and the
            # block log keeps a cut detail: the same rule on the same node and target is one trip
            key = (r["node_id"], rule, target[:80])
            if key in seen:
                continue
            seen.add(key)
            counted = True
            by_kind[guard_kind(rule)] += 1
            by_rule[rule] = by_rule.get(rule, 0) + 1
        runs_with += counted
    return {"total": sum(by_kind.values()), "by_kind": by_kind, "by_rule": dict(sorted(by_rule.items())),
            "runs_with_trips": runs_with}


def _denials(run_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """See the ``denials`` entry of :func:`snapshot`."""
    checked = denied = total = form = 0
    for r in run_rows:
        meta = r["meta"] or {}
        if not isinstance(meta.get("denials"), list):
            continue
        checked += 1
        ds = [d for d in meta["denials"] if isinstance(d, dict)]
        if ds:
            denied += 1
        total += len(ds)
        form += sum(1 for d in ds if d.get("form"))
    return {"runs_checked": checked, "runs_denied": denied, "rate": _r(denied / checked) if checked else None,
            "total": total, "form": form, "capability": total - form}


def _model_overrides(run_rows: list[dict[str, Any]], policy: dict[str, Any], tier_of: Any) -> dict[str, Any]:
    """See the ``model_overrides`` entry of :func:`snapshot`."""
    with_default = explicit = overrides = 0
    for r in run_rows:
        meta = r["meta"] or {}
        default = meta.get("policy_tier") or (policy["stages"].get(r["stage"]) if r["stage"] else None)
        if default is None:
            continue
        with_default += 1
        requested = str(meta.get("requested_model") or r["requested_model"] or "").strip()
        if requested.lower() in NO_MODEL_REQUEST:
            continue
        explicit += 1
        if tier_of(requested, policy) != default:
            overrides += 1
    return {"runs": with_default, "explicit": explicit, "overrides": overrides,
            "rate": _r(overrides / with_default) if with_default else None}


def snapshot(conn: psycopg.Connection, repo: str | None = None, since: dt.datetime | str | None = None) -> dict[str, Any]:
    """Metrics over nodes (optionally one repo) touched since ``since`` (ISO string or datetime).

    Returns:
        ``{"repo", "since", "nodes": {kind: {state: count}}, "contracts": {state: count},
        "median_hours_drafted_to_verified": float|None,
        "transitions": {"recorded": int, "refused": int, "refused_by_target": {state: int}},
        "runs_by_tier": {tier: {"runs", "refused", "model_overrides", "tokens_in", "tokens_out", "cost_usd"}},
        "runs_by_executor": {"local"|"runner": count}, "runs_by_auth_source": {source: count} (orchestrated runs only),
        "model_drift": int (runs whose resolved_model's tier differs from requested_model's),
        "fable_calls_per_epic": {epic_slug: int},
        "cost_per_merged_contract": float|None,
        "merged_contracts": int (contracts in state ``merged`` in the window),
        "turns_per_merged_contract": float|None (the ``turns`` of every run on a merged contract, summed, divided by
            the merged contracts; a run with no turns recorded counts 0, and ``runs_with_turns`` says how many had),
        "runs_with_turns": int (runs on merged contracts with ``turns`` recorded: orchestrated sessions report
            ``num_turns``; a hook-recorded hand-off reports tool uses, in ``meta.tool_use_count``, not turns),
        "guard_trips": {"total", "by_kind": {"frozen_test"|"scope"|"secret"|"other": int}, "by_rule": {rule: int},
            "runs_with_trips"}: the guards' refusals the ledger can see, from run meta: the orchestrator's gate
            denials with ``reason: guard_refused`` (``meta.denials``) and the hand-off hook's copy of the block log
            (``meta.guard_blocks``, hooks/record_agent_run.py); one refusal reaching both counts once (same node,
            rule and target). The kinds are :data:`GUARD_KINDS`,
        "denials": {"runs_checked", "runs_denied", "rate": float|None, "total", "form", "capability"}: over runs whose
            meta holds a ``denials`` list (every orchestrated run the gate finished), the share with at least one
            denial of any kind (permission denials and guard refusals); ``form`` counts denials of form only
            (``autonomy.is_form``), the rest are a capability the step did not have,
        "model_overrides": {"runs", "explicit", "overrides", "rate": float|None}: over runs whose stage
            has a routing default (``meta.policy_tier`` or the policy's stage tier), ``explicit`` asked for a model
            (``meta.requested_model``, else the column, not ``unspecified``), ``overrides`` asked for one whose tier
            differs from the default, rate = overrides / runs (the spawn guard records, never rewrites),
        "cost_per_merged_contract_by_tier": {tier: float} (each tier's run cost on merged contracts divided by
            the number of merged contracts, so the tiers sum to cost_per_merged_contract),
        "intent_survival": {"rate": float|None, "survived", "lost", "by_kind": {"epic"|"feature": {...}}}: over the
            epics and features in the window that were ever accepted (an epic has a transition to ``accepted``, a
            feature to ``ready``) plus epics ``rejected``: survived = now epic measured/closed-after-measured or
            feature released/evaluated; lost = epic rejected, epic closed without ever reaching measured, or any
            node whose attrs.abandoned is truthy; accepted-but-still-in-flight nodes are in neither count.
            rate = survived / (survived + lost), None when both are 0,
        "repeat_incidents": {"incidents", "repeats", "rate": float|None}: an incident is a repeat when an earlier
            incident of the same repo is related to it by graph edges only (this one supersedes it or it supersedes
            this one, or both are governed_by the same rule node) and that earlier incident already had a closing
            rule or test when this one was created: a rule node reached by ``governed_by`` from it or from an intent
            node ``caused_by`` it (the edge runs intent -> incident), or a merged contract among those intent nodes
            (the regression test shipped). rate = repeats / incidents in the window, None without incidents,
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
        t = tiers.setdefault(r["model_tier"], {"runs": 0, "refused": 0, "model_overrides": 0, "tokens_in": 0,
                                               "tokens_out": 0, "cost_usd": 0.0})
        t["runs"] += 1
        if r["outcome"] == runs_mod.REFUSED:
            t["refused"] += 1
        if (r["meta"] or {}).get("override") is True:  # the orchestrator chose a model off the stage's default
            t["model_overrides"] += 1
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
    merged_runs = [r for r in run_rows if r["node_id"] in set(merged)]
    turns_merged = sum(int(r["turns"] or 0) for r in merged_runs)
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

    cost_by_tier: dict[str, float] = {}
    merged_set = set(merged)
    for r in run_rows:
        if r["node_id"] in merged_set:
            cost_by_tier[r["model_tier"]] = cost_by_tier.get(r["model_tier"], 0.0) + float(r["cost_usd"] or 0)
    cost_by_tier = {t: _r(c / len(merged), 6) for t, c in sorted(cost_by_tier.items())} if merged else {}

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
        "cost_per_merged_contract_by_tier": cost_by_tier,
        "merged_contracts": len(merged),
        "turns_per_merged_contract": _r(turns_merged / len(merged)) if merged else None,
        "runs_with_turns": sum(1 for r in merged_runs if r["turns"] is not None),
        "guard_trips": _guard_trips(run_rows),
        "denials": _denials(run_rows),
        "model_overrides": _model_overrides(run_rows, policy, runs_mod.tier_of_model),
        "intent_survival": _intent_survival(nodes, per_node),
        "repeat_incidents": _repeat_incidents(conn, nodes, repo),
        "first_pass_merge_rate": _r(first_pass),
        "rework_cycles": rework,
        "gate_wait_hours": gate_wait,
        "intent_to_spec_hours": _mean(spec),
        "prediction": {"count": int(pred["n"]), "precision": _r(pred["p"]), "recall": _r(pred["r"])},
    }
