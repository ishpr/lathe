"""A fake GitHub OIDC issuer for tests: a generated RSA key, its JWKS written to a file that
``SDLC_OIDC_JWKS_FILE`` points at, and a minter for tokens signed with it.

The default token is what templates/ci/sdlc-evidence.yml holds on GitHub: a ``workflow_run`` job of the trusted
evidence workflow on the default branch of ``o/sample-app``; the issuer sets ``SDLC_OIDC_WORKFLOW_REF`` to it."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://token.actions.githubusercontent.com"
KID = "test-key"
REPO = "o/sample-app"
WORKFLOW_REF = REPO + "/.github/workflows/sdlc-evidence.yml@refs/heads/main"


class FakeIssuer:
    def __init__(self, monkeypatch, tmp_path: Path):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=KID, alg="RS256", use="sig")
        path = tmp_path / "jwks.json"
        path.write_text(json.dumps({"keys": [jwk]}))
        monkeypatch.setenv("SDLC_OIDC_JWKS_FILE", str(path))
        monkeypatch.delenv("SDLC_OIDC_AUDIENCE", raising=False)
        monkeypatch.delenv("SDLC_OIDC_EVENTS", raising=False)
        monkeypatch.setenv("SDLC_OIDC_WORKFLOW_REF", WORKFLOW_REF)

    def token(self, key: Any = None, **over: Any) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "sdlc-graph", "sub": f"repo:{REPO}:ref:refs/heads/main",
                  "repository": REPO, "repository_owner": "o", "event_name": "workflow_run",
                  "job_workflow_ref": WORKFLOW_REF, "workflow_ref": WORKFLOW_REF, "ref": "refs/heads/main",
                  "sha": "d" * 40, "run_id": "9001", "run_attempt": "1",
                  "iat": now, "nbf": now - 5, "exp": now + 300}
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": KID})
