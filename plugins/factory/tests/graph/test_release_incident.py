"""release_request (the release machine's entry), the human-only release moves, and incident open with its diagnosis."""

from __future__ import annotations

import json
import sys

import pytest

from sdlc_graph import cli, mcp_server
from sdlc_graph.ledger import evidence, nodes, release, transitions
from sdlc_graph.orchestrator import diagnose

from test_mcp import _call

FAKE = [sys.executable, "-I", "-m", "sdlc_graph.orchestrator.fake_executor"]


def _contract(conn, slug, state="merged"):
    return nodes.create(conn, kind="contract", slug=slug, title=slug, repo="r", created_by="@a", state=state)


# ----------------------------------------------------------------------------- release_request


def test_release_request_creates_requested_release_with_shipped_in_edges(clean_conn):
    _contract(clean_conn, "c1")
    _contract(clean_conn, "c2")
    out = release.request(clean_conn, actor="agent:mcp", contract_slugs=["c1", "c2"], slug="rel-1")
    assert out["release"]["kind"] == "release" and out["release"]["state"] == "requested"
    edges = nodes.neighbours(clean_conn, "rel-1", "shipped_in", "in")
    assert sorted(e["node"]["slug"] for e in edges) == ["c1", "c2"]


def test_release_request_from_a_feature_links_the_feature_too(clean_conn):
    nodes.create(clean_conn, kind="feature", slug="f1", title="f", repo="r", created_by="@a")
    _contract(clean_conn, "c1")
    nodes.link(clean_conn, "f1", "c1", "decomposes_into")
    out = release.request(clean_conn, actor="agent:mcp", feature_slug="f1", slug="rel-f")
    assert out["contracts"] == ["c1"] and out["feature"] == "f1"
    assert {e["node"]["slug"] for e in nodes.neighbours(clean_conn, "rel-f", "shipped_in", "in")} == {"c1", "f1"}


def test_release_request_refuses_unmerged_and_nonsense(clean_conn):
    _contract(clean_conn, "c1", state="in_review")
    with pytest.raises(ValueError, match="not merged: c1"):
        release.request(clean_conn, actor="agent:mcp", contract_slugs=["c1"])
    with pytest.raises(ValueError, match="one of them"):
        release.request(clean_conn, actor="agent:mcp")
    assert clean_conn.execute("SELECT count(*) AS n FROM node WHERE kind = 'release'").fetchone()["n"] == 0


def test_an_agent_cannot_move_a_release_past_staged(clean_conn, conn_factory):
    _contract(clean_conn, "c1")
    release.request(clean_conn, actor="agent:mcp", contract_slugs=["c1"], slug="rel-1")
    server = mcp_server.build_server(conn_factory)
    ok, res = _call(server, "release_request", {"contract_slugs": ["c1"], "slug": "rel-2"})
    assert not ok and res["release"]["state"] == "requested"
    for to in ("released", "verified", "rolled_back"):
        _, res = _call(server, "contract_transition", {"slug": "rel-1", "to_state": to})
        assert res["ok"] is False, to
    state = nodes.get_row(clean_conn, "rel-1")["state"]
    assert state == "requested"


def test_release_walks_to_verified_only_with_a_human_and_the_evidence(clean_conn):
    _contract(clean_conn, "c1")
    release.request(clean_conn, actor="agent:mcp", contract_slugs=["c1"], slug="rel-1")
    clean_conn.execute("UPDATE node SET state = 'staged' WHERE slug = 'rel-1'")   # past the CI-binding step
    assert not transitions.transition(clean_conn, "rel-1", "released", "agent:mcp")["ok"]
    assert transitions.transition(clean_conn, "rel-1", "released", "@ish")["ok"]
    r = transitions.transition(clean_conn, "rel-1", "verified", "@ish")
    assert not r["ok"]
    ids = [evidence.add(clean_conn, "rel-1", "ready_poll", {"url": "http://x/ready", "status": 200, "attempts": 2})["id"],
           evidence.add(clean_conn, "rel-1", "smoke", {"command": "smoke", "exit": 0})["id"]]
    assert not transitions.transition(clean_conn, "rel-1", "verified", "agent:mcp", evidence_ids=ids)["ok"]
    assert transitions.transition(clean_conn, "rel-1", "verified", "@ish", evidence_ids=ids)["ok"]


# ----------------------------------------------------------------------------- incident open


def test_diagnosis_evidence_kind_is_registered_and_validated(clean_conn):
    nodes.create(clean_conn, kind="incident", slug="i1", title="t", repo="r", created_by="@a")
    assert evidence.add(clean_conn, "i1", "diagnosis", {"summary": "x", "band": 2})["kind"] == "diagnosis"
    with pytest.raises(ValueError):
        evidence.add(clean_conn, "i1", "diagnosis", {"summary": "x", "band": 9})


def test_band_2_dry_run_prints_the_command_and_touches_nothing(tmp_path, capsys):
    rc = cli.main(["incident", "open", "inc-1", "--title", "p99 up", "--band", "2", "--dry-run", "--json",
                   "--repo-path", str(tmp_path), "--executor-cmd", "fake-exec"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["dry_run"] is True and out["incident"] is None
    assert out["command"].startswith("fake-exec ") and "--permission-mode dontAsk" in out["command"]
    assert "--model haiku" in out["command"]             # the triage stage is the utility tier
    assert "Edit" in out["denied"] and "Write" in out["denied"] and "Edit" not in out["tools"]
    assert not (tmp_path / "inc-1").exists()


def test_band_1_only_logs(clean_conn):
    out = diagnose.open_incident(clean_conn, "inc-1", "blip", 1, actor="@a", repo="r")
    assert out["action"] == "log" and out["incident"]["state"] == "open"
    assert clean_conn.execute("SELECT count(*) AS n FROM run").fetchone()["n"] == 0


def test_band_2_runs_the_fake_executor_records_the_run_and_posts_diagnosis(clean_conn, tmp_path):
    out = diagnose.open_incident(clean_conn, "inc-2", "5xx spike", 2, actor="@a", repo="r", repo_path=tmp_path,
                                 executor=FAKE, out_dir=tmp_path / "out")
    d = out["diagnosis"]
    assert d["ok"] and "Diagnosis (fake)" in d["summary"]
    run = clean_conn.execute("SELECT * FROM run WHERE id = %s", (d["run_id"],)).fetchone()
    assert run["step"] == "diagnose" and run["stage"] == "triage" and run["requested_model"] == "haiku"
    assert run["finished_at"] is not None and run["outcome"] == "success"
    view = nodes.get(clean_conn, "inc-2")
    assert view["evidence"]["diagnosis"]["count"] == 1
    assert (tmp_path / "out" / "inc-2" / "diagnose.allowed_tools.txt").read_text().splitlines()[0] == "Read"


def test_band_2_failure_records_a_failed_run_and_no_evidence(clean_conn, tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"diagnose": "nothing"}))
    monkeypatch.setenv("ORCH_FAKE_PLAN", str(plan))
    out = diagnose.open_incident(clean_conn, "inc-3", "x", 2, actor="@a", repo="r", repo_path=tmp_path,
                                 executor=FAKE, out_dir=tmp_path / "out")
    assert out["diagnosis"]["ok"] is False
    assert "diagnosis" not in nodes.get(clean_conn, "inc-3")["evidence"]


def test_band_3_opens_a_p1_decision_and_runs_nothing(clean_conn, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_NOTIFY", "none")
    out = diagnose.open_incident(clean_conn, "inc-4", "outage", 3, actor="@a", repo="r")
    assert out["action"] == "decision" and out["decision"]["severity"] == "P1"
    assert clean_conn.execute("SELECT count(*) AS n FROM run").fetchone()["n"] == 0


def test_bad_band_is_refused(clean_conn):
    with pytest.raises(ValueError):
        diagnose.open_incident(clean_conn, "inc-5", "x", 4, actor="@a", repo="r")
