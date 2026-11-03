# Per-key rate limiting: 429 over 100 req/min per key, with X-RateLimit headers

The autonomy blocks of the sample app's rate-limit contract (the repository's docs/DESIGN.md
section 14). The demo attaches them at draft time (`sdlc-graph node draft --contract`); the `ready`
guard refuses a contract until both are present.

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|---|---|---|---|---|
| 1 | What the limiter keys on, given that the sample app's auth accepts a single API_KEY | raw X-API-Key header · an API key registry | raw X-API-Key header | raw X-API-Key header | @live |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.
- Key identity (D1): the limiter keys on the raw X-API-Key header, counted before auth, so a second header value has its own counter; do not add more keys to app/auth.py.
- AC4 (key B's first request is 2xx): auth accepts one key, so the test makes key B the valid key for its own requests (monkeypatch app.auth.API_KEY) after key A is exhausted; the limiter keeps the two header values apart.
- AC6 (a 404 that carries the headers): the 404 comes from a route behind the limiter; add GET /items/{item_id} to app/routes/items.py, returning 404 for a missing item, with the auth and limiter dependencies.
- Per contract: follow the dependency style of app/auth.py (a FastAPI dependency on the router).
- Per contract: record every default applied in .claude/work/rate-limit/assumptions.md (default_applied, where, why, decision).

## Capabilities

- paths: `app/auth.py`, `app/config.py`, `app/ratelimit/**`, `app/main.py`, `app/routes/**`, `app/cache.py`, `tests/**`, `README.md`
- commands: `uv run pytest tests/ratelimit -q`, `bash scripts/smoke_rate_limit.sh`
- egress: `pypi.org`, `files.pythonhosted.org`
- data: `cache:rw`, `sqlite:ro`
