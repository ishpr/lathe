"""``workflow.next``: the next allowed step for a node. Computes, never runs.

Contract states map to the pack's slash commands and routing-policy stages:

======================  ==========================  ================  ==========================
state                   next command                stage             next state
======================  ==========================  ================  ==========================
drafted                 /factory:design        contract.design   ready
ready                   /factory:tests         tests             tests_frozen
tests_frozen            /factory:implement     implement         implementing
implementing            /factory:verify        implement         verified
verified                /factory:ship          review.lens       in_review
in_review               /factory:review-pr     review.synthesis  approved   (human_only)
approved                (merge by a human or CI)    -                 merged     (human_only)
merged                  -                           -                 -          (done)
======================  ==========================  ================  ==========================

``contract.design`` becomes ``contract.design.high`` (planner tier) when the node's tier is
``high``. Releases use ``/factory:deploy`` for staging and post-release ready poll and smoke.
Epics, features and incidents name the policy stage and, where the plugin has one, the command.
``human_only`` means the transition needs a human (or CI) actor, so an agent may prepare it but
not perform it; ``human_invoked`` means the plugin documents the command as human-invoked only.
"""

from __future__ import annotations

from typing import Any

import psycopg

CMD = "/factory:"

CONTRACT_STEPS: dict[str, dict[str, Any]] = {
    "drafted": {"command": CMD + "design", "stage": "contract.design", "next_state": "ready"},
    "ready": {"command": CMD + "tests", "stage": "tests", "next_state": "tests_frozen"},
    "tests_frozen": {"command": CMD + "implement", "stage": "implement", "next_state": "implementing"},
    "implementing": {"command": CMD + "verify", "stage": "implement", "next_state": "verified"},
    "verified": {"command": CMD + "ship", "stage": "review.lens", "next_state": "in_review"},
    "in_review": {"command": CMD + "review-pr", "stage": "review.synthesis", "next_state": "approved"},
    "approved": {"command": None, "stage": None, "next_state": "merged"},
    "merged": {"command": None, "stage": None, "next_state": None},
}

#: (kind, state) -> step for the other machines. next_state defaults to the first outgoing transition.
OTHER_STEPS: dict[tuple[str, str], dict[str, Any]] = {
    ("epic", "drafted"): {"command": None, "stage": None},
    ("epic", "accepted"): {"command": None, "stage": "epic.decompose"},
    ("epic", "in_progress"): {"command": None, "stage": "summarise"},
    ("epic", "measured"): {"command": None, "stage": None},
    ("feature", "proposed"): {"command": None, "stage": "contract.design"},
    ("feature", "ready"): {"command": CMD + "design", "stage": "contract.design"},
    ("feature", "building"): {"command": CMD + "feature", "stage": "implement"},
    ("feature", "released"): {"command": None, "stage": "summarise"},
    ("release", "requested"): {"command": CMD + "deploy", "stage": None},
    ("release", "staged"): {"command": None, "stage": None},
    ("release", "released"): {"command": CMD + "deploy", "stage": None},
    ("release", "verified"): {"command": CMD + "rollback", "stage": None},
    ("incident", "open"): {"command": None, "stage": "triage"},
    ("incident", "triaged"): {"command": None, "stage": "postmortem"},
    ("incident", "intent"): {"command": None, "stage": None},
    # Lifecycle machines (DESIGN 16): the commands that move them arrive with their increments; until then a step is
    # the guard report alone (command None).
    ("story", "drafted"): {"command": None, "stage": None},
    ("story", "ready"): {"command": None, "stage": None},
    ("story", "building"): {"command": None, "stage": None},
    ("story", "built"): {"command": None, "stage": None},
    ("decision", "proposed"): {"command": None, "stage": None},
    ("decision", "accepted"): {"command": None, "stage": None},
    ("finding", "open"): {"command": None, "stage": "triage"},
    ("finding", "triaged"): {"command": None, "stage": None},
    ("finding", "accepted"): {"command": None, "stage": None},
    ("finding", "fixing"): {"command": None, "stage": None},
    ("finding", "fixed"): {"command": None, "stage": None},
    ("finding", "verified"): {"command": None, "stage": None},
    ("finding", "risk_accepted"): {"command": None, "stage": None},
    ("idea", "captured"): {"command": None, "stage": None},
    ("idea", "shortlisted"): {"command": None, "stage": None},
    ("idea", "parked"): {"command": None, "stage": None},
}

HUMAN_INVOKED: frozenset[str] = frozenset({CMD + "ship", CMD + "deploy", CMD + "review-pr"})


#: Non-``evidence:`` guards that compare evidence rows with each other; reported with the evidence still missing.
EVIDENCE_GUARDS = ("ci_matches_approval", "merge_head_matches_ci")


def _stage_for(kind: str, stage: str | None, tier: str | None) -> str | None:
    if stage == "contract.design" and tier == "high":
        return "contract.design.high"
    return stage


def next_step(conn: psycopg.Connection, slug: str) -> dict[str, Any]:
    """Next allowed step.

    Returns:
        ``{"slug", "kind", "state", "next_state", "alternatives", "command", "human_invoked",
        "stage", "tier", "model", "model_tier", "model_alias", "missing", "missing_evidence",
        "human_only", "fable_budget", "fable_budget_remaining", "open_decisions", "done"}``.
        ``tier``/``model`` (aliases ``model_tier``/``model_alias``) come from the routing policy for
        ``stage`` (None when stage is None). ``missing`` is every guard not yet satisfied for
        ``next_state`` other than actor guards; ``missing_evidence`` is its ``evidence:`` subset.
        ``open_decisions`` lists the unresolved decisions.

    Raises:
        LookupError: unknown slug.
    """
    from . import decisions, runs, transitions
    from .nodes import get_row

    row = get_row(conn, slug)
    kind, state = row["kind"], row["state"]
    outgoing = transitions.transitions_from(kind, state)
    if kind == "contract":
        step = dict(CONTRACT_STEPS.get(state, {"command": None, "stage": None, "next_state": None}))
    else:
        step = dict(OTHER_STEPS.get((kind, state), {"command": None, "stage": None}))
        step["next_state"] = outgoing[0]["to"] if outgoing else None
    next_state = step["next_state"]
    stage = _stage_for(kind, step["stage"], row["tier"])
    policy = runs.load_policy()
    tier = runs.tier_for_stage(stage, policy) if stage and stage in policy["stages"] else None
    model = runs.model_for_tier(tier, policy) if tier else None

    missing: list[str] = []
    human_only = False
    if next_state is not None:
        ctx = transitions.context(conn, row)
        # Evaluate as a human so only the non-actor guards decide what is still missing.
        _, all_missing = transitions.evaluate(dict(row), next_state, ctx["evidence"], "@workflow",
                                              open_decisions=ctx["open_decisions"], edges_out=ctx["edges_out"],
                                              edges_in=ctx["edges_in"], children=ctx["children"],
                                              parents=ctx["parents"], decisions=ctx["decisions"])
        missing = [m for m in all_missing if not m.startswith("actor:") and m != "actor_is_owner"]
        edge = next((t for t in outgoing if t["to"] == next_state), None)
        actor_guards = [transitions.parse_guard(g) for g in (edge or {}).get("guards", []) if g.startswith("actor:")]
        human_only = next_state in transitions.PROTECTED_STATES or any("agent" not in g["classes"] for g in actor_guards) or (
            edge is not None and "actor_is_owner" in edge["guards"])
    budget = runs.budget(conn, slug, policy)
    command = step["command"]
    return {
        "slug": slug, "kind": kind, "state": state, "next_state": next_state,
        "alternatives": [t["to"] for t in outgoing if t["to"] != next_state],
        "command": command, "human_invoked": command in HUMAN_INVOKED,
        "stage": stage, "tier": tier, "model": model, "model_tier": tier, "model_alias": model,
        "missing": missing, "missing_evidence": [m for m in missing if m.startswith("evidence:") or m in EVIDENCE_GUARDS],
        "human_only": human_only, "fable_budget": budget, "fable_budget_remaining": budget["remaining"],
        "open_decisions": decisions.open_for(conn, slug), "done": next_state is None,
    }
