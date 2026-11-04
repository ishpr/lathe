"""The index's uses at intake (docs/DESIGN.md 2.2): semantic dedupe, precedent, applicable rules and incident
lookup, each with its no-provider fallback. Uses a concept-keyed fake provider (no network); database tests
skip when Postgres is down."""

from __future__ import annotations

import json

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from psycopg.types.json import Jsonb

from sdlc_graph import mcp_server
from sdlc_graph.index import chunk, embed, search
from sdlc_graph.ledger import decisions, intake, nodes

CONCEPTS = ("login", "billing", "export")


class ConceptProvider(embed.Provider):
    """Texts that mention the same concept share a vector (cosine distance 0); the rest hash to their own axis."""

    name = "fake"
    model = "fake:concept:1024"
    batch_size = 8

    def embed(self, texts, *, query=False):
        out = []
        for t in texts:
            v = [0.0] * embed.DIMENSIONS
            hit = next((i for i, c in enumerate(CONCEPTS) if c in t.lower()), None)
            v[hit if hit is not None else 100 + hash(t) % 800] = 1.0
            out.append(v)
        return out


@pytest.fixture
def provider(monkeypatch):
    p = ConceptProvider()
    monkeypatch.setattr(search, "get_provider", lambda *a, **k: p)
    return p


@pytest.fixture
def no_provider(monkeypatch, tmp_path):
    for name in ("OPENAI_API_KEY", "VOYAGE_API_KEY", "EMBED_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(embed, "env_file", lambda: tmp_path / "absent.env")


def _draft(conn, slug, title, paths, text="", **kw):
    return intake.contract_draft(conn, slug=slug, title=title, repo="demo", actor="@a", ticket_text=text or title,
                                 touched_symbols=paths, **kw)


def _age(conn, slug, days):
    conn.execute("UPDATE node SET updated_at = now() - make_interval(days => %s) WHERE slug = %s", (days, slug))


# --------------------------------------------------------------------------- pure


def test_parse_rule_reads_the_three_spellings_of_paths():
    assert chunk.parse_rule("---\npaths:\n  - app/auth/**\n  - \"tests/auth/**\"\n---\n# Auth\nbody") == \
        (["app/auth/**", "tests/auth/**"], "# Auth\nbody")
    assert chunk.parse_rule("---\npaths: [a/**, 'b/*.py']\n---\ntext")[0] == ["a/**", "b/*.py"]
    assert chunk.parse_rule("---\npaths: app/**\n---\ntext")[0] == ["app/**"]
    assert chunk.parse_rule("no frontmatter here") == ([], "no frontmatter here")
    assert chunk.parse_rule("---\ndescription: x\n---\ntext") == ([], "text")


def test_globs_match_screens_by_directory_and_extension():
    g = search.globs_match
    assert g([], ["anything/"])                                  # no paths: applies everywhere
    assert g(["app/auth/**"], ["app/auth/"]) and g(["app/**"], ["app/auth/login.py"])
    assert g(["app/auth/**"], ["app/"])                          # the allowed dir contains the rule's
    assert g(["**/*.py"], ["app/auth/"])                         # no extension on the allowed path: cannot rule it out
    assert not g(["docs/**"], ["app/auth/"]) and not g(["**/*.ts"], ["app/auth/login.py"])
    assert g(["app/auth/login.py"], ["function:login@app/auth/login.py"])  # a symbol key reduces to its path


# --------------------------------------------------------------------------- dedupe


def test_embedding_dedupe_quotes_the_open_contract_and_creates_nothing(clean_conn, provider):
    assert _draft(clean_conn, "rate-limit", "Rate limit the login endpoint", ["app/auth/"])["status"] == "created"
    chunk.build(clean_conn, provider, repo="demo")
    out = _draft(clean_conn, "throttle", "Throttle repeated sign-in attempts", ["app/other/"],
                 text="Slow down login brute force")
    assert out["status"] == "duplicate" and out["node"] is None
    d = out["duplicates"][0]
    assert d["slug"] == "rate-limit" and d["match"] == "embedding" and d["distance"] <= search.DEDUPE_DISTANCE
    assert d["state"] == "drafted" and d["title"] and "similarity" in d and d["same_external_id"] is False
    with pytest.raises(LookupError):
        nodes.get_row(clean_conn, "throttle")
    # the caller says it is different work: drafted, with the candidates reported
    ok = _draft(clean_conn, "throttle", "Throttle repeated sign-in attempts", ["app/other/"],
                text="Slow down login brute force", allow_duplicate=True)
    assert ok["status"] == "created" and ok["duplicates"] == [] and ok["duplicates_overridden"][0]["slug"] == "rate-limit"


def test_a_merged_contract_or_other_concept_is_not_a_duplicate(clean_conn, provider):
    _draft(clean_conn, "rate-limit", "Rate limit the login endpoint", ["app/auth/"])
    chunk.build(clean_conn, provider, repo="demo")
    assert _draft(clean_conn, "invoices", "Itemise the billing page", ["app/billing/"])["status"] == "created"
    clean_conn.execute("UPDATE node SET state = 'merged' WHERE slug = 'rate-limit'")
    assert _draft(clean_conn, "again", "Login throttling again", ["app/auth2/"])["status"] == "created"


def test_dedupe_without_a_provider_falls_back_to_the_title_check(clean_conn, no_provider):
    _draft(clean_conn, "rate-limit", "Rate limit the login endpoint", ["app/auth/"])
    # different words, same concept: only the embedding would catch it, and there is none
    assert _draft(clean_conn, "throttle", "Throttle repeated sign-in attempts", ["app/other/"],
                  text="login brute force")["status"] == "created"
    out = _draft(clean_conn, "rate-limit-2", "Rate limit the login endpoint", ["app/third/"])
    assert out["status"] == "duplicate" and out["duplicates"][0]["match"] == "trigram" \
        and out["duplicates"][0]["distance"] is None and out["duplicates"][0]["slug"] == "rate-limit"
    assert out["precedent"] == [] and out["rules"] == []


def test_dedupe_ignores_chunks_of_another_embed_model(clean_conn, provider):
    _draft(clean_conn, "rate-limit", "Rate limit the login endpoint", ["app/auth/"])
    chunk.build(clean_conn, provider, repo="demo")
    other = ConceptProvider()
    other.model = "fake:other:1024"
    assert search.similar_open(clean_conn, ["login"], repo="demo", provider=other) == []
    assert search.similar_open(clean_conn, ["login"], repo="demo", provider=provider)[0]["slug"] == "rate-limit"


# --------------------------------------------------------------------------- precedent


def test_precedent_is_the_last_three_overlapping_contracts_with_their_evidence(clean_conn, no_provider):
    # overlapping drafts would be refused by the overlap rule, so seed the rows directly
    for slug, paths, days in [("c-old", ["app/auth/"], 4), ("c-mid", ["app/auth/login.py"], 3), ("c-new", ["app/auth/"], 2),
                              ("c-newest", ["app/auth/session.py"], 1), ("c-elsewhere", ["app/billing/"], 0)]:
        nodes.create(clean_conn, kind="contract", slug=slug, title=f"Work {slug}", repo="demo", created_by="@a",
                     touched_symbols=paths, acceptance=[{"id": "AC1", "text": "x", "test": f"tests/auth/test_{slug}.py::test_it"}])
        _age(clean_conn, slug, days)
    d = decisions.propose(clean_conn, "c-newest", "Which store?", ["redis", "postgres"], actor="@a")
    decisions.resolve(clean_conn, d["id"], "redis", actor="@alice")
    row = nodes.get_row(clean_conn, "c-newest")
    clean_conn.execute("INSERT INTO evidence (node_id, kind, source, payload) VALUES (%s, 'review_approval', 'local', %s)",
                       (row["id"], Jsonb({"reviewer": "@r", "author": "@a", "url": "u", "findings": ["session cookie not HttpOnly"]})))
    got = search.precedent(clean_conn, "demo", ["app/auth/"], exclude_slug="not-there")
    assert [p["slug"] for p in got] == ["c-newest", "c-new", "c-mid"]           # most recent first, capped at 3
    top = got[0]
    assert top["decisions"] == [{"question": "Which store?", "choice": "redis"}]
    assert top["tests"] == ["tests/auth/test_c-newest.py::test_it"] and top["findings"] == ["session cookie not HttpOnly"]
    assert top["overlap"] and top["state"] == "drafted"
    assert [p["slug"] for p in search.precedent(clean_conn, "demo", ["app/auth/"], exclude_slug="c-newest")][0] == "c-new"
    assert search.precedent(clean_conn, "demo", []) == [] and search.precedent(clean_conn, "demo", ["lib/"]) == []


def test_contract_draft_returns_precedent(clean_conn, no_provider):
    nodes.create(clean_conn, kind="contract", slug="old", title="Old auth work", repo="demo", created_by="@a",
                 touched_symbols=["app/auth/"], state="merged")
    out = _draft(clean_conn, "new", "Brand new thing to build", ["app/auth/login.py"])
    assert out["status"] == "created" and [p["slug"] for p in out["precedent"]] == ["old"]


# --------------------------------------------------------------------------- rules


RULE_AUTH = "---\npaths:\n  - app/auth/**\n---\n# Auth routes\nEvery login route checks the session cookie is HttpOnly.\n"
RULE_DOCS = "---\npaths:\n  - docs/**\n---\n# Docs\nWrite docs about login in sentence case.\n"
REVIEW = "# Review\nFlag any login change without a negative test.\n"


@pytest.fixture
def rules_root(tmp_path):
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "auth.md").write_text(RULE_AUTH)
    (tmp_path / ".claude" / "rules" / "docs.md").write_text(RULE_DOCS)
    (tmp_path / "REVIEW.md").write_text(REVIEW)
    return tmp_path


def test_sync_rules_makes_rule_nodes_idempotently(clean_conn, rules_root):
    first = chunk.sync_rules(clean_conn, "demo", rules_root)
    assert sorted(first["created"]) == ["rule-auth", "rule-docs", "rule-review"]
    row = nodes.get_row(clean_conn, "rule-auth")
    assert row["kind"] == "rule" and row["attrs"]["paths"] == ["app/auth/**"] and row["attrs"]["source_file"] == ".claude/rules/auth.md"
    assert row["title"] == "Auth routes"
    assert chunk.sync_rules(clean_conn, "demo", rules_root)["unchanged"] == ["rule-auth", "rule-docs", "rule-review"]
    (rules_root / ".claude" / "rules" / "auth.md").write_text(RULE_AUTH + "Also rotate it.\n")
    assert chunk.sync_rules(clean_conn, "demo", rules_root)["updated"] == ["rule-auth"]
    assert "rotate" in nodes.get_row(clean_conn, "rule-auth")["attrs"]["body"]
    # another repo's file of the same name does not take over the first repo's node
    again = chunk.sync_rules(clean_conn, "other-repo", rules_root)
    assert len(again["created"]) == 3 and all(s != "rule-auth" for s in again["created"])


def test_build_indexes_rule_files_and_search_ranks_them(clean_conn, rules_root, provider):
    out = chunk.build(clean_conn, provider, repo="demo", rules_root=rules_root)
    assert sorted(out["rules"]["created"]) == ["rule-auth", "rule-docs", "rule-review"]
    sections = {r["section"] for r in clean_conn.execute("SELECT section FROM chunk WHERE slug = 'rule-auth'").fetchall()}
    assert "body" in sections and "paths" in sections
    got = search.applicable_rules(clean_conn, "harden the login cookie", ["app/auth/"], repo="demo", provider=provider)
    assert [r["slug"] for r in got] == ["rule-auth", "rule-review"] or [r["slug"] for r in got] == ["rule-review", "rule-auth"]
    assert "rule-docs" not in [r["slug"] for r in got] and got[0]["mode"] == "hybrid" and got[0]["file"]
    assert dict.fromkeys(r["slug"] for r in got if r["mode"] == "hybrid")  # both reached by the search


def test_applicable_rules_without_an_index_match_on_paths_only(clean_conn, rules_root, no_provider):
    chunk.sync_rules(clean_conn, "demo", rules_root)
    got = search.applicable_rules(clean_conn, "anything", ["app/auth/login.py"], repo="demo")
    assert [r["slug"] for r in got] == ["rule-auth", "rule-review"] and {r["mode"] for r in got} == {"paths"}
    assert "HttpOnly" in got[0]["body"]
    assert [r["slug"] for r in search.applicable_rules(clean_conn, "x", ["docs/a.md"], repo="demo")] == ["rule-docs", "rule-review"]
    assert search.applicable_rules(clean_conn, "x", ["app/auth/"], repo="empty-repo") == []


def test_contract_draft_returns_the_rules_for_its_allowed_paths(clean_conn, rules_root, no_provider):
    chunk.sync_rules(clean_conn, "demo", rules_root)
    out = _draft(clean_conn, "auth-work", "Harden the session cookie", ["app/auth/"], capabilities={"paths": ["app/auth/**"]})
    assert out["status"] == "created" and [r["slug"] for r in out["rules"]] == ["rule-auth", "rule-review"]


# --------------------------------------------------------------------------- incidents


@pytest.fixture
def incidents(clean_conn):
    for slug, title in [("inc-cookie", "Session cookie leaked over http on login"), ("inc-billing", "Invoice totals rounded wrong")]:
        nodes.create(clean_conn, kind="incident", slug=slug, title=title, repo="demo", created_by="@a")
    nodes.create(clean_conn, kind="rule", slug="rule-cookie", title="Cookies are HttpOnly", repo="demo", created_by="@a")
    nodes.create(clean_conn, kind="contract", slug="fix-cookie", title="Make the cookie HttpOnly", repo="demo", created_by="@a",
                 touched_symbols=["app/auth/"], acceptance=[{"id": "AC1", "text": "x", "test": "tests/auth/test_cookie.py::test_httponly"}])
    nodes.link(clean_conn, "inc-cookie", "rule-cookie", "governed_by")
    nodes.link(clean_conn, "fix-cookie", "inc-cookie", "caused_by")
    return clean_conn


def test_similar_incidents_without_an_index_uses_the_title(incidents, no_provider):
    got = search.similar_incidents(incidents, "session cookie leaked", 3)
    assert [g["slug"] for g in got] == ["inc-cookie"] and got[0]["mode"] == "trigram"
    closed = {(c["slug"], c["rel"], c["direction"]): c for c in got[0]["closed_by"]}
    assert set(closed) == {("rule-cookie", "governed_by", "out"), ("fix-cookie", "caused_by", "in")}
    assert closed[("fix-cookie", "caused_by", "in")]["tests"] == ["tests/auth/test_cookie.py::test_httponly"]
    assert search.similar_incidents(incidents, "zebra", 3) == []
    with pytest.raises(ValueError):
        search.similar_incidents(incidents, "  ", 3)
    with pytest.raises(ValueError):
        search.similar_incidents(incidents, "x", 0)


def test_similar_incidents_with_an_index_ranks_by_meaning(incidents, provider):
    chunk.build(incidents, provider, repo="demo")
    got = search.similar_incidents(incidents, "login tokens exposed", 1, provider=provider)
    assert got[0]["slug"] == "inc-cookie" and got[0]["mode"] == "hybrid" and len(got[0]["closed_by"]) == 2


def _tool(server, name, args):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            res = await client.call_tool(name, args)
            return res.structuredContent or json.loads(res.content[0].text)

    return anyio.run(go)


def test_mcp_incident_similar_and_intake_context_are_read_only(incidents, conn_factory, no_provider):
    assert "incident_similar" in mcp_server.READ_ONLY_TOOLS and "intake_context" in mcp_server.READ_ONLY_TOOLS
    server = mcp_server.build_server(conn_factory)
    res = _tool(server, "incident_similar", {"text": "session cookie leaked", "k": 2})
    assert res["ok"] and res["result"][0]["slug"] == "inc-cookie" and res["result"][0]["closed_by"]
    ctx = _tool(server, "intake_context", {"repo": "demo", "title": "Fix the cookie", "paths": ["app/auth/"]})
    assert ctx["mode"] == "lexical" and ctx["duplicates"] == []   # a dict result comes back unwrapped
    assert [p["slug"] for p in ctx["precedent"]] == ["fix-cookie"] and [r["slug"] for r in ctx["rules"]] == ["rule-cookie"]
    assert _tool(server, "incident_similar", {"text": " "})["ok"] is False
