"""Pure tests (no database) for the guard grammar the lifecycle machines need (all_out, all_in, decision:<key>, the
simple guards), the new evidence kinds, and the four machines walked with in-memory nodes."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from sdlc_graph.ledger import evidence, transitions
from sdlc_graph.ledger.transitions import evaluate

H = "a" * 40


def ev(i, kind, payload, source="local", signed_by=None):
    return {"id": i, "kind": kind, "payload": payload, "source": source, "signed_by": signed_by,
            "at": f"2026-11-02T10:{i:02d}:00+00:00", "binding": evidence.is_binding(source, signed_by)}


def node(kind, state, **kw):
    base = {"kind": kind, "state": state, "acceptance": [], "capabilities": {}, "attrs": {}, "path": None, "commit_sha": None}
    base.update(kw)
    return base


def child(kind, state, rel="decomposes_into", acceptance=None):
    return {"rel": rel, "kind": kind, "state": state, "slug": f"{kind}-{state}", "acceptance": acceptance or [], "attrs": {}}


def decision_row(key, by="@ana", due=None, id=1, resolved=True):
    return {"id": id, "decided_by": by, "choice": "yes", "due": due, "meta": {"key": key},
            "resolved_at": dt.datetime(2026, 11, 1) if resolved else None}


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize("guard,expected", [
    ("all_out:decomposes_into:contract=merged", {"type": "all_out", "rel": "decomposes_into", "kind": "contract", "states": ["merged"]}),
    ("all_out:decomposes_into:story=built|accepted|split|rejected",
     {"type": "all_out", "rel": "decomposes_into", "kind": "story", "states": ["built", "accepted", "split", "rejected"]}),
    ("all_in:supersedes:decision=accepted", {"type": "all_in", "rel": "supersedes", "kind": "decision", "states": ["accepted"]}),
    ("decision:risk_acceptance", {"type": "decision", "key": "risk_acceptance"}),
    ("owner_present", {"type": "owner_present"}),
    ("risk_expired", {"type": "risk_expired"}),
])
def test_parse_new_guards(guard, expected):
    parsed = transitions.parse_guard(guard)
    assert {k: parsed[k] for k in expected} == expected


@pytest.mark.parametrize("guard", [
    "all_out:decomposes_into:contract", "all_out:decomposes_into:contract=", "all_out:decomposes_into:contract=a||b",
    "all_out:likes:contract=merged", "all_out:decomposes_into:widget=merged", "all_in:supersedes",
    "decision:", "decision:Bad-Key", "owner_present:x",
])
def test_bad_new_guards_are_refused(guard):
    with pytest.raises(ValueError):
        transitions.parse_guard(guard)


def test_a_machine_naming_an_unknown_state_in_all_out_is_refused(tmp_path):
    path = tmp_path / "m.json"
    machines = {"story": {"states": ["a", "b"], "transitions": [{"from": "a", "to": "b", "guards": ["all_out:decomposes_into:contract=nope"]}]},
                "contract": {"states": ["drafted", "merged"], "transitions": []}}
    path.write_text(json.dumps(machines))
    with pytest.raises(ValueError, match="unknown contract state"):
        transitions.load_machines(path)


# --------------------------------------------------------------------------- all_out and all_in


def test_all_out_holds_with_no_children_and_fails_on_one_in_the_wrong_state():
    st = node("story", "building")
    assert evaluate(st, "built", [], "agent:orch", edges_out=["decomposes_into"])[0]  # zero children: holds trivially
    kids = [child("contract", "merged"), child("contract", "implementing")]
    ok, missing = evaluate(st, "built", [], "agent:orch", edges_out=["decomposes_into"], children=kids)
    assert not ok and "all_out:decomposes_into:contract=merged" in missing
    ok, missing = evaluate(st, "built", [], "agent:orch", edges_out=["decomposes_into"], children=[child("contract", "merged")])
    assert ok and missing == []


def test_all_out_only_looks_at_its_rel_and_kind():
    st = node("story", "building")
    kids = [child("contract", "merged"), child("feature", "proposed"), child("contract", "drafted", rel="depends_on")]
    assert evaluate(st, "built", [], "agent:orch", edges_out=["decomposes_into"], children=kids)[0]


def test_all_in_checks_incoming_edges():
    dec = node("decision", "accepted")
    kw = dict(edges_in=["supersedes"])
    ok, missing = evaluate(dec, "superseded", [], "@ana", parents=[child("decision", "proposed", rel="supersedes")], **kw)
    assert not ok and missing == ["all_in:supersedes:decision=accepted"]
    assert evaluate(dec, "superseded", [], "@ana", parents=[child("decision", "accepted", rel="supersedes")], **kw)[0]


# --------------------------------------------------------------------------- decision:<key>, owner, record, risk


def test_decision_key_needs_a_resolved_row_decided_by_a_human():
    f = node("finding", "triaged")
    ok, missing = evaluate(f, "false_positive", [], "@ana")
    assert missing == ["decision:dismissal"]
    ok, _ = evaluate(f, "false_positive", [], "@ana", decisions=[decision_row("dismissal")])
    assert ok
    assert not evaluate(f, "false_positive", [], "@ana", decisions=[decision_row("dismissal", by="agent:x")])[0]
    assert not evaluate(f, "false_positive", [], "@ana", decisions=[decision_row("dismissal", resolved=False)])[0]
    assert not evaluate(f, "false_positive", [], "@ana", decisions=[decision_row("triage")])[0]


def test_owner_present_and_actor_is_owner():
    rec = node("decision", "proposed", path="docs/adr/1.md", commit_sha=H, attrs={"owner": "@Ana", "record_type": "adr"})
    assert evaluate(rec, "accepted", [], "@ana")[0]  # case-insensitive
    assert evaluate(rec, "accepted", [], "@bob")[1] == ["actor_is_owner"]
    assert evaluate(node("decision", "proposed", path="p", commit_sha=H, attrs={"record_type": "adr"}), "accepted", [], "@bob")[1] == [
        "owner_present", "actor_is_owner"]
    assert "owner_present" in evaluate(node("decision", "proposed", path="p", commit_sha=H, attrs={"owner": "ana", "record_type": "adr"}),
                                       "accepted", [], "@ana")[1]


@pytest.mark.parametrize("kw,held", [
    ({"path": "docs/adr/1.md", "commit_sha": H, "record_type": "adr"}, True),
    ({"path": "docs/adr/1.md", "commit_sha": H, "record_type": "pdr"}, True),
    ({"path": "docs/adr/1.md", "commit_sha": H, "record_type": "note"}, False),
    ({"path": None, "commit_sha": H, "record_type": "adr"}, False),
    ({"path": "docs/adr/1.md", "commit_sha": "", "record_type": "ddr"}, False),
])
def test_record_file_present(kw, held):
    n = node("decision", "proposed", path=kw["path"], commit_sha=kw["commit_sha"], attrs={"owner": "@ana", "record_type": kw["record_type"]})
    assert ("record_file_present" not in evaluate(n, "accepted", [], "@ana")[1]) is held


def test_a_rejected_decision_record_needs_only_its_owner():
    rec = node("decision", "proposed", attrs={"owner": "@ana"})
    assert evaluate(rec, "rejected", [], "@ana")[0] and not evaluate(rec, "rejected", [], "@bob")[0]


def test_risk_expired_compares_the_due_date_with_today():
    f = node("finding", "risk_accepted")
    today = dt.date(2026, 11, 3)
    rows = lambda due: [decision_row("risk_acceptance", due=due)]  # noqa: E731
    assert evaluate(f, "open", [], "ci:cron", decisions=rows(dt.date(2026, 11, 2)), today=today)[0]
    assert evaluate(f, "open", [], "ci:cron", decisions=rows("2026-11-02"), today=today)[0]
    assert not evaluate(f, "open", [], "ci:cron", decisions=rows(dt.date(2026, 11, 3)), today=today)[0]
    assert evaluate(f, "open", [], "ci:cron", decisions=[], today=today)[1] == ["risk_expired"]
    assert evaluate(f, "open", [], "ci:cron", decisions=rows(None), today=today)[1] == ["risk_expired"]


def test_problem_present_needs_problem_who_and_evidence():
    full = {"problem": "p", "who": "w", "evidence": "e", "owner": "@ana"}
    assert evaluate(node("idea", "captured", attrs=full), "shortlisted", [], "@ana")[0]
    for k in ("problem", "who", "evidence"):
        bad = dict(full, **{k: "  "})
        assert evaluate(node("idea", "captured", attrs=bad), "shortlisted", [], "@ana")[1] == ["problem_present"]
    assert evaluate(node("idea", "captured", attrs=dict(full, evidence=["a", "b"])), "shortlisted", [], "@ana")[0]
    assert not evaluate(node("idea", "captured", attrs=dict(full, evidence=[])), "shortlisted", [], "@ana")[0]


# --------------------------------------------------------------------------- stories


def test_story_criteria_covered_needs_every_criterion_in_a_contracts_story_ac():
    st = node("story", "building", acceptance=[{"id": "S1.AC1", "hash": "h"}, {"id": "S1.AC2", "hash": "h"}])
    one = child("contract", "merged", acceptance=[{"id": "AC1", "text": "x", "story_ac": "S1.AC1"}])
    two = child("contract", "merged", acceptance=[{"id": "AC1", "text": "x", "story_ac": ["S1.AC2"]}])
    kw = dict(edges_out=["decomposes_into"])
    assert evaluate(st, "built", [], "agent:o", children=[one], **kw)[1] == ["story_criteria_covered"]
    assert evaluate(st, "built", [], "agent:o", children=[one, two], **kw)[0]
    stray = child("feature", "proposed", acceptance=[{"story_ac": "S1.AC2"}])
    assert not evaluate(st, "built", [], "agent:o", children=[one, stray], **kw)[0]


def test_story_accepted_needs_a_human_signoff_naming_every_criterion():
    st = node("story", "built", acceptance=[{"id": "S1.AC1", "hash": "h"}, {"id": "S1.AC2", "hash": "h"}])
    signoff = lambda crit: ev(1, "acceptance_signoff", {"criteria": crit, "release": "rel-1", "environment": "prod", "checked_by": "@pm"})  # noqa: E731
    assert evaluate(st, "accepted", [signoff(["S1.AC1", "S1.AC2"])], "@pm")[0]
    ok, missing = evaluate(st, "accepted", [signoff(["S1.AC1"])], "@pm")
    assert not ok and missing == ["criteria_signed_off"]
    assert "evidence:acceptance_signoff" in evaluate(st, "accepted", [], "@pm")[1]
    ok, missing = evaluate(st, "accepted", [signoff(["S1.AC1", "S1.AC2"])], "agent:o")
    assert not ok and "actor:not_agent" in missing


def test_a_story_walks_drafted_to_accepted_with_in_memory_facts():
    acc = [{"id": "S1.AC1", "text": "x"}]
    st = node("story", "drafted", acceptance=acc, attrs={"owner": "@pm"})
    assert evaluate(st, "ready", [], "@pm")[0]
    assert not evaluate(st, "ready", [], "agent:o")[0]
    st = node("story", "ready", acceptance=acc)
    assert evaluate(st, "building", [], "agent:o", edges_out=["decomposes_into"])[0]
    assert evaluate(node("story", "drafted"), "split", [], "@pm", edges_in=["supersedes"])[0]
    assert evaluate(node("story", "built"), "rejected", [], "@pm")[0]
    assert not evaluate(node("story", "built"), "rejected", [], "agent:o")[0]


# --------------------------------------------------------------------------- findings and ideas


def test_a_finding_is_verified_only_by_a_binding_absent_fingerprint_and_a_regression_test():
    f = node("finding", "fixed")
    absent = ev(1, "fingerprint_absent", {"fingerprint": "fp", "source": "ruff", "sha": H, "absent": True}, source="ci:run/1", signed_by="repo:o/r")
    reg = ev(2, "regression_test", {"test": "tests/t.py::test_x", "commit": H})
    assert evaluate(f, "verified", [absent, reg], "ci:gate")[0]
    unsigned = ev(1, "fingerprint_absent", {"fingerprint": "fp", "source": "ruff", "sha": H, "absent": True})
    assert evaluate(f, "verified", [unsigned, reg], "ci:gate")[1] == ["evidence:fingerprint_absent.binding"]
    still = ev(1, "fingerprint_absent", {"fingerprint": "fp", "source": "ruff", "sha": H, "absent": False}, source="ci:run/1", signed_by="repo:o/r")
    assert evaluate(f, "verified", [still, reg], "ci:gate")[1] == ["evidence:fingerprint_absent.absent==true"]
    assert evaluate(f, "verified", [absent], "ci:gate")[1] == ["evidence:regression_test"]
    assert evaluate(f, "verified", [absent, reg], "agent:x")[1] == ["actor:human|ci"]


def test_finding_fixing_to_fixed_needs_every_fixing_contract_merged():
    f = node("finding", "fixing")
    edge = dict(edges_in=["caused_by"])
    assert evaluate(f, "fixed", [], "agent:o", parents=[child("contract", "merged", rel="caused_by")], **edge)[0]
    ok, missing = evaluate(f, "fixed", [], "agent:o", parents=[child("contract", "merged", rel="caused_by"),
                                                                  child("contract", "verified", rel="caused_by")], **edge)
    assert not ok and missing == ["all_in:caused_by:contract=merged"]


def test_finding_back_edges_have_no_guards_and_triage_needs_evidence_and_a_decision():
    assert evaluate(node("finding", "verified"), "open", [], "agent:o")[0]
    assert evaluate(node("finding", "fixing"), "triaged", [], "agent:o")[0]
    assert evaluate(node("finding", "fixed"), "triaged", [], "agent:o")[0]
    triage = ev(1, "finding_triage", {"severity": "high", "likelihood": "likely", "owner": "@ana", "proposed_by": "agent:x"})
    assert evaluate(node("finding", "open"), "triaged", [], "agent:x")[1] == ["evidence:finding_triage", "decision:triage"]
    assert evaluate(node("finding", "open"), "triaged", [triage], "agent:x", decisions=[decision_row("triage")])[0]


def test_agents_cannot_enter_any_state_the_lifecycle_protects():
    for kind, to, frm in (("finding", "risk_accepted", "triaged"), ("finding", "duplicate", "triaged"),
                          ("idea", "shortlisted", "captured"), ("idea", "parked", "captured"), ("story", "split", "drafted"),
                          ("decision", "superseded", "accepted"), ("epic", "accepted", "drafted")):
        assert "actor:not_agent" in evaluate(node(kind, frm), to, [], "agent:o")[1], (kind, to)


def test_idea_promotion_needs_the_promoted_to_edge():
    assert evaluate(node("idea", "shortlisted"), "promoted", [], "@ana")[1] == ["edge_out:promoted_to"]
    assert evaluate(node("idea", "shortlisted"), "promoted", [], "@ana", edges_out=["promoted_to"])[0]
    assert evaluate(node("idea", "parked"), "captured", [], "@ana")[0]


# --------------------------------------------------------------------------- evidence kinds

GOOD = {
    "acceptance_signoff": {"criteria": ["S1.AC1"], "release": "rel-1", "environment": "prod", "checked_by": "@pm"},
    "design_review": {"path": "docs/design/x.md", "pr_url": "https://github.com/o/r/pull/3", "reviewer": "@a", "author": "@b", "merge_sha": H},
    "finding_triage": {"severity": "high", "likelihood": "possible", "owner": "@ana", "proposed_by": "agent:x", "cvss": 7.5, "duplicate_of": "f-1"},
    "regression_test": {"test": "tests/t.py::test_x", "commit": H},
    "fingerprint_absent": {"fingerprint": "fp", "source": "ruff", "sha": H, "absent": True},
    "finding_seen": {"source": "ruff", "sha": H, "count": 2},
    "symbols_resolved": {"sha": H, "resolved": {"function:a.f@a.py": "function:a.f@b.py"}, "vanished": ["function:a.g@a.py"]},
}


@pytest.mark.parametrize("kind", sorted(GOOD))
def test_new_evidence_kinds_accept_their_shape(kind):
    evidence.validate(kind, GOOD[kind])


@pytest.mark.parametrize("kind,change", [
    ("acceptance_signoff", {"criteria": []}), ("acceptance_signoff", {"criteria": ["", "x"]}),
    ("acceptance_signoff", {"checked_by": "pm"}), ("acceptance_signoff", {"environment": ""}),
    ("design_review", {"merge_sha": "xyz"}), ("design_review", {"reviewer": ""}),
    ("finding_triage", {"severity": "sev2"}), ("finding_triage", {"likelihood": "certain"}), ("finding_triage", {"cvss": 11}),
    ("finding_triage", {"cvss": True}), ("finding_triage", {"owner": ""}),
    ("regression_test", {"commit": "nope"}), ("regression_test", {"test": " "}),
    ("fingerprint_absent", {"absent": "yes"}), ("fingerprint_absent", {"sha": "z"}),
    ("finding_seen", {"count": 0}), ("finding_seen", {"count": True}),
    ("symbols_resolved", {"resolved": ["a"]}), ("symbols_resolved", {"vanished": "a"}), ("symbols_resolved", {"sha": "q"}),
])
def test_new_evidence_kinds_refuse_a_bad_field(kind, change):
    with pytest.raises(ValueError):
        evidence.validate(kind, {**GOOD[kind], **change})


def test_new_evidence_kinds_require_their_keys():
    for kind, payload in GOOD.items():
        for key in payload:
            if key in ("cvss", "duplicate_of"):
                continue
            with pytest.raises(ValueError, match=key):
                evidence.validate(kind, {k: v for k, v in payload.items() if k != key})
