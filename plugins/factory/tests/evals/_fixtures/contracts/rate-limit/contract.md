# Contract: rate-limit

## Outcome

Each API key may create at most 100 items per minute; a caller over the limit gets 429 and every response tells it where it stands.

## Acceptance criteria

1. `test_under_limit_has_headers` — the first `POST /items` with a valid key returns 200 with `X-RateLimit-Limit: 100` and `X-RateLimit-Remaining: 99`.
2. `test_over_limit_returns_429` — with the limit set to 3, the 4th `POST /items` in one minute returns 429, body `{"detail": "rate limit exceeded"}` and `X-RateLimit-Remaining: 0`. *(negative)*
3. `test_window_resets_next_minute` — with the limit set to 1 and the clock moved to the next minute after the limit is reached, the next `POST /items` returns 200. *(negative)*

## Acceptance

- $ python3 -m pytest tests/ratelimit -q
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| D1 | Fixed or sliding window? | fixed one-minute window · sliding window | **fixed**: simplest, and the reset header is exact | fixed one-minute window | @maria |
| D2 | What the limiter keys on | the raw `x-api-key` header · a key registry | **raw header**: the app has one key today | the raw x-api-key header | @maria |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- The limiter lives in `app/ratelimit/limiter.py` and exposes: `LIMIT = 100` (requests per minute, a module constant tests may monkeypatch), `now() -> float` (the clock, seconds; tests monkeypatch it), `reset() -> None` (forget every count; tests call it before each test) and `rate_limit(...)`, a FastAPI dependency.
- `POST /items` in `app/routes/items.py` takes `rate_limit` as a second dependency after `api_key_auth`.
- The window is the clock minute: `int(now() // 60)`.
- Counts live in this process, in a module-level dict marked `# state: ok <reason>` for the module-state checker; a shared store is a follow-up.
- The tests for this contract go in `tests/ratelimit/test_limiter.py` and use the `client` fixture from `tests/conftest.py`.

## Capabilities

- Paths: app/ratelimit/**, app/routes/items.py, tests/ratelimit/**
- Commands: python3 -m pytest -q, python3 -m ruff check, python3 -m mypy
- Egress: none
- Data: none

## Out of scope

- `app/auth.py`, `app/config.py`, `app/cache.py` and every route other than `POST /items`.

## Allowed paths

- `app/ratelimit/**`
- `app/routes/items.py`
- `tests/ratelimit/**`

## Risk tier

low — a new module and one dependency on one route; no tier-high path.

## Evidence plan

- The three tests above, frozen, passing; `python3 -m pytest tests/ratelimit -q` exits 0.
