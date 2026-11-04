"""The suite's own guards: it never runs DDL against a remote database or resets ``public``."""

from __future__ import annotations

import pytest

from graph_fakes import graph_conftest

_gc = graph_conftest()
TEST_DATABASE_ENV, require_test_schema, safe_test_database_url = (
    _gc.TEST_DATABASE_ENV, _gc.require_test_schema, _gc.safe_test_database_url)


def test_a_reset_of_public_is_refused():
    with pytest.raises(RuntimeError, match="refusing schema 'public'"):
        require_test_schema("public")
    with pytest.raises(RuntimeError):
        require_test_schema("customer_data")
    assert require_test_schema("t_abc123") == "t_abc123"


def test_a_remote_database_is_refused_without_an_explicit_opt_in(monkeypatch):
    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prod.example.com:5432/app")
    monkeypatch.setattr(_gc.db, "database_url", lambda: "postgresql://u:p@db.prod.example.com:5432/app")
    with pytest.raises(RuntimeError, match="refusing to run the test suite"):
        safe_test_database_url()


def test_an_explicit_test_url_is_accepted_and_exported(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "restored-by-monkeypatch")
    monkeypatch.setenv(TEST_DATABASE_ENV, "postgresql://u:p@ci-db.example.com/sdlc_test")
    assert safe_test_database_url() == "postgresql://u:p@ci-db.example.com/sdlc_test"
    import os
    assert os.environ["DATABASE_URL"] == "postgresql://u:p@ci-db.example.com/sdlc_test"
