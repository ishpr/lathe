# Contract: item-get

## Outcome

A caller with a valid API key can fetch one item by id at `GET /items/{item_id}`; a missing item is a clear 404.

## Acceptance criteria

1. `test_get_item_returns_item` — after creating an item, `GET /items/{id}` with `x-api-key: dev-key` returns 200 and `{"id": <id>, "name": <name>}`.
2. `test_get_missing_item_returns_404` — `GET /items/999` with a valid key returns 404 with body `{"detail": "item not found"}`. *(negative)*
3. `test_get_item_requires_key` — `GET /items/{id}` without `x-api-key` returns 401. *(negative)*

## Acceptance

- $ python3 -m pytest tests/items -q
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| D1 | Does reading one item need the API key, like creating one? | key required · public like GET /items | **key required**: the item may later carry private fields | key required | @maria |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- The route lives in `app/routes/items.py` beside the other item routes and uses the `api_key_auth` dependency the way `POST /items` does.
- The tests for this contract go in `tests/items/test_get_item.py` and use the `client` fixture from `tests/conftest.py`.
- Error responses use FastAPI's `HTTPException`, so the body is `{"detail": "..."}`.

## Capabilities

- Paths: app/routes/items.py, tests/items/**
- Commands: python3 -m pytest -q, python3 -m ruff check, python3 -m mypy
- Egress: none
- Data: sqlite:rw (the test database only)

## Out of scope

- `app/routes/admin.py`, `app/routes/users.py`, `app/auth.py` and the existing item routes' behaviour.

## Allowed paths

- `app/routes/items.py`
- `tests/items/**`

## Risk tier

low — one read-only route behind the existing auth dependency; no tier-high path.

## Evidence plan

- The three tests above, frozen, passing; `python3 -m pytest tests/items -q` exits 0.
