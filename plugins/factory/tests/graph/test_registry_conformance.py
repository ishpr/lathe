"""The registry (``sdlc_graph/shapes.py``) against every place that must know a kind, a relation or an evidence kind.

Adding a node kind and forgetting its CHECK constraint, state machine, workflow step, index sections, Spanner
CHECK or schema marker is a red test here, not a silent gap (DESIGN 16.9 rule 1). Pure checks first; the
``db`` ones need Postgres and skip without it.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from psycopg import sql

from sdlc_graph import db, mcp_server, shapes, status
from sdlc_graph.index import chunk
from sdlc_graph.ledger import evidence, lifecycle, transitions, workflow
from sdlc_graph.spanner import ddl, export

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _values(blob: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([^']+)'", blob))


def _newest_check(constraint: str, column: str, inline: re.Pattern[str]) -> tuple[str, ...]:
    """The value list of the newest ``<constraint> CHECK (<column> IN (...))`` in sql/*.sql (an ALTER), else the
    inline CHECK of the CREATE TABLE in 002_ledger.sql."""
    named = re.compile(rf"(?<!\w){constraint}\s+CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)")
    for path in reversed(_sql_files()):
        found = named.findall(path.read_text())
        if found:
            return _values(found[-1])
    m = inline.search((SQL_DIR / "002_ledger.sql").read_text())
    assert m, f"no {constraint} found in sql/*.sql"
    return _values(m.group(1))


def _machines() -> dict:
    return transitions.load_machines()


# --------------------------------------------------------------------------- 1-2: CHECK constraints


def test_node_kinds_equal_the_newest_node_kind_check():
    inline = re.compile(r"CREATE TABLE IF NOT EXISTS node \(.*?CHECK \(kind IN \(([^)]*)\)\)", re.S)
    assert _newest_check("node_kind_check", "kind", inline) == shapes.NODE_KINDS


def test_node_rels_equal_the_newest_edge_rel_check():
    inline = re.compile(r"CREATE TABLE IF NOT EXISTS edge \(.*?CHECK \(rel IN \(([^)]*)\)\)", re.S)
    assert _newest_check("edge_rel_check", "rel", inline) == shapes.NODE_RELS


@pytest.mark.db
def test_live_constraints_match_the_registry(conn, pg_schema):
    def listed(table: str, column: str) -> set[str]:
        rows = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = %s AND c.conrelid = %s::regclass AND c.contype = 'c'", (pg_schema, f"{pg_schema}.{table}")).fetchall()
        found = [set(_values(r["d"])) for r in rows if re.match(rf"CHECK \(\(?{column} = ANY", r["d"])]
        assert len(found) == 1, (table, [r["d"] for r in rows])
        return found[0]

    assert listed("node", "kind") == set(shapes.NODE_KINDS)
    assert listed("edge", "rel") == set(shapes.NODE_RELS)


# --------------------------------------------------------------------------- 3-4: machines


def test_every_kind_has_a_machine_or_is_stateless():
    machines = _machines()
    assert set(machines) <= set(shapes.NODE_KINDS)
    assert set(shapes.STATELESS_KINDS) <= set(shapes.NODE_KINDS)
    assert not set(machines) & set(shapes.STATELESS_KINDS)
    assert set(machines) == set(shapes.NODE_KINDS) - set(shapes.STATELESS_KINDS)


# --------------------------------------------------------------------------- 5: protected states


def test_protected_states_have_one_definition_and_every_way_in_is_not_agent():
    assert mcp_server.PROTECTED_STATES is transitions.PROTECTED_STATES
    for kind, m in _machines().items():
        for t in m["transitions"]:
            if t["to"] not in transitions.PROTECTED_STATES:
                continue
            classes = [transitions.parse_guard(g)["classes"] for g in t["guards"] if g.startswith("actor:")]
            assert classes and all("agent" not in c for c in classes), (
                f"{kind} {t['from']} -> {t['to']} enters a protected state without a non-agent actor guard")


# --------------------------------------------------------------------------- 6: workflow steps


def test_every_non_terminal_state_has_a_workflow_step():
    missing = []
    for kind, m in _machines().items():
        for state in m["states"]:
            if state in transitions.terminal_states(kind):
                continue
            known = state in workflow.CONTRACT_STEPS if kind == "contract" else (kind, state) in workflow.OTHER_STEPS
            if not known:
                missing.append((kind, state))
    assert not missing


# --------------------------------------------------------------------------- 7: evidence kinds


def test_evidence_kinds_are_the_registry_and_need_only_the_ledger():
    code = ("import sys, json; from sdlc_graph.ledger import evidence; "
            "print(json.dumps({'kinds': sorted(evidence.KINDS), 'orch': 'sdlc_graph.orchestrator' in sys.modules}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["kinds"] == sorted(shapes.EVIDENCE_KINDS) and got["orch"] is False
    assert set(evidence.KINDS) == set(shapes.EVIDENCE_KINDS)
    for kind, m in _machines().items():
        for t in m["transitions"]:
            for g in t["guards"]:
                parsed = transitions.parse_guard(g)
                if parsed["type"] == "evidence":
                    assert parsed["kind"] in shapes.EVIDENCE_KINDS, (kind, g)


def test_pack_kinds_are_the_review_pages_the_skills_post_as_evidence():
    skills = Path(__file__).resolve().parents[2] / "skills"
    posting = {d.name: d.joinpath("SKILL.md").read_text() for d in skills.iterdir()
               if d.joinpath("SKILL.md").is_file() and "review_pack" in d.joinpath("SKILL.md").read_text()}
    assert set(posting) == {"init", "assess", "brief", "design"}
    written = {k for t in posting.values() for k in re.findall(r"docs/reviews/<date>-([a-z_]+)\.html", t)}
    assert written == set(shapes.PACK_KINDS)
    assert "review_pack" in shapes.EVIDENCE_KINDS


# --------------------------------------------------------------------------- 8: schema markers


def test_every_migration_has_a_status_marker():
    assert [v for v, _ in status._MARKERS] == sorted({p.name[:3] for p in _sql_files()}, reverse=True)
    assert status.latest_version() == status._MARKERS[0][0]


@pytest.mark.db
def test_each_migration_is_recognised_by_its_prefix():
    files = _sql_files()
    for upto in sorted({f.name[:3] for f in files}):
        name = f"t_{secrets.token_hex(5)}"
        try:
            with db.connect(schema="public") as admin:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
            with db.connect(schema=name) as c:
                db.run_sql_files(c, [f for f in files if f.name[:3] <= upto])
                # 001 already carries the symbol-kind lists 008 widens, so a schema built from 001..007 alone
                # reports 008; a database an older plugin made has the short lists and reports its own prefix.
                assert status.schema_version(c, name) == max(upto, "008")
        finally:
            db.drop_schema(name)


# --------------------------------------------------------------------------- 9: Spanner


def _check_list(text: str, constraint: str) -> tuple[str, ...]:
    m = re.search(rf"CONSTRAINT {constraint} CHECK \(\w+ IN \(([^)]*)\)\)", text)
    assert m, f"{constraint} is not in the Spanner DDL"
    return _values(m.group(1))


def test_spanner_ddl_checks_are_generated_from_the_registry():
    text = ddl.render()
    assert _check_list(text, "node_kind_check") == shapes.NODE_KINDS
    assert _check_list(text, "edge_rel_check") == shapes.NODE_RELS
    assert "CHECK" not in ddl.render(checks=False)


def _ddl_columns(table: str) -> set[str]:
    body = ddl._TABLE_DDL[table].split("(", 1)[1].rsplit(") PRIMARY KEY", 1)[0]
    return {m.group(1).strip("`") for m in re.finditer(r"^\s*(`?\w+`?) [A-Z]", body, re.M)} - {"CONSTRAINT"}


def test_spanner_tables_carry_every_exported_column():
    for table in ("node", "decision"):
        assert set(export._TABLES[table][1]) == _ddl_columns(table), table


@pytest.mark.db
def test_every_postgres_column_of_node_and_decision_is_exported_or_dropped(conn, pg_schema):
    for table in ("node", "decision"):
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (pg_schema, table)).fetchall()}
        exported, dropped = set(export._TABLES[table][1]), set(export.DROPPED.get(table, ()))
        assert cols == exported | dropped, (table, sorted(cols ^ (exported | dropped)))
        assert not exported & dropped


# --------------------------------------------------------------------------- 10: index sections


def _fixture_row(kind: str) -> tuple[dict, list[dict]]:
    extras = [s for s in chunk.KIND_SECTIONS[kind] if s not in chunk.GENERIC_SECTIONS and s != "body"]
    attrs = {k: f"{k} value" for k in extras}
    if "body" in chunk.KIND_SECTIONS[kind]:
        attrs["body"] = "the text of the record"
    row = {"id": "00000000-0000-0000-0000-000000000001", "kind": kind, "slug": f"{kind}-x", "title": "T", "repo": "r",
           "path": "docs/x.md", "state": "s", "tier": "low", "acceptance": [{"id": "AC1", "text": "does a thing"}],
           "touched_symbols": ["function:a.b@a.py"], "capabilities": {"paths": ["app/"]}, "attrs": attrs}
    decisions = [{"question": "Q?", "options": ["a", "b"], "recommendation": "a", "choice": None}]
    return row, decisions


def test_index_sections_are_declared_for_every_kind_and_emitted():
    assert set(chunk.KIND_SECTIONS) == set(shapes.NODE_KINDS)
    for kind in shapes.NODE_KINDS:
        row, decisions = _fixture_row(kind)
        assert {c.section for c in chunk.chunks_for_node(row, decisions)} == set(chunk.KIND_SECTIONS[kind]), kind


# --------------------------------------------------------------------------- 11-12: lifecycle.json, the board


def test_lifecycle_json_maps_every_state_of_every_machine_exactly_once():
    data = lifecycle._data()
    assert tuple(data["stages"]) == lifecycle.STAGES
    machines = _machines()
    assert set(data["outside"]) <= set(machines) and not set(data["outside"]) & set(data["map"])
    assert set(data["map"]) | set(data["outside"]) == set(machines)
    for kind, m in machines.items():
        if kind in data["outside"]:
            continue
        mapped, derived = set(data["map"].get(kind, {})), set(data["derived"].get(kind, []))
        assert not mapped & derived, (kind, mapped & derived)
        assert mapped | derived == set(m["states"]), (kind, set(m["states"]) ^ (mapped | derived))
        assert set(data["map"].get(kind, {}).values()) <= set(data["stages"])
        assert set(data["killed"].get(kind, [])) <= set(m["states"])


@pytest.mark.xfail(strict=True, raises=ImportError, reason="the board arrives in lifecycle increment 8; remove this marker then")
def test_board_columns_are_the_lifecycle_stages():
    from sdlc_graph.ledger import board  # noqa: F401  (does not exist yet)

    assert board.COLUMNS == lifecycle.STAGES
