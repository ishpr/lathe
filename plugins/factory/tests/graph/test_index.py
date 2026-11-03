"""Embeddings layer: chunking, providers, RRF, build and hybrid search, with a deterministic fake
provider (no network). The real-provider test is skipped unless an OpenAI or Voyage key is present.
Database tests skip when Postgres is down."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import urllib.error

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

import graph_fakes
from sdlc_graph import cli, mcp_server
from sdlc_graph.codegraph import snapshot
from sdlc_graph.index import chunk, embed, search
from sdlc_graph.ledger import decisions, nodes


class FakeProvider(embed.Provider):
    """Bag-of-words hashing into 1024 dims, L2-normalised: texts that share words are close."""

    name = "fake"
    model = "fake:bow:1024"
    batch_size = 3  # small, so batching is exercised

    def __init__(self, model: str | None = None) -> None:
        if model:
            self.model = model
        self.calls: list[tuple[int, bool]] = []

    def embed(self, texts, *, query=False):
        self.calls.append((len(texts), query))
        out = []
        for t in texts:
            v = [0.0] * embed.DIMENSIONS
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % embed.DIMENSIONS] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    @property
    def embedded(self) -> int:
        return sum(n for n, _ in self.calls)


@pytest.fixture
def fakes(monkeypatch):
    graph_fakes.install(monkeypatch)


@pytest.fixture
def no_keys(monkeypatch, tmp_path):
    """No embedding key anywhere: not in the environment, not in the .env file."""
    for name in ("OPENAI_API_KEY", "VOYAGE_API_KEY", "EMBED_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(embed, "env_file", lambda: tmp_path / "absent.env")


def _make_nodes(conn):
    nodes.create(conn, kind="epic", slug="epic-billing", title="Usage based billing", repo="demo", created_by="@a",
                 attrs={"riskiest_assumption": "Customers accept metered invoices every month",
                        "guardrail_metric": "refund rate stays under two percent", "intake": {"flags": ["ignored"]}})
    nodes.create(conn, kind="rule", slug="rule-auth", title="Routes need authentication", repo="demo", created_by="@a",
                 path="app/routes/**")
    nodes.create(conn, kind="feature", slug="feat-ratelimit", title="Rate limit the login endpoint", repo="demo",
                 created_by="@a", touched_symbols=["app/auth/login.py"],
                 acceptance=[{"id": "AC1", "text": "Six failed logins in a minute return 429", "test": "tests/t.py::t"}])
    decisions.propose(conn, "feat-ratelimit", "Which store holds the counters?", ["redis", "postgres"], actor="@a",
                      recommendation="redis")


@pytest.fixture
def ledger(clean_conn):
    _make_nodes(clean_conn)
    return clean_conn


@pytest.fixture
def graph(fakes, ledger, synthetic_repo):
    out = snapshot.index(ledger, synthetic_repo, "HEAD", name="demo")
    return ledger, out["snapshot_id"]


# --------------------------------------------------------------------------- pure: RRF, query, summaries


def test_rrf_scores_and_orders():
    scores = search.rrf([["a", "b", "c"], ["b", "a", "d"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62) and scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63) and scores["d"] == pytest.approx(1 / 63)
    # agreement between the rankings beats first place in only one of them, and rank still orders the rest
    fused = search.rrf([["x", "q", "p"], ["y", "q", "r"]], k=60)
    assert fused["q"] > fused["x"] == fused["y"] > fused["p"] == fused["r"] > 0
    assert search.rrf([[], []]) == {}
    assert search.rrf([["a", "a"]], k=1) == {"a": 0.5}  # a repeated item counts once, at its best rank


def test_or_query_keeps_only_words():
    assert search.or_query("app/auth/login.py: rate-limit!") == "app | auth | login | py | rate | limit"
    assert search.or_query("'; drop table x --") == "drop | table"
    assert search.or_query("& | ! ()") == ""


def test_symbol_summary_uses_kind_path_and_attrs():
    text = chunk.symbol_summary({"kind": "route", "qualname": "app.routes.items.list_items", "path": "app/routes/items.py",
                                 "attrs": {"method": "GET", "path": "/items", "auth": True, "confidence": "high",
                                           "nested": {"x": [1, 2]}}})
    assert text.startswith("route app.routes.items.list_items")
    assert "route GET /items" in text and "file app/routes/items.py" in text and "auth: yes" in text
    assert "confidence" not in text
    assert "confidence: low" in chunk.symbol_summary({"kind": "function", "qualname": "m.f", "path": "m.py",
                                                      "attrs": {"confidence": "low"}})


# --------------------------------------------------------------------------- providers


def test_provider_selection(tmp_path):
    absent = tmp_path / "none.env"
    assert embed.get_provider(env={}, path=absent).name == "none"
    p = embed.get_provider(env={"OPENAI_API_KEY": "sk-test"}, path=absent)
    assert p.name == "openai" and p.model == "openai:text-embedding-3-small:1024"
    assert embed.get_provider(env={"OPENAI_API_KEY": "sk-test", "EMBED_PROVIDER": "none"}, path=absent).name == "none"
    assert embed.get_provider("voyage", env={}, path=absent).name == "none"  # named, but no key
    v = embed.get_provider(env={"EMBED_PROVIDER": "voyage", "VOYAGE_API_KEY": "pa-test"}, path=absent)
    assert v.name == "voyage" and v.model == "voyage:voyage-4-lite:1024"
    assert embed.get_provider("voyage", env={"VOYAGE_API_KEY": "k", "VOYAGE_MODEL": "voyage-code-4"}, path=absent).model \
        == "voyage:voyage-code-4:1024"
    with pytest.raises(ValueError, match="unknown embedding provider"):
        embed.get_provider("cohere", env={}, path=absent)
    with pytest.raises(embed.ProviderUnavailable, match="nothing is embedded without a key"):
        embed.get_provider(env={}, path=absent).embed(["x"])


def test_key_comes_from_env_then_dotenv_and_reads_only_key_lines(tmp_path):
    f = tmp_path / ".env"
    f.write_text("DATABASE_URL=postgresql://secret\n# OPENAI_API_KEY=commented\nexport OPENAI_API_KEY = \"sk-file\"\n")
    assert embed.find_key("OPENAI_API_KEY", env={}, path=f) == "sk-file"
    assert embed.find_key("OPENAI_API_KEY", env={"OPENAI_API_KEY": " sk-env "}, path=f) == "sk-env"
    assert embed.find_key("VOYAGE_API_KEY", env={}, path=f) is None
    with pytest.raises(ValueError):
        embed.find_key("DATABASE_URL", env={}, path=f)  # only the two embedding key names are ever read
    assert embed.get_provider(env={}, path=f).name == "openai"


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _vec(i):
    v = [0.0] * embed.DIMENSIONS
    v[i] = 1.0
    return v


def test_openai_request_shape_and_batching():
    seen = []

    def opener(req, timeout):
        body = json.loads(req.data)
        seen.append((req.full_url, req.get_header("Authorization"), body))
        # answer out of order to prove results are re-sorted by index
        data = [{"index": i, "embedding": _vec(i)} for i in range(len(body["input"]))][::-1]
        return _Resp({"data": data})

    p = embed.OpenAIProvider("sk-test", opener=opener)
    p.batch_size = 2
    vecs = p.embed(["a", "b", "c"])
    assert [len(v) for v in vecs] == [1024] * 3 and vecs[1][1] == 1.0 and vecs[0][0] == 1.0
    assert len(seen) == 2
    url, auth, body = seen[0]
    assert url == "https://api.openai.com/v1/embeddings" and auth == "Bearer sk-test"
    assert body["model"] == "text-embedding-3-small" and body["dimensions"] == 1024 and body["input"] == ["a", "b"]


def test_voyage_request_shape_marks_queries():
    bodies = []

    def opener(req, timeout):
        bodies.append(json.loads(req.data))
        return _Resp({"data": [{"index": 0, "embedding": _vec(0)}]})

    p = embed.VoyageProvider("pa-test", opener=opener)
    p.embed(["x"])
    p.embed(["x"], query=True)
    assert bodies[0]["model"] == "voyage-4-lite" and bodies[0]["output_dimension"] == 1024
    assert [b["input_type"] for b in bodies] == ["document", "query"]


def test_errors_retry_and_never_leak_the_key():
    calls = []

    def opener(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b'{"error":"limit for sk-secret-key"}'))

    p = embed.OpenAIProvider("sk-secret-key", opener=opener, sleep=lambda s: None)
    with pytest.raises(embed.ProviderError) as exc:
        p.embed(["x"])
    assert len(calls) == 3 and "429" in str(exc.value) and "sk-secret-key" not in str(exc.value)

    def bad(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

    calls.clear()
    with pytest.raises(embed.ProviderError, match="401"):
        embed.OpenAIProvider("k", opener=bad, sleep=lambda s: None).embed(["x"])

    def short(req, timeout):
        return _Resp({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    with pytest.raises(embed.ProviderError, match="1024"):
        embed.OpenAIProvider("k", opener=short).embed(["x"])


@pytest.mark.skipif(not (embed.find_key("OPENAI_API_KEY") or embed.find_key("VOYAGE_API_KEY")),
                    reason="no OPENAI_API_KEY or VOYAGE_API_KEY (environment or .env): real provider test skipped")
def test_real_provider_returns_1024_dimensional_vectors():
    provider = embed.get_provider()
    assert embed.available(provider)
    a, b = provider.embed(["rate limiting the login endpoint", "billing invoices"])
    assert len(a) == len(b) == 1024
    q = provider.embed(["login throttling"], query=True)[0]
    cos = lambda x, y: sum(i * j for i, j in zip(x, y)) / (math.sqrt(sum(i * i for i in x)) * math.sqrt(sum(j * j for j in y)))  # noqa: E731
    assert cos(q, a) > cos(q, b)


# --------------------------------------------------------------------------- chunking (database)


def test_node_chunks_are_sections_not_files(ledger):
    specs = {(c.ref, c.section): c for c in chunk.node_chunks(ledger, "demo")}
    assert {s for r, s in specs if r == "node:epic-billing"} == {"title", "riskiest_assumption", "guardrail_metric"}
    assert "intake" not in {s for _, s in specs}
    assert {s for r, s in specs if r == "node:feat-ratelimit"} == {"title", "acceptance", "decisions", "scope"}
    acc = specs[("node:feat-ratelimit", "acceptance")].text
    assert "429" in acc and "tests/t.py::t" in acc
    dec = specs[("node:feat-ratelimit", "decisions")].text
    assert "Which store" in dec and "redis, postgres" in dec and "Recommended: redis" in dec
    assert "app/auth/login.py" in specs[("node:feat-ratelimit", "scope")].text
    assert "app/routes/**" in specs[("node:rule-auth", "scope")].text
    assert chunk.node_chunks(ledger, "other") == []
    assert all(c.source == "node" and c.repo == "demo" for c in specs.values())


def test_symbol_chunks_cover_every_symbol(graph):
    conn, sid = graph
    specs = chunk.symbol_chunks(conn, sid)
    n = conn.execute("SELECT count(*) AS n FROM sym WHERE snapshot_id = %s", (sid,)).fetchone()["n"]
    assert len(specs) == n > 0
    assert len({s.ref for s in specs}) == n and all(s.section == "summary" and s.snapshot_id == sid for s in specs)
    assert all(s.ref == f"sym:demo:{s.kind}:{s.qualname}@{s.path}" for s in specs)


# --------------------------------------------------------------------------- build


def test_build_without_a_key_embeds_and_writes_nothing(ledger, no_keys):
    provider = embed.get_provider()
    assert provider.name == "none"
    with pytest.raises(embed.ProviderUnavailable, match="nothing is embedded without a key"):
        chunk.build(ledger, provider)
    assert ledger.execute("SELECT count(*) AS n FROM chunk").fetchone()["n"] == 0
    dry = chunk.build(ledger, provider, dry_run=True)  # counting needs no key
    assert dry["embedded"] == dry["chunks"] > 0 and dry["dry_run"] is True and dry["chars"] > 0
    assert ledger.execute("SELECT count(*) AS n FROM chunk").fetchone()["n"] == 0


def test_build_is_incremental_and_prunes(graph):
    conn, sid = graph
    fake = FakeProvider()
    first = chunk.build(conn, fake, repo="demo")
    total = conn.execute("SELECT count(*) AS n FROM chunk").fetchone()["n"]
    assert first["chunks"] == first["embedded"] == total and first["deleted"] == 0 and first["repos"] == ["demo"]
    assert fake.embedded == total and max(n for n, _ in fake.calls) == 3  # batched by provider.batch_size
    row = conn.execute("SELECT embed_model, vector_dims(embedding) AS d FROM chunk LIMIT 1").fetchone()
    assert row == {"embed_model": "fake:bow:1024", "d": 1024}

    again = chunk.build(conn, fake, repo="demo")
    assert again["embedded"] == 0 and again["unchanged"] == total and fake.embedded == total

    conn.execute("UPDATE node SET title = 'Rate limit the signin endpoint' WHERE slug = 'feat-ratelimit'")
    changed = chunk.build(conn, fake, repo="demo")
    assert changed["embedded"] == 1  # only the title section changed
    conn.execute("DELETE FROM node WHERE slug = 'rule-auth'")
    pruned = chunk.build(conn, fake, repo="demo")
    assert pruned["embedded"] == 0 and pruned["deleted"] == 0  # node delete cascaded the chunks already
    assert conn.execute("SELECT count(*) AS n FROM chunk WHERE ref = 'node:rule-auth'").fetchone()["n"] == 0

    conn.execute("DELETE FROM chunk WHERE ref = 'node:epic-billing' AND section = 'title'")
    conn.execute("UPDATE node SET attrs = '{}' WHERE slug = 'epic-billing'")
    pruned = chunk.build(conn, fake, repo="demo")
    assert pruned["deleted"] == 2 and pruned["embedded"] == 1  # the two attrs sections go, the title comes back


def test_a_new_embedding_model_re_embeds_everything(ledger):
    chunk.build(ledger, FakeProvider("fake:a:1024"))
    other = FakeProvider("fake:b:1024")
    result = chunk.build(ledger, other)
    assert result["embedded"] == result["chunks"] and other.embedded == result["chunks"]
    assert ledger.execute("SELECT DISTINCT embed_model FROM chunk").fetchall() == [{"embed_model": "fake:b:1024"}]


def test_symbol_chunks_follow_the_latest_snapshot_without_re_embedding(fakes, ledger, make_synthetic_repo, git_commit):
    repo = make_synthetic_repo()
    sha1 = snapshot.resolve_sha(repo)
    snapshot.index(ledger, repo, sha1, name="demo")
    fake = FakeProvider()
    chunk.build(ledger, fake, repo="demo")
    before = fake.embedded
    git_commit(repo, {"app/extra.py": "def fresh_symbol():\n    return 1\n"}, "extra")
    new = snapshot.index(ledger, repo, "HEAD", name="demo")["snapshot_id"]
    result = chunk.build(ledger, fake, repo="demo")
    assert result["repointed"] > 0 and fake.embedded - before == result["embedded"] >= 1
    snaps = ledger.execute("SELECT DISTINCT snapshot_id FROM chunk WHERE source = 'sym'").fetchall()
    assert snaps == [{"snapshot_id": new}]
    assert ledger.execute("SELECT count(*) AS n FROM chunk WHERE ref LIKE '%fresh_symbol%'").fetchone()["n"] >= 1


def test_max_chunks_refuses_before_embedding(ledger):
    fake = FakeProvider()
    with pytest.raises(ValueError, match="max-chunks"):
        chunk.build(ledger, fake, max_chunks=2)
    assert fake.embedded == 0


# --------------------------------------------------------------------------- hybrid search


def test_hybrid_search_finds_by_meaning_words_and_identifiers(graph):
    conn, sid = graph
    fake = FakeProvider()
    chunk.build(conn, fake, repo="demo")
    hits = search.search(conn, "limit login attempts rate", provider=fake, scope="node")
    assert hits[0]["slug"] == "feat-ratelimit" and hits[0]["mode"] == "hybrid"
    assert hits[0]["vector_rank"] == 1 and hits[0]["lexical_rank"] == 1 and hits[0]["cosine"] > 0
    assert fake.calls[-1] == (1, True)  # the query was embedded once, as a query
    # one hit per node, carrying every section that ranked
    assert len({h["ref"] for h in hits}) == len(hits)
    # an identifier the fake embedding cannot relate to anything is still found by full text
    ident = search.search(conn, "app/auth/login.py", provider=fake, scope="node")
    assert ident[0]["slug"] == "feat-ratelimit" and ident[0]["lexical_rank"] == 1
    # filters
    assert {h["kind"] for h in search.search(conn, "billing", provider=fake, scope="node", kind="epic")} == {"epic"}
    assert search.search(conn, "billing", provider=fake, repo="nowhere") == []
    assert all(h["source"] == "sym" for h in search.search(conn, "items", provider=fake, scope="sym"))
    with pytest.raises(ValueError):
        search.search(conn, "  ", provider=fake)
    with pytest.raises(ValueError):
        search.search(conn, "x", provider=fake, scope="files")


def test_fusion_combines_vector_only_and_lexical_only_hits(ledger):
    chunk.build(ledger, FakeProvider())
    fake = FakeProvider()
    # 'invoices' appears only in the epic's riskiest_assumption: the lexical list finds it, and the
    # vector list finds it too, but the rank fields show each list's own opinion
    hit = search.search(ledger, "invoices", provider=fake, scope="node")[0]
    assert hit["slug"] == "epic-billing" and "riskiest_assumption" in hit["sections"]
    assert hit["score"] == pytest.approx(1 / 61 + 1 / 61, abs=1e-6)
    # a word the fake vectors place nowhere near and no text contains: vector-only hits remain
    odd = search.search(ledger, "zzzunmatched", provider=fake, scope="node")
    assert odd and all(h["lexical_rank"] is None and h["vector_rank"] for h in odd)


def test_search_only_compares_vectors_of_the_same_model(ledger):
    chunk.build(ledger, FakeProvider("fake:a:1024"))
    hits = search.search(ledger, "rate limit login", provider=FakeProvider("fake:b:1024"), scope="node")
    assert hits and all(h["mode"] == "hybrid" and h["vector_rank"] is None for h in hits)  # lexical carried it


def test_search_without_a_provider_is_lexical_and_never_raises(ledger, no_keys):
    chunk.build(ledger, FakeProvider())
    hits = search.search(ledger, "rate limit login", scope="node")
    assert hits[0]["slug"] == "feat-ratelimit" and hits[0]["mode"] == "lexical" and hits[0]["vector_rank"] is None

    class Broken(FakeProvider):
        def embed(self, texts, *, query=False):
            raise embed.ProviderError("HTTP 500")

    assert search.search(ledger, "rate limit login", provider=Broken(), scope="node")[0]["mode"] == "lexical"


# --------------------------------------------------------------------------- tool bodies and fallback


def test_graph_search_and_node_search_fall_back_to_trigram_until_a_chunk_exists(graph, no_keys):
    conn, sid = graph
    assert not search.has_chunks(conn)
    sym = search.graph_search(conn, sid, "items")
    assert sym and "mode" not in sym[0]  # the existing queries.search shape
    node = search.node_search(conn, "Rate limit")
    assert node and node[0]["slug"] == "feat-ratelimit" and node[0]["mode"] == "trigram"
    assert search.node_search(conn, "billing", kind="epic")[0]["slug"] == "epic-billing"
    assert search.node_search(conn, "billing", kind="rule") == []
    with pytest.raises(ValueError):
        search.node_search(conn, "x", kind="nonsense")

    chunk.build(conn, FakeProvider(), repo="demo")
    assert search.has_chunks(conn, source="sym", snapshot_id=sid) and search.has_chunks(conn, source="node", repo="demo")
    sym = search.graph_search(conn, sid, "items", provider=FakeProvider())
    assert sym and sym[0]["mode"] == "hybrid" and {"kind", "name", "qualname", "path", "score"} <= set(sym[0])
    assert all(r["snapshot_id"] == sid for r in sym)
    node = search.node_search(conn, "rate limit login", provider=FakeProvider())
    assert node[0]["slug"] == "feat-ratelimit" and node[0]["title"] == "Rate limit the login endpoint"
    assert node[0]["mode"] == "hybrid" and node[0]["state"]
    with pytest.raises(ValueError):
        search.graph_search(conn, sid, "x", kind="nonsense", provider=FakeProvider())
    # a snapshot with no chunks still takes the trigram path
    other = conn.execute("INSERT INTO snapshot (repo, commit_sha, extractor_version) VALUES ('demo', %s, 'x') RETURNING id",
                         ("f" * 40,)).fetchone()["id"]
    assert not search.has_chunks(conn, source="sym", snapshot_id=other)


def _tool(server, name, args):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            res = await client.call_tool(name, args)
            return res.structuredContent or json.loads(res.content[0].text)

    return anyio.run(go)


def test_mcp_tools_use_the_index_when_chunks_exist(graph, conn_factory, no_keys):
    conn, sid = graph
    server = mcp_server.build_server(conn_factory)
    assert "node_search" in mcp_server.READ_ONLY_TOOLS
    before = _tool(server, "node_search", {"text": "rate limit"})
    assert before["ok"] and before["result"][0]["mode"] == "trigram"
    chunk.build(conn, FakeProvider(), repo="demo")
    after = _tool(server, "node_search", {"text": "rate limit login"})  # no key in this process: lexical, not an error
    assert after["ok"] and after["result"][0]["slug"] == "feat-ratelimit" and after["result"][0]["mode"] == "lexical"
    syms = _tool(server, "graph_search", {"text": "items", "repo": "demo"})
    assert syms["ok"] and syms["result"] and syms["result"][0]["mode"] == "lexical"
    bad = _tool(server, "node_search", {"text": "x", "kind": "nonsense"})
    assert bad["ok"] is False and bad["error"]["type"] == "invalid"


# --------------------------------------------------------------------------- CLI


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr()


def test_cli_index_build_search_status(graph, capsys, no_keys, monkeypatch):
    conn, sid = graph
    code, out = run_cli(capsys, "index", "build", "--repo", "demo", "--json")
    assert code == 2 and "nothing is embedded without a key" in json.loads(out.out)["message"]
    assert conn.execute("SELECT count(*) AS n FROM chunk").fetchone()["n"] == 0

    code, out = run_cli(capsys, "index", "build", "--repo", "demo", "--dry-run", "--json")
    dry = json.loads(out.out)
    assert code == 0 and dry["dry_run"] and dry["embedded"] > 0

    fake = FakeProvider()
    monkeypatch.setattr(embed, "get_provider", lambda name=None, **kw: fake)
    code, out = run_cli(capsys, "index", "build", "--repo", "demo", "--json")
    built = json.loads(out.out)
    assert code == 0 and built["embedded"] == built["chunks"] and built["model"] == "fake:bow:1024"

    code, out = run_cli(capsys, "index", "search", "rate limit login", "--scope", "node", "--json")
    hits = json.loads(out.out)
    assert code == 0 and hits[0]["slug"] == "feat-ratelimit" and hits[0]["mode"] == "hybrid"
    code, out = run_cli(capsys, "index", "search", "rate limit login", "--scope", "node")
    assert code == 0 and "node:feat-ratelimit" in out.out and "rank" in out.out
    code, out = run_cli(capsys, "index", "status", "--json")
    assert json.loads(out.out)["chunks"] == built["chunks"]


def test_cli_index_repo_still_extracts_a_commit(fakes, clean_conn, make_synthetic_repo, capsys):
    path = make_synthetic_repo()
    code, out = run_cli(capsys, "index", "--repo", str(path), "--name", path.name, "--json")
    assert code == 0 and json.loads(out.out)["repo"] == path.name
    code, out = run_cli(capsys, "index", "--json")
    assert code == 2 and "--repo is required" in json.loads(out.out)["message"]
    # build takes a checkout path as well as a repo name
    for repo in (str(path), path.name):
        code, out = run_cli(capsys, "index", "build", "--repo", repo, "--dry-run", "--json")
        dry = json.loads(out.out)
        assert code == 0 and dry["repos"] == [path.name] and dry["embedded"] > 0
