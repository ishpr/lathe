"""Database tests for the lifecycle (DESIGN 16): migration 013, the tree and lifecycle_view, the guards that read the
ledger (children, parents, decisions), decision keys, backfill and re-resolution, and the CLI."""

from __future__ import annotations

import json
import secrets

import pytest
from psycopg import sql

from sdlc_graph import cli, db
from sdlc_graph.codegraph import snapshot
from sdlc_graph.ledger import decisions, evidence, lifecycle, nodes, transitions

REPO = "o/lifecycle"


def mk(c, kind, slug, state=None, **kw):
    return nodes.create(c, kind=kind, slug=slug, title=slug, repo=REPO, created_by="@pm", state=state, **kw)


def chain(c):
    """idea -> epic -> feature -> story -> contract, each in a state that maps to a stage."""
    mk(c, "idea", "idea-1", "promoted")
    mk(c, "epic", "epic-1", "accepted")
    mk(c, "feature", "feat-1", "building")
    mk(c, "story", "story-1", "building")
    mk(c, "contract", "ct-1", "implementing")
    nodes.link(c, "idea-1", "epic-1", "promoted_to")
    nodes.link(c, "epic-1", "feat-1", "decomposes_into")
    nodes.link(c, "feat-1", "story-1", "decomposes_into")
    nodes.link(c, "story-1", "ct-1", "decomposes_into")


# --------------------------------------------------------------------------- migration 013


def test_013_remaps_stories_and_decisions_audits_it_and_reruns_as_a_noop():
    files = db.sql_files()
    old, new = [f for f in files if f.name[:3] < "013"], [f for f in files if f.name[:3] == "013"]
    name = f"t_{secrets.token_hex(5)}"
    try:
        with db.connect(schema="public") as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        with db.connect(schema=name) as c:
            db.run_sql_files(c, old)

            def raw(kind, slug, state):
                return c.execute("INSERT INTO node (kind, slug, title, repo, state, created_by) VALUES (%s,%s,%s,%s,%s,'@a') RETURNING id",
                                 (kind, slug, slug, REPO, state)).fetchone()["id"]

            lone, built, mid = raw("story", "s-lone", "active"), raw("story", "s-built", "active"), raw("story", "s-mid", "active")
            keep = raw("story", "s-keep", "drafted")
            dec, rule = raw("decision", "d-1", "active"), raw("rule", "r-1", "active")
            for slug, st, parent in (("c1", "merged", built), ("c2", "merged", built), ("c3", "merged", mid), ("c4", "implementing", mid)):
                cid = raw("contract", slug, st)
                c.execute("INSERT INTO edge (src, dst, rel) VALUES (%s,%s,'decomposes_into')", (parent, cid))
            db.run_sql_files(c, new)
            states = {r["slug"]: r["state"] for r in c.execute("SELECT slug, state FROM node WHERE kind IN ('story','decision','rule')").fetchall()}
            assert states == {"s-lone": "drafted", "s-built": "built", "s-mid": "building", "s-keep": "drafted", "d-1": "proposed", "r-1": "active"}
            rows = c.execute("SELECT n.slug, t.from_state, t.to_state, t.actor FROM transition t JOIN node n ON n.id = t.node_id "
                             "WHERE t.actor = 'ci:migrate-013' ORDER BY n.slug").fetchall()
            assert [(r["slug"], r["from_state"], r["to_state"]) for r in rows] == [
                ("d-1", "active", "proposed"), ("s-built", "active", "built"), ("s-lone", "active", "drafted"), ("s-mid", "active", "building")]
            before = c.execute("SELECT count(*) AS n FROM transition").fetchone()["n"]
            db.run_sql_files(c, new)
            assert c.execute("SELECT count(*) AS n FROM transition").fetchone()["n"] == before
            assert lone and keep and dec and rule
    finally:
        db.drop_schema(name)


def test_013_adds_the_kinds_the_rel_and_the_indexes(clean_conn):
    c = clean_conn
    mk(c, "finding", "f-1", attrs={"fingerprint": "fp"})
    with pytest.raises(Exception):  # the unique fingerprint index per repo
        mk(c, "finding", "f-2", attrs={"fingerprint": "fp"})
    mk(c, "idea", "i-1")
    nodes.link(c, "i-1", "f-1", "promoted_to")
    assert {r["indexname"] for r in c.execute("SELECT indexname FROM pg_indexes WHERE tablename IN ('node','decision')").fetchall()} >= {
        "node_finding_fingerprint_idx", "decision_key_idx"}


# --------------------------------------------------------------------------- tree and lifecycle_view


def test_lifecycle_view_of_idea_epic_feature_story_contract(clean_conn):
    c = clean_conn
    chain(c)
    view = lifecycle.lifecycle_view(c, "idea-1")
    assert (view["slug"], view["kind"], view["stage"], view["killed"]) == ("idea-1", "idea", "ideate", False)
    epic = view["children"][0]
    # the epic is accepted: its stage is the parent rule, which is a TODO for the product owner
    assert (epic["slug"], epic["stage"], epic["progress"]) == ("epic-1", None, {"todo": "parent_stage"})
    feat = epic["children"][0]
    assert (feat["slug"], feat["stage"]) == ("feat-1", "build")
    story = feat["children"][0]
    assert (story["slug"], story["stage"]) == ("story-1", "build")
    ct = story["children"][0]
    assert (ct["slug"], ct["stage"], ct["children"]) == ("ct-1", "build", [])
    assert story["progress"] == {"children": 1, "killed": 0, "by_stage": {"build": 1}}
    assert [r["slug"] for r in lifecycle.tree(c, "idea-1")] == ["epic-1", "feat-1", "story-1", "ct-1"]
    assert [r["depth"] for r in lifecycle.tree(c, "idea-1")] == [1, 2, 3, 4]


def test_killed_exits_and_outside_kinds(clean_conn):
    c = clean_conn
    mk(c, "idea", "idea-k", "parked")
    mk(c, "epic", "ep-k", "drafted")
    mk(c, "story", "st-k", "rejected")
    mk(c, "finding", "fi-k")
    nodes.link(c, "ep-k", "st-k", "decomposes_into")
    nodes.link(c, "ep-k", "fi-k", "decomposes_into")
    assert lifecycle.lifecycle_view(c, "idea-k")["killed"] is True
    assert lifecycle.lifecycle_view(c, "idea-k")["stage"] == "done"
    view = lifecycle.lifecycle_view(c, "ep-k")
    by = {k["slug"]: k for k in view["children"]}
    assert by["st-k"]["killed"] is True and by["st-k"]["stage"] == "done"
    assert by["fi-k"]["stage"] is None  # findings sit outside the funnel
    assert view["progress"] == {"children": 2, "killed": 1, "by_stage": {"done": 1}}
    with pytest.raises(LookupError):
        lifecycle.lifecycle_view(c, "nope")


def test_the_tree_stops_at_depth_six_and_survives_a_cycle(clean_conn):
    c = clean_conn
    for i in range(9):
        mk(c, "feature", f"n{i}")
    for i in range(8):
        nodes.link(c, f"n{i}", f"n{i + 1}", "decomposes_into")
    rows = lifecycle.tree(c, "n0")
    assert [r["slug"] for r in rows] == [f"n{i}" for i in range(1, 7)] and max(r["depth"] for r in rows) == 6
    nodes.link(c, "n8", "n0", "decomposes_into")  # a cycle through the whole chain
    nodes.link(c, "n6", "n2", "decomposes_into")  # and a short one
    again = lifecycle.tree(c, "n2")
    assert {r["slug"] for r in again} <= {f"n{i}" for i in range(9)} and len(again) < 50
    assert "n2" not in {r["slug"] for r in again if r["depth"] > 0 and r["parent"] == "n6"}
    view = lifecycle.lifecycle_view(c, "n2")  # terminates
    assert view["slug"] == "n2"


def test_a_diamond_shows_the_shared_child_under_each_parent(clean_conn):
    c = clean_conn
    for s in ("top", "l", "r", "bottom"):
        mk(c, "feature", s)
    for a, b in (("top", "l"), ("top", "r"), ("l", "bottom"), ("r", "bottom")):
        nodes.link(c, a, b, "decomposes_into")
    view = lifecycle.lifecycle_view(c, "top")
    assert [[k["slug"] for k in ch["children"]] for ch in view["children"]] == [["bottom"], ["bottom"]]


def test_the_lifecycle_view_follows_only_decomposes_into_and_promoted_to(clean_conn):
    c = clean_conn
    for s in ("a", "b", "d"):
        mk(c, "feature", s)
    nodes.link(c, "a", "b", "depends_on")
    nodes.link(c, "a", "d", "decomposes_into")
    assert [r["slug"] for r in lifecycle.tree(c, "a")] == ["d"]


@pytest.mark.xfail(strict=True, raises=NotImplementedError,
                   reason="parent_stage is a product rule (DESIGN 16.1); delete this marker when it is decided")
def test_parent_stage_examples():
    kids = [{"slug": "ct-a", "kind": "contract", "state": "merged", "stage": "done", "killed": False},
            {"slug": "ct-b", "kind": "contract", "state": "implementing", "stage": "build", "killed": False}]
    stage, progress = lifecycle.parent_stage(kids)
    assert stage == "build" and progress["done"] == 1 and progress["total"] == 2


# --------------------------------------------------------------------------- guards that read the ledger


def test_story_rollup_guards_read_children_through_the_ledger(clean_conn):
    c = clean_conn
    acc = [{"id": "S1.AC1", "hash": "h1"}, {"id": "S1.AC2", "hash": "h2"}]
    mk(c, "story", "st", "building", acceptance=acc)
    mk(c, "contract", "ct1", "merged", acceptance=[{"id": "AC1", "text": "x", "story_ac": "S1.AC1"}])
    mk(c, "contract", "ct2", "implementing", acceptance=[{"id": "AC1", "text": "y", "story_ac": "S1.AC2"}])
    nodes.link(c, "st", "ct1", "decomposes_into")
    nodes.link(c, "st", "ct2", "decomposes_into")
    res = transitions.transition(c, "st", "built", "agent:orchestrator")
    assert not res["ok"] and res["missing"] == ["all_out:decomposes_into:contract=merged"]
    c.execute("UPDATE node SET state = 'merged' WHERE slug = 'ct2'")
    assert transitions.transition(c, "st", "built", "agent:orchestrator")["ok"]
    # acceptance by an agent is refused whatever the evidence says
    res = transitions.transition(c, "st", "accepted", "agent:orchestrator")
    assert not res["ok"] and "actor:not_agent" in res["missing"]
    evidence.add(c, "st", "acceptance_signoff", {"criteria": ["S1.AC1", "S1.AC2"], "release": "rel-1", "environment": "prod",
                                                 "checked_by": "@pm"})
    assert transitions.transition(c, "st", "accepted", "@pm")["ok"]


def test_a_story_with_an_uncovered_criterion_is_not_built(clean_conn):
    c = clean_conn
    mk(c, "story", "st", "building", acceptance=[{"id": "S1.AC1", "hash": "h"}, {"id": "S1.AC2", "hash": "h"}])
    mk(c, "contract", "ct", "merged", acceptance=[{"id": "AC1", "text": "x", "story_ac": "S1.AC1"}])
    nodes.link(c, "st", "ct", "decomposes_into")
    assert transitions.transition(c, "st", "built", "agent:o")["missing"] == ["story_criteria_covered"]


def test_decision_keys_and_the_risk_acceptance_due_date(clean_conn):
    c = clean_conn
    mk(c, "finding", "f", "triaged", attrs={"owner": "@ana"})
    with pytest.raises(ValueError, match="due"):
        decisions.propose(c, "f", "Accept the risk?", ["yes", "no"], actor="agent:x", key="risk_acceptance")
    with pytest.raises(ValueError, match="lower_snake_case"):
        decisions.propose(c, "f", "q", [], actor="agent:x", key="Risk-Acceptance")
    d = decisions.propose(c, "f", "Accept the risk?", ["yes", "no"], actor="agent:x", key="risk_acceptance", due="2020-01-01")
    assert c.execute("SELECT meta FROM decision WHERE id = %s", (d["id"],)).fetchone()["meta"] == {"key": "risk_acceptance"}
    assert c.execute("SELECT meta FROM decision WHERE id = %s", (decisions.propose(c, "f", "plain?", [], actor="agent:x")["id"],)
                     ).fetchone()["meta"] == {}
    res = transitions.transition(c, "f", "risk_accepted", "@ana")
    assert not res["ok"] and res["missing"] == ["decision:risk_acceptance"]
    decisions.resolve(c, d["id"], "yes", actor="@ana")
    assert transitions.transition(c, "f", "risk_accepted", "@ana")["ok"]
    # the due date (2020) is past, so the acceptance has expired and the finding reopens
    assert transitions.transition(c, "f", "open", "ci:cron")["ok"]


def test_a_record_is_accepted_only_by_its_owner_with_a_file_and_a_commit(clean_conn):
    c = clean_conn
    mk(c, "decision", "adr-1", attrs={"owner": "@ana", "record_type": "adr"}, path="docs/adr/0001.md", commit_sha="a" * 40)
    assert nodes.get_row(c, "adr-1")["state"] == "proposed"
    assert transitions.transition(c, "adr-1", "accepted", "@bob")["missing"] == ["actor_is_owner"]
    assert transitions.transition(c, "adr-1", "accepted", "@Ana")["ok"]
    mk(c, "decision", "adr-2", attrs={"owner": "@ana", "record_type": "adr"})
    assert transitions.transition(c, "adr-2", "accepted", "@ana")["missing"] == ["record_file_present"]


# --------------------------------------------------------------------------- backfill and resolve


def test_backfill_gives_stories_their_contracts_symbols_dry_run_first_and_idempotently(clean_conn, tmp_path):
    c = clean_conn
    k1, k2 = "function:app.a.f@app/a.py", "function:app.b.g@app/b.py"
    mk(c, "story", "st")
    mk(c, "contract", "ct1", touched_symbols=[k1])
    mk(c, "contract", "ct2", touched_symbols=[k1, k2])
    nodes.link(c, "st", "ct1", "decomposes_into")
    nodes.link(c, "st", "ct2", "decomposes_into")
    dry = lifecycle.backfill(c, REPO, root=tmp_path)
    assert dry["apply"] is False and dry["stories"] == [{"slug": "st", "added": [k1, k2]}] and dry["changed"] == 1
    assert nodes.get_row(c, "st")["touched_symbols"] == []
    done = lifecycle.backfill(c, REPO, apply=True, root=tmp_path)
    assert done["changed"] == 1 and nodes.get_row(c, "st")["touched_symbols"] == [k1, k2]
    assert lifecycle.backfill(c, REPO, apply=True, root=tmp_path) == {"repo": REPO, "apply": True, "stories": [], "records": [], "changed": 0}


def test_backfill_resolves_an_accepted_records_paths_against_the_newest_snapshot(clean_conn, synthetic_repo, tmp_path):
    c = clean_conn
    info = snapshot.index(c, synthetic_repo, name=REPO)
    assert info["snapshot_id"]
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "adr.md").write_text("---\npaths:\n  - app/routes/**\n  - nothing/here/**\n---\nUse routers.\n")
    mk(c, "decision", "adr-r", "accepted", path="docs/adr.md")
    out = lifecycle.backfill(c, REPO, apply=True, root=tmp_path)
    rec = out["records"][0]
    assert rec["slug"] == "adr-r" and rec["unmatched"] == ["nothing/here/**"]
    assert set(rec["added"]) == {"app/routes/__init__.py", "app/routes/a.py", "app/routes/b.py"}
    assert set(nodes.get_row(c, "adr-r")["touched_symbols"]) == set(rec["added"])
    again = lifecycle.backfill(c, REPO, apply=True, root=tmp_path)
    assert again["records"][0]["added"] == [] and again["changed"] == 0


def test_resolve_follows_a_moved_symbol_flags_a_vanished_one_and_writes_once(clean_conn, synthetic_repo):
    c = clean_conn
    info = snapshot.index(c, synthetic_repo, name=REPO)
    sha = c.execute("SELECT commit_sha FROM snapshot WHERE id = %s", (info["snapshot_id"],)).fetchone()["commit_sha"]
    here = "function:app.routes.a.make_item@app/routes/a.py"
    moved = "function:app.routes.a.make_item@old/place.py"
    gone = "function:app.routes.a.deleted@app/routes/a.py"
    mk(c, "finding", "f-moved", attrs={"fingerprint": "x"}, touched_symbols=[here, moved, gone, "app/routes/", "nowhere/"])
    out = lifecycle.resolve(c, REPO, sha[:10])
    assert out["sha"] == sha and [n["slug"] for n in out["nodes"]] == ["f-moved"]
    assert out["nodes"][0]["resolved"] == {moved: here}
    assert out["nodes"][0]["vanished"] == sorted([gone, "nowhere/"])
    rows = evidence.list_for(c, "f-moved", "symbols_resolved")
    assert len(rows) == 1 and rows[0]["payload"]["sha"] == sha
    assert lifecycle.resolve(c, REPO, sha)["nodes"] == [] and len(evidence.list_for(c, "f-moved", "symbols_resolved")) == 1
    with pytest.raises(LookupError):
        lifecycle.resolve(c, REPO, "deadbeef")


# --------------------------------------------------------------------------- CLI


def test_cli_lifecycle_view_backfill_and_resolve(clean_conn, capsys):
    c = clean_conn
    chain(c)
    assert cli.main(["lifecycle", "view", "idea-1", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["slug"] == "idea-1" and view["children"][0]["children"][0]["children"][0]["children"][0]["slug"] == "ct-1"
    assert cli.main(["lifecycle", "view", "idea-1"]) == 0
    text = capsys.readouterr().out
    assert "idea-1" in text and "      ct-1" in text and "build" in text
    assert cli.main(["lifecycle", "backfill", "--repo", REPO, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["apply"] is False
    assert cli.main(["lifecycle", "view", "missing-slug"]) != 0
