"""depends_on in the goal loop: a dependent waits for its dependencies' merge, or (with stack) stacks on one open pull request.

Fixture feature (see test_orchestrator.py): contract ``rate-limit-headers`` (B) depends on ``rate-limit`` (A).
No model is called; the fake executor does every step.
"""

from __future__ import annotations

import json
from pathlib import Path

from sdlc_graph.ledger import nodes
from sdlc_graph.orchestrator import deps, gitops, loop, store

from test_orchestrator import (A, B, FEATURE, _contract, _git, cfg_for, env, feature, repo, state,  # noqa: F401  (fixtures)
                               tick_until)


def _by(report: dict) -> dict:
    return {e["slug"]: e for e in report["last"]["contracts"]}


def _merge(conn, slug: str) -> None:
    """The merge as the ledger sees it. The approved and merged transitions need review and CI evidence and a human
    or CI actor (not under test here), so the row is moved directly."""
    conn.execute("UPDATE node SET state = 'merged' WHERE slug = %s", (slug,))


def test_chain_runs_in_order_and_the_dependent_waits_for_the_merge(feature, repo, env):
    conn = feature
    cfg = cfg_for(repo)
    first = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert _by(first)[A]["action"] == "dispatched"
    assert _by(first)[B]["action"] == "waiting_on_dependency"
    assert _by(first)[B]["detail"] == [f"waiting on {A} (ready)"]
    assert not store.open_escalations(conn, [B])             # waiting raises no decision

    tick_until(conn, cfg, lambda r: state(conn, A) == "in_review")
    rep = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    # A is in_review: for the default (no stack) that is not enough, B still waits and nothing of B ran
    assert _by(rep)[B]["action"] == "waiting_on_dependency" and _by(rep)[B]["detail"] == [f"waiting on {A} (in_review)"]
    assert _by(rep)[B]["waiting_on"] == [{"slug": A, "state": "in_review"}]
    assert store.step_runs(conn, B, "tests") == [] and not gitops.worktree_path(repo, B).exists()

    _merge(conn, A)
    tick_until(conn, cfg, lambda r: state(conn, B) == "in_review")
    first_b = store.step_runs(conn, B, "tests")[0]
    assert first_b["meta"]["stacked_on"] is None and first_b["meta"]["base"] == first_b["meta"]["pr_base"] == "main"
    ships = {s: store.step_runs(conn, s, "ship")[-1] for s in (A, B)}
    assert ships[A]["finished_at"] < first_b["started_at"], "B started before A was merged"


def test_stack_runs_the_dependent_on_the_open_pull_request(feature, repo, env):
    conn = feature
    cfg = cfg_for(repo, stack=True)
    first = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert _by(first)[B]["detail"] == [f"waiting on {A} (ready)"]      # still waits while A has no pull request

    tick_until(conn, cfg, lambda r: state(conn, B) == "in_review")
    assert state(conn, A) == "in_review"
    branch_a = gitops.branch_for(A)
    runs = [r for step in ("tests", "implement", "verify", "ship") for r in store.step_runs(conn, B, step)]
    assert runs and all(r["meta"]["base"] == r["meta"]["pr_base"] == branch_a and r["meta"]["stacked_on"] == A for r in runs)
    # B's branch starts from A's head, so its diff is B's own change
    _git(repo, "merge-base", "--is-ancestor", branch_a, gitops.branch_for(B))
    cur = json.loads((gitops.worktree_path(repo, B) / ".claude/work/current.json").read_text())
    assert cur["pr_base"] == branch_a and cur["stacked_on"] == A
    ship_b = store.step_runs(conn, B, "ship")[-1]
    prompt = Path(ship_b["meta"]["prompt_file"]).read_text()
    assert f"gh pr create --base {branch_a}" in prompt
    assert "gh pr create --base" not in Path(store.step_runs(conn, A, "ship")[-1]["meta"]["prompt_file"]).read_text()
    assert loop.orchestrate(FEATURE, cfg, once=True, quiet=True)["done"]


def test_stack_needs_exactly_one_unmerged_dependency(feature, repo):
    conn = feature
    _contract(conn, "audit-log", "Audit every key rotation", "audit")
    _contract(conn, "dash", "A dashboard over the audit log", "dash", depends_on=[A, B])
    cfg = cfg_for(repo, stack=True)
    for slug in (A, B):
        conn.execute("UPDATE node SET state = 'in_review' WHERE slug = %s", (slug,))
    two = deps.eligibility(conn, cfg, "dash")
    assert two["stack_on"] is None
    assert two["lines"] == [f"waiting on {A} (in_review)", f"waiting on {B} (in_review)"]
    conn.execute("UPDATE node SET state = 'merged' WHERE slug = %s", (A,))
    one = deps.eligibility(conn, cfg, "dash")
    assert one == {"blocking": [], "lines": [], "stack_on": B}
    conn.execute("UPDATE node SET state = 'implementing' WHERE slug = %s", (B,))   # no pull request yet: cannot stack
    assert deps.eligibility(conn, cfg, "dash")["lines"] == [f"waiting on {B} (implementing)"]
    assert deps.eligibility(conn, cfg_for(repo), "dash")["stack_on"] is None
    conn.execute("UPDATE node SET state = 'merged' WHERE slug = %s", (B,))
    assert deps.eligibility(conn, cfg_for(repo), "dash") == {"blocking": [], "lines": [], "stack_on": None}


def test_a_dependency_cycle_raises_one_p1_decision(feature, repo, env):
    conn = feature
    nodes.link(conn, A, B, "depends_on")                      # B already depends on A
    cfg = cfg_for(repo)
    out = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert out["stopped"] == "dependency_cycle" and not out["done"]
    assert {e["action"] for e in out["last"]["contracts"]} == {"dependency_cycle"}
    esc = store.open_escalations(conn, [FEATURE])
    assert len(esc) == 1 and esc[0]["severity"] == "P1" and A in esc[0]["question"] and B in esc[0]["question"]
    assert esc[0]["meta"]["kind"] == "dependency_cycle" and esc[0]["meta"]["slugs"] == sorted([A, B])
    again = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert again["stopped"] == "dependency_cycle"
    assert [d["id"] for d in store.open_escalations(conn, [FEATURE])] == [esc[0]["id"]]       # once
    assert store.step_runs(conn, A, "tests") == [] and store.step_runs(conn, B, "tests") == []
