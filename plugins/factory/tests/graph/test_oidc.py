"""CI evidence is binding only with a verified GitHub OIDC token; nothing a caller types makes it so."""

from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from oidc_fakes import FakeIssuer
from sdlc_graph import cli
from sdlc_graph.ledger import evidence, nodes, oidc

GREEN = {"url": "https://github.com/o/sample-app/actions/runs/1", "conclusion": "success", "green": True}
SRC = "ci:" + GREEN["url"]


@pytest.fixture
def issuer(monkeypatch, tmp_path):
    return FakeIssuer(monkeypatch, tmp_path)


@pytest.fixture
def node(clean_conn):
    nodes.create(clean_conn, kind="contract", slug="c-oidc", title="t", repo="o/sample-app", created_by="@a")
    return clean_conn


def test_valid_token_is_binding_with_its_sub(node, issuer):
    row = evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())
    assert row["binding"] is True and row["signed_by"] == "repo:o/sample-app:pull_request"


def test_bare_node_repo_matches_the_name_part(clean_conn, issuer):
    nodes.create(clean_conn, kind="contract", slug="c-bare", title="t", repo="sample-app", created_by="@a")
    assert evidence.add(clean_conn, "c-bare", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())["binding"]


@pytest.mark.parametrize("over,why", [
    ({"aud": "someone-else"}, "audience"),
    ({"iss": "https://evil.example"}, "issuer"),
    ({"exp": int(time.time()) - 3600}, "expir"),
    ({"nbf": int(time.time()) + 3600}, "not yet valid"),
    ({"repository": "o/other"}, "repository"),
    ({"sub": None}, "sub"),
])
def test_bad_claims_are_refused_and_nothing_is_stored(node, issuer, over, why):
    with pytest.raises(oidc.OidcError):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(**over))
    assert evidence.list_for(node, "c-oidc") == []


def test_signature_from_another_key_is_refused(node, issuer):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OidcError, match="rejected"):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(key=other))
    assert evidence.list_for(node, "c-oidc") == []


def test_garbage_and_hs256_tokens_are_refused(node, issuer):
    import jwt

    for bad in ("not-a-jwt", jwt.encode({"sub": "x"}, "k" * 32, algorithm="HS256")):
        with pytest.raises(oidc.OidcError):
            evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=bad)


def test_ci_source_without_a_token_is_refused(node, issuer):
    for tok in (None, "", "  "):
        with pytest.raises(oidc.OidcError, match="refused"):
            evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=tok)
    assert evidence.list_for(node, "c-oidc") == []


def test_local_and_github_sources_are_never_binding(node, issuer):
    assert evidence.add(node, "c-oidc", "ci_run", GREEN)["binding"] is False
    gh = evidence.add(node, "c-oidc", "ci_run", GREEN, source="github:1")
    assert gh["binding"] is False and gh["signed_by"] is None
    with pytest.raises(ValueError, match="only goes with a ci:"):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source="local", oidc_token=issuer.token())


def test_add_has_no_signed_by_parameter(node):
    with pytest.raises(TypeError):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, signed_by="repo:o/r")


def test_resolve_token_forms(tmp_path, monkeypatch):
    f = tmp_path / "t.jwt"
    f.write_text("abc\n")
    monkeypatch.setenv("MY_JWT", "def")
    assert oidc.resolve_token("@" + str(f)) == "abc" and oidc.resolve_token("env:MY_JWT") == "def"
    assert oidc.resolve_token("lit") == "lit"
    for bad in ("@" + str(tmp_path / "missing"), "env:NOPE_UNSET"):
        with pytest.raises(oidc.OidcError):
            oidc.resolve_token(bad)


def test_custom_audience_from_env(node, issuer, monkeypatch):
    monkeypatch.setenv("SDLC_OIDC_AUDIENCE", "my-ledger")
    with pytest.raises(oidc.OidcError):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())
    assert evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(aud="my-ledger"))["binding"]


def test_cli_verifies_token_and_has_no_signed_by_flag(node, issuer, capsys, tmp_path, monkeypatch):
    tokfile = tmp_path / "t.jwt"
    tokfile.write_text(issuer.token())
    argv = ["evidence", "add", "c-oidc", "--kind", "ci_run", "--payload", json.dumps(GREEN), "--source", SRC, "--json"]
    assert cli.main(argv) == 2  # no token: refused
    capsys.readouterr()
    assert cli.main(argv + ["--oidc-token", "@" + str(tokfile)]) == 0
    assert json.loads(capsys.readouterr().out)["binding"] is True
    monkeypatch.setenv("TOK", issuer.token())
    assert cli.main(argv + ["--oidc-token", "env:TOK"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(argv + ["--signed-by", "repo:o/r"])
