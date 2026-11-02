"""Embedding providers: ``openai``, ``voyage`` and ``none``. Standard library only (urllib).

Every vector is 1024-dimensional to fit ``chunk.embedding vector(1024)``:

* ``openai``: ``text-embedding-3-small`` with the API's ``dimensions=1024`` parameter.
* ``voyage``: ``voyage-4-lite`` (or ``voyage-code-4`` via ``VOYAGE_MODEL``) with ``output_dimension=1024``.
* ``none``: no provider; :meth:`NoneProvider.embed` raises :class:`ProviderUnavailable`.

Selection (:func:`get_provider`): the ``EMBED_PROVIDER`` environment variable, else ``openai`` when an
OpenAI key is found, else ``none``. Keys come from the environment (``OPENAI_API_KEY``,
``VOYAGE_API_KEY``) or, failing that, from the gitignored ``plugins/graph/.env``. Only those two key
names are ever read from the file. A key is never logged, returned in a result or put in an error message.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

DIMENSIONS = 1024
OPENAI_URL = "https://api.openai.com/v1/embeddings"
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_VOYAGE_MODEL = "voyage-4-lite"
PROVIDERS = ("openai", "voyage", "none")
KEY_NAMES = ("OPENAI_API_KEY", "VOYAGE_API_KEY")
#: Longest text sent per chunk (characters); the providers cap inputs at several thousand tokens.
MAX_CHARS = 6000


class ProviderUnavailable(RuntimeError):
    """No usable provider: ``none`` selected, or the provider's key is missing."""


class ProviderError(RuntimeError):
    """The provider's API failed (HTTP error, bad payload, wrong dimension). Never contains a key."""


# --------------------------------------------------------------------------- keys


def env_file() -> Path:
    """``plugins/graph/.env`` (gitignored), two levels above this file's package directory."""
    return Path(__file__).resolve().parents[2] / ".env"


def _read_env_file(name: str, path: Path | None = None) -> str | None:
    """Value of ``name`` in the .env file, or None. Reads only the line for ``name``."""
    path = path or env_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = re.match(rf"\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*?)\s*$", line)
        if m:
            value = m.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


def find_key(name: str, *, env: dict[str, str] | None = None, path: Path | None = None) -> str | None:
    """The key ``name`` from ``env`` (default ``os.environ``), else from the .env file, else None."""
    if name not in KEY_NAMES:
        raise ValueError(f"{name!r} is not an embedding key name")
    value = (env if env is not None else os.environ).get(name)
    return value.strip() if value and value.strip() else _read_env_file(name, path)


# --------------------------------------------------------------------------- providers


def _post_json(url: str, payload: dict[str, Any], key: str, *, timeout: float = 60.0, retries: int = 3,
               opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """POST JSON with a bearer key; retry 429 and 5xx with backoff. Errors never echo the key."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "sdlc-graph"})
    open_url = opener or urllib.request.urlopen
    last = "no attempt made"
    for attempt in range(retries):
        try:
            with open_url(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            last = f"HTTP {exc.code} {body}".strip()
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"network error: {type(exc).__name__}"
        except json.JSONDecodeError:
            last = "response was not JSON"
            break
        if attempt + 1 < retries:
            sleep(1.5 * (2 ** attempt))
    raise ProviderError(last.replace(key, "***"))


class Provider:
    """Base class. ``model`` is the stored ``embed_model`` tag; vectors are always :data:`DIMENSIONS` long."""

    name = "base"
    model = ""
    batch_size = 64

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        """One vector per text, in order. ``query=True`` marks search queries (Voyage embeds them differently)."""
        raise NotImplementedError

    def _check(self, vectors: list[list[float]], n: int) -> list[list[float]]:
        if len(vectors) != n or any(len(v) != DIMENSIONS for v in vectors):
            raise ProviderError(f"{self.name}: expected {n} vectors of {DIMENSIONS} dimensions")
        return vectors


class NoneProvider(Provider):
    name = "none"
    model = "none"

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        raise ProviderUnavailable("no embedding provider configured: set OPENAI_API_KEY (or EMBED_PROVIDER=voyage "
                                  "with VOYAGE_API_KEY); nothing is embedded without a key")


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, key: str, model: str = DEFAULT_OPENAI_MODEL, *, url: str = OPENAI_URL,
                 opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._key, self._url, self._opener, self._sleep = key, url, opener, sleep
        self.model = f"openai:{model}:{DIMENSIONS}"
        self._api_model = model

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t[:MAX_CHARS] or " " for t in texts[i:i + self.batch_size]]
            body = _post_json(self._url, {"model": self._api_model, "input": batch, "dimensions": DIMENSIONS,
                                          "encoding_format": "float"}, self._key,
                              opener=self._opener, sleep=self._sleep)
            rows = sorted(body.get("data") or [], key=lambda r: r.get("index", 0))
            out.extend(self._check([r.get("embedding") or [] for r in rows], len(batch)))
        return out


class VoyageProvider(Provider):
    name = "voyage"
    batch_size = 64

    def __init__(self, key: str, model: str = DEFAULT_VOYAGE_MODEL, *, url: str = VOYAGE_URL,
                 opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._key, self._url, self._opener, self._sleep = key, url, opener, sleep
        self.model = f"voyage:{model}:{DIMENSIONS}"
        self._api_model = model

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t[:MAX_CHARS] or " " for t in texts[i:i + self.batch_size]]
            body = _post_json(self._url, {"model": self._api_model, "input": batch,
                                          "input_type": "query" if query else "document",
                                          "output_dimension": DIMENSIONS}, self._key,
                              opener=self._opener, sleep=self._sleep)
            rows = sorted(body.get("data") or [], key=lambda r: r.get("index", 0))
            out.extend(self._check([r.get("embedding") or [] for r in rows], len(batch)))
        return out


def get_provider(name: str | None = None, *, env: dict[str, str] | None = None,
                 path: Path | None = None) -> Provider:
    """Pick the provider: ``name``, else ``EMBED_PROVIDER``, else ``openai`` if its key is found, else ``none``.

    A named provider whose key is missing resolves to :class:`NoneProvider` too (embedding then raises
    :class:`ProviderUnavailable` with the instruction), so callers can always ask "is there a provider?".

    Raises:
        ValueError: an unknown provider name.
    """
    environ = env if env is not None else os.environ
    chosen = (name or environ.get("EMBED_PROVIDER") or "").strip().lower()
    if not chosen:
        chosen = "openai" if find_key("OPENAI_API_KEY", env=environ, path=path) else "none"
    if chosen not in PROVIDERS:
        raise ValueError(f"unknown embedding provider {chosen!r}; valid: {', '.join(PROVIDERS)}")
    if chosen == "openai":
        key = find_key("OPENAI_API_KEY", env=environ, path=path)
        return OpenAIProvider(key, environ.get("OPENAI_EMBED_MODEL") or DEFAULT_OPENAI_MODEL) if key else NoneProvider()
    if chosen == "voyage":
        key = find_key("VOYAGE_API_KEY", env=environ, path=path)
        return VoyageProvider(key, environ.get("VOYAGE_MODEL") or DEFAULT_VOYAGE_MODEL) if key else NoneProvider()
    return NoneProvider()


def available(provider: Provider) -> bool:
    """True unless ``provider`` is the ``none`` provider."""
    return not isinstance(provider, NoneProvider)
