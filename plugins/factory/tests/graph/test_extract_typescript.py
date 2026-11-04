"""TypeScript extractor over the fixture app (tests/fixtures/ts-app): symbols, edges, configuration, the snapshot
pipeline (index, delta, verify) through the database, the query commands and graph_impact on a command file.

The fixture holds two Foldkit programs: ``runs`` (a commands.ts file, a story and a scene test, one message tag no test
sends) and ``nodes`` (a command.ts file in the template's shape, one command calling an endpoint the api table does not
export, no scene test), an api table, a route union, helpers and a Next.js page.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import commit_files, _git
from sdlc_graph import mcp_server
from sdlc_graph.codegraph import appscan, extract_frontend, extract_typescript, queries, snapshot
from sdlc_graph.impact import affected
from sdlc_graph.shapes import SYM_KINDS, SYM_RELS

FIXTURE = Path(__file__).parent / "fixtures" / "ts-app"
RUNS, NODES = "src/programs/runs", "src/programs/nodes"
API = "src/lib/api/endpoints.ts"


@pytest.fixture(scope="module")
def result():
    return extract_typescript.extract(FIXTURE)


def syms(result, kind):
    return {s["qualname"]: s for s in result["symbols"] if s["kind"] == kind}


def edges(result, rel):
    return {(e["src"]["kind"], e["src"]["qualname"], e["dst"]["kind"], e["dst"]["qualname"]) for e in result["edges"] if e["rel"] == rel}


def test_kinds_and_rels_are_registered():
    assert {"message", "command", "view", "route_fe", "submodel"} <= set(SYM_KINDS)
    assert {"handles", "calls_api", "emits", "renders", "routes_to"} <= set(SYM_RELS)


def test_symbols_by_kind(result):
    kinds = {}
    for s in result["symbols"]:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    assert kinds == {"module": 19, "function": 21, "message": 7, "command": 4, "view": 2, "route_fe": 3, "submodel": 1, "test": 3}

    modules = syms(result, "module")
    assert modules[f"{RUNS}/update.ts"]["attrs"] == {"lang": "ts", "external_imports": ["foldkit"]}
    assert f"{RUNS}/runs.story.test.ts" in modules and "src/app/runs/page.tsx" in modules

    fns = syms(result, "function")
    assert fns[f"{API}:runsUrl"]["attrs"] == {"exported": True, "kind": "function"}
    assert fns[f"{API}:nodesUrl"]["attrs"] == {"exported": True, "kind": "function"}          # a function declaration
    assert fns[f"{API}:API_ROOT"]["attrs"] == {"exported": True, "kind": "const"}
    assert fns["src/lib/format.ts:internalOnly"]["attrs"]["exported"] is False
    assert fns[f"{RUNS}/update.ts:update"]["attrs"]["program"] == "runs"
    assert fns["src/app/runs/page.tsx:RunsPage"]["attrs"]["exported"] is True
    assert fns[f"{RUNS}/view.ts:rowLabel"]["attrs"]["exported"] is False                      # not a view: not exported
    assert fns[f"{API}:runsUrl"]["line_start"] == 4 and fns[f"{API}:nodesUrl"]["line_end"] == 10

    msgs = syms(result, "message")
    assert set(msgs) == {f"{RUNS}/message.ts:{t}" for t in ("ClickedRefresh", "ClickedSelect", "SucceededFetchRuns", "FailedFetchRuns")} | {
        f"{NODES}/message.ts:{t}" for t in ("ClickedDelete", "SucceededLoadNodes", "SucceededDeleteNode")}
    sel = msgs[f"{RUNS}/message.ts:ClickedSelect"]
    assert sel["attrs"] == {"union": "Message", "fields": ["id"], "exported": True, "program": "runs"} and sel["line_start"] == 6

    cmds = syms(result, "command")
    fetch_runs = cmds[f"{RUNS}/commands.ts:FetchRuns"]["attrs"]
    assert fetch_runs["command"] == "FetchRuns" and fetch_runs["endpoints"] == ["getJson", "runsUrl"] and fetch_runs["args"] == []
    assert fetch_runs["messages"] == ["Message.SucceededFetchRuns", "Message.FailedFetchRuns"]
    delete = cmds[f"{NODES}/command.ts:DeleteNode"]["attrs"]
    assert delete["args"] == ["id"] and delete["missing_endpoints"] == ["deleteNodeUrl"] and delete["endpoints"] == []
    assert "missing_endpoints" not in cmds[f"{NODES}/command.ts:LoadNodes"]["attrs"]

    views = syms(result, "view")
    assert set(views) == {f"{RUNS}/view.ts:view", f"{NODES}/view.ts:nodeList"}                  # arrow with Document, function with Html

    routes = syms(result, "route_fe")
    assert set(routes) == {f"src/lib/routes.ts:{t}" for t in ("Runs", "RunDetail", "NotFound")}
    assert routes["src/lib/routes.ts:RunDetail"]["attrs"]["fields"] == ["id"]

    tests = syms(result, "test")
    assert {q: t["attrs"]["runner"] for q, t in tests.items()} == {
        f"{RUNS}/runs.story.test.ts": "story", f"{RUNS}/runs.scene.test.ts": "scene", f"{NODES}/nodes.story.test.ts": "story"}
    assert tests[f"{RUNS}/runs.story.test.ts"]["attrs"]["nodeid"] == f"{RUNS}/runs.story.test.ts"


def test_defines_edges_cover_every_symbol(result):
    defined = {(e["dst"]["kind"], e["dst"]["qualname"]) for e in result["edges"] if e["rel"] == "defines"}
    for s in result["symbols"]:
        if s["kind"] != "module":
            assert (s["kind"], s["qualname"]) in defined, s["qualname"]
    for e in result["edges"]:
        if e["rel"] == "defines":
            assert e["src"] == {"kind": "module", "qualname": e["dst"]["path"], "path": e["dst"]["path"]}


def test_handles_edges_link_update_to_the_tags_it_names(result):
    h = edges(result, "handles")
    assert {(s, d) for _k, s, _dk, d in h if s.startswith(RUNS)} == {
        (f"{RUNS}/update.ts", f"{RUNS}/message.ts:{t}") for t in ("ClickedRefresh", "ClickedSelect", "SucceededFetchRuns", "FailedFetchRuns")}
    assert len(h) == 7 and all(k == "module" and dk == "message" for k, _s, dk, _d in h)


def test_calls_api_links_commands_to_endpoint_functions_and_leaves_the_missing_one_out(result):
    c = edges(result, "calls_api")
    assert c == {
        ("command", f"{RUNS}/commands.ts:FetchRuns", "function", f"{API}:getJson"),
        ("command", f"{RUNS}/commands.ts:FetchRuns", "function", f"{API}:runsUrl"),
        ("command", f"{NODES}/command.ts:LoadNodes", "function", f"{API}:nodesUrl"),
        ("command", f"{NODES}/command.ts:RefreshRuns", "function", f"{API}:getJson"),       # through `import * as Api`
        ("command", f"{NODES}/command.ts:RefreshRuns", "function", f"{API}:runUrl"),
    }
    assert not any(src.endswith(":DeleteNode") for _k, src, _dk, _d in c)


def test_emits_renders_and_routes_to_edges(result):
    assert set(syms(result, "submodel")) == {f"{NODES}/submodel.ts:NodeRow"}
    assert edges(result, "emits") == {
        ("command", f"{RUNS}/commands.ts:FetchRuns", "message", f"{RUNS}/message.ts:{t}") for t in ("SucceededFetchRuns", "FailedFetchRuns")
    } | {("command", f"{NODES}/command.ts:{c}", "message", f"{NODES}/message.ts:{t}") for c, t in (
        ("LoadNodes", "SucceededLoadNodes"), ("RefreshRuns", "SucceededLoadNodes"), ("DeleteNode", "SucceededDeleteNode"))}
    assert edges(result, "renders") == {("view", f"{NODES}/view.ts:nodeList", "submodel", f"{NODES}/submodel.ts:NodeRow")}
    assert edges(result, "routes_to") == {                       # NotFound names no view; `view as runsView` is followed
        ("route_fe", "src/lib/routes.ts:Runs", "view", f"{RUNS}/view.ts:view"),
        ("route_fe", "src/lib/routes.ts:RunDetail", "view", f"{NODES}/view.ts:nodeList")}
    fetch = syms(result, "command")[f"{RUNS}/commands.ts:FetchRuns"]["attrs"]
    assert fetch["emits"] == ["FailedFetchRuns", "SucceededFetchRuns"]


def test_tests_edges_story_to_tags_and_scene_to_views(result):
    t = edges(result, "tests")
    assert t == {
        ("test", f"{RUNS}/runs.story.test.ts", "message", f"{RUNS}/message.ts:{tag}") for tag in ("ClickedSelect", "SucceededFetchRuns", "FailedFetchRuns")
    } | {("test", f"{RUNS}/runs.scene.test.ts", "view", f"{RUNS}/view.ts:view")} | {
        ("test", f"{NODES}/nodes.story.test.ts", "message", f"{NODES}/message.ts:{tag}")                # one sent as { _tag: 'SucceededDeleteNode' }
        for tag in ("ClickedDelete", "SucceededDeleteNode", "SucceededLoadNodes")}
    tested = {d for _k, _s, _dk, d in t}
    untested = set(syms(result, "message")) - tested
    assert untested == {f"{RUNS}/message.ts:ClickedRefresh"}                                          # no story test sends it


def test_imports_edges_resolve_relative_and_alias_specifiers(result):
    i = edges(result, "imports")
    assert ("module", f"{RUNS}/commands.ts", "module", API) in i                                      # "@/lib/api/endpoints" from tsconfig paths
    assert ("module", f"{RUNS}/update.ts", "module", f"{RUNS}/commands.ts") in i
    assert ("test", f"{RUNS}/runs.scene.test.ts", "module", f"{RUNS}/view.ts") in i
    assert not any(s == f"{RUNS}/runs.scene.test.ts" and k == "module" for k, s, _dk, _d in i)     # a test's imports hang off its test symbol
    assert all(dk == "module" for _k, _s, dk, _d in i)


def test_extraction_is_deterministic_and_comments_do_not_define_symbols(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    (root / "src" / "lib" / "commented.ts").write_text("// export const Ghost = 1\n/* export function alsoGhost() {} */\nexport const real = 1\n")
    a, b = extract_typescript.extract(root), extract_typescript.extract(root)
    assert a == b
    names = {s["name"] for s in a["symbols"] if s["path"] == "src/lib/commented.ts"}
    assert names == {"commented.ts", "real"}


def test_node_modules_next_and_dist_are_not_scanned(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    for d in ("node_modules/pkg", ".next/server", "dist"):
        (root / "src" / d).mkdir(parents=True)
        (root / "src" / d / "x.ts").write_text("export const hidden = 1\nfetch('/api/hidden')\n")
    out = extract_typescript.extract(root)
    assert not any("hidden" in s["name"] or "/node_modules/" in s["path"] or "/.next/" in s["path"] or "/dist/" in s["path"]
                   for s in out["symbols"])
    assert not any(s["kind"] == "frontend_call" and "hidden" in s["qualname"] for s in extract_frontend.extract(root)["symbols"])


def test_without_sdlc_json_the_default_paths_are_scanned_and_programs_have_no_program_attr(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    (root / ".claude" / "sdlc.json").unlink()
    (root / "other").mkdir()
    (root / "other" / "ignored.ts").write_text("export const ignored = 1\n")
    out = extract_typescript.extract(root)
    assert "other/ignored.ts" not in syms(out, "module")
    assert len(syms(out, "module")) == 19 and len(syms(out, "command")) == 4
    assert all("program" not in s["attrs"] for s in out["symbols"])
    assert edges(out, "calls_api") == edges(extract_typescript.extract(FIXTURE), "calls_api")      # the default api table path
    assert appscan.load_config(root).app == list(appscan.DEFAULT_APP_GLOBS)


def test_configured_app_paths_limit_the_scan(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    cfg["paths"]["app"] = ["src/programs/**"]
    (root / ".claude" / "sdlc.json").write_text(json.dumps(cfg))
    out = extract_typescript.extract(root)
    assert "src/lib/format.ts" not in syms(out, "module") and f"{RUNS}/update.ts" in syms(out, "module")
    assert API in syms(out, "module")                                  # the api table is always scanned: commands resolve against it
    assert len(edges(out, "calls_api")) == 5


def test_extract_frontend_scans_the_configured_app_paths_for_fetch_calls():
    out = extract_frontend.extract(FIXTURE)
    calls = {s["qualname"]: s["path"] for s in out["symbols"] if s["kind"] == "frontend_call"}
    assert calls == {"GET /api/runs": "src/app/runs/page.tsx"}
    assert ("module", "src/app/runs/page.tsx") in {(s["kind"], s["path"]) for s in out["symbols"]}
    # the module symbol is the same dict the TypeScript extractor emits, so the two merge without a conflict
    ts_mod = syms(extract_typescript.extract(FIXTURE), "module")["src/app/runs/page.tsx"]
    assert ts_mod == next(s for s in out["symbols"] if s["kind"] == "module" and s["path"] == "src/app/runs/page.tsx")


def test_extract_all_merges_every_extractor():
    merged = snapshot.extract_all(FIXTURE)
    kinds = {s["kind"] for s in merged["symbols"]}
    assert {"module", "function", "message", "command", "view", "route_fe", "test", "frontend_call"} <= kinds
    assert {e["rel"] for e in merged["edges"]} >= {"defines", "imports", "handles", "calls_api", "tests"}


# --------------------------------------------------------------------------- the snapshot pipeline (database)


@pytest.fixture
def app_repo(tmp_path):
    repo = tmp_path / "ts-app"
    shutil.copytree(FIXTURE, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ts app")
    return repo


def test_snapshot_round_trip_index_delta_and_verify(clean_conn, app_repo):
    out = snapshot.index(clean_conn, app_repo, name="ts-app")
    first = _git(app_repo, "rev-parse", "HEAD")
    assert out["created"] and (out["symbols"], out["edges"]) == (61, 97)
    counts = {r["kind"]: r["n"] for r in clean_conn.execute(
        "SELECT kind, count(*) AS n FROM sym WHERE snapshot_id = %s GROUP BY kind", (out["snapshot_id"],))}
    assert counts["message"] == 7 and counts["command"] == 4 and counts["view"] == 2 and counts["route_fe"] == 3 and counts["test"] == 3
    rels = {r["rel"]: r["n"] for r in clean_conn.execute(
        "SELECT rel, count(*) AS n FROM sym_edge WHERE snapshot_id = %s GROUP BY rel", (out["snapshot_id"],))}
    assert rels["calls_api"] == 5 and rels["handles"] == 7 and rels["tests"] == 7
    assert snapshot.verify(clean_conn, app_repo, first, name="ts-app")["ok"] is True

    # a second commit: a new message tag, a new endpoint and a command that calls it, stored as a delta
    second = commit_files(app_repo, {
        f"{NODES}/message.ts": (app_repo / NODES / "message.ts").read_text().replace(
            "SucceededDeleteNode: {},", "SucceededDeleteNode: {},\n  ClickedReload: {},"),
        API: (app_repo / API).read_text() + "\nexport const deleteNodeUrl = (id: string): string => `${API_ROOT}/nodes/${id}`\n",
    }, "add reload and deleteNodeUrl")
    d = snapshot.delta(clean_conn, app_repo, first, second, name="ts-app")
    assert d["storage"] == "delta"
    rec = snapshot.reconstruct(clean_conn, d["snapshot_id"])
    assert f"{NODES}/message.ts:ClickedReload" in syms(rec, "message")
    assert ("command", f"{NODES}/command.ts:DeleteNode", "function", f"{API}:deleteNodeUrl") in edges(rec, "calls_api")   # the missing endpoint now resolves
    assert "missing_endpoints" not in syms(rec, "command")[f"{NODES}/command.ts:DeleteNode"]["attrs"]
    assert snapshot.verify(clean_conn, app_repo, second, name="ts-app")["ok"] is True
    # the query commands work on the delta snapshot too (it is materialised on first use)
    hit = queries.search(clean_conn, d["snapshot_id"], "ClickedReload", kind="message")
    assert hit[0]["qualname"] == f"{NODES}/message.ts:ClickedReload"


def test_search_and_deps_work_over_typescript_symbols(clean_conn, app_repo):
    snap = snapshot.index(clean_conn, app_repo, name="ts-app")["snapshot_id"]
    hits = queries.search(clean_conn, snap, "FetchRuns")
    assert {(h["kind"], h["qualname"]) for h in hits} >= {("command", f"{RUNS}/commands.ts:FetchRuns"),
                                                          ("message", f"{RUNS}/message.ts:SucceededFetchRuns")}
    assert [h["qualname"] for h in queries.search(clean_conn, snap, "nodeList", kind="view")] == [f"{NODES}/view.ts:nodeList"]
    out = queries.deps(clean_conn, snap, f"command:{RUNS}/commands.ts:FetchRuns@{RUNS}/commands.ts", direction="out", rels=["calls_api"])
    assert {n["qualname"] for n in out["nodes"]} == {f"{API}:runsUrl", f"{API}:getJson"}
    back = queries.deps(clean_conn, snap, f"function:{API}:runsUrl@{API}", direction="in", depth=2, rels=["calls_api", "defines"])
    assert f"{RUNS}/commands.ts:FetchRuns" in {n["qualname"] for n in back["nodes"]}
    tag = f"message:{RUNS}/message.ts:ClickedRefresh@{RUNS}/message.ts"
    users = queries.deps(clean_conn, snap, tag, direction="in", rels=["handles", "tests"])
    assert {n["qualname"] for n in users["nodes"]} == {f"{RUNS}/update.ts"}                           # handled, and no story test sends it
    assert queries.unmatched_fetches(clean_conn, snap)[0]["qualname"] == "GET /api/runs"              # no python route in this repo
    with pytest.raises(ValueError):
        queries.search(clean_conn, snap, "x", kind="nonsense")


def test_impact_of_a_changed_command_file_names_views_and_tests(clean_conn, app_repo):
    snap = snapshot.index(clean_conn, app_repo, name="ts-app")["snapshot_id"]
    res = affected.affected(clean_conn, snap, paths=[f"{RUNS}/commands.ts"])
    by_kind = {}
    for s in res["symbols"]:
        by_kind.setdefault(s["kind"], set()).add(s["qualname"])
    assert by_kind["view"] == {f"{RUNS}/view.ts:view"}                                                 # the program's view
    assert by_kind["command"] == {f"{RUNS}/commands.ts:FetchRuns"}
    assert f"{RUNS}/update.ts" in by_kind["module"]                                                    # imports commands.ts
    assert set(res["tests"]) == {f"{RUNS}/runs.story.test.ts", f"{RUNS}/runs.scene.test.ts"}
    assert f"{NODES}/nodes.story.test.ts" not in res["tests"] and f"{NODES}/view.ts:nodeList" not in by_kind["view"]
    # the story test is reached through update.ts (a direct importer); the scene test through the program's view
    assert res["order"][0] == f"{RUNS}/runs.story.test.ts" or res["order"][0] == f"{RUNS}/runs.scene.test.ts"
    assert "reverse walk" in res["reasons"][f"{RUNS}/runs.story.test.ts"]

    # a changed endpoint reaches every command that calls it, their programs' views and tests
    ep = affected.affected(clean_conn, snap, paths=[API])
    assert {s["qualname"] for s in ep["symbols"] if s["kind"] == "view"} == {f"{RUNS}/view.ts:view", f"{NODES}/view.ts:nodeList"}
    assert set(ep["tests"]) == {f"{RUNS}/runs.story.test.ts", f"{RUNS}/runs.scene.test.ts", f"{NODES}/nodes.story.test.ts"}

    # a changed Message file reaches its update module and the tests of the tags
    msg = affected.affected(clean_conn, snap, symbols=[f"message:{RUNS}/message.ts:ClickedSelect@{RUNS}/message.ts"])
    assert f"{RUNS}/update.ts" in {s["qualname"] for s in msg["symbols"]} and f"{RUNS}/runs.story.test.ts" in msg["tests"]


def test_impact_reaches_the_scene_and_route_through_the_new_edges(clean_conn, app_repo):
    snap = snapshot.index(clean_conn, app_repo, name="ts-app")["snapshot_id"]
    # a changed Message tag reaches the command that emits it (emits is walked in reverse)
    msg = affected.affected(clean_conn, snap, symbols=[f"message:{NODES}/message.ts:SucceededDeleteNode@{NODES}/message.ts"])
    assert f"{NODES}/command.ts:DeleteNode" in {s["qualname"] for s in msg["symbols"]}
    # a changed submodel reaches the view that renders it, then the route that routes to the view
    sub = affected.affected(clean_conn, snap, paths=[f"{NODES}/submodel.ts"])
    q = {s["qualname"] for s in sub["symbols"]}
    assert {f"{NODES}/view.ts:nodeList", "src/lib/routes.ts:RunDetail"} <= q and "src/lib/routes.ts:Runs" not in q
    # a changed runs view reaches its scene test and its route
    view = affected.affected(clean_conn, snap, symbols=[f"view:{RUNS}/view.ts:view@{RUNS}/view.ts"])
    assert f"{RUNS}/runs.scene.test.ts" in view["tests"] and "src/lib/routes.ts:Runs" in {s["qualname"] for s in view["symbols"]}


def test_graph_impact_tool_reports_the_same_answer(clean_conn, conn_factory, app_repo):
    snapshot.index(clean_conn, app_repo, name="ts-app")
    from test_mcp import _call
    server = mcp_server.build_server(conn_factory)
    is_error, payload = _call(server, "graph_impact", {"paths": [f"{RUNS}/commands.ts"], "repo": "ts-app"})
    assert not is_error and f"{RUNS}/runs.story.test.ts" in payload["tests"]
    assert any(s["kind"] == "view" for s in payload["symbols"])


def test_migrations_008_and_011_widen_the_check_constraints_of_an_older_schema(test_database_url):
    import secrets

    from sdlc_graph import db

    name = f"test_{secrets.token_hex(4)}"
    try:
        db.init(name)
    except Exception as exc:  # no database: the other tests skip too
        pytest.skip(f"Postgres unavailable ({type(exc).__name__})")
    try:
        with db.connect(schema=name) as conn:
            # put the constraints back as 001 had them before the TypeScript kinds
            conn.execute("ALTER TABLE sym DROP CONSTRAINT sym_kind_check")
            conn.execute("ALTER TABLE sym ADD CONSTRAINT sym_kind_check CHECK (kind IN ('module','class','function','method',"
                         "'route','model','env_var','package','frontend_call','test'))")
            conn.execute("ALTER TABLE sym_edge DROP CONSTRAINT sym_edge_rel_check")
            conn.execute("ALTER TABLE sym_edge ADD CONSTRAINT sym_edge_rel_check CHECK (rel IN ('imports','calls','defines',"
                         "'exposes_route','includes_router','reads_env','uses_model','depends_on_package','fetches','tests'))")
            conn.commit()
            sid = conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('r', 'a', '0') RETURNING id").fetchone()["id"]
            with pytest.raises(Exception):
                conn.execute("INSERT INTO sym (snapshot_id, kind, name, qualname, path) VALUES (%s, 'command', 'c', 'p:c', 'p')", (sid,))
            conn.rollback()
        db.init(name)                                                       # what `sdlc-graph db init` does on upgrade
        with db.connect(schema=name) as conn:
            sid = conn.execute("SELECT id FROM snapshot").fetchone()["id"]
            for kind in ("message", "command", "view", "route_fe", "submodel"):
                conn.execute("INSERT INTO sym (snapshot_id, kind, name, qualname, path) VALUES (%s, %s, 'x', %s, 'p')", (sid, kind, f"p:{kind}"))
            ids = [r["id"] for r in conn.execute("SELECT id FROM sym WHERE snapshot_id = %s ORDER BY id", (sid,))]
            for rel in ("handles", "calls_api", "emits", "renders", "routes_to"):
                conn.execute("INSERT INTO sym_edge (snapshot_id, src, dst, rel) VALUES (%s, %s, %s, %s)", (sid, ids[0], ids[1], rel))
            conn.commit()
    finally:
        db.drop_schema(name)
