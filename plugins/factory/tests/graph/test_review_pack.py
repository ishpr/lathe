"""review_pack evidence (DESIGN 17.2): what the human decider was shown is attached to the decision it preceded
and cited by the transition it preceded, and never gates anything. Database-backed (``clean_conn``)."""

from __future__ import annotations

import pytest

from sdlc_graph.ledger import decisions, evidence, nodes, transitions

H = "c" * 64
REPO = "o/sample-app"
ACC = [{"id": "AC1", "text": "401 without a token", "test": "tests/test_a.py::test_401"}]


def pack(path="docs/reviews/2026-11-04-decisions.html", sha=H, **shown):
    return {"pack": "decisions", "path": path, "sha256": sha, "shown_for": shown or {"decisions": [1]}}


def feature(c, slug="feature-pack", acceptance=ACC):
    return nodes.create(c, kind="feature", slug=slug, title="Pack", repo=REPO, created_by="@pm", acceptance=acceptance)


def backdate(c, evidence_id):
    c.execute("UPDATE evidence SET at = at - interval '1 day' WHERE id = %s", (evidence_id,))


# --------------------------------------------------------------------------- validation (pure)


def test_valid_pack_passes_with_optional_fields():
    evidence.validate("review_pack", {**pack(transition="ready"), "commit": "abc1234", "warnings": 0})
    evidence.validate("review_pack", pack(decisions=[3, 4], transition="ready"))


@pytest.mark.parametrize("bad", [
    {"path": "docs/other/2026-11-04-decisions.html"},
    {"path": "docs/reviews/../x/2026-11-04-decisions.html"},
    {"path": "docs/reviews/decisions.html"},
    {"pack": "slides"},
    {"sha256": "ABC"},
    {"shown_for": {}},
    {"shown_for": {"decisions": []}},
    {"shown_for": {"decisions": [0]}},
    {"shown_for": {"decisions": ["31"]}},
    {"shown_for": {"transition": ""}},
    {"shown_for": {"stories": [1]}},
    {"commit": "nothex"},
    {"warnings": -1},
])
def test_invalid_pack_is_rejected(bad):
    with pytest.raises(ValueError):
        evidence.validate("review_pack", {**pack(), **bad})


def test_pack_missing_a_required_key_is_rejected():
    p = pack()
    del p["sha256"]
    with pytest.raises(ValueError, match="sha256"):
        evidence.validate("review_pack", p)


# --------------------------------------------------------------------------- decisions (db)


def test_resolve_attaches_the_newest_pack_that_asked_the_decision(clean_conn):
    c = clean_conn
    feature(c)
    d31 = decisions.propose(c, "feature-pack", "Redis or memory?", ["Redis", "memory"], actor="agent:opus")
    d32 = decisions.propose(c, "feature-pack", "TTL?", ["60", "300"], actor="agent:opus")
    old = evidence.add(c, "feature-pack", "review_pack", pack(sha="1" * 64, decisions=[d31["id"]]))
    new = evidence.add(c, "feature-pack", "review_pack", pack(sha="2" * 64, decisions=[d31["id"]]))
    evidence.add(c, "feature-pack", "review_pack", pack(sha="3" * 64, decisions=[d32["id"]]))
    out = decisions.resolve(c, d31["id"], "Redis", actor="@po")
    assert out["meta"]["pack"] == {"evidence_id": new["id"], "path": "docs/reviews/2026-11-04-decisions.html",
                                   "sha256": "2" * 64}
    assert old["id"] != new["id"]
    assert decisions.get(c, d31["id"])["meta"]["pack"]["sha256"] == "2" * 64


def test_pack_for_another_decision_is_not_attached(clean_conn):
    c = clean_conn
    feature(c)
    d31 = decisions.propose(c, "feature-pack", "Redis or memory?", ["Redis", "memory"], actor="agent:opus", key="storage")
    d32 = decisions.propose(c, "feature-pack", "TTL?", ["60", "300"], actor="agent:opus")
    evidence.add(c, "feature-pack", "review_pack", pack(decisions=[d32["id"]]))
    out = decisions.resolve(c, d31["id"], "memory", actor="@po")
    assert "pack" not in out["meta"] and out["meta"]["key"] == "storage"


def test_resolve_without_any_pack_leaves_meta_alone(clean_conn):
    c = clean_conn
    feature(c)
    d = decisions.propose(c, "feature-pack", "Q?", ["a", "b"], actor="agent:opus")
    assert decisions.resolve(c, d["id"], "a", actor="@po")["meta"] == {}


def test_a_pack_on_another_node_is_not_attached(clean_conn):
    c = clean_conn
    feature(c)
    feature(c, slug="feature-other")
    d = decisions.propose(c, "feature-pack", "Q?", ["a", "b"], actor="agent:opus")
    evidence.add(c, "feature-other", "review_pack", pack(decisions=[d["id"]]))
    assert "pack" not in decisions.resolve(c, d["id"], "a", actor="@po")["meta"]


# --------------------------------------------------------------------------- transitions (db)


def test_transition_cites_the_matching_pack_and_ignores_an_older_one(clean_conn):
    c = clean_conn
    feature(c)
    stale = evidence.add(c, "feature-pack", "review_pack", pack(transition="ready"))
    backdate(c, stale["id"])  # shown before the node's previous transition (its creation)
    wrong = evidence.add(c, "feature-pack", "review_pack", pack(transition="building"))
    fresh = evidence.add(c, "feature-pack", "review_pack", pack(sha="4" * 64, transition="ready"))
    res = transitions.transition(c, "feature-pack", "ready", "@alice")
    assert res["ok"], res
    assert fresh["id"] in res["evidence_ids"]
    assert stale["id"] not in res["evidence_ids"] and wrong["id"] not in res["evidence_ids"]


def test_transition_does_not_cite_a_pack_shown_before_the_previous_transition(clean_conn):
    c = clean_conn
    feature(c)
    stale = evidence.add(c, "feature-pack", "review_pack", pack(transition="ready"))
    backdate(c, stale["id"])
    res = transitions.transition(c, "feature-pack", "ready", "@alice")
    assert res["ok"] and stale["id"] not in res["evidence_ids"]


def test_no_guard_outcome_changes_with_or_without_a_pack(clean_conn):
    c = clean_conn
    feature(c, slug="feature-bare", acceptance=[])
    feature(c, slug="feature-packed", acceptance=[])
    evidence.add(c, "feature-packed", "review_pack", pack(transition="ready"))
    bare = transitions.transition(c, "feature-bare", "ready", "@alice")
    packed = transitions.transition(c, "feature-packed", "ready", "@alice")
    assert not bare["ok"] and not packed["ok"]
    assert bare["missing"] == packed["missing"] and packed["evidence_ids"] == []
    feature(c, slug="feature-ok")
    ok = transitions.transition(c, "feature-ok", "ready", "@alice")
    feature(c, slug="feature-ok-packed")
    evidence.add(c, "feature-ok-packed", "review_pack", pack(transition="ready"))
    ok_packed = transitions.transition(c, "feature-ok-packed", "ready", "@alice")
    assert ok["ok"] and ok_packed["ok"] and ok["missing"] == ok_packed["missing"] == []
