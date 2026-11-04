"""metrics.snapshot: intent survival, repeat incidents, cost per merged contract by tier."""

from __future__ import annotations

from sdlc_graph.ledger import metrics, nodes, runs

REPO = "sample-app"


def mk(c, kind, slug, state, **kw):
    nodes.create(c, kind=kind, slug=slug, title=slug, repo=REPO, created_by="@a", state=state, **kw)


def test_intent_survival(clean_conn):
    c = clean_conn
    mk(c, "epic", "e-ok", "drafted")
    mk(c, "epic", "e-rej", "drafted")
    mk(c, "epic", "e-flight", "drafted")
    mk(c, "epic", "e-drop", "drafted")
    mk(c, "feature", "f-ok", "proposed")
    mk(c, "feature", "f-never", "proposed")
    for slug, path in (("e-ok", ["accepted", "in_progress", "measured"]), ("e-flight", ["accepted", "in_progress"]),
                       ("e-drop", ["accepted", "closed"]), ("f-ok", ["ready", "building", "released"])):
        prev = None
        for st in path:
            c.execute("INSERT INTO transition (node_id, from_state, to_state, actor) "
                      "SELECT id, %s, %s, '@a' FROM node WHERE slug = %s", (prev, st, slug))
            prev = st
        c.execute("UPDATE node SET state = %s WHERE slug = %s", (path[-1], slug))
    c.execute("UPDATE node SET state = 'rejected' WHERE slug = 'e-rej'")
    s = metrics.snapshot(c, repo=REPO)["intent_survival"]
    assert (s["survived"], s["lost"]) == (2, 2)  # e-ok, f-ok survived; e-rej, e-drop lost
    assert s["rate"] == 0.5
    assert s["by_kind"]["feature"] == {"survived": 1, "lost": 0, "rate": 1.0}
    assert metrics.snapshot(c, repo="elsewhere")["intent_survival"]["rate"] is None


def test_repeat_incidents(clean_conn):
    c = clean_conn
    for i, slug in enumerate(("inc-1", "inc-2", "inc-3")):
        mk(c, "incident", slug, "open")
        c.execute("UPDATE node SET created_at = now() + make_interval(hours => %s) WHERE slug = %s", (i * 2, slug))
    mk(c, "rule", "rule-a", "active")
    c.execute("UPDATE node SET created_at = now() + interval '1 hour' WHERE slug = 'rule-a'")
    mk(c, "epic", "fix", "accepted")
    nodes.link(c, "inc-1", "rule-a", "governed_by")
    nodes.link(c, "inc-3", "rule-a", "governed_by")  # same rule failed to prevent inc-3
    # inc-2 is unrelated by edges: not a repeat, however similar its title
    m = metrics.snapshot(c, repo=REPO)["repeat_incidents"]
    assert m == {"incidents": 3, "repeats": 1, "rate": 0.333}
    # a supersedes edge relates inc-2 to inc-1, but inc-1's rule appeared after inc-2? it did (1h < 2h): a repeat
    nodes.link(c, "inc-2", "inc-1", "supersedes")
    assert metrics.snapshot(c, repo=REPO)["repeat_incidents"]["repeats"] == 2
    # no closing artifact before it: inc-1 alone is never a repeat
    assert metrics.snapshot(c, repo=REPO, since="2999-01-01")["repeat_incidents"]["rate"] is None


def test_repeat_incident_closed_by_merged_contract(clean_conn):
    c = clean_conn
    mk(c, "incident", "i-a", "closed")
    mk(c, "incident", "i-b", "open")
    c.execute("UPDATE node SET created_at = now() + interval '5 hours' WHERE slug = 'i-b'")
    mk(c, "contract", "c-fix", "merged")
    nodes.link(c, "c-fix", "i-a", "caused_by")
    nodes.link(c, "i-b", "i-a", "supersedes")
    assert metrics.snapshot(c, repo=REPO)["repeat_incidents"]["repeats"] == 1


def test_cost_per_merged_contract_by_tier(clean_conn):
    c = clean_conn
    mk(c, "contract", "c-1", "merged")
    mk(c, "contract", "c-2", "merged")
    mk(c, "contract", "c-open", "ready")
    for slug, stage, model, cost in (("c-1", "implement", "sonnet", 1.0), ("c-2", "contract.design.high", "fable", 3.0),
                                     ("c-open", "implement", "sonnet", 9.0)):
        r = runs.start(c, stage=stage, requested_model=model, slug=slug)
        runs.finish(c, r["id"], outcome="ok", usage={"cost_usd": cost})
    m = metrics.snapshot(c, repo=REPO)
    by = m["cost_per_merged_contract_by_tier"]
    assert sum(by.values()) == m["cost_per_merged_contract"] == 2.0
    assert by["planner"] == 1.5 and by["executor"] == 0.5
