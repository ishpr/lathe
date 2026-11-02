# sample-app

A deliberately imperfect FastAPI service. It is the target of the lathe demo, the bash harness
and the graph tests. Everything runs locally: SQLite through SQLAlchemy, and an in-memory dict
standing in for Redis.

    uv run pytest
    uv run uvicorn app.main:app --port 8001

## Planted findings (the pack should detect each)

| Finding | Where |
|---|---|
| 6 routes, only one with the auth dependency | `app/routes/items.py`, `app/routes/users.py` |
| An `APIRouter` defined but never included | `app/routes/admin.py` |
| A module-level dict mutated on a request path | `app/cache.py` |
| An `os.environ` read outside `app/config.py` | `app/routes/items.py` |
| A credential-named query parameter on a login route | `app/routes/users.py` (`password`) |
| A TODO without a ticket id | `app/routes/items.py` |
| An f-string SQL statement | `app/db.py` |
| A frontend call to a route that does not exist | `frontend/src/api.js` |
| An assertion-free test | `tests/test_items.py` |
