# Onboarding report

## Stack

- Stack: python (uv). No stack marker at the repo root; found below it, project `plugins/factory`. No other stacks reported.
- App paths (`.claude/sdlc.json`): `plugins/factory/sdlc_graph/**`. The human confirmed widening to `plugins/factory/{sdlc_graph,scripts,lib,hooks}/**` (their own edit to the protected `sdlc.json`); the audit used the widened set.
- Test paths: `plugins/factory/tests/**`.
- Test command: `uv run --project plugins/factory pytest -q plugins/factory/tests`.
- `paths.protected`: `[]` (confirmed empty).
- Note: `tier_high_paths` names `plugins/factory/app/auth/**`, `app/models/**` and `alembic/**`, which do not exist.

System map (top-level packages under `plugins/factory`):

- `hooks/`: 10 hook modules registered in `hooks.json` (guard_agent_spawn, guard_bash, guard_edit, observe_capabilities, post_edit, record_agent_run, record_model_switch, session_start, stop_check).
- `lib/`: sdlc, e2e, frontend_foldkit, skeleton, test_reasons.
- `scripts/`: init_project, ratchet, verify, security_assess, maturity, graph, freeze_tests, acceptance, baseline, check_*, and `checkers/{python,node,go,rust}`.
- `sdlc_graph/`: cli, mcp_server, mcp_auth, db, codegraph/*, ledger/*, orchestrator/*, index/*, spanner/*.
- `demo/`.
- Dependencies (from module external_imports; `lib` is loaded via sys.path, so graph import edges are empty): hooks -> lib/sdlc (guard_bash also skeleton); scripts and checkers -> lib/sdlc (verify -> ratchet, e2e, frontend_foldkit, test_reasons; init_project -> e2e, frontend_foldkit; maturity -> check_commit_message); lib/sdlc -> lib/e2e; sdlc_graph modules -> sdlc_graph, psycopg.
- Entry points: `hooks.json`, the MCP server (stdio, optional HTTP), the `sdlc-graph` CLI, and scripts invoked by path from skills. No HTTP routes in the graph.
- Tests: `plugins/factory/tests/{sdlc,graph,epic,e2e}`.

Checks this stack gets: route auth, module state, test quality; lint and types from the stack's own tools when installed.

## Baseline

`.claude/baseline.json` was not recorded: the sandbox forbids the ratchet's write. A human runs, outside the sandbox, and commits the file:

`python3 -I /Users/ishprasad/.claude/plugins/cache/lathe/factory/1.0.6/scripts/ratchet.py --root /Users/ishprasad/caylent/demo/lathe --update --require-tools`

Total keys and violations by key prefix: unknown (no baseline). For context only, not a baseline: the services auditor's `ratchet.py --all` run printed 1251 violations, 600 new keys, 0 grown. The `dead|`, `stubs|`, `tests|` and `contract|` checks were scoped to `plugins/factory/sdlc_graph/**` only; `lint|` and `types|` were repo-wide.

## Graph

- On, provider local (`postgresql://***@localhost:5435/sdlc`), schema 013.
- Indexed sha `b7da2cfa29b2c240c706d36a0f63691d10b9d904` as a delta snapshot (id 4, from base 76e51be): 3996 symbols, 12743 edges, 0 owners. verify-snapshot ok (no missing, extra or changed).
- Repo node: `repo-ishpr-lathe`.

## Audit findings

Entries below are quoted from the five audit tables (routes and auth, services and workers, tests, docs vs code, TODO ages). The same file:line in two tables is one entry. Checks that found nothing, and so have no entry: hook registration vs files; webhooks; identity from request body; secrets in a handler; SQL from strings; unmounted MCP tools; route auth (no HTTP routers); unused lib/hooks/scripts modules other than `protect_main.sh`; shell=True and secrets in handler under `sdlc_graph`; existing-only tests; env var, MCP tool and CLI, and scripts and commands docs; `contract|` printed no keys. The graph has no HTTP routes, so the entries under "Routes without auth" concern the MCP server and CLI.

### Routes without auth

- medium, `sdlc_graph/mcp_server.py:493-498,191`: `--http --insecure-no-auth` serves every write tool (contract_transition, evidence_add, decision_resolve) unauthenticated on loopback. Fix: require a per-process secret or stdio only.
- medium, `sdlc_graph/cli.py:540,548,580,590,624,628`: `--actor` is self-asserted; `@handle` maps to a human actor_class, so anyone with DB access can perform human-only transitions. Fix: bind to a verifiable identity or document the DB as the trust boundary.
- medium, `sdlc_graph/mcp_auth.py:80-110` (`from_env`): returns None when issuer/audience are unset; caller untraced, may mean an unauthenticated MCP server. Fix: confirm fail-closed on non-loopback and add a test.
- low, `sdlc_graph/mcp_auth.py:154-161`: OAuth verifier is sound; `SDLC_MCP_OAUTH_WRITE_SCOPES=none` disables the write-scope check. Fix: warn at startup.

### Env reads outside config

- low, `sdlc_graph/db.py:61,118` and others: 38 `stubs|` env reads outside config, at connect time; about 35 more in cli.py, oidc.py, config.py, status.py; `env_access_allowed` is `[]`. Fix: move into graphcfg, allowlist config modules or centralize.

### Stubs and TODOs without a ticket

- medium, `sdlc_graph/ledger/lifecycle.py:69-77,119`: parent_stage stub raises NotImplementedError, the caller returns `{"todo":"parent_stage"}`; `TODO(product)` (DESIGN 16.1) has no ticket id (TODO-NO-ID), arrived 2026-11-03 (722b0a6), about 1 day old, the only TODO under app paths. Fix: decide and implement or remove; add a ticket id; remove the xfail at `tests/graph/test_lifecycle.py:163`.
- medium, `sdlc_graph/cli.py:80-84,1020`: `index` is registered as `not_implemented()`, always exit 3. Fix: wire or delete.
- medium, `sdlc_graph/index/embed.py:121`: `Provider.embed` is a bare NotImplementedError base. Fix: `abc.abstractmethod`.

### Other findings

Security and code:

- medium, `sdlc_graph/db.py:31`, `sdlc_graph/graphcfg.py:31`: hardcoded `DEFAULT_DATABASE_URL` with `sdlc:sdlc` credential fallback, duplicated. Fix: fail closed unless configured; one definition.
- medium, `scripts/ratchet.py:204`, `scripts/verify.py:308`, `scripts/tests_failing_first.py:125`, `lib/e2e.py:257,266,310`, `hooks/post_edit.py:42`: shell=True runs commands from the repo's `.claude/sdlc.json`; a compromised checkout runs arbitrary code when a hook fires (security SEC-01..07). Fix: treat sdlc.json as trusted and refuse when untracked or changed; argv lists.
- medium, `sdlc_graph/db.py:248-256` (also `db.py:61`): `use_schema` writes `os.environ["SDLC_SCHEMA"]` process-globally; concurrent callers cross schemas. Fix: explicit schema param or ContextVar.
- medium, `sdlc_graph/ledger/runs.py:278,287`: SQL f-strings from `where`/`cols` keys; cols source untraced; values parameterized. Fix: column allowlist and a test.
- medium, `sdlc_graph/ledger/oidc.py:113` (`_jwks_cache`), `sdlc_graph/orchestrator/dispatch.py:54` (`CHILDREN`): module-level mutable state on a request path. Fix: interface with injected clock or store.
- medium, `.claude/sdlc.json` `paths.app` / `tier_high_paths`: app omitted scripts/lib/hooks (since widened by the human, see Stack); `tier_high_paths` names non-existent paths. Fix: point at real paths.
- medium, `sdlc_graph/codegraph/queries.py:402`: the real finding is still open as medium (known issue: the security merge's rejection of SEC-27, `sdlc_graph/spanner/export.py:285`, carries a reason that describes `queries.py:402`, which the merge renumbered; the `export.py:285` rejection is unverified).
- low, `sdlc_graph/codegraph/queries.py:34` (also 19): S608 f-string interpolates only the constant `_SYM_COLS`; no injection. Fix: noqa with a reason.
- low, `hooks/guard_bash.py:43,2055`, `hooks/guard_edit.py:70`, `lib/sdlc.py:852`, `hooks/observe_capabilities.py:323`: lint debt; blind or broad except and try-except-pass in guards may fail open; `hooks.json` `onFailure: block` mitigates. Fix: narrow excepts, log, state the fail mode.
- low, `sdlc_graph/codegraph/appscan.py:1`, `extract_deps.py:1` and about 53 more (55 `dead|` keys): string-registered (EXTRACTORS) false positives, 5 confirmed. Fix: teach the checker or baseline.
- low, `sdlc_graph/orchestrator/fake_claude.py`, `fake_executor.py`, `fake_gh.py`: test doubles in the app package, used via `python -m` strings. Fix: move under tests/ or baseline.
- low, `scripts/protect_main.sh`: nothing calls it; only READMEs name it. Fix: wire in or delete.
- low, repo-wide `lint|`/`types|` (1251 total; FURB167 191, PLW1510 132, UP031 132): real faults include `scripts/checkers/python/api_contract.py:28-29` F811, psycopg import-not-found, `extract_python.py:265` None attr. Fix: `ruff --fix`, then by hand.

Tests:

- medium, `plugins/factory/tests/sdlc/test_ratchet.py:1`: 39 tests, no negative case for the gate. Fix: malformed baseline, unknown check, grown violation tests.
- medium, `plugins/factory/tests/sdlc/test_observe_capabilities.py:1`: 9 tests, no negative case. Fix: malformed input, non-blocking failure.
- medium, `plugins/factory/tests/graph/test_orchestrator_isolation.py:1`: 5 tests, no negative case on isolation. Fix: escape attempt refused.
- medium, `plugins/factory/tests/graph/test_metrics_measure.py:1`, `test_metrics_survival.py:1`: 8 and 4 tests, no negative case. Fix: invalid input, bad window.
- medium, `plugins/factory/tests/graph/test_lifecycle_parity.py:1`, `test_integration.py:1`, `test_orchestrator_live.py:1`: 3, 3 and 1 tests, no negative case. Fix: illegal transition or failure case.
- low, `plugins/factory/tests/epic/test_lint_epic.py:1`: 19 tests, no negative case (and 18 `tests|` keys). Fix: a bad epic yields a finding.
- low, `tests/sdlc/test_init_epics.py`, `test_other_stacks.py`, `test_real_paths.py`, `test_skill_form.py`: 7, 3, 2 and 4 tests, no negative case.
- low, `tests/graph/test_repo_node.py`, `test_extract_frontend.py`, `test_demo_guard.py`: 3, 6 and 1 tests, no negative case.
- low, `tests/graph/test_guards_lifecycle.py:260`, `test_review_pack.py:30`: "asserts nothing" is a false positive (validate raises). Optional.
- low, `examples/sample-app/tests/test_items.py:37`: asserts nothing (outside app and test paths). Fix: add an assertion.

Docs vs code:

- medium, `plugins/factory/README.md:6`: says Version 0.5.0; plugin.json and marketplace.json say 1.0.5. Fix: drop the version.
- medium, `plugins/factory/README.md:48`, `README.md:48`, `plugins/factory/skills/README.md:179`, `plugins/factory/docs/graph.md:171`: name a `/factory:contract` skill that does not exist (replaced by design). Fix: delete; "six skills".
- medium, `plugins/factory/skills/maturity/SKILL.md:11,61`, `skills/security/SKILL.md:83`, `skills/assess/SKILL.md:97`: invoke or allow `Skill(factory:epic)`, which does not exist (it is brief). Fix: point at factory:brief.
- medium, `plugins/factory/docs/graph.md:168-171`: says the plugin is `graph` and `.mcp.json` passes DATABASE_URL; the plugin is factory and the env holds only SDLC_ACTOR. Fix: rewrite.
- low, `plugins/factory/scripts/README.md:43,63`: "Called by ticket"; no ticket skill. Fix: design.
- low, `plugins/factory/docs/agents.md:86`: names the removed `ticket`. Fix: design.
- low, `docs/DESIGN.md:806-991,1481`: names /factory:ideate, accept, findings, lens; none exist; not marked planned.
- low, `plugins/factory/lib/README.md:125`: README admits the `frontend_foldkit.py` docstring lists `--config PATH` that main does not read (unverified).

## What to do first

Highest audit severity is medium (no high in any table). Graph impact (affected routes + tests) orders equals: `hooks/post_edit.py` 0 routes + 6 tests (8 symbols); `sdlc_graph/mcp_auth.py` 0 routes + 5 tests (19 symbols); `lib/e2e.py` 0 + 0 (26 symbols); `sdlc_graph/mcp_server.py` 0 + 0 (12 symbols); `sdlc_graph/db.py` 0 + 0 (17 symbols).

1. medium, `hooks/post_edit.py:42` (with `scripts/ratchet.py:204`, `scripts/verify.py:308`, `scripts/tests_failing_first.py:125`, `lib/e2e.py:257,266,310`): shell=True on `.claude/sdlc.json` commands (SEC-01..07). Reachable on every edit, and the most tests reach it (6).
2. medium, MCP auth: `--http --insecure-no-auth` (`sdlc_graph/mcp_server.py:493-498`) with `mcp_auth.py` `from_env` returning None, unconfirmed (`mcp_auth.py:80-110`). 5 tests reach mcp_auth.py; it is the next reachable write path.
3. medium, `sdlc_graph/db.py:248-256`: process-global `SDLC_SCHEMA` via `os.environ` (the hardcoded `sdlc:sdlc` `DEFAULT_DATABASE_URL` at `db.py:31` / `graphcfg.py:31` is the alternative). 0 routes + 0 tests reach db.py, the same as the other remaining candidates, so it ranks after the two with test reach.

## Assessments

Maturity level 2 of 5 ("Individuals get results, nothing is shared", mean 2.00) in `docs/maturity-assessment.md`; security 0 critical, 7 high, 14 medium, 131 low (152 total, 9 rejected as not confirmed) in `docs/security-assessment.md`.

## Failures

none
