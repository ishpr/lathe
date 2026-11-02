# Per-key rate limiting: 429 over 100 req/min per key, with X-RateLimit headers

The autonomy blocks of the sample app's rate-limit contract (the repository's docs/DESIGN.md
section 14). The demo attaches them at draft time (`sdlc-graph node draft --contract`); the `ready`
guard refuses a contract until both are present.

## Decisions

None.

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.
- Per contract: follow the dependency style of app/auth.py (a FastAPI dependency on the router).
- Per contract: record every default applied in .claude/work/rate-limit/assumptions.md (default_applied, where, why).

## Capabilities

- paths: `app/auth.py`, `app/ratelimit/**`, `app/main.py`, `app/routes/**`, `app/cache.py`, `tests/**`, `README.md`
- commands: `python3 -m pytest tests/ratelimit -q`, `bash scripts/smoke_rate_limit.sh`
- egress: `pypi.org`, `files.pythonhosted.org`
- data: `cache:rw`, `sqlite:ro`
