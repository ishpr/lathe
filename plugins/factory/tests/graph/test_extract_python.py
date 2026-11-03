"""Python extractor: the synthetic repo's planted facts and the sample app's known shape."""

from __future__ import annotations

import time

import pytest

from sdlc_graph.codegraph import extract_python as ep
from sdlc_graph.shapes import key_str


def _syms(result, kind):
    return [s for s in result["symbols"] if s["kind"] == kind]


def _edges(result, rel):
    return [(key_str(e["src"]), key_str(e["dst"])) for e in result["edges"] if e["rel"] == rel]


@pytest.fixture(scope="module")
def syn(synthetic_repo):
    return ep.extract(synthetic_repo)


@pytest.fixture(scope="module")
def app(sample_app):
    return ep.extract(sample_app)


def test_module_name():
    assert ep.module_name("app/routes/a.py") == "app.routes.a"
    assert ep.module_name("app/__init__.py") == "app"
    assert ep.module_name("conftest.py") == "conftest"
    with pytest.raises(ValueError):
        ep.module_name("app/a.ts")


def test_synthetic_modules(syn, synthetic_facts):
    assert sorted(s["qualname"] for s in _syms(syn, "module")) == synthetic_facts["modules"]


def test_synthetic_routes(syn, synthetic_facts):
    routes = {s["qualname"]: s for s in _syms(syn, "route")}
    assert sorted(routes) == sorted(synthetic_facts["routes"])
    for qual, want in synthetic_facts["routes"].items():
        r = routes[qual]
        assert r["path"] == want["path"], qual
        assert r["attrs"]["handler"] == want["handler"], qual
        assert r["attrs"]["mounted"] is want["mounted"], qual
        assert r["attrs"]["auth"] is want["auth"], qual
    assert routes["GET /items"]["attrs"]["router_prefix"] == "/items"
    assert routes["POST /items"]["attrs"]["dependencies"] == ["require_user"]
    handler = "function:app.routes.a.create_item@app/routes/a.py"
    assert (handler, "route:POST /items@app/routes/a.py") in _edges(syn, "exposes_route")


def test_synthetic_includes_router(syn, synthetic_facts):
    got = [(e["src"]["qualname"], e["dst"]["qualname"]) for e in syn["edges"] if e["rel"] == "includes_router"]
    assert got == synthetic_facts["includes_router"]


def test_synthetic_env(syn, synthetic_facts):
    env = {}
    for s in _syms(syn, "env_var"):
        env.setdefault(s["qualname"], []).append(s["path"])
    assert env == synthetic_facts["env_vars"]
    outside = sorted(s["qualname"] for s in _syms(syn, "env_var") if not s["attrs"]["in_config"])
    assert outside == synthetic_facts["env_reads_outside_config"]
    by_name = {s["qualname"]: s["attrs"] for s in _syms(syn, "env_var")}
    assert by_name["DATABASE_URL"]["access"] == "environ[]"
    assert by_name["DEBUG"]["access"] == "getenv" and by_name["DEBUG"]["default"] == "'0'"
    assert by_name["B_TOKEN"]["access"] == "environ.get"
    reads = _edges(syn, "reads_env")
    assert ("function:app.routes.b.orphan@app/routes/b.py", "env_var:B_TOKEN@app/routes/b.py") in reads
    assert ("module:app.config@app/config.py", "env_var:DATABASE_URL@app/config.py") in reads


def test_synthetic_models(syn, synthetic_facts):
    models = {s["qualname"]: s["attrs"]["table"] for s in _syms(syn, "model")}
    assert models == {k: v["table"] for k, v in synthetic_facts["models"].items()}
    assert ("class:app.models.Item@app/models.py", "model:app.models.Item@app/models.py") in _edges(syn, "defines")
    assert ("class:app.models.Item@app/models.py", "method:app.models.Item.label@app/models.py") in _edges(syn, "defines")
    assert ("function:app.routes.a.make_item@app/routes/a.py", "model:app.models.Item@app/models.py") in _edges(syn, "uses_model")


def test_synthetic_tests_and_calls(syn, synthetic_facts):
    tests = {s["attrs"]["nodeid"]: s for s in _syms(syn, "test")}
    assert set(tests) == set(synthetic_facts["tests"])
    t = "test:tests.test_a.test_list_items@tests/test_a.py"
    for want in synthetic_facts["tests"]["tests/test_a.py::test_list_items"]["imports"]:
        assert (t, f"function:{want}@app/routes/a.py") in _edges(syn, "tests")
    calls = _edges(syn, "calls")
    assert (t, "function:app.routes.a.list_items@app/routes/a.py") in calls
    # the chain the impact layer relies on: Item <- make_item <- list_items <- test
    assert ("function:app.routes.a.make_item@app/routes/a.py", "class:app.models.Item@app/models.py") in calls
    assert ("function:app.routes.a.list_items@app/routes/a.py", "function:app.routes.a.make_item@app/routes/a.py") in calls
    # unique method name: resolved by name only, so low confidence
    low = [e for e in syn["edges"] if e["rel"] == "calls" and e["dst"]["qualname"] == "app.models.Item.label"]
    assert low and all(e["attrs"].get("confidence") == "low" for e in low)
    # Depends(require_user) is a reference from the handler
    assert ("function:app.routes.a.create_item@app/routes/a.py", "function:app.auth.require_user@app/auth.py") in calls


def test_synthetic_imports(syn):
    imports = _edges(syn, "imports")
    assert ("module:app.main@app/main.py", "module:app.config@app/config.py") in imports
    assert ("module:tests.test_a@tests/test_a.py", "module:app.routes.a@app/routes/a.py") in imports
    mod = {s["qualname"]: s for s in _syms(syn, "module")}
    assert mod["app.routes.a"]["attrs"]["external_imports"] == ["fastapi"]
    assert mod["app.models"]["attrs"]["external_imports"] == ["sqlalchemy"]
    assert mod["app.config"]["attrs"]["external_imports"] == []  # os is stdlib


def test_deterministic_and_idempotent(synthetic_repo, syn):
    assert ep.extract(synthetic_repo) == syn
    keys = [(s["path"], s["line_start"] or 0, s["kind"], s["qualname"]) for s in syn["symbols"]]
    assert keys == sorted(keys)


def test_parse_error_is_not_fatal(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")
    result = ep.extract(tmp_path)
    bad = [s for s in result["symbols"] if s["path"] == "bad.py"]
    assert len(bad) == 1 and bad[0]["kind"] == "module"
    assert "parse_error" in bad[0]["attrs"] and bad[0]["attrs"]["confidence"] == "low"
    assert any(s["qualname"] == "ok.f" for s in result["symbols"])
    with pytest.raises(FileNotFoundError):
        ep.extract(tmp_path / "missing")


def test_nested_router_include_and_reexport(tmp_path):
    files = {
        "app/__init__.py": "",
        "app/main.py": "from fastapi import FastAPI\nfrom app.api import api\n\napp = FastAPI()\napp.include_router(api, prefix='/v1')\n",
        "app/api/__init__.py": "from .users import router as users_router\nfrom fastapi import APIRouter\n\n"
                               "api = APIRouter(prefix='/api')\napi.include_router(users_router, prefix='/users')\n",
        "app/api/users.py": "from fastapi import APIRouter, Depends\nfrom app.deps import get_current_user\n\n"
                            "router = APIRouter(dependencies=[Depends(get_current_user)])\n\n\n"
                            "@router.get('/{uid}')\ndef one(uid: int):\n    return uid\n",
        "app/deps.py": "def get_current_user():\n    return 1\n",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    routes = {s["qualname"]: s["attrs"] for s in _syms(ep.extract(tmp_path), "route")}
    assert list(routes) == ["GET /v1/api/users/{uid}"]
    assert routes["GET /v1/api/users/{uid}"]["mounted"] is True
    assert routes["GET /v1/api/users/{uid}"]["auth"] is True


# --------------------------------------------------------------------------- sample app (examples/sample-app)


def test_sample_app_routes(app):
    routes = _syms(app, "route")
    mounted = [r for r in routes if r["attrs"]["mounted"]]
    assert sorted(r["qualname"] for r in mounted) == sorted([
        "GET /items", "GET /items/search", "POST /items", "DELETE /items/{item_id}", "POST /login", "GET /users/{user_id}"])
    # the route on the router nothing includes is still recorded, flagged unmounted
    assert sorted(r["qualname"] for r in routes if not r["attrs"]["mounted"]) == ["GET /admin/stats"]


def test_sample_app_exactly_one_authenticated_route(app):
    authed = [r["qualname"] for r in _syms(app, "route") if r["attrs"]["auth"]]
    assert authed == ["POST /items"]
    create = next(r for r in _syms(app, "route") if r["qualname"] == "POST /items")
    assert create["attrs"]["dependencies"] == ["api_key_auth"]


def test_sample_app_unmounted_router(app):
    includes = {e["dst"]["qualname"] for e in app["edges"] if e["rel"] == "includes_router"}
    assert includes == {"app.routes.items", "app.routes.users"}
    modules_with_routes = {r["path"] for r in _syms(app, "route")}
    never = sorted(p for p in modules_with_routes if ep.module_name(p) not in includes and p != "app/main.py")
    assert never == ["app/routes/admin.py"]


def test_sample_app_env_reads_outside_config(app):
    outside = {(s["qualname"], s["path"]) for s in _syms(app, "env_var") if not s["attrs"]["in_config"]}
    assert outside == {("REGION", "app/routes/items.py")}
    inside = {s["qualname"] for s in _syms(app, "env_var") if s["attrs"]["in_config"]}
    assert inside == {"DATABASE_URL", "API_KEY"}


def test_sample_app_models_and_tests(app):
    assert sorted(s["attrs"]["table"] for s in _syms(app, "model")) == ["items", "users"]
    tests = {s["qualname"] for s in _syms(app, "test")}
    assert len(tests) == 11 and "tests.test_items.test_create_requires_key" in tests
    # a route test reaches production code through the client fixture's app import
    item_tests = [e for e in app["edges"] if e["rel"] == "tests" and e["src"]["path"] == "tests/test_items.py"]
    assert {e["dst"]["qualname"] for e in item_tests} >= {"app.main"}


def test_sample_app_speed_and_determinism(sample_app, app):
    start = time.perf_counter()
    again = ep.extract(sample_app)
    assert time.perf_counter() - start < 5
    assert again == app


def test_sample_repo_extracts_deterministically(sample_repo):
    """SAMPLE_REPO (optional): any checkout extracts, twice, to the same result."""
    first = ep.extract(sample_repo)
    assert first["symbols"] and ep.extract(sample_repo) == first


def test_tests_reach_code_through_fixtures_and_module_vars(tmp_path):
    files = {
        "app/__init__.py": "",
        "app/svc.py": "def work():\n    return 1\n",
        "app/other.py": "def other():\n    return 2\n",
        "tests/__init__.py": "",
        "tests/conftest.py": "import pytest\nfrom app.svc import work\n\n\n@pytest.fixture\ndef prepared():\n    return work()\n",
        "tests/test_x.py": "from app import other\n\nvalue = other\n\n\ndef test_fixture(prepared):\n    assert prepared\n\n\n"
                           "def test_var():\n    assert value\n",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    result = ep.extract(tmp_path)
    tests = {(e["src"]["qualname"], e["dst"]["qualname"]): e["attrs"] for e in result["edges"] if e["rel"] == "tests"}
    assert tests[("tests.test_x.test_fixture", "app.svc.work")]["via"] == "fixture"
    assert tests[("tests.test_x.test_var", "app.other")]["confidence"] == "low"
