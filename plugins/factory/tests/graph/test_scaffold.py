"""Scaffold checks: the schema applies and re-applies, fixtures build, policy files are coherent,
and the CLI lists every subcommand group. Owned by the scaffold; owners add their own test files."""

from __future__ import annotations

import json
import subprocess

import pytest

from sdlc_graph import cli, db, shapes
from sdlc_graph.ledger import ROUTING_POLICY_PATH, STATE_MACHINES_PATH

EXPECTED_TABLES = {"snapshot", "sym", "sym_edge", "owner", "sym_delta", "node", "edge", "transition", "evidence",
                   "decision", "run", "prediction", "prediction_outcome"}


def test_schema_applied_and_idempotent(conn, pg_schema):
    assert EXPECTED_TABLES <= set(db.list_tables(conn, pg_schema))
    again = db.run_sql_files(conn)  # re-running every file must be a no-op, not an error
    assert again == [p.name for p in db.sql_files()]
    assert conn.execute("SHOW search_path").fetchone()["search_path"].startswith(pg_schema)


def test_check_constraints_replace_enums(clean_conn):
    with pytest.raises(Exception):
        clean_conn.execute("INSERT INTO node (kind, slug, title, repo, state, created_by) VALUES ('nope','s','t','r','x','@a')")


def test_state_machines_are_coherent():
    machines = json.loads(STATE_MACHINES_PATH.read_text())
    assert set(machines) == set(shapes.NODE_KINDS) - set(shapes.STATELESS_KINDS)
    for kind, m in machines.items():
        states = set(m["states"])
        for t in m["transitions"]:
            assert t["from"] in states and t["to"] in states, (kind, t)
            assert isinstance(t["guards"], list)
    assert machines["contract"]["states"][0] == "drafted"
    assert machines["contract"]["states"][-1] == "merged"


def test_routing_policy_stages_map_to_tiers():
    policy = json.loads(ROUTING_POLICY_PATH.read_text())
    assert set(policy["stages"].values()) <= set(policy["tiers"])
    assert policy["fable_budget_per_epic"] == 3


def test_symbol_key_round_trip():
    key = {"kind": "route", "qualname": "GET /items", "path": "app/routes/a.py"}
    assert shapes.parse_key(shapes.key_str(key)) == key
    with pytest.raises(ValueError):
        shapes.parse_key("app/routes/a.py")


def test_synthetic_repo_builds(synthetic_repo, synthetic_facts):
    assert (synthetic_repo / "app/routes/b.py").exists()
    log = subprocess.run(["git", "-C", str(synthetic_repo), "log", "--oneline"], capture_output=True, text=True, check=True)
    assert len(log.stdout.splitlines()) == 1
    assert "GET /b/orphan" in synthetic_facts["routes"]


def test_git_commit_helper(make_synthetic_repo, git_commit):
    repo = make_synthetic_repo()
    sha = git_commit(repo, {"app/routes/c.py": "X = 1\n", "app/routes/b.py": None}, "add c, drop b")
    assert len(sha) == 40 and not (repo / "app/routes/b.py").exists()


def test_cli_lists_every_group(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for group in ("db", "index", "delta", "verify-snapshot", "query", "node", "evidence", "decision", "workflow",
                  "run", "metrics", "impact", "capabilities", "hooks", "spanner", "mcp"):
        assert group in out
