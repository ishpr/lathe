"""CI evidence is binding only with a verified GitHub OIDC token from the trusted evidence workflow, for the
node's exact owner/name; nothing a caller types makes it so."""

from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from oidc_fakes import REPO, WORKFLOW_REF, FakeIssuer
from sdlc_graph import cli
from sdlc_graph.ledger import evidence, nodes, oidc

GREEN = {"url": "https://github.com/o/sample-app/actions/runs/1", "conclusion": "success", "green": True,
         "sha": "a" * 40, "run_id": 1}
SRC = "ci:" + GREEN["url"]
SUB = "repo:o/sample-app:ref:refs/heads/main"


@pytest.fixture
def issuer(monkeypatch, tmp_path):
    return FakeIssuer(monkeypatch, tmp_path)


@pytest.fixture
def node(clean_conn):
    nodes.create(clean_conn, kind="contract", slug="c-oidc", title="t", repo=REPO, created_by="@a")
    return clean_conn


def test_valid_token_is_binding_with_its_sub(node, issuer):
    row = evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())
    assert row["binding"] is True and row["signed_by"] == SUB


def test_verified_claims_are_stored_server_side(node, issuer):
    row = evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(run_id="777"))
    p = row["payload"]
    assert p["oidc"]["job_workflow_ref"] == WORKFLOW_REF and p["oidc"]["event_name"] == "workflow_run"
    assert p["oidc"]["repository"] == REPO and p["oidc"]["sub"] == SUB and p["oidc"]["run_id"] == "777"
    assert p["evidence_run_id"] == "777" and p["run_id"] == 1  # the token's run vs the gate run the payload names
    assert "aud" not in p["oidc"] and "iss" not in p["oidc"]


@pytest.mark.parametrize("key", ["oidc", "evidence_run_id"])
def test_a_caller_cannot_supply_the_server_written_keys(node, issuer, key):
    for kw in ({"source": SRC, "oidc_token": issuer.token()}, {}):
        with pytest.raises(ValueError, match="written by the ledger"):
            evidence.add(node, "c-oidc", "ci_run", {**GREEN, key: {"sub": "x"}}, **kw)
    assert evidence.list_for(node, "c-oidc") == []


@pytest.mark.parametrize("repo", ["sample-app", "", "  ", "o/", "/sample-app"])
def test_bare_or_empty_node_repo_is_refused(clean_conn, issuer, repo):
    nodes.create(clean_conn, kind="contract", slug="c-bare", title="t", repo="placeholder", created_by="@a")
    clean_conn.execute("UPDATE node SET repo = %s WHERE slug = 'c-bare'", (repo,))  # create() refuses an empty repo
    with pytest.raises(oidc.OidcError, match="owner/name"):
        evidence.add(clean_conn, "c-bare", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())
    assert evidence.list_for(clean_conn, "c-bare") == []


def test_cross_owner_repo_is_refused(clean_conn, issuer, monkeypatch):
    """The old bare-name rule let attacker/sample-app sign for a node whose repo was sample-app; now the
    claim must equal the node's owner/name exactly, even when the trusted ref is a {repository} template."""
    monkeypatch.setenv("SDLC_OIDC_WORKFLOW_REF", "{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main")
    nodes.create(clean_conn, kind="contract", slug="c-x", title="t", repo="o/sample-app", created_by="@a")
    with pytest.raises(oidc.OidcError, match="repository"):
        evidence.add(clean_conn, "c-x", "ci_run", GREEN, source=SRC,
                     oidc_token=issuer.token(repository="attacker/sample-app",
                                             job_workflow_ref="attacker/sample-app/.github/workflows/sdlc-evidence.yml@refs/heads/main"))
    assert evidence.list_for(clean_conn, "c-x") == []


@pytest.mark.parametrize("over,why", [
    ({"aud": "someone-else"}, "(?i)audience"),
    ({"iss": "https://evil.example"}, "issuer"),
    ({"exp": int(time.time()) - 3600}, "expir"),
    ({"nbf": int(time.time()) + 3600}, "not yet valid"),
    ({"repository": "o/other"}, "repository"),
    ({"sub": None}, "sub"),
    # a PR's own workflow: its file at the PR's merge ref, on the pull_request event
    ({"event_name": "pull_request"}, "pull_request"),
    ({"job_workflow_ref": REPO + "/.github/workflows/evil.yml@refs/pull/7/merge"}, "trusted evidence workflow"),
    ({"job_workflow_ref": REPO + "/.github/workflows/sdlc-evidence.yml@refs/heads/feature"}, "trusted evidence workflow"),
    ({"job_workflow_ref": None}, "trusted evidence workflow"),
    ({"event_name": None}, "None"),
    ({"run_id": None}, "run_id"),
])
def test_bad_claims_are_refused_and_nothing_is_stored(node, issuer, over, why):
    with pytest.raises(oidc.OidcError, match=why):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(**over))
    assert evidence.list_for(node, "c-oidc") == []


def test_unset_trusted_workflow_ref_refuses_all_ci_evidence(node, issuer, monkeypatch):
    for value in (None, "", " , "):
        if value is None:
            monkeypatch.delenv("SDLC_OIDC_WORKFLOW_REF")
        else:
            monkeypatch.setenv("SDLC_OIDC_WORKFLOW_REF", value)
        with pytest.raises(oidc.OidcError, match="SDLC_OIDC_WORKFLOW_REF"):
            evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())
    assert evidence.list_for(node, "c-oidc") == []


def test_trusted_ref_template_and_list(node, issuer, monkeypatch):
    monkeypatch.setenv("SDLC_OIDC_WORKFLOW_REF", "x/y/.github/workflows/a.yml@refs/heads/main, "
                                                 "{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main")
    assert evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())["binding"]
    # the placeholder takes the token's verified repository, and the node must still be that repository
    with pytest.raises(oidc.OidcError):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC,
                     oidc_token=issuer.token(repository="o/other",
                                             job_workflow_ref="o/other/.github/workflows/sdlc-evidence.yml@refs/heads/main"))


def test_allowed_events_are_configurable_but_default_to_workflow_run(node, issuer, monkeypatch):
    tok = issuer.token(event_name="workflow_dispatch")
    with pytest.raises(oidc.OidcError, match="workflow_dispatch"):
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=tok)
    monkeypatch.setenv("SDLC_OIDC_EVENTS", "workflow_dispatch,push")
    assert evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=tok)["binding"]
    with pytest.raises(oidc.OidcError):  # widening to the smoke test's events does not admit workflow_run's absence
        evidence.add(node, "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token(event_name="pull_request"))


def test_token_is_verified_before_any_database_access(issuer, monkeypatch):
    """A refused token, or a JWKS fetch that fails, never touches the connection: no query, no transaction."""

    class NoDb:
        def __getattr__(self, name):
            raise AssertionError(f"the database was touched ({name}) before the token was verified")

    def boom():
        raise oidc.OidcError("cannot load the GitHub OIDC signing keys (test): down")

    for tok in ("not-a-jwt", issuer.token(event_name="pull_request")):
        with pytest.raises(oidc.OidcError):
            evidence.add(NoDb(), "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=tok)
    monkeypatch.setattr(oidc, "_jwks", boom)
    with pytest.raises(oidc.OidcError, match="signing keys"):
        evidence.add(NoDb(), "c-oidc", "ci_run", GREEN, source=SRC, oidc_token=issuer.token())


def test_verify_requires_node_repo(issuer):
    with pytest.raises(TypeError):
        oidc.verify(issuer.token())  # no default: a caller cannot skip the repository binding
    with pytest.raises(oidc.OidcError, match="owner/name"):
        oidc.verify(issuer.token(), node_repo="")
    assert oidc.verify(issuer.token(), node_repo=REPO)["sub"] == SUB


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


def test_ci_run_green_must_match_the_conclusion(node, issuer):
    with pytest.raises(ValueError, match="green"):
        evidence.add(node, "c-oidc", "ci_run", {**GREEN, "conclusion": "failure"}, source=SRC, oidc_token=issuer.token())
    red = evidence.add(node, "c-oidc", "ci_run", {**GREEN, "conclusion": "failure", "green": False}, source=SRC,
                       oidc_token=issuer.token())
    assert red["binding"] is True and red["payload"]["green"] is False


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


def test_cli_refuses_a_bad_token_without_opening_a_connection(issuer, capsys, monkeypatch):
    from sdlc_graph import db

    def no_connect(*a, **k):
        raise AssertionError("connected before the token was verified")

    monkeypatch.setattr(db, "connect", no_connect)
    argv = ["evidence", "add", "c-oidc", "--kind", "ci_run", "--payload", json.dumps(GREEN), "--source", SRC, "--json",
            "--oidc-token", issuer.token(event_name="pull_request")]
    assert cli.main(argv) == 2
    assert "pull_request" in json.loads(capsys.readouterr().out)["message"]


# --------------------------------------------------------------------------- evidence find (the slug from the ledger)


def test_find_the_contract_by_its_pr(clean_conn):
    c = clean_conn
    for slug, repo in (("c-one", REPO), ("c-two", REPO), ("c-elsewhere", "attacker/sample-app")):
        nodes.create(c, kind="contract", slug=slug, title=slug, repo=repo, created_by="@a")
    evidence.add(c, "c-one", "pr_url", {"url": "https://github.com/o/sample-app/pull/7"})
    evidence.add(c, "c-two", "pr_url", {"url": "https://github.com/o/sample-app/pull/8"})
    # another repo's contract naming this PR does not claim it
    evidence.add(c, "c-elsewhere", "pr_url", {"url": "https://github.com/o/sample-app/pull/7"})
    by_url = evidence.find(c, "pr_url", url="https://github.com/o/sample-app/pull/7")
    assert [r["slug"] for r in by_url] == ["c-one"]
    assert [r["slug"] for r in evidence.find(c, "pr_url", url="https://api.github.com/repos/O/Sample-App/pulls/7")] == ["c-one"]
    assert [r["slug"] for r in evidence.find(c, "pr_url", repo=REPO, pr=8)] == ["c-two"]
    assert evidence.find(c, "pr_url", repo=REPO, pr=9) == []
    assert evidence.find(c, "pr_url", url="https://github.com/o/sample-app/pull/70") == []
    with pytest.raises(ValueError):
        evidence.find(c, "pr_url")
    with pytest.raises(ValueError):
        evidence.find(c, "pr_url", url="https://github.com/o/sample-app/issues/7")


def test_find_reports_every_claimant_once(clean_conn):
    c = clean_conn
    for slug in ("c-a", "c-b"):
        nodes.create(c, kind="contract", slug=slug, title=slug, repo=REPO, created_by="@a")
    for slug in ("c-a", "c-a", "c-b"):
        evidence.add(c, slug, "pr_url", {"url": "https://github.com/o/sample-app/pull/3"})
    assert sorted(r["slug"] for r in evidence.find(c, "pr_url", repo=REPO, pr=3)) == ["c-a", "c-b"]


def test_cli_evidence_find(clean_conn, capsys):
    nodes.create(clean_conn, kind="contract", slug="c-cli", title="t", repo=REPO, created_by="@a")
    evidence.add(clean_conn, "c-cli", "pr_url", {"url": "https://github.com/o/sample-app/pull/11"})
    assert cli.main(["evidence", "find", "--kind", "pr_url", "--repo", REPO, "--pr", "11"]) == 0
    assert capsys.readouterr().out.strip() == "c-cli"
    assert cli.main(["evidence", "find", "--kind", "pr_url", "--url", "https://github.com/o/sample-app/pull/12"]) == 0
    assert capsys.readouterr().out.strip() == ""
    assert cli.main(["evidence", "find", "--kind", "pr_url", "--url", "https://github.com/o/sample-app/pull/11", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["slug"] == "c-cli"
