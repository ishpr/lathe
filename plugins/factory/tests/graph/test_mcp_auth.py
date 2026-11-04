"""OAuth on the Streamable HTTP transport: JWT verification, metadata, and the refuse-to-start rule."""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from sdlc_graph import mcp_auth, mcp_server

ISS, AUD, RES = "https://idp.example.com", "https://graph.example.com/mcp", "https://graph.example.com/mcp"


@pytest.fixture
def keys(tmp_path):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
    jwk.update(kid="k1", alg="RS256", use="sig")
    f = tmp_path / "jwks.json"
    f.write_text(json.dumps({"keys": [jwk]}))
    return priv, str(f)


def token(priv, **over):
    claims = {"iss": ISS, "aud": AUD, "sub": "alice", "exp": int(time.time()) + 600, "scope": "graph:read graph:write"}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, priv, algorithm="RS256", headers={"kid": "k1"})


def cfg(jwks_file, scopes=("graph:read",)):
    return mcp_auth.from_env({"SDLC_MCP_OAUTH_ISSUER": ISS, "SDLC_MCP_OAUTH_AUDIENCE": AUD, "SDLC_MCP_OAUTH_SCOPES": " ".join(scopes),
                              "SDLC_MCP_OAUTH_JWKS_FILE": jwks_file})


def test_from_env():
    assert mcp_auth.from_env({}) is None
    with pytest.raises(mcp_auth.AuthConfigError):
        mcp_auth.from_env({"SDLC_MCP_OAUTH_ISSUER": ISS})
    with pytest.raises(mcp_auth.AuthConfigError):
        mcp_auth.from_env({"SDLC_MCP_OAUTH_ISSUER": "ftp://x", "SDLC_MCP_OAUTH_AUDIENCE": "a"})
    c = mcp_auth.from_env({"SDLC_MCP_OAUTH_ISSUER": ISS + "/", "SDLC_MCP_OAUTH_AUDIENCE": "a", "SDLC_MCP_OAUTH_SCOPES": "x,y"})
    assert c.scopes == ("x", "y") and c.jwks_url == ISS + "/.well-known/jwks.json"


@pytest.mark.anyio
@pytest.mark.parametrize("over,ok", [({}, True), ({"aud": "other"}, False), ({"iss": "https://evil"}, False),
                                     ({"exp": int(time.time()) - 3600}, False), ({"sub": None}, False)])
async def test_verifier(keys, over, ok):
    priv, f = keys
    got = await mcp_auth.JwtVerifier(cfg(f)).verify_token(token(priv, **over))
    assert (got is not None) is ok
    if ok:
        assert got.subject == "alice" and "graph:read" in got.scopes and got.resource == RES


@pytest.mark.anyio
async def test_verifier_rejects_wrong_key_and_hs256(keys):
    _, f = keys
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = mcp_auth.JwtVerifier(cfg(f))
    assert await v.verify_token(token(other)) is None
    hs = jwt.encode({"iss": ISS, "aud": AUD, "sub": "a", "exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    assert await v.verify_token(hs) is None


def test_http_requires_bearer_and_serves_metadata(keys):
    priv, f = keys
    app = mcp_server.build_server(auth=cfg(f)).streamable_http_app()
    with TestClient(app, base_url="https://graph.example.com") as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer junk"}).status_code == 401
        no_scope = token(priv, scope="other")
        assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {no_scope}"}).status_code == 403
        ok = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {token(priv)}"})
        assert ok.status_code not in (401, 403)
        meta = client.get("/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200 and meta.json()["authorization_servers"] == [ISS + "/"] or ISS in str(meta.json())


def test_http_refuses_to_start_without_auth(monkeypatch, capsys):
    for k in ("SDLC_MCP_OAUTH_ISSUER", "SDLC_MCP_OAUTH_AUDIENCE"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        mcp_server.main(["--http"])
    assert e.value.code == 2 and "OAuth" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        mcp_server.main(["--http", "--insecure-no-auth", "--host", "0.0.0.0"])


def test_insecure_no_auth_starts_on_loopback(monkeypatch):
    for k in ("SDLC_MCP_OAUTH_ISSUER", "SDLC_MCP_OAUTH_AUDIENCE"):
        monkeypatch.delenv(k, raising=False)
    ran = {}
    monkeypatch.setattr("mcp.server.fastmcp.FastMCP.run", lambda self, transport="stdio", **kw: ran.update(t=transport, auth=self.settings.auth))
    assert mcp_server.main(["--http", "--insecure-no-auth"]) == 0
    assert ran == {"t": "streamable-http", "auth": None}


# --------------------------------------------------------------------------- JWKS off the loop, rate-limited refetch


def test_algorithms_are_asymmetric_and_include_pss():
    assert {"PS256", "PS384", "PS512", "RS256", "ES256"} <= set(mcp_auth.ALGORITHMS)
    assert not any(a.startswith("HS") or a.lower() == "none" for a in mcp_auth.ALGORITHMS)
    assert not hasattr(mcp_auth.AuthConfig, "_extra")


@pytest.mark.anyio
async def test_unknown_kid_refetches_at_most_once_per_interval(keys):
    priv, f = keys
    now = [1000.0]
    v = mcp_auth.JwtVerifier(cfg(f), clock=lambda: now[0])

    def with_kid(kid):
        claims = {"iss": ISS, "aud": AUD, "sub": "alice", "exp": int(time.time()) + 600, "scope": "graph:read"}
        return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})

    assert await v.verify_token(token(priv)) is not None and v.fetches == 1
    assert await v.verify_token(token(priv)) is not None and v.fetches == 1  # cached
    for i in range(20):  # random kids: one refetch at most inside the window, then fail fast
        assert await v.verify_token(with_kid(f"rand-{i}")) is None
    assert v.fetches == 1  # the first fetch was < REFETCH_INTERVAL ago: no refetch at all
    now[0] += mcp_auth.REFETCH_INTERVAL + 1
    assert await v.verify_token(with_kid("rand-x")) is None and v.fetches == 2
    for i in range(5):
        assert await v.verify_token(with_kid(f"again-{i}")) is None
    assert v.fetches == 2
    assert await v.verify_token(token(priv)) is not None  # the known key still works from the cache


@pytest.mark.anyio
async def test_a_rotated_key_is_picked_up_after_the_interval(keys, tmp_path):
    priv, f = keys
    now = [0.0]
    v = mcp_auth.JwtVerifier(cfg(f), clock=lambda: now[0])
    assert await v.verify_token(token(priv)) is not None
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new.public_key()))
    jwk.update(kid="k2", alg="RS256", use="sig")
    doc = json.loads(open(f).read())
    doc["keys"].append(jwk)
    open(f, "w").write(json.dumps(doc))
    rotated = jwt.encode({"iss": ISS, "aud": AUD, "sub": "bob", "exp": int(time.time()) + 600, "scope": "graph:read"},
                         new, algorithm="RS256", headers={"kid": "k2"})
    assert await v.verify_token(rotated) is None  # inside the window: no refetch
    now[0] += mcp_auth.REFETCH_INTERVAL + 1
    got = await v.verify_token(rotated)
    assert got is not None and got.subject == "bob"


@pytest.mark.anyio
async def test_the_jwks_fetch_runs_off_the_event_loop(keys, monkeypatch):
    import asyncio
    import threading

    priv, f = keys
    loop_thread = threading.get_ident()
    seen = {}
    v = mcp_auth.JwtVerifier(cfg(f))
    real = v._load

    def load():
        seen["thread"] = threading.get_ident()
        return real()

    monkeypatch.setattr(v, "_load", load)
    calls = []
    real_to_thread = asyncio.to_thread

    async def spy(fn, *a, **k):
        calls.append(fn)
        return await real_to_thread(fn, *a, **k)

    monkeypatch.setattr(mcp_auth.asyncio, "to_thread", spy)
    assert await v.verify_token(token(priv)) is not None
    assert calls and seen["thread"] != loop_thread


# --------------------------------------------------------------------------- per-request actor from the token subject


def _rpc(client, bearer, name, args):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res.get("structuredContent") or json.loads(res["content"][0]["text"])


def test_oauth_actor_is_agent_class_and_unique():
    from sdlc_graph.ledger.transitions import actor_class

    for sub in ("alice", "auth0|12345", "alice@example.com", "x" * 300, "a b"):
        a = mcp_server.oauth_actor(sub)
        assert a.startswith("agent:oauth/") and actor_class(a) == "agent", a
    assert mcp_server.oauth_actor("a|b") != mcp_server.oauth_actor("a_b")


def test_http_calls_act_as_their_own_token_subject(keys, conn_factory, clean_conn):
    from sdlc_graph.ledger import nodes

    priv, f = keys
    nodes.create(clean_conn, kind="contract", slug="c-who", title="t", repo="o/r", created_by="@a")
    srv = mcp_server.build_server(conn_factory, auth=cfg(f))
    from mcp.server.transport_security import TransportSecuritySettings

    srv.settings.stateless_http = True
    srv.settings.json_response = True
    srv.settings.transport_security = TransportSecuritySettings(allowed_hosts=["graph.example.com"],
                                                                allowed_origins=["https://graph.example.com"])
    with TestClient(srv.streamable_http_app(), base_url="https://graph.example.com") as client:
        for sub in ("alice", "bob@example.com"):
            out = _rpc(client, token(priv, sub=sub), "decision_propose", {"slug": "c-who", "question": f"q from {sub}?", "options": ["a", "b"]})
            assert out["proposed_by"] == mcp_server.oauth_actor(sub), out
        # still an agent: a token never lifts the caller to a human-only transition
        out = _rpc(client, token(priv, sub="alice"), "decision_resolve", {"decision_id": "1", "choice": "a"})
        assert out["ok"] is False and out["error"]["type"] == "forbidden"
    rows = clean_conn.execute("SELECT proposed_by FROM decision ORDER BY id").fetchall()
    assert [r["proposed_by"] for r in rows] == ["agent:oauth/alice", mcp_server.oauth_actor("bob@example.com")]


def test_with_auth_but_no_verified_token_writes_are_refused(keys, conn_factory, clean_conn):
    """In-process (no HTTP request, so no bearer token) a server built with OAuth refuses to attribute a write."""
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    from sdlc_graph.ledger import nodes

    _, f = keys
    nodes.create(clean_conn, kind="contract", slug="c-anon", title="t", repo="o/r", created_by="@a")
    srv = mcp_server.build_server(conn_factory, auth=cfg(f))

    async def go():
        async with create_connected_server_and_client_session(srv._mcp_server) as client:
            res = await client.call_tool("decision_propose", {"slug": "c-anon", "question": "q?", "options": ["a"]})
            return res.structuredContent or json.loads(res.content[0].text)

    out = anyio.run(go)
    assert out["ok"] is False and out["error"]["type"] == "forbidden" and "subject" in out["error"]["message"]
    assert clean_conn.execute("SELECT count(*) AS n FROM decision").fetchone()["n"] == 0
