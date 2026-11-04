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
