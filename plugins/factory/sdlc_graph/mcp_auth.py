"""OAuth resource-server auth for the MCP server's Streamable HTTP transport.

The server is a protected resource, not an authorization server: it verifies bearer JWTs issued by a configured
issuer (checks signature against the issuer's JWKS, ``iss``, ``aud``, ``exp``, ``nbf`` and the required scopes) and the
SDK serves the RFC 9728 protected-resource metadata (``/.well-known/oauth-protected-resource/mcp``) that points clients
at the issuer, so managed settings can allow-list the server. stdio is unaffected.

Environment (all read by :func:`from_env`):
    SDLC_MCP_OAUTH_ISSUER      issuer URL (required to enable auth); tokens' ``iss`` must equal it
    SDLC_MCP_OAUTH_AUDIENCE    expected ``aud`` (required to enable auth)
    SDLC_MCP_OAUTH_SCOPES      space or comma separated scopes every token must carry (default none)
    SDLC_MCP_OAUTH_WRITE_SCOPES  scopes of which a token must carry at least one to call a write tool (default
                               ``graph:write``; ``none`` switches the check off, which a server open to more than
                               trusted operators should not do). Read tools only need a valid token.
    SDLC_MCP_OAUTH_JWKS_URL    signing keys (default ``<issuer>/.well-known/jwks.json``)
    SDLC_MCP_OAUTH_JWKS_FILE   a local JWKS document instead of the URL (air-gapped, and the test seam)
    SDLC_MCP_RESOURCE_URL      this server's public URL for the metadata (default: the audience when it is a URL)

Signing keys are fetched off the event loop (``asyncio.to_thread``) and cached. A token whose ``kid`` is not in
the cache triggers at most one refetch per ``REFETCH_INTERVAL`` seconds; any other unknown ``kid`` fails at once,
so a stream of tokens with random ``kid`` values costs no network calls and never stalls the loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"]  # asymmetric only: never HS*, never none
REFETCH_INTERVAL = 60.0  # at most one JWKS fetch per this many seconds on a kid miss
FETCH_TIMEOUT = 5.0
_LEEWAY = 30


DEFAULT_WRITE_SCOPES: tuple[str, ...] = ("graph:write",)


class AuthConfigError(ValueError):
    """The OAuth environment is incomplete or malformed."""


@dataclass(frozen=True)
class AuthConfig:
    issuer: str
    audience: str
    resource_url: str
    scopes: tuple[str, ...] = ()
    jwks_url: str = ""
    jwks_file: str = ""
    write_scopes: tuple[str, ...] = DEFAULT_WRITE_SCOPES

    def settings(self) -> AuthSettings:
        return AuthSettings(issuer_url=self.issuer, resource_server_url=self.resource_url,
                            required_scopes=list(self.scopes) or None, validate_token_resource=True)


def from_env(env: dict[str, str] | None = None, *, host: str = "127.0.0.1", port: int = 8765) -> AuthConfig | None:
    """The configured :class:`AuthConfig`, or None when neither issuer nor audience is set.

    Raises:
        AuthConfigError: only one of issuer and audience is set, or a URL is not http(s).
    """
    e = os.environ if env is None else env
    issuer = (e.get("SDLC_MCP_OAUTH_ISSUER") or "").strip()
    audience = (e.get("SDLC_MCP_OAUTH_AUDIENCE") or "").strip()
    if not issuer and not audience:
        return None
    if not issuer or not audience:
        raise AuthConfigError("set both SDLC_MCP_OAUTH_ISSUER and SDLC_MCP_OAUTH_AUDIENCE (or neither)")
    resource = (e.get("SDLC_MCP_RESOURCE_URL") or "").strip() or (audience if audience.startswith("http") else f"http://{host}:{port}")
    for name, url in (("SDLC_MCP_OAUTH_ISSUER", issuer), ("SDLC_MCP_RESOURCE_URL", resource)):
        if not re.match(r"^https?://[^\s]+$", url):
            raise AuthConfigError(f"{name} must be an http(s) URL, not {url!r}")
    scopes = tuple(s for s in re.split(r"[\s,]+", e.get("SDLC_MCP_OAUTH_SCOPES") or "") if s)
    jwks_url = (e.get("SDLC_MCP_OAUTH_JWKS_URL") or "").strip() or issuer.rstrip("/") + "/.well-known/jwks.json"
    raw_write = (e.get("SDLC_MCP_OAUTH_WRITE_SCOPES") or "").strip()
    write_scopes = (DEFAULT_WRITE_SCOPES if not raw_write else
                    () if raw_write.lower() == "none" else tuple(s for s in re.split(r"[\s,]+", raw_write) if s))
    return AuthConfig(issuer=issuer, audience=audience, resource_url=resource, scopes=scopes, jwks_url=jwks_url,
                      write_scopes=write_scopes,
                      jwks_file=(e.get("SDLC_MCP_OAUTH_JWKS_FILE") or "").strip())


class JwtVerifier:
    """``mcp.server.auth.provider.TokenVerifier``: a valid token becomes an AccessToken, anything else None."""

    def __init__(self, cfg: AuthConfig, *, clock: Any = time.monotonic) -> None:
        self.cfg = cfg
        self._clock = clock
        self._keys: dict[str | None, Any] = {}   # kid -> PyJWK, from the last fetch
        self._fetched_at: float | None = None
        self._lock = threading.Lock()
        self.fetches = 0  # how many times the JWKS was loaded (tests read it)

    def _load(self) -> dict[str, Any]:
        if self.cfg.jwks_file:
            return json.loads(Path(self.cfg.jwks_file).read_text(encoding="utf-8"))
        import urllib.request

        with urllib.request.urlopen(self.cfg.jwks_url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 (configured URL)
            return json.loads(resp.read().decode("utf-8"))

    def _refresh(self) -> None:
        """Reload the key set unless one was loaded less than REFETCH_INTERVAL ago. Blocking: call it off the loop."""
        import jwt

        with self._lock:
            now = self._clock()
            if self._fetched_at is not None and now - self._fetched_at < REFETCH_INTERVAL:
                return
            self._fetched_at = now  # set before the fetch: a failing issuer is not retried in a tight loop
            self.fetches += 1
            keys: dict[str | None, Any] = {}
            for k in self._load().get("keys", []):
                if not isinstance(k, dict):
                    continue
                try:
                    keys[k.get("kid")] = jwt.PyJWK(k)
                except jwt.PyJWTError:
                    continue  # an unusable key (unsupported kty) never takes the others down
            self._keys = keys

    def _cached(self, kid: str | None) -> Any:
        if kid is None:
            return next(iter(self._keys.values()), None) if len(self._keys) == 1 else None
        return self._keys.get(kid)

    async def _signing_key(self, token: str) -> Any:
        import jwt

        kid = jwt.get_unverified_header(token).get("kid")
        key = self._cached(kid)
        if key is None:
            await asyncio.to_thread(self._refresh)  # the fetch never runs on the event loop
            key = self._cached(kid)
        if key is None:
            raise jwt.PyJWTError(f"no signing key with kid {kid!r}")
        return key.key

    async def verify_token(self, token: str) -> AccessToken | None:
        import jwt

        try:
            claims = jwt.decode(token, await self._signing_key(token), algorithms=ALGORITHMS, audience=self.cfg.audience,
                                issuer=self.cfg.issuer, leeway=_LEEWAY, options={"require": ["exp", "iss", "aud", "sub"]})
        except (jwt.PyJWTError, OSError, ValueError):
            return None
        raw = claims.get("scope", claims.get("scp", ""))
        scopes = raw.split() if isinstance(raw, str) else [str(s) for s in raw or []]
        aud = claims.get("aud")
        resource = self.cfg.resource_url if (aud == self.cfg.audience or (isinstance(aud, list) and self.cfg.audience in aud)) else None
        return AccessToken(token=token, client_id=str(claims.get("azp") or claims.get("client_id") or claims["sub"]),
                           scopes=scopes, expires_at=int(claims["exp"]), resource=resource, subject=str(claims["sub"]),
                           claims=claims)
