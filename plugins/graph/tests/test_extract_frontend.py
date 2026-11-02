"""Frontend extractor and its cross-extractor ``link`` to routes."""

from __future__ import annotations

import pytest

from sdlc_graph.codegraph import extract_frontend as fe
from sdlc_graph.codegraph import extract_python as ep


def _calls(result):
    return {s["qualname"]: s for s in result["symbols"] if s["kind"] == "frontend_call"}


def _write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_normalise_url():
    n = fe.normalise_url
    assert n("/items/?q=1") == "/items"
    assert n("https://api.example.com/v1//items/#top") == "/v1/items"
    assert n("/") == "/"
    assert n("items") == "/items"


def test_synthetic_calls_and_link(synthetic_repo, synthetic_facts):
    fx = fe.extract(synthetic_repo)
    calls = _calls(fx)
    assert set(calls) == set(synthetic_facts["frontend_calls"])
    py = ep.extract(synthetic_repo)
    merged = {"symbols": py["symbols"] + fx["symbols"], "edges": py["edges"] + fx["edges"]}
    edges = fe.link(merged)
    assert [(e["src"]["qualname"], e["dst"]["qualname"], e["attrs"]["match"]) for e in edges] == [
        ("GET /items", "GET /items", "exact")]


def test_no_frontend_dir(tmp_path):
    assert fe.extract(tmp_path) == {"symbols": [], "edges": []}


def test_call_forms(tmp_path):
    _write(tmp_path, {"frontend/src/a.ts": '''\
const BASE = "/api/base";
// fetch("/commented/out")
/* fetch("/also/commented") */
export const a = () => fetch("/api/plain");
export const b = (id: string) => fetch(`/api/things/${id}/steps?x=1`, { method: "DELETE" });
export const c = () => axios.post("/api/ax", body);
export const d = () => axios.get(BASE + "/x");
export const e = () => fetch(BASE);
export const f = () => axios({ url: "/api/cfg", method: "put" });
export const g = () => fetch(dynamicOnly);
export const h = () => fetch("https://svc.example.com/api/abs/");
''', "frontend/src/a.test.tsx": 'fetch("/api/only-in-test");\n'})
    result = fe.extract(tmp_path)
    calls = _calls(result)
    assert set(calls) == {"GET /api/plain", "DELETE /api/things/{}/steps", "POST /api/ax", "GET /api/base/x",
                          "GET /api/base", "PUT /api/cfg", "GET /api/abs"}
    assert "confidence" not in calls["GET /api/plain"]["attrs"]
    assert calls["DELETE /api/things/{}/steps"]["attrs"]["confidence"] == "low"
    assert calls["GET /api/plain"]["attrs"]["line"] == 4
    assert calls["GET /api/base"]["attrs"]["confidence"] == "low"  # resolved through a constant
    assert {s["qualname"] for s in result["symbols"] if s["kind"] == "module"} == {
        "frontend/src/a.ts", "frontend/src/a.test.tsx"}


def test_param_match_and_unmounted_route_not_matched():
    syms = [
        {"kind": "route", "name": "get", "qualname": "GET /things/{tid}", "path": "r.py", "line_start": 1,
         "line_end": 2, "attrs": {"method": "GET", "path": "/things/{tid}", "mounted": True}},
        {"kind": "route", "name": "ghost", "qualname": "GET /ghost", "path": "r.py", "line_start": 5,
         "line_end": 6, "attrs": {"method": "GET", "path": "/ghost", "mounted": False}},
        {"kind": "frontend_call", "name": "x", "qualname": "GET /things/{}", "path": "f.ts", "line_start": 1,
         "line_end": 1, "attrs": {"method": "GET", "url": "/things/{}"}},
        {"kind": "frontend_call", "name": "y", "qualname": "GET /ghost", "path": "f.ts", "line_start": 2,
         "line_end": 2, "attrs": {"method": "GET", "url": "/ghost"}},
        {"kind": "frontend_call", "name": "z", "qualname": "POST /things/{}", "path": "f.ts", "line_start": 3,
         "line_end": 3, "attrs": {"method": "POST", "url": "/things/{}"}},
    ]
    edges = fe.link({"symbols": syms, "edges": []})
    assert [(e["src"]["qualname"], e["dst"]["qualname"], e["attrs"]["match"]) for e in edges] == [
        ("GET /things/{}", "GET /things/{tid}", "param")]


def test_sample_app_fetch_to_a_missing_route(sample_app):
    fx = fe.extract(sample_app)
    calls = _calls(fx)
    assert {"GET /items", "GET /reports/summary"} <= set(calls)
    py = ep.extract(sample_app)
    merged = {"symbols": py["symbols"] + fx["symbols"], "edges": py["edges"] + fx["edges"]}
    linked = [(e["src"]["qualname"], e["dst"]["qualname"], e["attrs"]["match"]) for e in fe.link(merged)]
    assert linked == [("GET /items", "GET /items", "exact")]   # /reports/summary has no route behind it
    paths = {r["attrs"]["path"] for r in py["symbols"] if r["kind"] == "route"}
    assert "/reports/summary" not in paths
