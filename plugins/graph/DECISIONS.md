# Decisions

Judgment calls, with the reason and what would reverse each. Scaffold decisions are D1-D15;
owners append theirs below with their own prefix (E1, G1, L1, ...).

## Scaffold (phase 1)

**D1. Postgres on host port 5435, not 5433.** 5433 is a common choice for a second local Postgres
(another project's `pgvector/pgvector:pg16` container often holds it), and reusing or stopping it
would touch someone else's data. Compose maps `127.0.0.1:${SDLC_PG_PORT:-5435}`, and
`db.DEFAULT_DATABASE_URL` and the Makefile default to 5435. Reverse: free 5433 and set
`SDLC_PG_PORT=5433`.

**D2. Spanner emulator in compose under the `spanner` profile, ports 9110/9120.** The Spanner Graph
export runs on the emulator locally. Its default ports 9010/9020 often belong to another emulator,
so this one maps gRPC to 9110 and REST to 9120; `SPANNER_EMULATOR_HOST=localhost:9110`. It is a
profile so `docker compose up -d` stays a Postgres-only, healthchecked start; `make spanner-up`
starts it. The image is `:latest` (the emulator publishes no stable minor tags worth pinning here);
it has no shell, so there is no healthcheck and the smoke test retries instead.

**D3. `mcp>=1.2,<2`.** `uv sync` resolved mcp 2.3.0, where `mcp.server.fastmcp` no longer exists
(FastMCP became `mcp.server.mcpserver.MCPServer`). the original plan names `mcp.server.fastmcp`, so the
upper bound keeps every owner on the API the plan describes (1.30.0 resolved). Reverse: port
`mcp_server.py` to the 2.x API and lift the bound.

**D4. Extensions live in `public`; tables are unqualified and placed by `search_path`.**
`CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public` (and `pg_trgm`) so per-test schemas
share one copy and dropping a test schema never drops the extension. `db.connect()` sets
`search_path = <schema>, public`. Concurrent first-time creation is tolerated in `db.py`.

**D5. Symbol identity is (kind, qualname, path), not (qualname, path).** the original plan says qualname +
path, but a SQLAlchemy class and its `model` symbol share both. Adding `kind` makes the key
unique without losing anything. The
string form is `kind:qualname@path`; `node.touched_symbols` uses it (or bare paths).

**D6. Snapshot table carries `storage` and `base_snapshot_id`.** The plan's columns cannot say
whether a snapshot's rows are full or a delta against which base. Delta rows reference symbols by
identity key, never by sym id, because ids differ per snapshot. `reconstruct` is Python
(`snapshot.reconstruct`), not a SQL function, so G owns it without touching SQL; delta snapshots
may be materialised into sym/sym_edge for querying without changing `storage`.

**D7. Guard language extensions and how the plan's contract guards parse.** The contract machine
is exactly as in the original plan. To make its strings evaluable: reserved segments `binding`, `green`,
`non_author` are row checks that do not move the payload cursor (so `ci_run.green.binding` means
green and binding); `evidence:acceptance...` and `evidence:ratchet...` also resolve from a
verify_stamp's sub-object, newest candidate wins. The other four machines (from section 2.1) need
two guards the plan's list lacks: `edge_out:<rel>` / `edge_in:<rel>` (feature -> building needs a
decomposed contract; incident -> intent needs a `caused_by` link). Epic "resolved riskiest
assumption and guardrail metric" became `decisions_resolved` + `evidence:guardrail_metric`;
"metrics snapshot" became `evidence:metrics_snapshot`; release `verified` uses
`ready_poll.status==200` and `smoke.exit==0`; incident `triaged` uses `evidence:triage`. New
evidence kinds: `ratchet`, `guardrail_metric`, `metrics_snapshot`, `triage`.

**D8. State machines stay in JSON, not a `state_machine` table.** Section 2.1 suggests a table so
a change is a migration; the original plan puts them in `state_machines.json`. The JSON wins for this
increment (reviewed in PRs, loaded by the evaluator); the Spanner export does not need it.

**D9. CI evidence is "binding" by shape only.** `source` starts with `ci:` and `signed_by` is
non-empty. Real GitHub OIDC verification (issuer, audience, `sub` claim, signature against the
JWKS) is a TODO before anything here gates a real merge.

**D10. `allowed_paths_present` reads `capabilities.paths`.** One declaration, two uses: the guard
and the sandbox compile. No separate `allowed_paths` column.

**D11. Evidence kinds are validated in Python, not by a CHECK constraint.** `evidence.KINDS` is the
registry; adding a kind is a code change with tests, not a migration.

**D12. Extra columns beyond section 2.1.** `node.attrs` (per-kind extras), `decision.proposed_by`,
`decision.created_at`, `run.tool_use_id` (joins the Pre and Post hook calls), `run.agent_type`,
`run.meta`, `prediction.inputs`; `run.model_tier` is CHECKed to the policy tiers plus `unknown`.
`prediction_outcome` is keyed by `prediction_id` (one outcome, upserted). Section 2.2's `chunk`
table is not created: the original plan's `graph_search` is name/path search over `sym` (full-text +
trigram), and embeddings are out of scope.

**D13. Connection defaults: autocommit and dict rows.** Fewer forgotten commits across seven
parallel owners; explicit `conn.transaction()` where atomicity matters (transitions lock the
node row).

**D14. Shared shapes module (`sdlc_graph/shapes.py`).** Not in the plan's layout. The TypedDicts and
the key helpers are the contract between extractors, the store, impact, the ledger and MCP; one
frozen module beats four private copies drifting.

**D15. Contract machine has no rework edge.** the original plan's contract machine only moves forward, so a
reviewer's "changes requested" cannot move `in_review` back to `implementing`. Metrics therefore
treat repeated `verify_stamp` rows as the rework signal. Adding `in_review -> implementing` is a
one-line JSON change, left for a maintainer to decide.

## Owners (phase 2), merged by the integrator

Each owner's judgment calls, from their reports. The long form is in the module docstrings.

**E1. Calls are resolved conservatively.** Any function, method or test emits `calls` to the
functions, methods and classes it calls; `Depends(x)` is a `calls` edge with `attrs.via='Depends'`.
A method call on an unknown receiver resolves only when exactly one method in the repo has that
name and it is not a common builtin name (`confidence: low`); everything else unresolved is dropped.
**E2. Test edges.** A test gets `tests` to every production symbol it references, including bare
modules it imports; fixtures (same file or an ancestor `conftest.py`) give `via='fixture'`, module
variables such as `client = TestClient(app)` give `confidence: low`. **E3. Routers.** Relative
imports, aliases, `__init__` re-exports and nested `include_router` prefixes are followed; unmounted
routes are kept with `mounted=false` and `fetches` links only mounted ones. The sample app: 7 routes,
6 mounted; the unmounted router is `app/routes/admin.py`. **E4. CODEOWNERS**
is last-match-wins, a rule with no handles un-owns, `*` does not cross `/`.

**G1. Deltas diff against the stored base**, not a fresh extraction of the base commit, so replaying
deltas reproduces the extraction exactly; an edge whose attrs changed is stored as remove + add;
delta snapshots keep `storage='delta'` and are materialised on first query. **G2. `impact` also
returns routes declared in any affected module** (a module re-runs at import), beyond handlers
reached through `exposes_route`. **G3. Owner matching** uses a gitignore-style regex because
fnmatch's `*` crosses `/`. **G4. Search** ranks literal ILIKE matches (0.6-1.0) above fuzzy
trigram/full-text matches (capped at 0.5).

**L1. `transition()` returns `{ok: false}` by default**; `strict=True` raises `GuardError` (the
task asked for an exception, ARCHITECTURE and the MCP server rely on the normal result).
**L2. Refused transitions** are logged in `node.attrs.refused_transitions` (newest last, capped at
50), because the schema has no refusal table; metrics count them. An audit-grade
`transition_refusal` table is a follow-up. **L3. A planner run over budget** is recorded with outcome
`refused` and then refused (PermissionError, exit 2); `run start --record-only` skips the check for
the PostToolUse hook, which records a spawn that already ran. **L4. The budget counts** planner-tier
runs under the nearest epic plus runs whose resolved model is Fable. **L5. The duplicate check**
skips contracts named in `depends_on`. **L6. Flagged ticket lines** never become the title or an
acceptance criterion. `workflow.next_step` returns ARCHITECTURE's keys plus `model_tier`,
`model_alias`, `missing_evidence`, `fable_budget_remaining`, `open_decisions` (a list),
`alternatives` and `human_invoked`; commands are `/sdlc:ticket|tests|implement|verify|ship|review-pr|deploy`.

**M1. List results are wrapped** as `{"ok": true, "result": [...]}` (FastMCP 1.30 splits a bare list
into one content block per item). Errors are `{"ok": false, "error": {"type", "message"}}`.
**M2. `rules://` globs are percent-encoded** (the template matches one path segment).
**M3. `contract_transition` refuses `agent:*` into protected states in the wrapper** as well as in
the ledger, and `decision_resolve` refuses non-`@` actors.

**I1. Ordering** is direct tests, then transitive, then mirror-only (`tests/test_<module>.py`,
`tests/<pkg>/`). **I2. `compile_capabilities` returns only verified keys**; `data` and `secrets`
come back separately as `unenforced`. **I3. A glob in `allowWrite`** is reduced to its literal
directory prefix (which widens it, and the summary says so); `secrets` entries are paths, and an
exact entry lifts the matching Read deny but cannot carve an exception from a wildcard deny.
**I4. `allowWrite` does not narrow writes inside the project** (the working directory is writable
under the sandbox); the Edit allow rules only pre-approve. A real restriction needs `denyWrite`.

**H1. The guard writes no run row**; it leaves a note in `.claude/work/spawns.json` and the
PostToolUse hook does start + finish from it, because the CLI cannot hand a run id between hooks.
**H2. With no tag, env or `current.json`, a spawn is treated as an executor stage** and pinned to
sonnet. **H3. `totalTokens` has no in/out split**, so it lands in `run.meta.total_tokens`.

**S1. One property graph, `SdlcGraph`,** over ledger and code tables (labels Node/Symbol,
Rel/SymRel); property names are graph-global, so `sym.id` is exposed as `sym_id`. Only MATCH
patterns are demoed on the emulator: its README says graph algorithms return empty. **S2. Ids come
from the loader** (no sequences): Postgres is the system of record, Spanner a one-way export.
**S3. Ports 9110/9120** (D2). Emulator findings: `DECISIONS-spanner.md`.

## Integrator (phase 3)

**INT1. `query impact` takes positional targets** (`query impact app/auth/api_key.py`); each is a
symbol key if it parses as one, else a path. `--paths`/`--symbols` still work.

**INT2. A contract's prediction uses `touched_symbols`; `capabilities.paths` only when nothing is
touched.** Unioning the two predicted every test of a brownfield repository (86 of 86) for the rate-limit contract, because
`capabilities.paths` says where a contract may write (`tests/**`, `app/routes/**`), not what it
changes. With the fallback rule it predicts 26 and both real failures are in the first three.
Reverse: if contracts stop declaring touched symbols, the union becomes the better default.

**INT3. The record hook passes `--record-only` and `--agent-type`.** Without `--record-only` a spawn
that already ran on a spent epic was recorded as refused and the hook got exit 2; the refusal belongs
to the PreToolUse guard. `run start` gained `--agent-type` so `run.agent_type` is filled.

**INT4. `spanner export` is a wholesale, one-snapshot read model.** It drops and recreates the
`sdlc_export` database, loads every ledger table and the chosen snapshot's `sym`/`sym_edge`
(materialising a delta first), runs the named GQL queries (`chain`, `tests_reaching`,
`contract_routes`) and keeps the database for `spanner query`. One snapshot only, so no query needs a
`snapshot_id` filter. Named query rows are sorted client-side because the emulator did not honour
`ORDER BY` after `RETURN DISTINCT`. `smoke --from-pg` now matches every ledger edge: a real ledger
need not contain the `depends_on` edges the sample query looks for. Built and run on the emulator
only; real Spanner was not tried.

**INT5. MCP tools carry annotations** (`readOnlyHint` on the nine read-only tools, not on the seven
writes) so a client can auto-approve reads; the guards stay server-side. `contract_transition`'s
`evidence_ids` schema is `list[int]`, matching the ledger's bigint ids.

**INT6. `hooks settings` is implemented**: it prints `hooks/hooks.example.json` with absolute paths
and this venv's interpreter (the example uses `$CLAUDE_PROJECT_DIR`-relative paths).

**INT7. The demo gives its copy a CODEOWNERS file when the fixture has none** (the sample app ships
one), runs the app's whole suite rather than only the predicted tests (otherwise recall is 1.0 by
construction), uses a throwaway sqlite file through `DATABASE_URL`, makes its regression in the
working tree only, and refuses to `db reset` a non-local `DATABASE_URL`. A gitignored `.env` is read
for an embedding key only; nothing reads a database URL from it.

**INT8. The capabilities summary no longer says "writable beyond the working directory"** for paths
that are inside it; it says these are the contract's write paths and points at the caveat (I4).

**INT9. Owner rules stay per repo, not per snapshot** (G asked). Indexing an older commit after a
newer one restores the older CODEOWNERS. Versioning them needs a `snapshot_id` column on `owner`;
not needed for the demo, which indexes forward only.

**INT10. Still open:** D15 (no `in_review -> implementing` rework edge) and real OIDC
verification of CI evidence (D9).

## Review hardening (phase 4)

**R1. MCP identity is the server's, not the caller's.** `build_server(actor=...)` /
`SDLC_ACTOR` (default `agent:mcp`, must be an `agent:*` label) fixes the actor of every tool call. No
tool takes `actor`; `evidence_add` takes no `source` or `signed_by` and stores local rows;
`decision_resolve` refuses. Supersedes the "actor is an argument" shape of the original tools. The CLI
`--actor`/`--source` remain typed strings (the human/CI entry point), so the guard is advisory against an
agent with a shell; this is now stated in the README and DEMO.md. D9 (OIDC) is still the real fix.

**R2. New guards close vacuous passes.** `acceptance_covered` (verify stamp passed >= rows),
`frozen_tests_match` (failing-first covers every `acceptance[].test`, frozen on the same commit),
`capabilities_approved` (egress/secrets/test_command need a human `drafted -> ready`),
`ticket_text_reviewed` (ticket-derived acceptance needs a human). `acceptance_present` and
`allowed_paths_present` validate row shape and reject bare wildcards. Release `staged` needs binding CI;
`released -> verified` needs a human or CI actor. `evidence_ids` is a check on the rows that satisfied
the guards, no longer a filter, so a newer failing stamp cannot be hidden.

**R3. CODEOWNERS is versioned per snapshot** (supersedes INT9): `owner.snapshot_id`,
`snapshot.committed_at` and `ancestors` (migration 004). "Latest" is the newest commit. `verify` checks
owners.

**R4. Impact additions.** Importers get `imports` edges to each ancestor package `__init__`; tests that
send literal HTTP requests get `tests` edges to routes; a reached `conftest.py` pulls in the tests
beneath it; a touched function adds its module's closure at low confidence, ordered after direct tests;
`conftest.py`/`__init__.py` are never test ids; absent entries are returned in `unresolved`.

**R5. Spawn guard fails closed and reserves atomically** (ledger advisory lock, else `flock` + atomic
rename); tags are read from the description only.

**R6. The Spanner `tests_reaching` GQL is restricted to the impact rels**, so it is a subset of the
Postgres answer (tested). The two numbers are labelled, not equated.
