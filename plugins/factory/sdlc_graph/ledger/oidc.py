"""Verification of a GitHub Actions OIDC token, the only thing that makes evidence binding.

A CI job asks GitHub for a short-lived JWT (``permissions: id-token: write``) and hands it to
``sdlc-graph evidence add --source ci:<run url> --oidc-token ...``. :func:`verify` checks the RS256
signature against the issuer's JWKS, the issuer, the audience, ``exp``/``nbf`` and, when the node
names a repository, that the token's ``repository`` claim is that repository. The verified ``sub`` is
what the ledger stores as ``signed_by``; nothing a caller types can become one.

Configuration: ``SDLC_OIDC_AUDIENCE`` (default ``sdlc-graph``; the CI step requests the same audience).
``SDLC_OIDC_JWKS_FILE`` points at a local JWKS document and skips the network (the test seam, and an
air-gapped ledger's option); otherwise the issuer's JWKS is fetched with a short timeout.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
DEFAULT_AUDIENCE = "sdlc-graph"
FETCH_TIMEOUT = 5.0
_LEEWAY = 30  # seconds of clock skew tolerated on exp/nbf


class OidcError(ValueError):
    """The token is missing, malformed, or fails a check. The message says which check."""


def resolve_token(arg: str) -> str:
    """The JWT named by a CLI argument: ``@path`` reads a file, ``env:VAR`` reads a variable, anything
    else is the token itself. Raises OidcError when the file or variable is missing or empty."""
    arg = (arg or "").strip()
    if arg.startswith("@"):
        try:
            text = Path(arg[1:]).read_text(encoding="utf-8")
        except OSError as exc:
            raise OidcError(f"cannot read the OIDC token file {arg[1:]!r}: {exc}") from exc
    elif arg.startswith("env:"):
        text = os.environ.get(arg[4:], "")
    else:
        text = arg
    text = text.strip()
    if not text:
        raise OidcError(f"the OIDC token from {arg[:12]!r} is empty")
    return text


def audience() -> str:
    return os.environ.get("SDLC_OIDC_AUDIENCE", "").strip() or DEFAULT_AUDIENCE


def _jwks() -> dict[str, Any]:
    local = os.environ.get("SDLC_OIDC_JWKS_FILE", "").strip()
    try:
        if local:
            return json.loads(Path(local).read_text(encoding="utf-8"))
        with urllib.request.urlopen(JWKS_URL, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 (fixed https URL)
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise OidcError(f"cannot load the GitHub OIDC signing keys ({local or JWKS_URL}): {exc}") from exc


def _repo_matches(claim: str, node_repo: str) -> bool:
    """The claim is ``owner/name``. A node's repo is ``owner/name`` too, or a bare name when it was
    indexed with ``--name`` or from a checkout without an origin; a bare name matches on the name part."""
    if claim == node_repo:
        return True
    return "/" not in node_repo and claim.rsplit("/", 1)[-1] == node_repo


def verify(token: str, *, node_repo: str | None = None) -> dict[str, Any]:
    """Verified claims of a GitHub Actions OIDC token.

    Args:
        token: the compact JWT.
        node_repo: the node's repo; when non-empty the ``repository`` claim must match it.

    Raises:
        OidcError: any failed check (signature, alg, issuer, audience, exp, nbf, missing sub, repo).
    """
    import jwt  # PyJWT[crypto]; imported here so the ledger loads without it

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OidcError(f"not a JWT: {exc}") from exc
    if header.get("alg") != "RS256":
        raise OidcError(f"OIDC token alg must be RS256, got {header.get('alg')!r}")
    kid = header.get("kid")
    keys = [k for k in _jwks().get("keys", []) if isinstance(k, dict) and (kid is None or k.get("kid") == kid)]
    if not keys:
        raise OidcError(f"no signing key with kid {kid!r} in the issuer's JWKS")
    try:
        key = jwt.PyJWK(keys[0]).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience(), issuer=ISSUER, leeway=_LEEWAY,
                            options={"require": ["exp", "iss", "aud", "sub"]})
    except jwt.PyJWTError as exc:
        raise OidcError(f"OIDC token rejected: {exc}") from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise OidcError("OIDC token has no sub claim")
    if node_repo and node_repo.strip():
        repo = claims.get("repository")
        if not isinstance(repo, str) or not _repo_matches(repo, node_repo.strip()):
            raise OidcError(f"OIDC token is for repository {repo!r}, not this node's {node_repo!r}")
    return claims
