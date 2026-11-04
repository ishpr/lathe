"""Verification of a GitHub Actions OIDC token, the only thing that makes evidence binding.

What a binding row proves, and what it does not
-----------------------------------------------
The token is minted by GitHub for one job of one workflow run. After :func:`verify_token` and
:func:`check_repository` pass, the ledger knows, from claims GitHub signed:

* the run belongs to the node's repository (``repository`` equals the node's ``owner/name`` exactly);
* the job ran the trusted evidence workflow (``job_workflow_ref`` equals ``SDLC_OIDC_WORKFLOW_REF``, which
  names the file *and* the ref, e.g. ``owner/repo/.github/workflows/sdlc-evidence.yml@refs/heads/main``);
* it was triggered by ``workflow_run`` (``event_name``), so GitHub ran the default branch's definition of
  that file, never a pull request's copy.

A pull request cannot mint such a token: any workflow it adds or edits has a ``job_workflow_ref`` at the
PR's ref (``refs/pull/N/merge``) or runs on the ``pull_request`` event, and both are refused. The verified
claims (``sub``, ``repository``, ``job_workflow_ref``, ``run_id``, ``event_name`` ...) are written into the row's
payload under ``oidc`` by the ledger itself; a caller cannot supply them.

What the token cannot prove: the payload's content. ``conclusion``, ``head_sha`` and the gate's ``run_id``
are what the trusted workflow read from the ``workflow_run`` event and passed on. The ledger trusts that
workflow (reviewed, on the protected default branch) to read the gate's conclusion correctly and to check
that the gate run it reports used the default branch's gate definition. The token's own ``sha`` claim on a
``workflow_run`` job is the default branch's commit, not the PR head, so it is recorded but never compared
with the payload's ``sha``.

Configuration (ledger side, wherever ``sdlc-graph evidence add`` runs):

``SDLC_OIDC_WORKFLOW_REF`` (required for CI evidence)
    The trusted ``job_workflow_ref``; comma separated for several. ``{repository}`` in a value is replaced by
    the token's verified ``repository`` before the exact comparison, so one setting can serve every repo
    that installs the template: ``{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main``.
    Unset or empty: every ``ci:`` row is refused (fail closed).
``SDLC_OIDC_EVENTS`` (default ``workflow_run``)
    The ``event_name`` values accepted. Only the plugin's own smoke workflow widens it (to
    ``workflow_dispatch,push``); a customer ledger must never widen it, since ``pull_request`` tokens are
    exactly what this check keeps out.
``SDLC_OIDC_AUDIENCE`` (default ``sdlc-graph``)
    The audience the workflow requests.
``SDLC_OIDC_JWKS_FILE``
    A local JWKS document instead of the issuer's (the test seam, and an air-gapped ledger's option).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
DEFAULT_AUDIENCE = "sdlc-graph"
DEFAULT_EVENTS: tuple[str, ...] = ("workflow_run",)
WORKFLOW_REF_ENV = "SDLC_OIDC_WORKFLOW_REF"
EVENTS_ENV = "SDLC_OIDC_EVENTS"
FETCH_TIMEOUT = 5.0
_LEEWAY = 30  # seconds of clock skew tolerated on exp/nbf
_OWNER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")

#: The verified claims the ledger copies into a binding row's payload (``payload.oidc``), when present.
RECORDED_CLAIMS: tuple[str, ...] = ("sub", "repository", "repository_id", "repository_owner", "job_workflow_ref",
                                    "job_workflow_sha", "workflow_ref", "event_name", "ref", "sha", "run_id",
                                    "run_attempt", "actor", "jti")


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


def trusted_workflow_refs() -> tuple[str, ...]:
    """``SDLC_OIDC_WORKFLOW_REF`` split on commas. Raises OidcError when it is unset (CI evidence is refused)."""
    refs = tuple(r.strip() for r in os.environ.get(WORKFLOW_REF_ENV, "").split(",") if r.strip())
    if not refs:
        raise OidcError(f"{WORKFLOW_REF_ENV} is not set on the ledger side, so CI evidence is refused: set it to the "
                        "trusted evidence workflow's job_workflow_ref, e.g. "
                        "'{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main'")
    return refs


def allowed_events() -> tuple[str, ...]:
    events = tuple(e.strip() for e in os.environ.get(EVENTS_ENV, "").split(",") if e.strip())
    return events or DEFAULT_EVENTS


_JWKS_TTL = 300.0  # seconds a fetched issuer JWKS is reused (GitHub rotates keys far more slowly)
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _jwks() -> dict[str, Any]:
    local = os.environ.get("SDLC_OIDC_JWKS_FILE", "").strip()
    try:
        if local:
            return json.loads(Path(local).read_text(encoding="utf-8"))
        hit = _jwks_cache.get(JWKS_URL)
        if hit and time.monotonic() - hit[0] < _JWKS_TTL:
            return hit[1]
        with urllib.request.urlopen(JWKS_URL, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 (fixed https URL)
            doc = json.loads(resp.read().decode("utf-8"))
        _jwks_cache[JWKS_URL] = (time.monotonic(), doc)
        return doc
    except (OSError, ValueError) as exc:
        raise OidcError(f"cannot load the GitHub OIDC signing keys ({local or JWKS_URL}): {exc}") from exc


def require_owner_name(node_repo: str | None) -> str:
    """The node's repo when it is ``owner/name``. Raises OidcError for a bare name or an empty repo: CI
    evidence is bound to a repository exactly, and a bare ``app`` would match anyone's ``*/app``."""
    repo = (node_repo or "").strip()
    if not _OWNER_NAME.fullmatch(repo):
        raise OidcError(f"CI evidence needs the node's repo as owner/name, and this node's repo is {repo!r}: "
                        "index the repo with its origin, or set repo to owner/name")
    return repo


def verify_token(token: str) -> dict[str, Any]:
    """Verified claims of a GitHub Actions OIDC token, before anything is read from the database.

    Checks, in order: ``SDLC_OIDC_WORKFLOW_REF`` is set; RS256 signature against the issuer's JWKS; issuer,
    audience, ``exp``/``nbf``; a ``sub``; ``event_name`` in ``SDLC_OIDC_EVENTS``; ``job_workflow_ref`` exactly
    one of the trusted refs (``{repository}`` substituted with the verified claim); a ``run_id``. The
    repository is compared with the node's by :func:`check_repository`.

    Raises:
        OidcError: any failed check; the message names it.
    """
    import jwt  # PyJWT[crypto]; imported here so the ledger loads without it

    trusted = trusted_workflow_refs()  # fail closed before any network call
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
    repo = claims.get("repository")
    if not isinstance(repo, str) or not _OWNER_NAME.fullmatch(repo):
        raise OidcError(f"OIDC token has no owner/name repository claim (got {repo!r})")
    event = claims.get("event_name")
    if event not in allowed_events():
        raise OidcError(f"OIDC token is from a {event!r} run; CI evidence is accepted only from "
                        f"{', '.join(allowed_events())} runs of the trusted evidence workflow")
    ref = claims.get("job_workflow_ref")
    accepted = {t.replace("{repository}", repo) for t in trusted}
    if not isinstance(ref, str) or ref not in accepted:
        raise OidcError(f"OIDC token is from workflow {ref!r}, not the trusted evidence workflow "
                        f"({WORKFLOW_REF_ENV}={', '.join(sorted(accepted))})")
    run_id = claims.get("run_id")
    if not (isinstance(run_id, (str, int)) and not isinstance(run_id, bool) and str(run_id).isdigit()):
        raise OidcError("OIDC token has no run_id claim")
    return claims


def check_repository(claims: dict[str, Any], node_repo: str | None) -> None:
    """The token's ``repository`` claim is exactly the node's ``owner/name``.

    Raises:
        OidcError: the node's repo is bare or empty (:func:`require_owner_name`), or the claim differs.
    """
    repo = require_owner_name(node_repo)
    if claims.get("repository") != repo:
        raise OidcError(f"OIDC token is for repository {claims.get('repository')!r}, not this node's {repo!r}")


def verify(token: str, *, node_repo: str | None) -> dict[str, Any]:
    """:func:`verify_token` then :func:`check_repository`; ``node_repo`` is required (no default), so no
    caller can skip the repository binding by leaving it out."""
    require_owner_name(node_repo)
    claims = verify_token(token)
    check_repository(claims, node_repo)
    return claims


def recorded(claims: dict[str, Any]) -> dict[str, Any]:
    """The subset of verified claims a binding row stores under ``payload.oidc``."""
    return {k: claims[k] for k in RECORDED_CLAIMS if k in claims}
