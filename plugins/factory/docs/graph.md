# graph (sdlc-graph)

A code graph derived from source for every commit SHA, an intent ledger whose state machines move
only on evidence, and impact-ordered verification, served to Claude Code over MCP and exported to
Spanner Graph. Design: `../../../docs/DESIGN.md` sections 2, 3, 4, 9 and 10. Interfaces:
`graph-architecture.md`. Judgment calls: `graph-decisions.md`. The walkthrough on the sample app: `../demo/DEMO.md`.

- **Code graph.** Extractors rebuild modules, classes, functions, routes (with `include_router`
  prefixes and mounted/unmounted), models, env reads, packages, frontend fetch calls, tests and
  CODEOWNERS from a commit. Later commits are stored as deltas and replay exactly.
- **Ledger.** Epics, features, contracts, releases and incidents move through state machines whose
  guards cite evidence rows. Local evidence is advisory; CI evidence is binding. The MCP server
  acts as one fixed `agent:*` identity set at launch (`SDLC_ACTOR`, default `agent:mcp`) and takes
  no `actor`, `source` or `signed_by` from a caller, so an agent holding only the MCP tools cannot
  resolve decisions, write binding CI evidence, or move anything into approved, merged, released or
  closed. Read "Trust boundary" below before you say more than that.
- **Impact.** Affected tests (closest first), routes and owners from the graph, recorded as a
  prediction before a run and scored against the JUnit report after it.
- **Routing.** Each stage maps to a model tier; planner (Fable) runs are budgeted per epic. Two
  Claude Code hooks pin the model of every Agent spawn and record each run.
- **Spanner Graph.** The same tables as GoogleSQL with `CREATE PROPERTY GRAPH SdlcGraph`, loaded
  from Postgres into the local emulator and queried with GQL.

## The rule: derived, never written

The code graph (`snapshot`, `sym`, `sym_edge`, `sym_delta`, `owner`) is written only by `index`
and `delta`, from `git archive` of a commit. No API, MCP tool or agent writes it, and
`verify-snapshot` proves it: it re-extracts the commit and diffs it against the stored rows.

The ledger (`node`, `edge`, `transition`, `evidence`, `decision`, `run`) is written only through
the typed functions in `sdlc_graph/ledger/`, which evaluate the guards. A guard failure is a normal
`{"ok": false, "missing": [...]}` result, not an exception. Predictions are proposals: nothing the
impact layer computes changes ledger state. Spanner is a read model rebuilt from Postgres.

## Trust boundary (what the guards do and do not stop)

- **Held:** a caller of the MCP server cannot choose its identity. A forged `actor` argument is
  ignored, `evidence_add` stores local (advisory) rows only, and `decision_resolve` always refuses.
  Approve, merge, release, close, decision resolution and CI evidence go through the CLI
  (`--actor @you`, `evidence add --source ci:...`).
- **Not held:** the CLI's `--actor` and `--source` are strings the operator types. An agent that can
  run shell commands as you can type `@you`. The guard is therefore advisory against that agent
  until the CLI runs outside the agent's sandbox. CI evidence itself is verified (GitHub OIDC, D9).
  Say "an agent with only the MCP tools cannot approve or merge", not "agents cannot".
- Ticket screening is a regex heuristic, advisory by design: ticket-derived acceptance rows carry
  `source: ticket` and need a human to mark the contract ready; titles are single-line and
  control-character free; `--ticket` is literal text (use `--ticket-file` for a file you chose).
- A capability that widens the sandbox (egress, secrets, a test command) needs a human to mark the
  contract ready; `test_command` is limited to pytest runners and secrets never lift `~/.ssh` or `~/.aws`.
- The spawn guard reserves a planner slot atomically (ledger advisory lock when `SDLC_EPIC` is set,
  else a `flock`ed counter file that fails closed when corrupt). The counter file is a convenience
  an agent could delete; the ledger is the enforcement.

## Quick start

```bash
uv venv --python 3.11 .venv && uv sync
docker compose up -d --wait db                     # Postgres (pgvector) on localhost:5435
.venv/bin/sdlc-graph db init
docker compose --profile spanner up -d spanner     # optional: Spanner emulator on localhost:9110
.venv/bin/python -m pytest -q                      # DB tests need Postgres; the export test skips unless the emulator is up (smoke starts its own)
bash demo/run_demo.sh                              # the whole flow on a copy of examples/sample-app, about 15 s
```

`DATABASE_URL` defaults to `postgresql://sdlc:sdlc@localhost:5435/sdlc` (port 5435, not the usual
5433, D1); `SDLC_SCHEMA` picks a schema (tests use their own). `SPANNER_EMULATOR_HOST` defaults to
`localhost:9110` (away from an emulator on the default 9010, D2). A gitignored `.env` in this
directory is read for one thing only, an embedding key (see Embeddings); nothing else is read from it.

The tests use `examples/sample-app` (copied into a temporary git checkout). `SAMPLE_REPO=<path>`
optionally names a second repository (any git checkout) for the tests that only need "some real
repository" (extract, index and verify it); unset, those few skip.

## CLI

Every command prints a table, or JSON with `--json`. Exit codes: 0 ok, 1 a refusal or diff
returned as a normal result, 2 bad input or forbidden actor, 3 not implemented, 4 not found,
5 database unavailable.

| Command | What it does |
|---|---|
| `db init` / `db reset` | apply `sql/*.sql` (idempotent) / drop the tables in the schema and re-init |
| `index --repo P [--sha S] [--name N]` | extract one commit into a full snapshot (the checkout is only read) |
| `index build [--repo R] [--dry-run]` / `index search TEXT [--scope node\|sym]` / `index status` | embed nodes and symbol summaries (needs a key) / hybrid search / what is indexed |
| `delta --repo P --from A --to B` | store B as a delta against A's stored snapshot |
| `verify-snapshot --repo P [--sha S]` | re-extract and diff against the stored snapshot (exit 1 on a diff) |
| `query impact PATH\|KEY ... [--repo R]` | static blast radius: symbols, routes, tests, owners |
| `query deps SYMBOL` / `query owners PATH... --repo R` / `query search TEXT` / `query snapshots` | neighbourhood, CODEOWNERS, symbol search, stored snapshots |
| `node create` / `node draft [--contract MD] [--strict] [--default LINE]...` | create an epic, feature, etc. / draft a contract through intake (dedupe, overlap rule, ticket flags); `--contract` fills every block not passed explicitly from the contract.md (Allowed paths, Capabilities, Defaults when ambiguous, Acceptance criteria, Risk tier, Decisions) and stores the text; `--strict` refuses a draft with a missing block |
| `contract update SLUG --from-file MD --actor A` / `contract parse MD` | re-read a changed contract.md onto a drafted or ready contract (refused from tests_frozen on; records `contract_updated` evidence, e.g. a product owner's `@handle` in Decided by) / print the parse, touching nothing |
| `node get` / `node neighbours` / `node link` / `node transition SLUG STATE --actor A` | node view, ledger edges, guarded transition |
| `evidence add SLUG --kind K --payload JSON [--source ci:URL --oidc-token JWT]` | one evidence row |
| `decision propose` / `decision resolve ID --choice C --actor @h` | anyone proposes; only a human resolves |
| `workflow next SLUG` | next state, `/factory:` command, stage, tier and model, missing guards, Fable budget |
| `run start` / `run finish` / `run budget SLUG` | record a run (requested vs resolved model, tokens); the epic's Fable budget |
| `metrics snapshot` | per-stage metrics computed from ledger rows |
| `impact affected` / `impact predict` / `impact outcome --junit F` / `impact summary` | ordered affected set, store a prediction, score it, mean precision/recall |
| `capabilities compile SLUG [--write F]` | a contract's paths/egress/data/secrets as a Claude Code settings fragment |
| `hooks settings` | the settings.json fragment that installs both hooks, with absolute paths |
| `spanner ddl [--out F]` / `spanner smoke [--from-pg]` | GoogleSQL + property graph DDL / emulator smoke test |
| `spanner export [--slug S --symbol Q]` / `spanner query NAME\|GQL` | load Postgres into the emulator and run the GQL queries / query it live |
| `mcp [--http --port N] [--insecure-no-auth]` | the MCP server on stdio (or Streamable HTTP with OAuth, below) |
| `epic decompose SLUG [--root DIR]` | proposed feature nodes from the epic's file, walking skeleton first; writes nothing |

## Remote MCP with OAuth (Streamable HTTP)

`--http` makes the server an OAuth 2.0 resource server: every request needs `Authorization: Bearer <JWT>` issued by
your identity provider, and the protected-resource metadata (RFC 9728) is served at
`/.well-known/oauth-protected-resource/mcp`, so managed settings can allow-list the server. The server verifies
tokens (RS/ES/PS signature against the issuer's JWKS, `iss`, `aud`, `exp`, `nbf`, required scopes); it issues none.
stdio is unchanged and never checks tokens.

| Variable | Meaning |
|---|---|
| `SDLC_MCP_OAUTH_ISSUER` | issuer URL; the token's `iss` must equal it (required) |
| `SDLC_MCP_OAUTH_AUDIENCE` | expected `aud` (required) |
| `SDLC_MCP_OAUTH_SCOPES` | scopes every token must carry, space or comma separated (default none) |
| `SDLC_MCP_OAUTH_JWKS_URL` | signing keys (default `<issuer>/.well-known/jwks.json`) |
| `SDLC_MCP_OAUTH_JWKS_FILE` | a local JWKS file instead of the URL (air-gapped) |
| `SDLC_MCP_RESOURCE_URL` | this server's public URL for the metadata (default: the audience when it is a URL) |

`--http` without `SDLC_MCP_OAUTH_ISSUER` and `SDLC_MCP_OAUTH_AUDIENCE` refuses to start. For local use pass
`--insecure-no-auth`: no authentication, bound to 127.0.0.1 only (a `--host` elsewhere is refused). Put TLS in front
of the server (a reverse proxy or load balancer); the token proves who is calling, the actor of every call is still
the server's fixed `SDLC_ACTOR`.

## MCP registration

The server starts without a database. While no reachable, initialised Postgres exists, every tool answers
`{"ok": false, "error": "graph not configured", "hint": "set DATABASE_URL ...", "reason": ...}` (credentials redacted),
and `graph_status` (CLI: `sdlc-graph status`, exit 1 when not configured) returns `{configured, provider, reachable,
schema_version, snapshots, nodes, url, hint, error}`; `provider` comes from `graph.provider` in `.claude/sdlc.json`
when the project sets it, else from the URL's host (`local`, `neon`, `aws`, `postgres`).

The server is `graph` (Claude Code names its tools `mcp__graph__<tool>` from a project `.mcp.json`,
and `mcp__plugin_factory_graph__<tool>` when the plugin provides it): eleven read-only
tools (`graph_status`, `graph_deps`, `graph_impact`, `graph_owners`, `graph_search`, `graph_snapshot`, `node_get`,
`node_search`, `node_neighbours`, `workflow_next`, `metrics_snapshot`, annotated `readOnlyHint`) and eight guarded
writes (`contract_draft` (pass the whole contract.md as `contract_md`; strict by default, so a draft with a missing
block is refused), `contract_update`, `contract_transition`, `evidence_add`, `decision_propose`,
`decision_resolve` (always refused: it needs a human), `run_start`, `run_finish`), plus resources `contract://{slug}`,
`epic://{slug}` and `rules://{repo}/{glob}`. Register it with the project's
`.mcp.json` (template: `mcp.example.json`):

```json
{"mcpServers": {"graph": {
  "command": "/path/to/lathe/plugins/factory/.venv/bin/python",
  "args": ["-m", "sdlc_graph.cli", "mcp"],
  "env": {"DATABASE_URL": "postgresql://sdlc:sdlc@localhost:5435/sdlc", "SDLC_ACTOR": "agent:claude"}}}}
```

or `claude mcp add graph -- /path/to/lathe/plugins/factory/.venv/bin/sdlc-graph mcp`.
List results come back as `{"ok": true, "result": [...]}` and errors as
`{"ok": false, "error": {"type", "message"}}`. A glob in a `rules://` URI must be percent-encoded
(`rules://sample-app/app%2Froutes%2F*`). The hooks: `sdlc-graph hooks settings` prints the
`PreToolUse`/`PostToolUse` fragment for `.claude/settings.json`.

## Claude Code plugin

This directory is the plugin `graph` (`.claude-plugin/plugin.json`; install with
`/plugin marketplace add ishpr/lathe`, then `/plugin install factory@lathe`). It ships the MCP server
(`.mcp.json`: `uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph mcp`, with `DATABASE_URL` passed
through and `SDLC_ACTOR=agent:claude`), seven skills (`/factory:index`, `impact`, `contract`,
`next`, `feed`, `orchestrate`, `demo`) and `hooks/hooks.json` (the Agent spawn guard, the run recorder, and a
SessionStart hook that primes a session that has `.claude/work/current.json`). **`uv` must be
installed**; Postgres must be reachable at `DATABASE_URL` (default `localhost:5435`). Check with
`claude plugin validate .`.

## Embeddings (optional)

The retrieval index (`sql/005_index.sql`, `sdlc_graph/index/`) adds meaning-based search over ledger
nodes and the latest snapshot's symbols: cosine over an HNSW index plus Postgres full-text rank,
merged by reciprocal rank fusion. It is off until you give it a key, and **nothing is embedded, sent
to any API or written to the `chunk` table without one**. Without a key `graph_search` keeps its
trigram match, `node_search` matches titles and slugs, and `index build` refuses with the instruction
(`index build --dry-run` still counts what it would embed, with no key and no network).

Where the key goes: `OPENAI_API_KEY` in your shell, or one line `OPENAI_API_KEY=...` in the gitignored
`plugins/factory/.env` (never committed, never printed; only `OPENAI_API_KEY` and `VOYAGE_API_KEY` are ever
read from that file). `EMBED_PROVIDER` picks the provider: `openai` (`text-embedding-3-small`,
`dimensions=1024`; the default whenever an OpenAI key is found), `voyage` (`voyage-4-lite`, or
`voyage-code-4` via `VOYAGE_MODEL`; needs `VOYAGE_API_KEY`) or `none`.

```bash
sdlc-graph db init                                  # applies sql/005_index.sql too
sdlc-graph index build --repo sample-app            # repo name or checkout path; embeds only what changed
sdlc-graph index search "rate limit the login" --scope node
sdlc-graph index status                             # chunks per source and model, and the active provider
```

What is embedded: one chunk per ledger-node section (title, acceptance, decisions, scope, and each
free-text `attrs` key such as `riskiest_assumption`) and one short summary per symbol (kind,
qualname, path, scalar attrs). Source code is never embedded. Re-running `index build` re-embeds only
changed text or a changed model and deletes chunks whose node or symbol is gone; a query only compares
vectors made by the same model. If the provider is unreachable at query time, search degrades to
full-text only (`mode: lexical`) rather than failing. Cost on a brownfield FastAPI repository of about
90 tests: 524 chunks, about 61 thousand characters.

## Where the orchestrator's Claude comes from

`sdlc-graph orchestrate <feature> --executor local|runner`: `local` (default) uses the engineer's own Claude Code login;
`runner` refuses to start without an API key, a subscription token, Claude Platform on AWS or Bedrock. Every run row
records `executor` and `auth_source`; see "Where the executor's Claude comes from" in `docs/orchestrator.md`.

## Demo

`bash demo/run_demo.sh` resets the local database, copies `examples/sample-app` (or `SAMPLE_REPO`, a
different repository with an auth dependency in `DEMO_AUTH_FILE`), and runs ten steps: index, delta and
verify; impact of `app/auth.py`; the sample app's rate-limit contract from draft (with its autonomy
blocks attached at draft time) to merged, with the overlap refusal, human-only decisions and the
binding-evidence refusal, and the ledger-rendered contract checked by the pack's
`acceptance.py --ready`; routing and the Fable budget; the prediction scored against the sample app's
real pytest run; capabilities; the Spanner Graph export on the emulator; and the MCP server on stdio.
`demo/DEMO.md` has the talking point per step and what to do if one fails.

## Status and limits

- CI evidence is binding only with a verified GitHub OIDC token (D9): `--source ci:<run url>
  --oidc-token <jwt|@path|env:VAR>`; a `ci:` row without one is refused. The CI templates' `evidence` job
  (needs the `SDLC_DATABASE_URL` secret and `.claude/work/current.json`; skips otherwise) posts it. Set
  `SDLC_OIDC_AUDIENCE` on the ledger side only if you change the template's audience (default `sdlc-graph`).
- The contract machine has no rework edge (`in_review -> implementing`); D15 is open.
- CODEOWNERS is stored per snapshot and "latest" means the newest commit (`committed_at`), not the
  last one indexed (supersedes INT9).
- Two "impact" answers exist on purpose. Postgres `affected()` is the reverse import/call/test closure
  plus conftest expansion, HTTP test-to-route edges, the module-level closure of a touched function
  (ordered last) and mirror paths. The Spanner `tests_reaching` GQL is structural reachability over the
  same three rels to the same depth, so it is a subset of the Postgres answer; the tests assert that.
- `affected()` reports touched paths and symbols that are absent from the snapshot in `unresolved`.
- Spanner graph algorithms are not demoed: the emulator returns empty for them (S1).
