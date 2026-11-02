"""Impact fixes from the review: package __init__ edges, conftest expansion, HTTP test -> route edges,
module-level closure for touched functions, unresolved entries, conftest/__init__ never a test id."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import _git, commit_files
from sdlc_graph.codegraph import extract_python, snapshot
from sdlc_graph.impact import affected

FILES = {
    "app/__init__.py": "from app.services.billing import charge  # package init runs on every import beneath it\n",
    "app/services/__init__.py": "",
    "app/services/billing.py": "def charge(x):\n    return x\n",
    "app/services/mail.py": "def send_mail(x):\n    return x\n",
    "app/models.py": "class Item:\n    pass\n",
    "app/auth.py": "def verify_password(pw):\n    return bool(pw)\n\n\ndef login(pw):\n    return verify_password(pw)\n",
    "app/routes.py": (
        "from fastapi import APIRouter\n\nfrom app.auth import login\n\nrouter = APIRouter()\n\n\n"
        "@router.post('/login')\ndef do_login(body):\n    return login(body)\n\n\n"
        "@router.get('/items/{item_id}')\ndef get_item(item_id):\n    return item_id\n"),
    "app/main.py": "from fastapi import FastAPI\n\nfrom app.routes import router\n\napp = FastAPI()\napp.include_router(router)\n",
    "tests/__init__.py": "",
    "tests/test_mail.py": "from app.services.mail import send_mail\n\n\ndef test_mail():\n    assert send_mail(1)\n",
    "tests/test_billing.py": "from app.services.billing import charge\n\n\ndef test_charge():\n    assert charge(1)\n",
    "tests/repo/__init__.py": "",
    "tests/repo/conftest.py": "import pytest\n\nfrom app.models import Item\n\n\n@pytest.fixture\ndef item():\n    return Item()\n",
    "tests/repo/test_a.py": "def test_a(item):\n    assert item\n",
    "tests/repo/test_b.py": "def test_b(item):\n    assert item\n",
    "tests/repo/test_c.py": "def test_c():\n    assert True\n",   # uses no fixture, but pytest still imports conftest
    "tests/test_http.py": (
        "from fastapi.testclient import TestClient\n\nfrom app.main import app\n\nclient = TestClient(app)\n\n\n"
        "def test_login_over_http():\n    assert client.post('/login', json={}).status_code\n\n\n"
        "def test_item_over_http(i=3):\n    assert client.get(f'/items/{i}').status_code\n"),
}


@pytest.fixture
def brownfield_like(tmp_path) -> Path:
    repo = tmp_path / "rc"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    commit_files(repo, FILES, "init")
    return repo


def edges(repo: Path, rel: str) -> set[tuple[str, str]]:
    result = extract_python.extract(repo)
    return {(e["src"]["path"] + ":" + e["src"]["qualname"], e["dst"]["path"]) for e in result["edges"] if e["rel"] == rel}


def test_importing_a_module_also_imports_its_ancestor_packages(brownfield_like):
    imports = edges(brownfield_like, "imports")
    assert ("tests/test_mail.py:tests.test_mail", "app/services/__init__.py") in imports
    assert ("tests/test_mail.py:tests.test_mail", "app/__init__.py") in imports
    assert ("tests/test_mail.py:tests.test_mail", "app/services/mail.py") in imports


def test_tests_that_send_literal_requests_get_edges_to_the_routes(brownfield_like):
    result = extract_python.extract(brownfield_like)
    tests = {s["qualname"]: s["attrs"].get("requests") for s in result["symbols"] if s["kind"] == "test"}
    assert tests["tests.test_http.test_login_over_http"] == [{"method": "POST", "path": "/login"}]
    assert tests["tests.test_http.test_item_over_http"] == [{"method": "GET", "path": "/items/{}"}]
    merged = {"symbols": result["symbols"], "edges": result["edges"]}
    links = {(e["src"]["qualname"], e["dst"]["qualname"]) for e in extract_python.link(merged)}
    assert ("tests.test_http.test_login_over_http", "POST /login") in links
    assert ("tests.test_http.test_item_over_http", "GET /items/{item_id}") in links


def index(conn, repo) -> int:
    return snapshot.index(conn, repo, "HEAD", name="rc")["snapshot_id"]


def test_touching_a_package_init_predicts_the_tests_that_import_beneath_it(clean_conn, brownfield_like):
    sid = index(clean_conn, brownfield_like)
    out = affected.affected(clean_conn, sid, paths=["app/services/__init__.py"])
    assert {"tests/test_mail.py::test_mail", "tests/test_billing.py::test_charge"} <= set(out["tests"])
    # a sibling change reaches a test that imports a different module of the same package? Only via the
    # package: billing.py is not imported by test_mail, but breaking billing breaks app/__init__ (imports it)
    out = affected.affected(clean_conn, sid, paths=["app/services/billing.py"])
    assert "tests/test_mail.py::test_mail" in out["tests"]


def test_a_reached_conftest_contributes_the_tests_beneath_it_and_is_never_a_test_id(clean_conn, brownfield_like):
    sid = index(clean_conn, brownfield_like)
    out = affected.affected(clean_conn, sid, paths=["app/models.py"])
    assert {"tests/repo/test_a.py::test_a", "tests/repo/test_b.py::test_b",
            "tests/repo/test_c.py::test_c"} <= set(out["tests"])
    assert not [t for t in out["tests"] if t.endswith("conftest.py") or "__init__" in t.split("::")[0]]
    assert not [t for t in out["order"] if "conftest" in t]


def test_http_tests_reach_the_handler_and_function_level_impact_has_a_closure(clean_conn, brownfield_like):
    sid = index(clean_conn, brownfield_like)
    key = "function:app.auth.verify_password@app/auth.py"
    out = affected.affected(clean_conn, sid, symbols=[key])
    assert "tests/test_http.py::test_login_over_http" in out["tests"]          # via login -> do_login -> POST /login
    out = affected.affected(clean_conn, sid, symbols=["function:app.routes.get_item@app/routes.py"])
    assert "tests/test_http.py::test_item_over_http" in out["tests"]
    assert "GET /items/{item_id}" in out["routes"]
    # a function nothing calls directly still predicts through its module's importers, ranked last
    out = affected.affected(clean_conn, sid, symbols=["function:app.services.mail.send_mail@app/services/mail.py"])
    assert "tests/test_mail.py::test_mail" in out["tests"]
    closure = affected.affected(clean_conn, sid, symbols=["function:app.models.Item@app/models.py".replace("function", "class")])
    assert {"tests/repo/test_a.py::test_a"} <= set(closure["tests"])
    reasons = " ".join(closure["reasons"].values())
    assert "module-level closure" in reasons or "direct" in reasons or "transitive" in reasons


def test_unresolved_entries_are_reported_not_silent_and_not_fatal(clean_conn, brownfield_like):
    sid = index(clean_conn, brownfield_like)
    out = affected.affected(clean_conn, sid, paths=["app/gone.py", "app/models.py"],
                            symbols=["function:app.routes.renamed@app/routes.py"])
    assert {u["entry"] for u in out["unresolved"]} == {"app/gone.py", "function:app.routes.renamed@app/routes.py"}
    assert all(u["reason"] == "absent_in_snapshot" for u in out["unresolved"])
    assert out["tests"]                                                           # the resolvable path still predicts
    empty = affected.affected(clean_conn, sid, paths=["app/gone.py"])
    assert empty["tests"] == [] and empty["unresolved"] == [{"entry": "app/gone.py", "reason": "absent_in_snapshot"}]
