"""Code-graph queries: find_symbol, deps, impact, owners, search, structural checks, and the CLI wiring.
Uses ``graph_fakes`` in place of the extractors; database tests skip when Postgres is down."""

from __future__ import annotations

import json
import subprocess

import pytest

import graph_fakes
from sdlc_graph import cli
from sdlc_graph.codegraph import queries, snapshot
from sdlc_graph.shapes import key_str


@pytest.fixture
def fakes(monkeypatch):
    graph_fakes.install(monkeypatch)


@pytest.fixture
def graph(fakes, clean_conn, synthetic_repo):
    """The synthetic repo indexed once; returns (conn, snapshot_id)."""
    out = snapshot.index(clean_conn, synthetic_repo, "HEAD", name="synthetic")
    return clean_conn, out["snapshot_id"]


# ---------------------------------------------------------------- find_symbol


def test_find_symbol_accepts_key_qualname_and_path(graph):
    conn, sid = graph
    by_key = queries.find_symbol(conn, sid, "function:app.routes.a.list_items@app/routes/a.py")
    by_name = queries.find_symbol(conn, sid, "app.routes.a.list_items")
    assert by_key["id"] == by_name["id"] and by_key["kind"] == "function"
    mod = queries.find_symbol(conn, sid, "app/routes/a.py")
    assert (mod["kind"], mod["qualname"]) == ("module", "app.routes.a")
    assert queries.find_symbol(conn, sid, "app.routes.a")["id"] == mod["id"]


def test_find_symbol_errors_name_candidates(graph):
    conn, sid = graph
    with pytest.raises(LookupError, match="ambiguous") as exc:      # class and model share a qualname
        queries.find_symbol(conn, sid, "app.models.Item")
    assert "class:app.models.Item@app/models.py" in str(exc.value) and "model:app.models.Item@app/models.py" in str(exc.value)
    with pytest.raises(LookupError):
        queries.find_symbol(conn, sid, "no.such.thing")
    with pytest.raises(LookupError):
        queries.find_symbol(conn, sid, "function:no.such@x.py")
    with pytest.raises(LookupError):
        queries.find_symbol(conn, 10**9, "app.main")


# ---------------------------------------------------------------- deps


def test_deps_out_depth_one_and_two(graph):
    conn, sid = graph
    one = queries.deps(conn, sid, "app.routes.a.list_items")
    assert one["snapshot_id"] == sid and one["root"]["qualname"] == "app.routes.a.list_items"
    assert {(n["qualname"], n["depth"]) for n in one["nodes"]} == {("app.routes.a.make_item", 1), ("GET /items", 1)}
    assert {(e["rel"], e["dst"]["qualname"]) for e in one["edges"]} == {("calls", "app.routes.a.make_item"), ("exposes_route", "GET /items")}
    two = queries.deps(conn, sid, "app.routes.a.list_items", depth=2)
    assert ("app.models.Item", 2) in {(n["qualname"], n["depth"]) for n in two["nodes"]}
    assert all("src_id" in e and "dst_id" in e for e in two["edges"])
    assert [n["depth"] for n in two["nodes"]] == sorted(n["depth"] for n in two["nodes"])


def test_deps_in_and_both_and_rel_filter(graph):
    conn, sid = graph
    callers = queries.deps(conn, sid, "app.routes.a.make_item", direction="in", rels=["calls"])
    assert {n["qualname"] for n in callers["nodes"]} == {"app.routes.a.list_items", "app.routes.a.create_item"}
    deep = queries.deps(conn, sid, "app.routes.a.make_item", direction="in", rels=["calls", "tests"], depth=2)
    assert "tests.test_a.test_list_items" in {n["qualname"] for n in deep["nodes"]}
    both = queries.deps(conn, sid, "app.routes.a.make_item", direction="both", rels=["calls"])
    assert {n["qualname"] for n in both["nodes"]} == {"app.routes.a.list_items", "app.routes.a.create_item", "app.models.Item"}
    imports = queries.deps(conn, sid, "app/routes/a.py", rels=["imports"])
    assert {n["qualname"] for n in imports["nodes"]} == {"app.auth", "app.models"}


def test_deps_validates_arguments(graph):
    conn, sid = graph
    for bad in ({"direction": "sideways"}, {"depth": 0}, {"depth": 7}, {"rels": ["nope"]}):
        with pytest.raises(ValueError):
            queries.deps(conn, sid, "app.main", **bad)
    with pytest.raises(LookupError):
        queries.deps(conn, sid, "missing.symbol")


# ---------------------------------------------------------------- impact


def test_impact_of_routes_module_returns_its_test(graph, synthetic_facts):
    conn, sid = graph
    out = queries.impact(conn, sid, paths=["app/routes/a.py"])
    assert "tests/test_a.py::test_list_items" in out["tests"]
    assert {"GET /items", "POST /items"} <= set(out["routes"])
    assert out["snapshot_id"] == sid and "reasons" not in out
    assert out["owners"]["app/routes/a.py"] == synthetic_facts["owners"]["app/routes/a.py"]
    assert out["symbols"][0]["depth"] == 0 and all("depth" in s for s in out["symbols"])
    assert "embedding" not in out["symbols"][0]


def test_impact_of_models_reaches_tests_and_routes_within_depth_four(graph, synthetic_facts):
    conn, sid = graph
    expected = synthetic_facts["impact"]["app/models.py"]
    out = queries.impact(conn, sid, paths=["app/models.py"])
    assert set(expected["tests"]) <= set(out["tests"])
    assert set(expected["routes_include"]) <= set(out["routes"])
    shallow = queries.impact(conn, sid, paths=["app/models.py"], depth=1)
    assert shallow["tests"] == []                                           # the test is 3 hops away
    zero = queries.impact(conn, sid, paths=["app/models.py"], depth=0)
    assert zero["tests"] == [] and zero["routes"] == [] and {s["depth"] for s in zero["symbols"]} == {0}
    assert max(s["depth"] for s in out["symbols"]) <= 4
    assert max(s["depth"] for s in shallow["symbols"]) <= 1


def test_impact_by_symbol_and_directory_and_union(graph):
    conn, sid = graph
    by_sym = queries.impact(conn, sid, symbols=["function:app.routes.a.make_item@app/routes/a.py"])
    assert "tests/test_a.py::test_list_items" in by_sym["tests"] and {"GET /items", "POST /items"} <= set(by_sym["routes"])
    by_dir = queries.impact(conn, sid, paths=["app/routes/"])
    assert {"app/routes/a.py", "app/routes/b.py"} <= {s["path"] for s in by_dir["symbols"]}
    assert "GET /b/orphan" in by_dir["routes"]
    union = queries.impact(conn, sid, paths=["app/auth.py"], symbols=["app.main.health"])
    assert "GET /health" in union["routes"] and "POST /items" in union["routes"]       # create_item imports require_user
    assert all(isinstance(v, list) for v in union["owners"].values())


def test_impact_input_errors_and_empty_answers(graph):
    conn, sid = graph
    with pytest.raises(ValueError):
        queries.impact(conn, sid)
    with pytest.raises(LookupError):
        queries.impact(conn, sid, symbols=["nope.nope"])
    empty = queries.impact(conn, sid, paths=["does/not/exist.py"])
    assert empty["symbols"] == [] and empty["tests"] == [] and empty["routes"] == [] and empty["owners"] == {}


def test_impact_through_a_delta_snapshot(fakes, clean_conn, make_synthetic_repo, git_commit):
    path = make_synthetic_repo()
    sha1 = snapshot.resolve_sha(path)
    sha2 = git_commit(path, {"app/extra.py": "from app.models import Item\n\n\ndef fresh() -> Item:\n    return Item(name='x')\n"}, "extra")
    snapshot.index(clean_conn, path, sha1, name="synthetic")
    d = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    out = queries.impact(clean_conn, d["snapshot_id"], paths=["app/models.py"])
    assert "app.extra.fresh" in {s["qualname"] for s in out["symbols"]}
    assert "tests/test_a.py::test_list_items" in out["tests"]
    assert queries.search(clean_conn, d["snapshot_id"], "fresh")[0]["qualname"] == "app.extra.fresh"


# ---------------------------------------------------------------- owners


def set_rules(conn, rules):
    conn.execute("DELETE FROM snapshot WHERE repo = 'r'")
    sid = conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('r', %s, 'test') RETURNING id",
                       ("f" * 40,)).fetchone()["id"]
    for i, (glob, handles) in enumerate(rules):
        for h in handles or [""]:
            conn.execute("INSERT INTO owner (snapshot_id, repo, path_glob, handle, ord) VALUES (%s, 'r', %s, %s, %s)",
                         (sid, glob, h, i))


def test_owners_from_synthetic_codeowners(graph, synthetic_facts):
    conn, _ = graph
    got = queries.owners(conn, "synthetic", list(synthetic_facts["owners"]) + ["README.md"])
    assert got == {**synthetic_facts["owners"], "README.md": ["@team-core"]}
    assert queries.owners(conn, "unknown-repo", ["app/main.py"]) == {"app/main.py": []}
    assert queries.owners(conn, "synthetic", []) == {}


@pytest.mark.parametrize("rules,path,expected", [
    ([("*", ["@a"])], "deep/er/file.py", ["@a"]),
    ([("*.ts", ["@fe"])], "frontend/src/api.ts", ["@fe"]),
    ([("*.ts", ["@fe"])], "frontend/src/api.py", []),
    ([("/docs/", ["@d"])], "docs/guide/x.md", ["@d"]),
    ([("/docs/", ["@d"])], "src/docs/x.md", []),
    ([("docs/", ["@d"])], "src/docs/x.md", ["@d"]),                    # unanchored directory matches at any depth
    ([("/app/*.py", ["@p"])], "app/x.py", ["@p"]),
    ([("/app/*.py", ["@p"])], "app/sub/x.py", []),                     # * does not cross a slash
    ([("app/**/models.py", ["@m"])], "app/a/b/models.py", ["@m"]),
    ([("**/migrations/**", ["@db"])], "svc/migrations/0001.sql", ["@db"]),
    ([("/Makefile", ["@ops"])], "Makefile", ["@ops"]),
    ([("*", ["@a"]), ("/app/", ["@b", "@c"])], "app/x.py", ["@b", "@c"]),   # last match wins, several handles kept
    ([("/app/", ["@b"]), ("*", ["@a"])], "app/x.py", ["@a"]),
    ([("*", ["@a"]), ("/vendor/", [])], "vendor/x.py", []),                  # a rule with no handles un-owns
    ([("*", ["@a"])], "./app/x.py", ["@a"]),
])
def test_owner_glob_semantics(clean_conn, rules, path, expected):
    set_rules(clean_conn, rules)
    assert queries.owners(clean_conn, "r", [path]) == {path: expected}


# ---------------------------------------------------------------- search


def test_search_name_qualname_path_and_scores(graph):
    conn, sid = graph
    hits = queries.search(conn, sid, "list_items")
    assert hits[0]["qualname"] == "app.routes.a.list_items" and hits[0]["score"] == 1.0
    assert all(h["score"] <= hits[0]["score"] for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert "GET /items" in {h["qualname"] for h in queries.search(conn, sid, "items", kind="route")}
    assert {h["kind"] for h in queries.search(conn, sid, "items", kind="route")} == {"route"}
    by_path = queries.search(conn, sid, "routes/b")
    assert by_path[0]["path"] == "app/routes/b.py"                      # ILIKE on the path ranks first; fuzzier hits follow
    assert queries.search(conn, sid, "ITEM", kind="class")[0]["qualname"] == "app.models.Item"


def test_search_is_fuzzy_limited_and_safe_with_wildcards(graph):
    conn, sid = graph
    assert "app.routes.a.list_items" in {h["qualname"] for h in queries.search(conn, sid, "lst_items")}     # trigram
    assert len(queries.search(conn, sid, "app", limit=3)) == 3
    assert queries.search(conn, sid, "100%") == []                                                           # % is literal
    underscore = queries.search(conn, sid, "_", limit=100)                                                    # _ is not "any char"
    assert underscore and all("_" in h["name"] + h["qualname"] + h["path"] for h in underscore)
    assert queries.search(conn, sid, "zzzzqq") == []


def test_search_validates_input(graph):
    conn, sid = graph
    for bad in ({"text": "  "}, {"text": "x", "kind": "nope"}, {"text": "x", "limit": 0}):
        text = bad.pop("text")
        with pytest.raises(ValueError):
            queries.search(conn, sid, text, **bad)


# ---------------------------------------------------------------- structural checks, snapshots


def test_unmatched_fetches_and_unmounted_routers(graph, synthetic_facts):
    conn, sid = graph
    assert [s["qualname"] for s in queries.unmatched_fetches(conn, sid)] == ["GET /api/missing"]
    assert [s["qualname"] for s in queries.unmounted_routers(conn, sid)] == synthetic_facts["unmounted_router_modules"]
    fetches = conn.execute("SELECT count(*) AS n FROM sym_edge WHERE rel = 'fetches'").fetchone()["n"]
    assert fetches == 1


def test_snapshots_listing(graph):
    conn, sid = graph
    rows = queries.snapshots(conn, "synthetic")
    assert [r["id"] for r in rows] == [sid] and queries.snapshots(conn) == rows


# ---------------------------------------------------------------- CLI


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, out


def test_cli_index_delta_verify_and_queries(fakes, clean_conn, make_synthetic_repo, git_commit, capsys):
    path = make_synthetic_repo()
    sha1 = snapshot.resolve_sha(path)
    sha2 = git_commit(path, {"app/extra.py": "def fresh():\n    return 1\n"}, "extra")

    code, out = run_cli(capsys, "index", "--repo", str(path), "--sha", sha1, "--name", "synthetic", "--json")
    first = json.loads(out)
    assert code == 0 and first["created"] is True and first["storage"] == "full"
    code, out = run_cli(capsys, "delta", "--repo", str(path), "--from", sha1, "--to", sha2, "--name", "synthetic", "--json")
    d = json.loads(out)
    assert code == 0 and d["storage"] == "delta" and d["added"] >= 2

    code, out = run_cli(capsys, "verify-snapshot", "--repo", str(path), "--sha", sha2, "--name", "synthetic", "--json")
    assert code == 0 and json.loads(out)["ok"] is True

    code, out = run_cli(capsys, "query", "impact", "--paths", "app/routes/a.py", "--repo", "synthetic", "--sha", sha1[:8], "--json")
    assert code == 0 and "tests/test_a.py::test_list_items" in json.loads(out)["tests"]
    code, out = run_cli(capsys, "query", "deps", "app.routes.a.list_items", "--depth", "2", "--rel", "calls", "--json")
    assert code == 0 and "app.models.Item" in {n["qualname"] for n in json.loads(out)["nodes"]}
    code, out = run_cli(capsys, "query", "owners", "app/routes/a.py", "--repo", "synthetic", "--json")
    assert code == 0 and json.loads(out) == {"app/routes/a.py": ["@alice"]}
    code, out = run_cli(capsys, "query", "search", "fresh", "--kind", "function", "--json")
    assert code == 0 and json.loads(out)[0]["qualname"] == "app.extra.fresh"
    code, out = run_cli(capsys, "query", "snapshots", "--repo", "synthetic", "--json")
    assert code == 0 and len(json.loads(out)) == 2
    code, out = run_cli(capsys, "query", "snapshots", "--repo", "synthetic")           # table form
    assert code == 0 and "commit_sha" in out.splitlines()[0]


def test_cli_exit_codes(fakes, clean_conn, make_synthetic_repo, capsys):
    path = make_synthetic_repo()
    sha = snapshot.resolve_sha(path)
    code, out = run_cli(capsys, "verify-snapshot", "--repo", str(path), "--sha", sha, "--name", "synthetic", "--json")
    assert code == 4 and json.loads(out)["error"] == "not_found"                       # never indexed
    run_cli(capsys, "index", "--repo", str(path), "--name", "synthetic")
    clean_conn.execute("DELETE FROM sym WHERE kind = 'route' AND qualname = 'GET /health'")
    code, out = run_cli(capsys, "verify-snapshot", "--repo", str(path), "--name", "synthetic", "--json")
    assert code == 1 and json.loads(out)["ok"] is False                                  # drift: exit 1, result still printed
    code, out = run_cli(capsys, "index", "--repo", str(path), "--sha", "bogus", "--json")
    assert code == 2 and json.loads(out)["error"] == "ValueError"
    code, out = run_cli(capsys, "query", "deps", "no.such", "--json")
    assert code == 4
    code, out = run_cli(capsys, "query", "deps", "app.main", "--depth", "9", "--json")
    assert code == 2


def test_cli_on_empty_database_reports_not_found(clean_conn, capsys):
    code, out = run_cli(capsys, "query", "search", "x", "--json")
    assert code == 4 and "no snapshots" in json.loads(out)["message"]


# ---------------------------------------------------------------- real extractors (skip until E lands)


def test_real_extractors_impact_matches_synthetic_facts(clean_conn, synthetic_repo, synthetic_facts):
    try:
        out = snapshot.index(clean_conn, synthetic_repo, "HEAD", name="synthetic")
    except NotImplementedError:
        pytest.skip("real extractors are not implemented yet")
    got = queries.impact(clean_conn, out["snapshot_id"], paths=["app/routes/a.py"])
    assert "tests/test_a.py::test_list_items" in got["tests"]
    models = queries.impact(clean_conn, out["snapshot_id"], paths=["app/models.py"])
    assert set(synthetic_facts["impact"]["app/models.py"]["tests"]) <= set(models["tests"])
    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"]) == {"app/routes/a.py": ["@alice"]}
