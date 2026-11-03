"""Test isolation: every database test has its own ``t_<random>`` schema and the suite leaves ``public`` alone.

The sentinel row itself is created by the session fixture ``public_sentinel`` (conftest.py) before the first test
and checked, then deleted, after the last one: a test that resets, truncates or drops anything in ``public`` makes
the session fail at teardown. The tests here check the pieces the guard relies on.
"""

from __future__ import annotations

import os

import pytest
from psycopg import sql

from sdlc_graph import db
from sdlc_graph.ledger import nodes


_USED: list[str] = []


def test_each_test_gets_its_own_empty_schema(clean_conn, pg_schema):
    _USED.append(pg_schema)
    assert pg_schema.startswith("t_") and pg_schema != "public"
    assert os.environ["SDLC_SCHEMA"] == pg_schema
    assert clean_conn.execute("SELECT current_schema() AS s").fetchone()["s"] == pg_schema
    assert clean_conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"] == 0
    nodes.create(clean_conn, kind="feature", slug="isolation-probe", title="Probe", repo="r", created_by="@t")


def test_the_next_test_does_not_see_the_last_one_s_rows(clean_conn):
    assert clean_conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"] == 0


def test_the_schema_is_dropped_after_the_test(pg_schema):
    assert _USED, "runs after test_each_test_gets_its_own_empty_schema"
    with db.connect(schema="public") as c:
        left = [r["schema_name"] for r in c.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s)",
                                                   (_USED,))]
    assert left == [] and pg_schema not in _USED


def test_the_sentinel_row_in_public_is_there(public_sentinel):
    if public_sentinel is None:
        pytest.skip("no database")
    with db.connect(schema="public") as c:
        rows = c.execute(sql.SQL("SELECT token FROM public.{}").format(sql.Identifier(public_sentinel["table"]))).fetchall()
    assert [r["token"] for r in rows] == [public_sentinel["token"]]


def test_a_reset_of_public_is_not_something_the_fixtures_do(clean_conn, pg_schema):
    # clean_conn never calls db.reset(): the sentinel guard would fail the session if it did
    assert db.schema_name() == pg_schema
