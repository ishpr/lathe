"""Snapshot store: index, delta, reconstruct, materialise, verify. Database tests skip when Postgres is down.
The extractors are replaced by ``graph_fakes`` so these tests do not depend on E's state; the last
tests run the real extractors and skip while they are not implemented."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

import graph_fakes
from sdlc_graph.codegraph import snapshot
from sdlc_graph.shapes import key_str

SHA_RE = r"^[0-9a-f]{40}$"

SECOND_COMMIT = {
    "app/models.py": '''\
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# a comment that moves every line below it


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()

    def label(self) -> str:
        return f"item:{self.name}"
''',
    "app/extra.py": "from app.models import Item\n\n\ndef fresh() -> Item:\n    return Item(name='x')\n",
    "app/routes/b.py": None,
    "CODEOWNERS": "* @team-core\n/app/routes/ @carol\n",
}
THIRD_COMMIT = {
    "app/routes/a.py": '''\
from fastapi import APIRouter, Depends

from app.auth import require_user
from app.extra import fresh
from app.models import Item

router = APIRouter(prefix="/items")


def make_item(name: str) -> Item:
    return Item(name=name)


@router.get("")
def list_items():
    return [make_item("x").label(), fresh()]


@router.delete("/{item_id}")
def delete_item(item_id: int):
    return item_id
''',
}


@pytest.fixture
def fakes(monkeypatch):
    graph_fakes.install(monkeypatch)


@pytest.fixture
def repo(make_synthetic_repo, git_commit):
    path = make_synthetic_repo()
    shas = [snapshot.resolve_sha(path)]
    shas.append(git_commit(path, SECOND_COMMIT, "second"))
    shas.append(git_commit(path, THIRD_COMMIT, "third"))
    return path, shas


def fresh_extract(path: Path, sha: str):
    with tempfile.TemporaryDirectory() as tmp:
        return snapshot.extract_all(snapshot.export_tree(path, sha, Path(tmp) / "t"))


def test_index_writes_full_snapshot(fakes, clean_conn, repo):
    path, (sha1, _, _) = repo
    out = snapshot.index(clean_conn, path, sha1, name="synthetic")
    assert out["created"] is True and out["storage"] == "full" and out["repo"] == "synthetic"
    assert out["commit_sha"] == sha1 and out["parent_sha"] is None
    assert out["symbols"] > 20 and out["edges"] > 10 and out["owners"] == 3
    quals = {r["qualname"] for r in clean_conn.execute("SELECT qualname FROM sym WHERE kind = 'module'")}
    assert {"app.routes.a", "app.models", "tests.test_a"} <= quals
    n_edges = clean_conn.execute("SELECT count(*) AS n FROM sym_edge").fetchone()["n"]
    assert n_edges == out["edges"]


def test_index_is_idempotent_and_resolves_refs(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    a = snapshot.index(clean_conn, path, sha1, name="synthetic")
    b = snapshot.index(clean_conn, path, sha1, name="synthetic")
    assert b["created"] is False and b["snapshot_id"] == a["snapshot_id"]
    head = snapshot.index(clean_conn, path, name="synthetic")          # HEAD -> third commit
    assert head["commit_sha"] == repo[1][2] and head["parent_sha"] == sha2
    assert clean_conn.execute("SELECT count(*) AS n FROM sym WHERE snapshot_id = %s", (a["snapshot_id"],)).fetchone()["n"] == a["symbols"]
    with pytest.raises(ValueError):
        snapshot.index(clean_conn, path, "no-such-ref")
    with pytest.raises(FileNotFoundError):
        snapshot.index(clean_conn, path / "nope", "HEAD")


def test_index_never_writes_into_the_repo(fakes, clean_conn, repo):
    path, (sha1, _, _) = repo
    before = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--ignored"], capture_output=True, text=True).stdout
    snapshot.index(clean_conn, path, sha1, name="synthetic")
    snapshot.verify(clean_conn, path, sha1, name="synthetic")
    after = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--ignored"], capture_output=True, text=True).stdout
    assert before == after == ""


def test_delta_reconstruct_equals_fresh_extraction_across_two_commits(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    full = snapshot.index(clean_conn, path, sha1, name="synthetic")
    d = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    assert d["created"] is True and d["storage"] == "delta" and d["base_snapshot_id"] == full["snapshot_id"]
    assert snapshot.reconstruct(clean_conn, full["snapshot_id"]) == fresh_extract(path, sha1)
    assert snapshot.reconstruct(clean_conn, d["snapshot_id"]) == fresh_extract(path, sha2)
    # the delta really is a delta: no sym rows until something materialises it
    assert clean_conn.execute("SELECT count(*) AS n FROM sym WHERE snapshot_id = %s", (d["snapshot_id"],)).fetchone()["n"] == 0


def test_delta_counts_describe_the_change(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    d = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    rows = clean_conn.execute("SELECT op, table_name, row FROM sym_delta WHERE snapshot_id = %s", (d["snapshot_id"],)).fetchall()
    added = {key_str(r["row"]) for r in rows if r["table_name"] == "sym" and r["op"] == "add"}
    removed = {key_str(r["row"]) for r in rows if r["table_name"] == "sym" and r["op"] == "remove"}
    changed = {key_str(r["row"]) for r in rows if r["table_name"] == "sym" and r["op"] == "change"}
    assert "module:app.extra@app/extra.py" in added and "function:app.extra.fresh@app/extra.py" in added
    assert {"module:app.routes.b@app/routes/b.py", "route:GET /b/orphan@app/routes/b.py"} <= removed
    assert "class:app.models.Item@app/models.py" in changed            # line numbers moved
    assert "module:app.routes.a@app/routes/a.py" not in changed | added | removed
    assert (d["added"], d["removed"], d["changed"]) == (len(added), len(removed), len(changed))
    assert d["edges_added"] > 0 and d["edges_removed"] > 0
    # an edge to a removed symbol is removed with it, and edges are keyed by symbol key
    e_removed = [r["row"] for r in rows if r["table_name"] == "sym_edge" and r["op"] == "remove"]
    assert any(e["src"]["path"] == "app/routes/b.py" or e["dst"]["path"] == "app/routes/b.py" for e in e_removed)


def test_delta_chain_and_idempotence(fakes, clean_conn, repo):
    path, (sha1, sha2, sha3) = repo
    d2 = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")          # indexes sha1 itself
    d3 = snapshot.delta(clean_conn, path, sha2, sha3, name="synthetic")          # delta over a delta
    assert d3["base_snapshot_id"] == d2["snapshot_id"]
    assert snapshot.reconstruct(clean_conn, d3["snapshot_id"]) == fresh_extract(path, sha3)
    again = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    assert again["created"] is False and again["snapshot_id"] == d2["snapshot_id"]
    routes = {s["qualname"] for s in snapshot.reconstruct(clean_conn, d3["snapshot_id"])["symbols"] if s["kind"] == "route"}
    assert "DELETE /items/{item_id}" in routes and "GET /b/orphan" not in routes


def test_delta_of_identical_commits_is_empty(fakes, clean_conn, repo, git_commit):
    path, (sha1, _, _) = repo
    same = git_commit(path, {}, "empty commit")
    d = snapshot.delta(clean_conn, path, snapshot.resolve_sha(path, "HEAD~1"), same, name="synthetic")
    assert (d["added"], d["removed"], d["changed"], d["edges_added"], d["edges_removed"]) == (0, 0, 0, 0, 0)
    assert snapshot.reconstruct(clean_conn, d["snapshot_id"]) == fresh_extract(path, same)


def test_materialise_fills_rows_without_changing_storage(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    d = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    n = snapshot.materialise(clean_conn, d["snapshot_id"])
    assert n == len(fresh_extract(path, sha2)["symbols"])
    assert snapshot.materialise(clean_conn, d["snapshot_id"]) == n          # idempotent
    row = clean_conn.execute("SELECT storage FROM snapshot WHERE id = %s", (d["snapshot_id"],)).fetchone()
    assert row["storage"] == "delta"
    assert clean_conn.execute("SELECT count(*) AS n FROM sym_edge WHERE snapshot_id = %s", (d["snapshot_id"],)).fetchone()["n"] \
        == len(fresh_extract(path, sha2)["edges"])
    assert snapshot.reconstruct(clean_conn, d["snapshot_id"]) == fresh_extract(path, sha2)
    assert snapshot.verify(clean_conn, path, sha2, name="synthetic")["ok"]


def test_verify_snapshot_empty_diff(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    snapshot.index(clean_conn, path, sha1, name="synthetic")
    snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    for sha in (sha1, sha2):
        v = snapshot.verify(clean_conn, path, sha, name="synthetic")
        assert v["ok"] is True
        assert v["missing"] == v["extra"] == v["changed"] == v["edges_missing"] == v["edges_extra"] == []


def test_verify_reports_drift(fakes, clean_conn, repo):
    path, (sha1, _, _) = repo
    out = snapshot.index(clean_conn, path, sha1, name="synthetic")
    sid = out["snapshot_id"]
    clean_conn.execute("UPDATE sym SET line_start = 999 WHERE snapshot_id = %s AND qualname = 'app.routes.a.make_item'", (sid,))
    clean_conn.execute("DELETE FROM sym WHERE snapshot_id = %s AND kind = 'route' AND qualname = 'GET /health'", (sid,))
    clean_conn.execute("INSERT INTO sym (snapshot_id, kind, name, qualname, path) VALUES (%s, 'module', 'ghost', 'ghost', 'ghost.py')", (sid,))
    clean_conn.execute("DELETE FROM sym_edge WHERE snapshot_id = %s AND rel = 'imports' AND src = "
                       "(SELECT id FROM sym WHERE snapshot_id = %s AND qualname = 'tests.test_a' AND kind = 'module')", (sid, sid))
    v = snapshot.verify(clean_conn, path, sha1, name="synthetic")
    assert v["ok"] is False
    assert {"kind": "route", "qualname": "GET /health", "path": "app/main.py"} in v["missing"]
    assert {"kind": "module", "qualname": "ghost", "path": "ghost.py"} in v["extra"]
    assert {"kind": "function", "qualname": "app.routes.a.make_item", "path": "app/routes/a.py"} in v["changed"]
    assert any(e["src"]["qualname"] == "tests.test_a" and e["rel"] == "imports" for e in v["edges_missing"])


def test_verify_requires_an_indexed_sha(fakes, clean_conn, repo):
    path, (sha1, _, _) = repo
    with pytest.raises(LookupError):
        snapshot.verify(clean_conn, path, sha1, name="synthetic")


def test_failed_extraction_leaves_nothing_behind(fakes, clean_conn, repo, monkeypatch):
    path, (sha1, _, _) = repo

    def boom(root):
        raise RuntimeError("extractor crashed")

    monkeypatch.setattr(snapshot, "extract_all", boom)
    with pytest.raises(RuntimeError):
        snapshot.index(clean_conn, path, sha1, name="synthetic")
    assert clean_conn.execute("SELECT count(*) AS n FROM snapshot").fetchone()["n"] == 0


def test_owners_are_stored_in_rule_order_per_snapshot(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    a = snapshot.index(clean_conn, path, sha1, name="synthetic")
    rows = clean_conn.execute("SELECT path_glob, handle, ord FROM owner WHERE snapshot_id = %s ORDER BY ord",
                              (a["snapshot_id"],)).fetchall()
    assert [(r["path_glob"], r["handle"]) for r in rows] == [("*", "@team-core"), ("/app/routes/", "@alice"), ("/frontend/", "@bob")]
    assert [r["ord"] for r in rows] == sorted(r["ord"] for r in rows)
    b = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")      # CODEOWNERS changed in commit 2
    handles = {r["handle"] for r in clean_conn.execute("SELECT handle FROM owner WHERE snapshot_id = %s",
                                                       (b["snapshot_id"],))}
    assert handles == {"@team-core", "@carol"}
    # the first snapshot still answers with ITS commit's CODEOWNERS (the old table was rewritten per call)
    from sdlc_graph.codegraph import queries

    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"], snapshot_id=a["snapshot_id"]) == {"app/routes/a.py": ["@alice"]}
    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"], snapshot_id=b["snapshot_id"]) == {"app/routes/a.py": ["@carol"]}
    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"], sha=sha1) == {"app/routes/a.py": ["@alice"]}
    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"]) == {"app/routes/a.py": ["@carol"]}  # newest commit


def test_latest_is_the_newest_commit_not_the_last_indexed(fakes, clean_conn, repo):
    """Review finding: indexing an old commit after a new one made the old one 'latest', and owners()
    flipped with it."""
    from sdlc_graph.codegraph import queries
    from sdlc_graph.impact import affected

    path, (sha1, sha2, sha3) = repo
    new = snapshot.index(clean_conn, path, sha3, name="synthetic")
    snapshot.index(clean_conn, path, sha1, name="synthetic")                # an OLD commit, indexed last
    assert snapshot.resolve_snapshot(clean_conn, "synthetic") == new["snapshot_id"]
    assert affected.latest_snapshot_id(clean_conn, "synthetic") == new["snapshot_id"]
    assert snapshot.list_snapshots(clean_conn, "synthetic")[0]["commit_sha"] == sha3
    assert queries.owners(clean_conn, "synthetic", ["app/routes/a.py"]) == {"app/routes/a.py": ["@carol"]}


def test_verify_checks_codeowners(fakes, clean_conn, repo):
    path, (sha1, _, _) = repo
    a = snapshot.index(clean_conn, path, sha1, name="synthetic")
    assert snapshot.verify(clean_conn, path, sha1, name="synthetic")["ok"]
    clean_conn.execute("UPDATE owner SET handle = '@mallory' WHERE snapshot_id = %s AND path_glob = '*'", (a["snapshot_id"],))
    v = snapshot.verify(clean_conn, path, sha1, name="synthetic")
    assert not v["ok"] and v["owners_extra"] and v["owners_missing"]


def test_list_and_resolve_snapshot(fakes, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    a = snapshot.index(clean_conn, path, sha1, name="synthetic")
    b = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    rows = snapshot.list_snapshots(clean_conn, "synthetic")
    assert [r["id"] for r in rows] == [b["snapshot_id"], a["snapshot_id"]]
    assert rows[0]["storage"] == "delta" and rows[0]["base_snapshot_id"] == a["snapshot_id"]
    assert isinstance(rows[0]["indexed_at"], str) and rows[1]["extractor_version"] == snapshot.EXTRACTOR_VERSION
    assert snapshot.list_snapshots(clean_conn, "other") == []
    assert snapshot.resolve_snapshot(clean_conn) == b["snapshot_id"]
    assert snapshot.resolve_snapshot(clean_conn, "synthetic") == b["snapshot_id"]
    assert snapshot.resolve_snapshot(clean_conn, "synthetic", sha1[:10]) == a["snapshot_id"]
    with pytest.raises(LookupError):
        snapshot.resolve_snapshot(clean_conn, "synthetic", "0000000")
    with pytest.raises(LookupError):
        snapshot.resolve_snapshot(clean_conn, "unknown-repo")
    with pytest.raises(LookupError):
        snapshot.reconstruct(clean_conn, 10**9)


def test_resolve_snapshot_ambiguous_prefix(clean_conn):
    for sha in ("ab" + "0" * 38, "ab" + "1" * 38):
        clean_conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('r', %s, 'x')", (sha,))
    with pytest.raises(LookupError, match="ambiguous"):
        snapshot.resolve_snapshot(clean_conn, "r", "ab")
    assert snapshot.resolve_snapshot(clean_conn, "r", "ab1") > 0


def test_broken_delta_chain_is_an_error(clean_conn):
    base = clean_conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('r', 'a', 'x') RETURNING id").fetchone()["id"]
    d = clean_conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version, storage, base_snapshot_id) "
                           "VALUES ('r', 'b', 'x', 'delta', %s) RETURNING id", (base,)).fetchone()["id"]
    clean_conn.execute("INSERT INTO sym_delta (snapshot_id, op, table_name, row) VALUES (%s, 'change', 'sym', "
                       "'{\"kind\":\"module\",\"name\":\"x\",\"qualname\":\"x\",\"path\":\"x.py\"}')", (d,))
    with pytest.raises(ValueError, match="broken delta"):
        snapshot.reconstruct(clean_conn, d)


def test_diff_treats_an_edge_attrs_change_as_remove_plus_add():
    k = lambda q: {"kind": "function", "qualname": q, "path": "a.py"}          # noqa: E731
    sym = lambda q: {"kind": "function", "name": q, "qualname": q, "path": "a.py", "line_start": 1, "line_end": 1, "attrs": {}}   # noqa: E731
    edge = lambda attrs: {"src": k("a"), "dst": k("b"), "rel": "calls", "attrs": attrs}                                       # noqa: E731
    syms = {("function", q, "a.py"): sym(q) for q in "ab"}
    old = (syms, {(("function", "a", "a.py"), ("function", "b", "a.py"), "calls"): edge({})})
    new = (syms, {(("function", "a", "a.py"), ("function", "b", "a.py"), "calls"): edge({"confidence": "low"})})
    d = snapshot._diff(old, new)
    assert d["edge_add"] == [edge({"confidence": "low"})] and d["edge_remove"] == [edge({})]
    assert d["add"] == d["remove"] == d["change"] == []


# ---------------------------------------------------------------- git helpers and extract_all (no database)


def test_repo_name_from_origin_or_directory(make_synthetic_repo):
    path = make_synthetic_repo()
    assert snapshot.repo_name(path) == path.name
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True)
    assert snapshot.repo_name(path) == "acme/widgets"
    subprocess.run(["git", "-C", str(path), "remote", "set-url", "origin", "https://github.com/acme/other"], check=True)
    assert snapshot.repo_name(path) == "acme/other"


def test_resolve_sha_and_export_tree(make_synthetic_repo, tmp_path):
    path = make_synthetic_repo()
    sha = snapshot.resolve_sha(path)
    assert snapshot.resolve_sha(path, "main") == sha and len(sha) == 40
    with pytest.raises(ValueError):
        snapshot.resolve_sha(path, "nope")
    with pytest.raises(FileNotFoundError):
        snapshot.resolve_sha(tmp_path / "missing")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(FileNotFoundError):
        snapshot.resolve_sha(plain)
    out = snapshot.export_tree(path, sha, tmp_path / "out")
    assert (out / "app/routes/a.py").is_file() and not (out / ".git").exists()
    with pytest.raises(ValueError):
        snapshot.export_tree(path, "deadbeef" * 5, tmp_path / "bad")


def test_extract_all_rejects_dangling_edges_and_conflicting_symbols(monkeypatch, tmp_path):
    import sys
    import types

    def install(extract, link=lambda merged: []):
        mod = types.ModuleType("fake_bad")
        mod.extract, mod.link = extract, link
        monkeypatch.setitem(sys.modules, "fake_bad", mod)
        monkeypatch.setattr("sdlc_graph.codegraph.EXTRACTORS", ("fake_bad",))

    sym = lambda line: {"kind": "module", "name": "a", "qualname": "a", "path": "a.py", "line_start": line, "line_end": line, "attrs": {}}  # noqa: E731
    ghost = {"kind": "module", "qualname": "ghost", "path": "ghost.py"}
    a = {"kind": "module", "qualname": "a", "path": "a.py"}
    install(lambda root: {"symbols": [sym(1)], "edges": [{"src": a, "dst": ghost, "rel": "imports", "attrs": {}}]})
    with pytest.raises(ValueError, match="no extractor emitted"):
        snapshot.extract_all(tmp_path)
    install(lambda root: {"symbols": [sym(1)], "edges": []}, link=lambda merged: [{"src": a, "dst": ghost, "rel": "imports"}])
    with pytest.raises(ValueError, match="no extractor emitted"):
        snapshot.extract_all(tmp_path)
    install(lambda root: {"symbols": [sym(1), sym(2)], "edges": []})
    with pytest.raises(ValueError, match="different fields"):
        snapshot.extract_all(tmp_path)
    install(lambda root: {"symbols": [sym(1), sym(1)], "edges": []})
    assert len(snapshot.extract_all(tmp_path)["symbols"]) == 1


# ---------------------------------------------------------------- the real extractors (skip until E lands)


@pytest.fixture
def real_extractors():
    try:
        snapshot.extract_all(Path(tempfile.mkdtemp()))
    except NotImplementedError:
        pytest.skip("real extractors are not implemented yet")


def test_real_extractors_round_trip_on_synthetic_repo(real_extractors, clean_conn, repo):
    path, (sha1, sha2, _) = repo
    snapshot.index(clean_conn, path, sha1, name="synthetic")
    d = snapshot.delta(clean_conn, path, sha1, sha2, name="synthetic")
    assert snapshot.reconstruct(clean_conn, d["snapshot_id"]) == fresh_extract(path, sha2)
    assert snapshot.verify(clean_conn, path, sha1, name="synthetic")["ok"]
    assert snapshot.verify(clean_conn, path, sha2, name="synthetic")["ok"]


def test_real_extractors_on_the_sample_app(real_extractors, clean_conn, sample_app):
    out = snapshot.index(clean_conn, sample_app, "HEAD", name="sample-app")
    assert out["symbols"] > 40
    assert snapshot.verify(clean_conn, sample_app, "HEAD", name="sample-app")["ok"]


def test_real_extractors_on_sample_repo(real_extractors, clean_conn, sample_repo):
    """SAMPLE_REPO (optional): any checkout indexes and verifies."""
    out = snapshot.index(clean_conn, sample_repo, "HEAD", name="sample-repo")
    assert out["symbols"] > 0
    assert snapshot.verify(clean_conn, sample_repo, "HEAD", name="sample-repo")["ok"]


def test_committed_symlinks_are_not_followed_out_of_the_tree(tmp_path):
    """Review finding: a repo committing requirements.txt -> a secret file, or app/leak.py -> an
    outside module, had the extractors read that file into the graph."""
    import os

    from sdlc_graph.codegraph import snapshot as snap
    from graph_fakes import graph_conftest

    _git, commit_files = graph_conftest()._git, graph_conftest().commit_files  # noqa: F841

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("import os\nOUTSIDE = os.environ.get('OUTSIDE_FIXTURE_VAR', 'default-FIXTURE-VALUE')\n")
    (outside / "fake.env").write_text("API_TOKEN=tok-FIXTURE-123\n")
    repo = tmp_path / "evil"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    commit_files(repo, {"app/__init__.py": "", "README.md": "x\n"}, "base")
    os.symlink(outside / "secret.py", repo / "app" / "leak.py")
    os.symlink(outside / "fake.env", repo / "requirements.txt")
    os.symlink(outside, repo / "app" / "linkdir")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "evil")
    sha = _git(repo, "rev-parse", "HEAD")
    dest = tmp_path / "tree"
    snap.export_tree(repo, sha, dest)
    assert not any(p.is_symlink() for p in dest.rglob("*"))
    result = snap.extract_all(dest)
    blob = json.dumps(result)
    for needle in ("OUTSIDE_FIXTURE_VAR", "default-FIXTURE-VALUE", "tok-FIXTURE-123", "API_TOKEN"):
        assert needle not in blob, needle
    assert snap.drop_symlinks(dest) == []
