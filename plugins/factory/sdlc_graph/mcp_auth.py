"""OAuth resource-server auth for the MCP server's Streamable HTTP transport.

The server is a protected resource, not an authorization server: it verifies bearer JWTs issued by a configured
issuer (checks signature against the issuer's JWKS, ``iss``, ``aud``, ``exp``, ``nbf`` and the required scopes) and the
SDK serves the RFC 9728 protected-resource metadata (``/.well-known/oauth-protected-resource/mcp``) that points clients
at the issuer, so managed settings can allow-list the server. stdio is unaffected.

Environment (all read by :func:`from_env`):
    SDLC_MCP_OAUTH_ISSUER      issuer URL (required to enable auth); tokens' ``iss`` must equal it
    SDLC_MCP_OAUTH_AUDIENCE    expected ``aud`` (required to enable auth)
    SDLC_MCP_OAUTH_SCOPES      space or comma separated scopes every token must carry (default none)
    SDLC_MCP_OAUTH_JWKS_URL    signing keys (default ``<issuer>/.well-known/jwks.json``)
    SDLC_MCP_OAUTH_JWKS_FILE   a local JWKS document instead of the URL (air-gapped, and the test seam)
    SDLC_MCP_RESOURCE_URL      this server's public URL for the metadata (default: the audience when it is a URL)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]  # asymmetric only: never HS*, never none
_LEEWAY = 30


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
    _extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

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
    return AuthConfig(issuer=issuer, audience=audience, resource_url=resource, scopes=scopes, jwks_url=jwks_url,
                      jwks_file=(e.get("SDLC_MCP_OAUTH_JWKS_FILE") or "").strip())


class JwtVerifier:
    """``mcp.server.auth.provider.TokenVerifier``: a valid token becomes an AccessToken, anything else None."""

    def __init__(self, cfg: AuthConfig) -> None:
        self.cfg = cfg
        self._client: Any = None

    def _signing_key(self, token: str) -> Any:
        import jwt

        if self.cfg.jwks_file:
            kid = jwt.get_unverified_header(token).get("kid")
            keys = [k for k in json.loads(Path(self.cfg.jwks_file).read_text(encoding="utf-8")).get("keys", [])
                    if isinstance(k, dict) and (kid is None or k.get("kid") == kid)]
            if not keys:
                raise jwt.PyJWTError(f"no key with kid {kid!r}")
            return jwt.PyJWK(keys[0]).key
        if self._client is None:
            self._client = jwt.PyJWKClient(self.cfg.jwks_url, cache_keys=True, timeout=5)
        return self._client.get_signing_key_from_jwt(token).key

    async def verify_token(self, token: str) -> AccessToken | None:
        import jwt

        try:
            claims = jwt.decode(token, self._signing_key(token), algorithms=ALGORITHMS, audience=self.cfg.audience,
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
