# sdlc-graph architecture: the interface contract

Every module codes against this file: it fixes the signatures, data shapes and conventions
between them. The docstrings in each module are the long form; where they and this file
disagree, fix whichever is wrong in the same change.

Two rules govern everything:

1. **The code graph is derived, never written.** Extractors rebuild it from source per commit SHA
   (`snapshot`, `sym`, `sym_edge`, `sym_delta`, `owner`). No API, tool or agent writes it.
2. **The ledger is written only through typed functions with guards** (`node`, `edge`,
   `transition`, `evidence`, `decision`, `run`). Predictions (`prediction`,
   `prediction_outcome`) are proposals and never change ledger state.

## 1. Runtime and conventions

| Thing | Value |
|---|---|
| Python | 3.11 in `.venv` (`uv venv --python 3.11 .venv && uv sync`); run tests with `.venv/bin/python -m pytest` |
| Postgres | `pgvector/pgvector:pg16` via `docker compose up -d --wait db`, **host port 5435** (not 5433; see DECISIONS.md D1) |
| `DATABASE_URL` | default `postgresql://sdlc:sdlc@localhost:5435/sdlc` (`db.DEFAULT_DATABASE_URL`) |
| Schema | `SDLC_SCHEMA` env (default `public`); tests use `test_<hex>` from `tests/graph/conftest.py` |
| Spanner emulator | `docker compose --profile spanner up -d spanner`, gRPC `localhost:9110`, REST `localhost:9120`; `SPANNER_EMULATOR_HOST=localhost:9110` |
| MCP SDK | `mcp>=1.2,<2`, `from mcp.server.fastmcp import FastMCP` (2.x renamed it; pinned below 2, D3) |
| Allowed deps | psycopg[binary], mcp, google-cloud-spanner (spanner package only), pytest. Everything else stdlib. |

**Connections.** Always `sdlc_graph.db.connect()` (or a factory from `db.connection_factory()`).
It returns a psycopg 3 connection with `autocommit=True`, `row_factory=dict_row` and
`search_path = <schema>, public`. SQL is written **unqualified** (`SELECT ... FROM node`). Wrap
multi-statement writes in `with conn.transaction():`. Write jsonb with
`psycopg.types.json.Jsonb(obj)`; jsonb reads come back as dict/list; `text[]` is `list[str]`;
uuids come back as `uuid.UUID` (render as `str` in results).

**Library functions take `conn` first** and return plain JSON-ready dicts/lists (uuids as str,
timestamps ISO strings). The CLI and MCP layers are thin wrappers; neither contains logic.

**Errors** are exceptions with a message a human can act on:
`LookupError` (unknown slug/symbol/snapshot), `ValueError` (bad input, unknown kind/rel, malformed
key), `PermissionError` (actor not allowed). A guard failure is **not** an exception: it is a
normal `{"ok": false, "missing": [...]}` result. The CLI maps exceptions to exit codes
(2 ValueError/PermissionError, 3 NotImplementedError, 4 LookupError, 5 database unavailable).

**Actors** are strings: `@handle` (human), `agent:<model alias>` (e.g. `agent:sonnet`),
`ci:<system>` (e.g. `ci:github`). Anything else is a `ValueError`
(`ledger.transitions.actor_class`).

## 2. Modules and responsibilities

| Module | Owner | Responsibility |
|---|---|---|
| `sdlc_graph/db.py` | scaffold | connect, schema scoping, apply/reset DDL |
| `sdlc_graph/shapes.py` | scaffold | shared TypedDicts and the symbol key helpers (frozen) |
| `sdlc_graph/cli.py` | scaffold + each owner's `register_<area>` body | argparse entry point |
| `codegraph/extract_python.py` | E | modules, classes, functions, methods, routes, models, env vars, tests |
| `codegraph/extract_frontend.py` | E | frontend fetch/axios calls; `link` adds `fetches` to routes |
| `codegraph/extract_deps.py` | E | packages from requirements*/pyproject; `link` adds `depends_on_package` |
| `codegraph/extract_owners.py` | E | CODEOWNERS parse + last-match-wins `match` |
| `codegraph/snapshot.py` | G | index (full), delta, reconstruct, materialise, verify, snapshot lookup |
| `codegraph/queries.py` | G | deps, impact (static), owners, search, unmatched fetches, unmounted routers |
| `ledger/models.py` | L | dataclasses for the six ledger tables |
| `ledger/nodes.py` | L | node/edge CRUD, node view, symbol overlap |
| `ledger/transitions.py` | L | state machines, guard language, guarded transitions |
| `ledger/evidence.py` | L | evidence kinds, validation, binding rule, resolution rule |
| `ledger/decisions.py` | L | propose / resolve (humans only) |
| `ledger/runs.py` | L | routing policy, run rows, Fable budget |
| `ledger/workflow.py` | L | next allowed step |
| `ledger/intake.py` | L | ticket scan, `contract_draft` (dedupe + overlap rule), `contract_update` |
| `ledger/contract_md.py` | L | contract.md parser: the pack template's blocks as ledger fields (pure) |
| `ledger/metrics.py` | L | per-stage metrics from rows |
| `impact/affected.py` | I | `queries.impact` + mirror-path tests + reasons + ordering |
| `impact/record.py` | I | predictions, outcomes, precision/recall |
| `impact/capabilities.py` | I | capabilities -> Claude Code settings fragment |
| `mcp_server.py` | M | FastMCP server, all 24 tools (graph_status and lifecycle_view included) |
| `spanner/ddl.py`, `spanner/emulator_smoke.py` | S | GoogleSQL + CREATE PROPERTY GRAPH, emulator smoke |
| `hooks/guard_agent_spawn.py`, `hooks/record_agent_run.py` | H | PreToolUse model record (request, default, override) + ceilings, PostToolUse run row |
| `demo/` | integrator | the end-to-end walkthrough on the sample app |

## 3. Public signatures

Types: `Conn = psycopg.Connection`; shapes from `sdlc_graph.shapes`.

### db (implemented)

```python
DEFAULT_DATABASE_URL: str
database_url() -> str
schema_name(schema: str | None = None) -> str                      # ValueError on a bad name
sql_dir() -> Path;  sql_files() -> list[Path]
connect(url: str | None = None, *, schema: str | None = None, autocommit: bool = True) -> Conn
connection_factory(url: str | None = None, *, schema: str | None = None) -> Callable[[], Conn]
run_sql_files(conn: Conn, paths: list[Path] | None = None) -> list[str]
init(schema: str | None = None, url: str | None = None) -> {"schema", "applied", "tables"}
reset(schema: str | None = None, url: str | None = None) -> {"schema", "applied", "tables", "dropped"}
drop_schema(schema: str, url: str | None = None) -> None             # refuses public
list_tables(conn: Conn, schema: str | None = None) -> list[str]
truncate_all(conn: Conn, schema: str | None = None) -> None
use_schema(schema: str) -> ContextManager[str]                       # sets SDLC_SCHEMA temporarily
```

### shapes (implemented)

```python
SYM_KINDS, SYM_RELS, NODE_KINDS, NODE_RELS: tuple[str, ...]
sym_key(sym) -> tuple[str, str, str]          # (kind, qualname, path)
key_dict(sym) -> SymKey
key_str(sym | tuple) -> str                   # "kind:qualname@path"
parse_key(text: str) -> SymKey                # ValueError if not a key
TypedDicts: SymKey, Sym, Edge, ExtractResult, OwnerRule, AffectedSet, GuardResult
```

### codegraph

```python
# every extract_* module (E)
extract(repo_root: Path) -> ExtractResult
link(merged: ExtractResult) -> list[Edge]
extract_python.module_name(rel_path: str) -> str
extract_python.SKIP_DIRS: frozenset[str]
extract_frontend.normalise_url(url: str) -> str
extract_deps.normalise(name: str) -> str
extract_owners.parse(repo_root: Path) -> list[OwnerRule]
extract_owners.match(path: str, rules: list[OwnerRule]) -> list[str]

# snapshot (G)
EXTRACTOR_VERSION: str
repo_name(repo_path) -> str
resolve_sha(repo_path, ref="HEAD") -> str
export_tree(repo_path, sha: str, dest: Path) -> Path
extract_all(repo_root: Path) -> ExtractResult
index(conn, repo_path, sha="HEAD", *, name=None) -> {"snapshot_id","repo","commit_sha","parent_sha","storage","symbols","edges","owners","created"}
delta(conn, repo_path, from_sha, to_sha, *, name=None) -> {"snapshot_id","base_snapshot_id","repo","commit_sha","storage","added","removed","changed","edges_added","edges_removed","created"}
reconstruct(conn, snapshot_id: int) -> ExtractResult
materialise(conn, snapshot_id: int) -> int
verify(conn, repo_path, sha="HEAD", *, name=None) -> {"ok","snapshot_id","missing","extra","changed","edges_missing","edges_extra"}
list_snapshots(conn, repo=None) -> list[dict]
resolve_snapshot(conn, repo=None, sha=None) -> int

# queries (G)
find_symbol(conn, snapshot_id, symbol: str) -> Sym
deps(conn, snapshot_id, symbol, *, depth=1, direction="out", rels=None) -> {"snapshot_id","root","nodes","edges"}
impact(conn, snapshot_id, *, paths=None, symbols=None, depth=4) -> AffectedSet      # no "reasons"
owners(conn, repo, paths: list[str]) -> dict[str, list[str]]
search(conn, snapshot_id, text, *, kind=None, limit=20) -> list[Sym + {"score"}]
unmatched_fetches(conn, snapshot_id) -> list[Sym]
unmounted_routers(conn, snapshot_id) -> list[Sym]
```

### ledger (L)

```python
# transitions
PROTECTED_STATES (one definition, in transitions.py: approved, merged, released, closed plus the lifecycle states accepted, rejected, split, superseded, risk_accepted, false_positive, duplicate, shortlisted, promoted, parked); RESERVED_SEGMENTS; STATELESS = "active"
load_machines(path=None) -> dict
machine(kind) -> dict | None;  initial_state(kind) -> str;  terminal_states(kind) -> frozenset[str]
actor_class(actor) -> "human" | "agent" | "ci"
parse_guard(guard: str) -> dict
evaluate(node, to_state, evidence, actor, *, open_decisions=0, edges_out=(), edges_in=()) -> tuple[bool, list[str]]
evaluate_detail(...same...) -> {"ok","missing","evidence_ids"}
transition(conn, slug, to_state, actor, *, evidence_ids=None, reason=None, strict=False) -> GuardResult   # strict: raise GuardError
transitions_from(kind, state) -> list[dict];  context(conn, node_row, evidence_ids=None) -> dict;  GuardError

# nodes
create(conn, *, kind, slug, title, repo, created_by, state=None, path=None, commit_sha=None, tier=None,
       external_id=None, touched_symbols=None, capabilities=None, acceptance=None, attrs=None) -> Node dict
get(conn, slug) -> NodeView;  get_row(conn, slug) -> dict
neighbours(conn, slug, rel=None, direction="both") -> list[{"direction","rel","node"}]
link(conn, src_slug, dst_slug, rel) -> {"src","dst","rel"}
symbols_overlap(a: list[str], b: list[str]) -> list[str];  validate_touched(entries) -> list[str];  node_id(conn, slug) -> str
overlaps(conn, repo, touched_symbols, *, exclude_slug=None) -> list[{"slug","state","overlap"}]

# evidence
KINDS: dict[str, tuple[str, ...]]
validate(kind, payload) -> None;  is_binding(source, signed_by) -> bool
add(conn, slug, kind, payload, *, source="local", oidc_token=None) -> Evidence dict  # ci: needs a verified token
find(conn, kind, *, url=None, repo=None, pr=None) -> list[{slug, kind, repo, state, evidence_id}]  # pr_url -> contract
list_for(conn, slug, kind=None, ids=None) -> list[Evidence dict]
resolve(evidence: list[dict], kind) -> dict | None;  summary(evidence) -> dict
# review_pack {pack in shapes.PACK_KINDS, path docs/reviews/<date>-<pack>.html, sha256, shown_for {decisions: [id], transition: state}}:
#   decisions.resolve stores the newest matching one as decision.meta.pack; transitions.transition cites the newest for to_state
#   since the previous transition. Never a guard input.

# decisions
FABLE_BUDGET_QUESTION = "fable_budget_increase"
propose(conn, slug, question, options, *, actor, recommendation=None, due=None) -> Decision dict
resolve(conn, decision_id, choice, *, actor) -> Decision dict          # PermissionError for non-humans
open_for(conn, slug) -> list[Decision dict];  get(conn, decision_id) -> Decision dict (with meta)

# runs
load_policy(path=None) -> dict;  tier_for_stage(stage, policy=None) -> str
model_for_tier(tier, policy=None) -> str;  tier_of_model(model, policy=None) -> str
epic_of(conn, slug) -> str | None
start(conn, *, stage, requested_model, slug=None, tool_use_id=None, agent_type=None, model_tier=None, meta=None,
      enforce_budget=True) -> Run dict      # PermissionError after recording a refused planner run on a spent epic
finish(conn, run_id=None, *, tool_use_id=None, outcome, usage=None) -> Run dict
budget(conn, slug, policy=None) -> {"epic","budget","used","remaining"}

# workflow / intake / metrics
workflow.next_step(conn, slug) -> {"slug","kind","state","next_state","alternatives","command","stage","tier","model",
    "model_tier","model_alias","missing","missing_evidence","human_only","human_invoked","fable_budget",
    "fable_budget_remaining","open_decisions": [Decision dict],"done"}
    # command is /factory:design|tests|implement|verify|ship|review-pr|deploy (or null)
intake.scan(ticket_text) -> {"flags","clean","acceptance"}
intake.contract_draft(conn, *, slug, title, repo, ticket_text, feature=None, touched_symbols=None,
                      capabilities=None, acceptance=None, depends_on=None, tier=None, external_id=None,
                      defaults=None, contract_text=None, strict=False)
    -> {"status": "created"|"conflict"|"duplicate"|"refused", "node", "conflicts", "duplicates", "flags",
        "blocks": {"missing", "unknown", "warnings", "sha256"}}     # blocks: with contract_text, defaults or strict
intake.contract_update(conn, slug, contract_text, *, actor)
    -> {"status": "updated"|"unchanged"|"conflict", "slug", "state", "changed", "decisions_confirmed",
        "evidence_id", "conflicts", "blocks", "node"}   # PermissionError from tests_frozen on
contract_md.parse_contract(md) -> {"title", "outcome", "acceptance_criteria": [{id, name, text, negative}],
    "acceptance_lines": [{kind, command, expects}], "decisions": [{n, question, options, recommendation, value,
    decided_by, confirmed, handle}], "defaults", "capabilities": {paths, commands, egress, data}, "allowed_paths",
    "tier", "out_of_scope", "evidence_plan", "missing", "optional_missing", "unknown", "warnings"}
contract_md.touched_from_paths(paths) -> (touched_symbols, skipped);  contract_md.acceptance_rows(parsed) -> rows
metrics.snapshot(conn, repo=None, since=None) -> dict
```

### impact (I)

```python
affected.mirror_tests(module_path: str, test_paths: list[str]) -> list[str]
affected.affected(conn, snapshot_id, *, paths=None, symbols=None, depth=4) -> AffectedSet   # with reasons, order
affected.affected_for_node(conn, slug, snapshot_id=None) -> AffectedSet   # touched_symbols; capabilities.paths only if none
affected.latest_snapshot_id(conn, repo=None, sha=None) -> int
record.score(predicted: list[str], actual: list[str]) -> tuple[float | None, float | None]
record.predict(conn, snapshot_id, result=None, *, slug=None, inputs=None, paths=None, symbols=None, depth=4) -> {"prediction_id","node","snapshot_id","predicted_tests","predicted_routes"}
record.record_outcome(conn, prediction_id, actual_failing_tests | junit_path) -> {"prediction_id","actual_failing_tests","precision","recall","matched","missed","extra"}
record.outcome = record_outcome;  record.summary(conn, repo=None) -> dict;  record.failing_from_junit(path, known_files=None) -> list[str]
capabilities.validate(capabilities) -> None
capabilities.compile_capabilities(capabilities) -> dict        # settings fragment
capabilities.compile_for(conn, slug) -> {"slug","capabilities","settings","unenforced","notes","summary"}
capabilities.compile_full(capabilities) -> {"settings","unenforced","notes","summary"}
```

### mcp_server (M), spanner (S), hooks (H)

```python
mcp_server.SERVER_NAME = "graph"; READ_ONLY_TOOLS; WRITE_TOOLS; TOOL_NAMES
mcp_server.build_server(conn_factory=None) -> FastMCP
mcp_server.main(argv=None) -> int

spanner.ddl.TABLES; table_ddl() -> list[str]; property_graph_ddl() -> list[str]; ddl_statements() -> list[str]; render() -> str
spanner.emulator_smoke.emulator_host() -> str
spanner.emulator_smoke.smoke(*, host=None, project="sdlc-graph", instance="sdlc", database="sdlc", from_pg=False, recreate=True) -> dict
spanner.export.QUERIES: dict[str, str]   # chain, tests_reaching ({symbol}), contract_routes ({slug})
spanner.export.export(conn, *, snapshot_id=None, host=None, instance="sdlc", database="sdlc_export", queries=None) -> dict
spanner.export.query(name_or_gql, *, slug=None, symbol=None, host=None, database="sdlc_export") -> {"gql","columns","rows"}

hooks/guard_agent_spawn.py: parse_tag(tool_input) -> (stage, node); decide(hook_input, *, policy, fable_remaining=None) -> dict; main(argv=None) -> int
hooks/record_agent_run.py:  usage_from(hook_input) -> dict; main(argv=None) -> int
```

## 4. Data shapes between layers

### 4.1 Symbols and edges (codegraph)

Symbol identity across snapshots is **(kind, qualname, path)** (ids differ per snapshot; kind is
part of the key because a class and its model share qualname and path). Its string form is
`kind:qualname@path` (`shapes.key_str`), e.g. `function:app.routes.a.list_items@app/routes/a.py`.
Paths are repo-relative POSIX. `Sym` dict:

```json
{"kind": "route", "name": "list_items", "qualname": "GET /items", "path": "app/routes/a.py",
 "line_start": 13, "line_end": 15,
 "attrs": {"method": "GET", "path": "/items", "router": "router", "router_prefix": "/items",
           "include_prefix": "", "mounted": true, "handler": "app.routes.a.list_items",
           "dependencies": [], "auth": false}}
```

`Edge` from extractors: `{"src": SymKey, "dst": SymKey, "rel": "...", "attrs": {}}`. Rows read back
also carry `src_id`, `dst_id`.

| kind | qualname | path | attrs keys |
|---|---|---|---|
| module | dotted name (`app.routes.a`); frontend files: the path | file | `external_imports: [top-level names]`, `lang` (frontend), `parse_error` |
| class | `app.models.Item` | file | `bases: [...]` |
| function / method | `app.routes.a.list_items` / `app.models.Item.label` | file | `decorators: [...]`, `async: bool` |
| route | `METHOD /full/path` | decorator file | `method, path, router, router_prefix, include_prefix, mounted, handler, dependencies, auth` |
| model | same as its class | file | `table, base` |
| env_var | variable name | reading file | `access` (`environ[]`, `environ.get` or `getenv`), `default`, `in_config: bool` |
| package | PEP 503 name | manifest | `spec, manifest_kind, extras, group` |
| frontend_call | `METHOD url` | file | `method, url, raw, line` |
| test | `tests.test_a.test_list_items` | file | `nodeid: "tests/graph/test_a.py::test_list_items"` |
| function (TypeScript) | `path:name` (`src/lib/api/endpoints.ts:runsUrl`) | file | `exported, kind` (`function` or `const`), `program` |
| message | `path:Tag` | file of the `defineMessageUnion` | `union, fields, exported, program` |
| command | `path:Name` | file of the `Command.define` | `command, args, messages, endpoints, missing_endpoints, program` |
| view | `path:name` | file | `exported, kind, program` |
| route_fe | `path:Route` | file of the `defineRouteUnion` | `union, fields, exported` |
| test (TypeScript) | the test file's path | file | `runner` (`story`, `scene`, `unit`), `nodeid` (the path), `program` |

Every guess carries `attrs.confidence = "low"`; absent means high.

| rel | src -> dst |
|---|---|
| imports | module -> module (in-repo only) |
| calls | function/method -> function/method/class (best effort; unresolved dropped) |
| defines | module -> class/function/test/frontend_call; class -> method/model |
| exposes_route | handler function -> route |
| includes_router | including module -> router module (`attrs.prefix`, `attrs.router`) |
| reads_env | function or module -> env_var |
| uses_model | function/method -> model |
| depends_on_package | module -> package |
| fetches | frontend_call -> route (added by `extract_frontend.link`) |
| tests | test -> symbol it imports or calls |
| handles | update module -> message tag whose name appears in it (`extract_typescript`) |
| calls_api | command -> endpoint function of the `frontend.api_table` file it references (`extract_typescript`) |

TypeScript (`codegraph/extract_typescript.py`, with `appscan.py` for the configured paths): module per `.ts`/`.tsx`
file under `paths.app` of `.claude/sdlc.json` (default `src/**`, `app/**`, `lib/**`), `defines` from each module to its
symbols, `imports` between modules (relative and tsconfig-alias specifiers), `tests` from a story test to the message tags
it sends and from a scene test to the view it renders. A command whose api call names something the api table does not
export has no `calls_api` edge for it and lists it in `attrs.missing_endpoints`. `queries.impact` also walks `handles`
and `calls_api`, adds the story tests of the tags an affected update module handles, and adds the views of an affected
program (`attrs.program`) at low confidence (`CLOSURE_DEPTH`), then the scene tests of those views. `sql/008_typescript.sql`
widens the two CHECK constraints of schemas created before.

**Delta rows** (`sym_delta.row`, relative to `snapshot.base_snapshot_id`):
`table_name='sym'`, op `add|remove|change`: a Sym without id/snapshot_id;
`table_name='sym_edge'`, op `add|remove`: `{"src": SymKey, "dst": SymKey, "rel", "attrs"}`.

### 4.2 Affected set (codegraph -> impact -> ledger/mcp)

`queries.impact` and `impact.affected.affected` return `AffectedSet`:

```json
{"snapshot_id": 3,
 "symbols": [{"kind": "module", "qualname": "app.models", "path": "app/models.py", "depth": 0, "...": "..."}],
 "routes":  ["GET /items", "POST /items"],
 "tests":   ["tests/graph/test_a.py::test_list_items"],
 "owners":  {"app/models.py": ["@team-core"], "app/routes/a.py": ["@alice"]},
 "reasons": {"tests/graph/test_a.py::test_list_items": "tests app.routes.a.list_items (depth 3)",
             "GET /items": "exposed by app.routes.a.list_items (depth 2)"}}
```

Test ids are pytest node ids; a bare file path means the whole file. `impact.record.predict`
stores `tests` and `routes` in `prediction`; `/verify` reports the actual failing node ids to
`record_outcome`. Nothing in this flow writes ledger state.

### 4.3 Ledger node and node view (ledger -> mcp)

`Node.to_dict()`:

```json
{"id": "uuid-str", "kind": "contract", "slug": "contract-items-auth", "title": "...", "repo": "sample-app",
 "path": "intent/items-auth.md", "commit_sha": null, "state": "drafted", "tier": "low", "external_id": null,
 "created_by": "@alice", "created_at": "ISO", "updated_at": "ISO",
 "touched_symbols": ["function:app.routes.a.create_item@app/routes/a.py", "app/auth.py"],
 "capabilities": {"paths": ["app/routes/**", "tests/graph/test_a.py"], "egress": ["pypi.org"], "data": ["postgres:ro"], "secrets": []},
 "acceptance": [{"id": "AC1", "text": "POST /items without a token returns 401", "test": "tests/graph/test_a.py::test_create_requires_auth"}],
 "attrs": {}, "contract_md": null}
```

`contract_md` (column `node.contract_md`, sql/009) is the contract.md text the node was drafted or last updated
from. A contract drafted from one also carries `attrs.defaults`, `attrs.decisions` (the Decisions table, below),
`attrs.acceptance_lines` and `attrs.contract_sha256`.

**Contracts from contract.md.** `ledger.contract_md.parse_contract` reads the sdlc pack's template
(`plugins/factory/templates/contract.template.md`; headings case-insensitive, HTML comments ignored, `\|` inside a table
cell is a literal pipe). `contract_draft(contract_text=...)` (MCP `contract_draft(contract_md=...)`, CLI
`node draft --contract F`) fills every block the caller did not pass explicitly:

| contract.md | ledger field |
|---|---|
| `## Allowed paths` | `touched_symbols` (a glob reduces to its directory: `src/lib/api/**` -> `src/lib/api/`; `(group)` and `[id]` segments are literal) |
| `## Capabilities` (`- Paths:`, `- Commands:`, `- Egress:`, `- Data:`; backticked items literal, `none ...` = `[]`) | `capabilities` (per key, an explicit key wins; no Paths line: the Allowed paths); validated, a refused value creates nothing |
| `## Defaults when ambiguous` | `attrs.defaults` |
| `## Acceptance criteria` | `acceptance`: `{id: "AC<n>", text, name, source: "contract"}` (`name` is the backticked test name; it is not `test`, which stays a pytest node id bound later) |
| `## Risk tier` | `tier` (the first of trivial, low, medium, high) |
| `## Decisions` | `attrs.decisions`: `{n, question, options, recommendation, value, decided_by, confirmed, handle}` |
| `## Acceptance` | `attrs.acceptance_lines`: `{kind: shell\|mcp, command, expects}` |

A decision is **confirmed** when Decided by names a human handle (`@name`) and none of the words recommended,
unconfirmed or assumed (`agent:*`, an e-mail address and the template's `<@handle>` are not handles).
`blocks.missing` names what the `drafted -> ready` guards will still ask for after the explicit arguments and the
text are combined (`## Acceptance criteria`, `## Decisions (...)`, `## Defaults when ambiguous`,
`## Capabilities (<keys>)`, `## Allowed paths`, `## Risk tier`). With `strict` (default true for the MCP tool, false
for the library, `--strict` on the CLI) a draft with any missing block returns `status: "refused"` and creates
nothing.

`contract_update(slug, contract_text, actor)` (MCP `contract_update(slug, contract_md)`, CLI
`contract update <slug> --from-file F --actor A`) re-parses and replaces `touched_symbols`, `capabilities`, defaults,
`acceptance` (a row's bound `test`/`command` is kept by id), `tier`, `attrs.decisions` and `contract_md`; a block the
text lacks keeps its value and is named in `blocks.missing`. Only in `drafted` or `ready` (PermissionError from
`tests_frozen` on: the allowed paths and tests are frozen). Anyone may update a drafted contract; on a `ready` one an
`agent:*` actor may change only the decisions and the text. A changed touched set runs the overlap rule (`status:
"conflict"`, nothing written). Any change records evidence `contract_updated` `{changed: [field...],
decisions_confirmed: [n...], actor, sha256, missing}`; `sdlc-graph contract parse F` prints the parse and touches
nothing.

`touched_symbols` entries are symbol keys or bare paths (a file, or a directory ending in `/`).
**Overlap**: two entries overlap when equal, or when one is a bare path equal to or containing
the other's path (`nodes.symbols_overlap`). Two open contracts (non-terminal state) in the same
repo that overlap must be linked by `depends_on` from the new one, or `contract_draft` returns
`status: "conflict"` and creates nothing. `allowed_paths_present` reads `capabilities.paths`
(there is no separate allowed_paths column). Kinds with no state machine (change, rule, repo)
sit in state `"active"` and refuse transitions.

Node view (`nodes.get`, `node_get`): `{"node", "open_decisions", "evidence": {kind: {"count",
"latest_id", "latest_at", "binding"}}, "edges": {"out": [...], "in": [...]}, "transitions": [...]}`
with edge items `{"rel", "slug", "kind", "state"}`.

### 4.4 Evidence payloads

| kind | payload | used by |
|---|---|---|
| `tests_failing_first` | `{"tests": ["tests/x.py::test_y"], "commit": "sha"}`; an entry may be `{"id": "tests/x.py::test_y", "reason": "not_implemented"}`, or a `"reasons": {id: reason}` map sits beside the list; reason is one of not_implemented, assertion, status_mismatch, other, import_error, attribute_error, name_error, syntax_error, collection_error | contract ready -> tests_frozen |
| `tests_frozen` | `{"files": {"tests/x.py": "<sha256 hex>"}, "commit": "sha"}` | contract ready -> tests_frozen |
| `verify_stamp` | `{"frozen_ok": true, "acceptance": {"passed": 3, "failed": 0}, "ratchet": {"new": 0, "known": 2}, "db_backend": "postgresql", "tool_versions": {"pytest": "8.3"}}` | contract implementing -> verified |
| `acceptance` | `{"passed": 3, "failed": 0, "rows": [{"id": "AC1", "ok": true}]}` | standalone alternative to `verify_stamp.acceptance` |
| `ratchet` | `{"new": 0, "known": 2}` | standalone alternative to `verify_stamp.ratchet` |
| `review_approval` | `{"reviewer": "@bob", "author": "@alice", "url": "https://github.com/.../pull/7#pullrequestreview-1", "commit": "40-hex"}` | in_review -> approved |
| `ci_run` | `{"url": "https://github.com/.../actions/runs/1", "conclusion": "success", "sha": "40-hex"}` | in_review -> approved (binding; sha must equal review_approval.commit), release requested -> staged |
| `pr_url` | `{"url": "https://github.com/.../pull/7"}` | verified -> in_review |
| `merge_sha` | `{"sha": "40-hex", "head": "40-hex (optional)"}` | approved -> merged |
| `ready_poll` | `{"url": "https://staging/.../health", "status": 200, "attempts": 3}` | release released -> verified |
| `smoke` | `{"command": "make smoke", "exit": 0}` | release released -> verified |
| `guardrail_metric` | `{"metric": "p95_latency_ms", "baseline": 180, "target": 200}` | epic drafted -> accepted |
| `metrics_snapshot` | `{"metrics": {...metrics.snapshot output...}, "since": "ISO"}` | epic -> measured, feature -> evaluated |
| `triage` | `{"summary": "...", "severity": "sev2"}` | incident open -> triaged |
| `contract_updated` | `{"changed": ["decisions", "contract_md"], "decisions_confirmed": [2, 4], "actor": "@pat", "sha256": "...", "missing": []}` | written by `contract_update`; the feed shows it |
| `superseded` | `{"ids": [12, 13], "reason": "run #4 was set aside"}` | written by the orchestrator when it sets an attempt's commits aside (a retry or drift reset); every guard and the frozen-test reading skip the `tests_failing_first` / `tests_frozen` rows it names (no other kind can be superseded) |

Extra payload keys are allowed and kept (the pack's verify stamp also has `ok`, `fingerprint`,
`head`, `stack`, `checks`). Evidence row dict: `{"id", "node_id", "kind", "source", "signed_by",
"payload", "at", "binding"}`. **Binding** = `source` starts with `ci:` and `signed_by` is non-empty;
`signed_by` is only ever the `sub` of a verified GitHub OIDC token from the trusted `workflow_run` evidence workflow
(D9), whose verified claims the ledger stores in `payload.oidc`; a `ci:` row without one is refused. Local evidence is never upgraded.

### 4.5 Guard mini-language

| Guard | Holds when |
|---|---|
| `evidence:<kind>` | an evidence candidate of that kind exists |
| `evidence:<kind>.<field>` | the resolved payload's field (dotted path, left to right) is truthy |
| `evidence:<kind>.<field>==<value>` | that field equals `<value>` parsed as JSON (`0`, `true`, `"x"`), else the raw string |
| reserved segments | `binding` (row is CI-binding), `green` (`payload.conclusion == "success"`), `non_author` (reviewer and author non-empty and different, case-insensitive, `@` stripped). They test the row and do not move the cursor, so `evidence:ci_run.green.binding` = a green AND binding ci_run |
| `actor:human\|ci` | the actor's class is one of the listed (`human`, `ci`, `agent`) |
| `decisions_resolved` | no open decision on the node, and every `attrs.decisions` row (the contract.md table) has a Value; at tier `medium` or `high` every row is also confirmed by a human `@handle` (at trivial, low or no tier a recommended value passes) |
| `acceptance_present` | `node.acceptance` is a non-empty list of rows that each have non-empty `id` and `text` |
| `allowed_paths_present` | `node.capabilities.paths` non-empty strings, none a bare wildcard (`**`, `*`, `.`) |
| `capabilities_approved` | capabilities with egress, secrets or a `test_command` need a human actor; path-only passes |
| `ticket_text_reviewed` | acceptance rows with `source: "ticket"` need a human actor |
| `acceptance_covered` | newest `acceptance` evidence (or verify_stamp sub-object) has `passed >= len(node.acceptance)` (at least 1) |
| `frozen_tests_match` | newest `tests_failing_first` lists a test for every `acceptance[].test` (the id, `id::...` or a parametrize case `id[...]`) and newest `tests_frozen` is on the same commit |
| `failing_first_reasons` | when the newest `tests_failing_first` gives per-test reasons, none is import_error, attribute_error, name_error, syntax_error or collection_error; refused, `missing` holds the guard name and `test <id> fails for the wrong reason: <reason>` per test. No reasons: passes |
| `edge_out:<rel>` / `edge_in:<rel>` | the node has an outgoing / incoming ledger edge with that rel (extension used by epic, feature, incident; D7) |

**Resolution** of `evidence:<kind>`: candidates are rows of that kind plus every `verify_stamp`
whose payload has a top-level `<kind>` key (its sub-object becomes the payload). The newest
candidate wins (by `at`, then `id`), over ALL of the node's evidence. `evidence_ids` given to a
transition is a check, not a filter: each id must belong to the node and be one of the rows that
satisfied the guards, else the transition is refused with `evidence_ids:not_current`. **Missing** entries are the failed guard strings verbatim plus
`transition:<from>-><to>` (no such edge) and `actor:not_agent` (an `agent:*` actor into
approved/merged/released/closed, refused whatever the guards say).

State machines (contract, epic, feature, release, incident, story, decision, finding, idea) are in `sdlc_graph/ledger/state_machines.json`; `states[0]` is the
initial state and a state with no outgoing transition is terminal.

### 4.6 Runs, routing and the hook tag

`routing_policy.json`: tiers `planner=fable, orchestrator=opus, executor=sonnet, utility=haiku`;
stages map to tiers; `fable_budget_per_epic = 3`. A planner run counts against the epic of its
node (the node itself or the nearest ancestor by reverse `decomposes_into`). A human raises the
budget by resolving a decision on the epic with question `fable_budget_increase` and an integer
choice. Run dict: the `run` columns (`requested_model`, `resolved_model`, `model_tier`, tokens,
`cost_usd`, `turns`, `outcome`, `tool_use_id`, `agent_type`, `meta`). Spawns identify their stage
and node with a tag in the Agent tool's `description` or `prompt`:
`[sdlc stage=<stage> node=<slug>]` (fallback env `SDLC_STAGE`, `SDLC_NODE`).

### 4.7 Capabilities

Input `{"paths": [...], "egress": [...], "data": [...], "secrets": [...]}`; output a Claude Code
settings fragment using only keys the Claude Code settings documentation describes:
`sandbox.enabled`, `sandbox.filesystem.allowWrite`, `sandbox.network.allowedDomains`,
`permissions.allow` (`Edit(./...)`), `permissions.deny` (`Read(...)` rules for credential files,
since `sandbox.filesystem.denyRead` does not stop the Read tool). The fragment has no other keys:
`data` and `secrets` are returned beside it as `unenforced` by `compile_full`/`compile_for` (I2).

## 5. MCP tools

Server name `graph` (Claude Code: `mcp__graph__<tool>`, or `mcp__plugin_factory_graph__<tool>` from the plugin). The actor of every call is the
server's fixed `agent:*` identity (`SDLC_ACTOR`, default `agent:mcp`); no tool takes an actor and an
`actor` argument is ignored. Each tool opens a connection
from the factory, calls the library function, returns its dict. `repo`/`sha` default to the
latest snapshot. Read-only tools never write.

| Tool | Arguments | Calls | Returns |
|---|---|---|---|
| `graph_deps` | `symbol, depth=1, direction="out", rels=None, repo=None, sha=None` | `queries.deps` | deps dict |
| `graph_impact` | `paths=None, symbols=None, depth=4, repo=None, sha=None` | `impact.affected.affected` | AffectedSet |
| `graph_owners` | `paths, repo, sha=None` | `queries.owners` | `{path: [handles]}` (rules of that commit; default newest) |
| `graph_search` | `text, kind=None, limit=20, repo=None, sha=None` | `queries.search` | list |
| `graph_snapshot` | `repo=None` | `snapshot.list_snapshots` | list |
| `node_get` | `slug` | `nodes.get` | node view |
| `node_neighbours` | `slug, rel=None, direction="both"` | `nodes.neighbours` | list |
| `workflow_next` | `slug` | `workflow.next_step` | dict |
| `metrics_snapshot` | `repo=None, since=None` | `metrics.snapshot` | dict |
| `contract_draft` | `slug, title, repo, ticket_text, feature=None, touched_symbols=None, capabilities=None, acceptance=None, depends_on=None, tier=None, defaults=None, contract_md=None, strict=True` | `intake.contract_draft` | draft result (`refused` when strict and a block is missing) |
| `contract_update` | `slug, contract_md` | `intake.contract_update` | update result |
| `contract_transition` | `slug, to_state, evidence_ids=None, reason=None` | `transitions.transition` | GuardResult |
| `evidence_add` | `slug, kind, payload` (always local, never binding) | `evidence.add` | evidence dict |
| `decision_propose` | `slug, question, options, recommendation=None, due=None` | `decisions.propose` | decision dict |
| `decision_resolve` | `decision_id, choice` (always refused: the server is an agent) | `decisions.resolve` | decision dict |
| `run_start` | `stage, model, slug=None, tool_use_id=None` | `runs.start` | run dict |
| `run_finish` | `run_id, outcome, usage=None` | `runs.finish` | run dict |

Read-only tools carry `readOnlyHint: true`, writes `readOnlyHint: false` (INT5). A tool whose
library result is a list returns `{"ok": true, "result": [...]}` (M1); every error is
`{"ok": false, "error": {"type", "message"}}` with type in `not_found, invalid, forbidden,
not_implemented, database, unavailable, internal`. Resources: `contract://{slug}`, `epic://{slug}`,
`rules://{repo}/{glob}` (glob percent-encoded, M2).

## 6. CLI

`sdlc-graph <command> ... [--json]`. Commands and arguments are declared (stubs) in `cli.py`:
`db init|reset`; `index`, `delta`, `verify-snapshot`, `query deps|impact|owners|search`;
`node create|draft|get|neighbours|link|transition`, `evidence add`, `decision propose|resolve`,
`workflow next`, `run start|finish|budget` (`start --record-only --agent-type`), `metrics snapshot`;
`impact affected|predict|outcome|record|summary`, `capabilities compile`; `hooks settings`;
`spanner ddl|smoke|export|query`; `mcp`. `query impact` also takes positional paths or keys (INT1). Handlers return data;
`main` prints it. `verify-snapshot` returns `CommandResult(result, 1)` on a diff.

## 7. Tests

`tests/graph/conftest.py` fixtures: `pg_schema`, `conn_factory`, `conn`, `clean_conn` (truncated),
`sample_app` (a git checkout of `examples/sample-app`, read-only), `sample_app_copy` (mutable),
`sample_repo` (the optional `SAMPLE_REPO` checkout; skips when unset), `synthetic_repo`,
`synthetic_facts`, `make_synthetic_repo`, `git_commit`. Database tests skip when Postgres is down.
The synthetic repo plants: routes `GET /health`, `GET /items`, `POST /items` (auth via
`Depends(require_user)`), and `GET /b/orphan` on a router that is never included; env reads in
`app/config.py` plus `B_TOKEN` outside it; model `app.models.Item` (table `items`);
`tests/graph/test_a.py::test_list_items` importing `app.routes.a`; frontend fetches `/api/missing`
(no route) and `/items` (matches `GET /items`); three requirements; three CODEOWNERS rules. The
exact facts are `SYNTHETIC_FACTS` in conftest.

Test files per owner: `test_extract_*.py` (E), `test_snapshot.py`, `test_queries.py` (G),
`test_ledger*.py` (L), `test_impact*.py` (I), `test_mcp.py` (M, using
`mcp.shared.memory.create_connected_server_and_client_session`), `test_hooks.py` (H),
`test_spanner.py` (S, skip when the emulator is unreachable), `test_scaffold.py` (scaffold).

## 8. File ownership

| Owner | Paths |
|---|---|
| scaffold (phase 1) | everything below until phase 2; afterwards `db.py`, `shapes.py`, `cli.py` outside the register bodies, `sql/`, `tests/graph/conftest.py`, `tests/graph/test_scaffold.py`, pyproject, compose, Makefile, docs/graph-architecture.md |
| **E** extractors | `sdlc_graph/codegraph/extract_*.py`, `tests/test_extract_*.py` |
| **G** graph store | `sdlc_graph/codegraph/snapshot.py`, `queries.py`, the index, delta, verify-snapshot and query wiring in `cli.py` (`register_codegraph` body only), `tests/graph/test_snapshot.py`, `tests/graph/test_queries.py` |
| **L** ledger | `sdlc_graph/ledger/**`, the node, evidence, decision, workflow, run and metrics wiring (`register_ledger` body), `tests/test_ledger*.py` |
| **M** mcp | `sdlc_graph/mcp_server.py`, the `mcp` wiring (`register_mcp` body), `tests/graph/test_mcp.py` |
| **I** impact | `sdlc_graph/impact/**`, the impact and capabilities wiring (`register_impact` body), `tests/test_impact*.py` |
| **H** hooks | `hooks/**`, the `hooks` wiring (`register_hooks` body), `tests/graph/test_hooks.py` |
| **S** spanner | `sdlc_graph/spanner/**`, the `spanner` wiring (`register_spanner` body), `tests/graph/test_spanner.py` |
| integrator (phase 3) | everything, including `demo/`, README.md, docs/graph-decisions.md merges |

A change outside your paths goes in `needs_from_others`. The SQL files belong to the scaffold in
phase 1 and the integrator in phase 3: if you need a column or index, ask.

## 9. Orchestrator (increment 4, owner: orchestrator)

Design: docs/DESIGN.md section 12; long form: `docs/orchestrator.md`. The loop is
deterministic Python; its only state is the ledger plus `orchestrator_lock`. It never edits code (the
executor sessions do), never sets a feature `released`, and never makes `approved`/`merged`
transitions (its actor `agent:orchestrator` cannot).

**Schema** (`sql/004_orchestrator.sql`): `orchestrator_lock(feature_id pk, owner, heartbeat_at,
acquired_at, tick_seconds, meta)`, stale after 3 × `tick_seconds`; `run` gains `session_id, pid,
start_commit, log_path, summary, last_assistant_message, step, attempt` (orchestrated runs have
`agent_type = 'orchestrator'` and `step` in tests|implement|verify|ship|conformance|judge);
`decision` gains `severity` (P0|P1|P2, checked) and `meta jsonb` (`key` dedupes, `source =
'orchestrator'`, `step`, `worktree`). Evidence kind `conformance` `{score 0..1, findings[]}` is
registered in `ledger.evidence.KINDS` when `sdlc_graph.orchestrator` is imported.

```python
# orchestrator/__init__.py
CONFORMANCE_KIND = "conformance";  ACTOR = "agent:orchestrator"

# steps
Step(name, command, from_state, to_state, expects, frozen_check, needs_change, conformance, egress)
STEPS: dict[state, Step]   # ready->tests, tests_frozen->implement, implementing->verify, verified->ship
STEP_BY_NAME; HUMAN_STATES; FAILURES; CONFORMANCE_LOW = "conformance_low"
step_for(state) -> Step | None;  done_states(refinery=False) -> frozenset

# config
Config(repo, base=None, tick=60, max_runs=2, stall_minutes=30, max_failures=3, max_turns=40, budget_usd=5,
       max_cost_usd=None, reviewer="@reviewer", plugin_dirs=..., executor=..., conformance=True,
       conformance_threshold=0.8, conformance_reentries=2, judge=False, refinery=False, log_root=None)
executor_command(explicit=None) -> list[str]   # --executor, ORCHESTRATOR_EXECUTOR, else step_executor.sh
claude_command() -> list[str]                  # ORCHESTRATOR_CLAUDE or "claude"

# store
start_run(conn, slug, *, step, stage, model, attempt, meta=None, **cols) -> run row
update_run(conn, run_id, *, meta=None, **cols);  finish_run(conn, run_id, outcome, *, usage, summary, last_message, meta) -> row
get_run, open_runs(conn, slug), open_run_count(conn, slugs), step_runs(conn, slug, step, since=None), last_run(conn, slug)
acquire_lock(conn, feature, owner, tick_seconds, meta=None) -> row     # LockHeld
heartbeat(conn, feature, owner)                                        # LockLost
release_lock(conn, feature, owner=None) -> bool;  lock_status(conn, feature) -> row | None
escalate(conn, slug, *, key, question, severity="P1", options=None, recommendation=None, meta=None) -> decision row (deduped by key)
open_escalations(conn, slugs=None) -> list;  skipped(conn, slug) -> bool;  notify(decision)

# gitops
worktree_path(repo, slug) -> <repo>-<slug>;  branch_for(slug) -> "sdlc/<slug>"
ensure_worktree(repo, slug, base) -> (Path, created);  changed_files(wt, start) -> list[str]   # .claude/work/ excluded
dirty_files(wt);  dirty_snapshot(wt) -> {path: sha256|None};  new_dirty(wt, before) -> list[str]
snapshot(wt) -> commit sha | None   # worktree state as a commit object, HEAD/index untouched
restore(wt, commit, snap) -> discarded HEAD   # drift/frozen reset: HEAD at commit + snap's uncommitted work
reset_to(wt, commit) -> discarded HEAD;  outside(files, allowed_paths) -> list[str]

# dispatch
dispatch(conn, cfg, slug, step, *, feature, attempt, feedback=None, start_commit=None) -> run row
current_json(...), contract_md(conn, node), settings_for(node, step, cfg), prompt_for(...)
CHILDREN: dict[pid, Popen];  DENY_TOOLS

# gates
evaluate(conn, cfg, run, *, crashed=False) -> Verdict(outcome, reasons, transitioned, transition, reset_from, conformance, evidence_ids, result)
read_result(run) -> dict | None;  frozen_violations(conn, slug, wt) -> list[str];  pr_from_github(wt) -> url | None

# watchdog
alive(pid) -> bool;  stop(pid, grace=5);  patrol(conn, cfg, run) -> {"status": running|warned|finished|stalled, "run", "verdict"?}

# judge
call(prompt, schema, *, model, cwd=None, timeout=600, tools="Read Grep Glob") -> (structured, envelope)   # ModelCallError
parse_output(stdout) -> (structured, envelope)
conformance(conn, cfg, slug, wt, start_commit, *, attempt) -> {"score","findings","passed","evidence_id","threshold"}
judge(conn, slug, *, question, options, context) -> {"choice","reason","escalate"}

# loop
contracts_of(conn, feature);  topo_order(conn, feature) -> list[slug]   # ValueError on a cycle
tick(conn, cfg, feature) -> {"feature","done","running","contracts":[{"slug","state","action",...}],"escalations"}
orchestrate(feature, cfg, *, once=False, max_ticks=None, owner=None, connect=None, quiet=False)
    -> {"feature","owner","ticks","done","stopped": done|once|max_ticks|lock_lost|signal,"last"}   # LockHeld
stop(conn, feature) -> {"feature","released","was","note"};  refinery(conn, slug, wt) -> {"action","detail"}

# prime / feed
prime.prime(project_dir=None) -> str;  prime.prime_data(project_dir=None) -> dict      # never raise
feed.feed(conn, *, feature=None, since=None, limit=50) -> [{"at","kind","slug","what"}]
feed.escalations(conn, *, feature=None, all_open=False) -> [{"id","severity","slug","question","options","created_at","worktree","resolve"}]
```

Tick actions per contract: `running`, `stall_warned`, `dispatched`, `dispatch_failed`, `done`,
`skipped`, `waiting_on_dependency`, `blocked_on_decision`, `waiting_on_human`, `escalated`,
`queued`, `budget_spent`, `refinery`, `nothing`.

**CLI** (`register_orchestrator`): `orchestrate <feature> [--once] [--tick 60] [--max-runs 2]
[--refinery] [--repo P] [--base B] [--stall-minutes 30] [--max-turns 40] [--budget 5] [--max-cost USD]
[--reviewer @h] [--executor CMD] [--plugin-dir D]... [--no-conformance] [--judge] [--max-ticks N]
[--live] [--quiet] [--stop]` (the default executor needs `--live`; `lock_held` exits 1); `feed
[--feature F] [--since ISO] [--limit 50]`; `prime [--project-dir D]` (always exits 0); `escalations
[--feature F] [--all]`. **Hook**: `hooks/hooks.json` SessionStart runs `sdlc-graph prime` (through
`uv run --project ${CLAUDE_PLUGIN_ROOT}`) only when `.claude/work/current.json` exists, falling back to
`hooks/session_prime.sh`; it always exits 0. **Skill**: `skills/orchestrate/SKILL.md`
(human-invoked only). **Tests**: `tests/graph/test_orchestrator.py` (fake executor, fake claude, temp git
repos), `tests/graph/test_orchestrator_units.py`.
