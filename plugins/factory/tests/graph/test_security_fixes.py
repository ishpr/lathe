"""Fixes from lathe's onboarding of itself: MCP write attribution, OIDC kid handling, write scopes, settings template."""

from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from oidc_fakes import ISSUER, KID, REPO, FakeIssuer
from sdlc_graph import mcp_auth, mcp_server
from sdlc_graph.ledger import evidence, nodes, oidc, runs
from test_mcp import _call

ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- 1. MCP write surface


def test_run_start_records_actor_and_another_actor_cannot_finish_it(conn_factory, clean_conn):
    a = mcp_server.build_server(conn_factory, actor="agent:alpha")
    b = mcp_server.build_server(conn_factory, actor="agent:beta")
    _, started = _call(a, "run_start", {"stage": "tests", "model": "sonnet"})
    assert started["actor"] == "agent:alpha"
    _, refused = _call(b, "run_finish", {"run_id": str(started["id"]), "outcome": "ok"})
    assert refused["ok"] is False and refused["error"]["type"] == "forbidden"
    assert clean_conn.execute("SELECT finished_at FROM run WHERE id = %s", (started["id"],)).fetchone()["finished_at"] is None
    _, done = _call(a, "run_finish", {"run_id": str(started["id"]), "outcome": "ok"})
    assert done["outcome"] == "ok"


def test_a_run_with_no_recorded_actor_cannot_be_finished_over_mcp(conn_factory, clean_conn):
    r = runs.start(clean_conn, stage="tests", requested_model="sonnet")  # a hook-style run: no actor
    _, out = _call(mcp_server.build_server(conn_factory, actor="agent:alpha"), "run_finish",
                   {"run_id": str(r["id"]), "outcome": "ok"})
    assert out["ok"] is False and out["error"]["type"] == "forbidden"


def test_evidence_add_records_the_calling_actor(conn_factory, clean_conn):
    nodes.create(clean_conn, kind="contract", slug="c-ev", title="t", repo=REPO, created_by="@a")
    _, row = _call(mcp_server.build_server(conn_factory, actor="agent:alpha"), "evidence_add",
                   {"slug": "c-ev", "kind": "pr_url", "payload": {"url": "https://github.com/o/sample-app/pull/1"}})
    assert row.get("actor") == "agent:alpha", row
    assert clean_conn.execute("SELECT actor FROM evidence").fetchone()["actor"] == "agent:alpha"


def test_protected_state_refusal_names_the_caller(conn_factory, clean_conn):
    nodes.create(clean_conn, kind="contract", slug="c-pr", title="t", repo=REPO, created_by="@a")
    _, out = _call(mcp_server.build_server(conn_factory, actor="agent:alpha"), "contract_transition",
                   {"slug": "c-pr", "to_state": "approved"})
    assert "agent:alpha" in out["error"]["message"]


def test_decision_resolve_is_a_documented_refusal(conn_factory):
    _, out = _call(mcp_server.build_server(conn_factory, actor="agent:alpha"), "decision_resolve",
                   {"decision_id": "1", "choice": "a"})
    assert out["ok"] is False and out["error"]["type"] == "forbidden" and "CLI" in out["error"]["message"]


# ----------------------------------------------------------------------------- 2. OIDC kid


@pytest.fixture
def issuer(monkeypatch, tmp_path):
    oidc._jwks_cache.clear()
    return FakeIssuer(monkeypatch, tmp_path)


def _headerless(issuer, **headers):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "sdlc-graph", "sub": "s", "repository": REPO, "event_name": "workflow_run",
              "job_workflow_ref": f"{REPO}/.github/workflows/sdlc-evidence.yml@refs/heads/main", "run_id": "1",
              "exp": now + 300}
    return jwt.encode(claims, issuer.key, algorithm="RS256", headers=headers or None)


def test_a_token_without_kid_is_refused_even_though_the_only_key_would_verify(issuer):
    with pytest.raises(oidc.OidcError, match="no kid"):
        oidc.verify_token(_headerless(issuer))


def test_unknown_kid_refetches_once_so_a_rotation_inside_the_ttl_is_accepted(issuer, monkeypatch, tmp_path):
    monkeypatch.delenv("SDLC_OIDC_JWKS_FILE")
    old = {"keys": [{"kid": "old", "kty": "RSA"}]}
    new_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(issuer.key.public_key()))
    new_jwk.update(kid=KID, alg="RS256", use="sig")
    fetched = [old, {"keys": [new_jwk]}]
    calls = []
    clock = [1000.0]

    class Resp:
        def __init__(self, doc): self.doc = doc
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self.doc).encode()

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return Resp(fetched[min(len(calls) - 1, 1)])

    monkeypatch.setattr(oidc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(oidc.time, "monotonic", lambda: clock[0])
    oidc._jwks()                      # cache the old set
    clock[0] += oidc._REFETCH_MIN + 1  # inside the TTL, past the refetch floor
    claims = oidc.verify_token(issuer.token())
    assert claims["repository"] == REPO and len(calls) == 2


def test_unknown_kid_refetch_is_rate_limited(issuer, monkeypatch):
    monkeypatch.delenv("SDLC_OIDC_JWKS_FILE")
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"keys": []}'

    monkeypatch.setattr(oidc.urllib.request, "urlopen", lambda url, timeout=None: calls.append(url) or Resp())
    for _ in range(3):
        with pytest.raises(oidc.OidcError, match="no signing key"):
            oidc.verify_token(issuer.token())
    assert len(calls) == 1


# ----------------------------------------------------------------------------- 3. OAuth write scope

ISS, AUD = "https://idp.example.com", "https://graph.example.com/mcp"


def test_write_scopes_default_and_configurable():
    base = {"SDLC_MCP_OAUTH_ISSUER": ISS, "SDLC_MCP_OAUTH_AUDIENCE": AUD}
    assert mcp_auth.from_env(base).write_scopes == ("graph:write",)
    assert mcp_auth.from_env({**base, "SDLC_MCP_OAUTH_WRITE_SCOPES": "a, b"}).write_scopes == ("a", "b")
    assert mcp_auth.from_env({**base, "SDLC_MCP_OAUTH_WRITE_SCOPES": "none"}).write_scopes == ()


def test_a_read_only_token_cannot_write_but_a_write_token_can(conn_factory, clean_conn, tmp_path):
    from starlette.testclient import TestClient
    from mcp.server.transport_security import TransportSecuritySettings

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
    jwk.update(kid="k1", alg="RS256")
    f = tmp_path / "jwks.json"
    f.write_text(json.dumps({"keys": [jwk]}))
    cfg = mcp_auth.from_env({"SDLC_MCP_OAUTH_ISSUER": ISS, "SDLC_MCP_OAUTH_AUDIENCE": AUD,
                             "SDLC_MCP_OAUTH_JWKS_FILE": str(f)})
    nodes.create(clean_conn, kind="contract", slug="c-sc", title="t", repo="o/r", created_by="@a")
    srv = mcp_server.build_server(conn_factory, auth=cfg)
    srv.settings.stateless_http = True
    srv.settings.json_response = True
    srv.settings.transport_security = TransportSecuritySettings(allowed_hosts=["graph.example.com"],
                                                                allowed_origins=["https://graph.example.com"])

    def tok(scope):
        return jwt.encode({"iss": ISS, "aud": AUD, "sub": "alice", "exp": int(time.time()) + 600, "scope": scope},
                          priv, algorithm="RS256", headers={"kid": "k1"})

    def rpc(client, bearer, name, args):
        r = client.post("/mcp", headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        res = r.json()["result"]
        return res.get("structuredContent") or json.loads(res["content"][0]["text"])

    args = {"slug": "c-sc", "question": "q?", "options": ["a", "b"]}
    with TestClient(srv.streamable_http_app(), base_url="https://graph.example.com") as client:
        refused = rpc(client, tok(""), "decision_propose", args)
        assert refused["ok"] is False and refused["error"]["type"] == "forbidden" and "graph:write" in refused["error"]["message"]
        assert rpc(client, tok("graph:read"), "decision_propose", args)["ok"] is False
        assert rpc(client, tok("graph:write"), "decision_propose", args).get("proposed_by") == "agent:oauth/alice"


# ----------------------------------------------------------------------------- 5/6. settings template and .mcp.json


def test_settings_template_hardening():
    s = json.loads((ROOT / "templates" / "settings.project.json").read_text())
    deny_write = s["sandbox"]["filesystem"]["denyWrite"]
    assert {"./.claude/settings.json", "./.mcp.json", "./.github/**"} <= set(deny_write)
    assert "gh *" not in s["sandbox"]["excludedCommands"]
    assert {"gh pr create *", "gh pr view *", "gh pr diff *", "gh issue view *"} <= set(s["sandbox"]["excludedCommands"])
    deny, allow = s["permissions"]["deny"], s["permissions"]["allow"]
    for rule in ("Bash(gh api *)", "Bash(gh secret *)", "Bash(gh workflow *)", "Bash(gh auth *)", "Bash(git push *+*)"):
        assert rule in deny, rule
    assert "Bash(python3 -I scripts/*)" not in allow
    assert "Bash(python3 -I scripts/freeze_tests.py *)" in allow


def test_plugin_mcp_json_carries_no_database_credential():
    text = (ROOT / ".mcp.json").read_text()
    assert "sdlc:sdlc" not in text and "DATABASE_URL" not in text
