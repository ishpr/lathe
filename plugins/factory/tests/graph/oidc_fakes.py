"""A fake GitHub OIDC issuer for tests: a generated RSA key, its JWKS written to a file that
``SDLC_OIDC_JWKS_FILE`` points at, and a minter for tokens signed with it."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://token.actions.githubusercontent.com"
KID = "test-key"


class FakeIssuer:
    def __init__(self, monkeypatch, tmp_path: Path):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=KID, alg="RS256", use="sig")
        path = tmp_path / "jwks.json"
        path.write_text(json.dumps({"keys": [jwk]}))
        monkeypatch.setenv("SDLC_OIDC_JWKS_FILE", str(path))
        monkeypatch.delenv("SDLC_OIDC_AUDIENCE", raising=False)

    def token(self, key: Any = None, **over: Any) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "sdlc-graph", "sub": "repo:o/sample-app:pull_request",
                  "repository": "o/sample-app", "iat": now, "nbf": now - 5, "exp": now + 300}
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": KID})
