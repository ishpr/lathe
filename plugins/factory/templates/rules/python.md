---
paths:
  - "**/*.py"
---
# Python

- Never build SQL from strings. The ORM or bound parameters.
<!-- sdlc:if routes -->
- Routes: put the auth dependency on the router (`APIRouter(dependencies=[Depends(<auth>)])`), not
  on each endpoint. Public endpoints go in `.claude/public_routes.txt` with a reason. The checker
  reads the dependency names from `.claude/sdlc.json` → `auth.dependency_names`.
- Headers that must survive error responses go on `request.state` and are copied by middleware.
- Error bodies name the problem; they never carry `str(exc)` from an upstream call.
<!-- sdlc:end -->
- Tests: pytest, one behaviour per test named `test_<behaviour>_<condition>`, fixtures over
  module state, `pytest.raises` (or a 4xx/5xx where there are routes) for the negative case, a `tmp_path` or in-memory
  database, never a shared file.
- Lint and format with ruff (the post-edit hook runs them); types with mypy on the configured dirs.
