# sdlc_graph

The optional code graph and ledger half of the `factory` plugin. It keeps two stores in one PostgreSQL database with pgvector. The code graph is derived from git, one snapshot per commit. The ledger holds nodes, edges, evidence, decisions and runs, and every state change in it is a guarded transition. On top sit an impact analysis, an orchestrator daemon, an MCP server named `graph` that the skills call, and the `sdlc-graph` command line. PostgreSQL is the source of truth. The Spanner export is a read-only mirror.

Every graph step in the skills is optional. With the graph off, a skill prints one line and skips the step. The hooks, the contract files and the evidence files work the same either way.

## Why a team would turn it on

- **It tells you what a change touches before you run everything.** `graph_impact` lists the symbols, routes, tests and CODEOWNERS owners a change can reach, closest tests first. It is static reachability: use it to order work, not to skip tests.
- **It is a system of record a human can read and an agent cannot forge.** A contract changes state only when evidence rows and the right kind of actor satisfy its guards. `sdlc-graph node get` and `sdlc-graph feed` show the state, the evidence and the history. The MCP server acts as one fixed `agent:*` identity, stores only local (advisory) evidence, and cannot approve, merge, release or close anything. That holds against an agent that has only the MCP tools; see Limits.
- **It works a feature to a pull request without a person in the loop, and stops at the production gate.** Once a human has readied the contracts, `sdlc-graph orchestrate` runs each one through tests, implement, verify and ship in its own git worktree, one headless Claude Code session per step. It checks every result itself before it moves the ledger. Approving, merging and releasing need a human or CI actor, so it stops there.

A separate console, outside this directory, reads the same ledger through an API to show epics and their work moving through stages.

## The ledger (`ledger/`, `../sql/`)

Tables: `node`, `edge`, `evidence`, `transition`, `decision`, `run`, plus `prediction` and `prediction_outcome` (impact), `orchestrator_lock`, `chunk` (retrieval index) and the view `assumption`. Every write goes through a typed function, and every state change is a guarded transition. Actors are strings: `@handle` for a human, `agent:<alias>` for an agent, `ci:<system>` for CI.

**Nodes.** Kinds: `epic`, `feature`, `story`, `contract`, `change`, `release`, `incident`, `decision`, `rule`, `repo`. A node has a slug, a state and an optional tier (`trivial`, `low`, `medium`, `high`). It also carries `touched_symbols`, `capabilities`, `acceptance`, `attrs` and `contract_md`, which contracts use. States come from `ledger/state_machines.json`:

| Kind | States |
|---|---|
| `contract` | `drafted`, `ready`, `tests_frozen`, `implementing`, `verified`, `in_review`, `approved`, `merged` |
| `epic` | `drafted`, `accepted`, `in_progress`, `measured`, `closed`, `rejected` |
| `feature` | `proposed`, `ready`, `building`, `released`, `evaluated` |
| `release` | `requested`, `staged`, `released`, `verified`, `rolled_back` |
| `incident` | `open`, `triaged`, `intent`, `closed` |

The other kinds sit in state `active` and refuse transitions. A `repo` node, one per repository, carries repository-level evidence.

**Relations** between nodes: `decomposes_into`, `depends_on`, `verified_by`, `shipped_in`, `supersedes`, `caused_by`, `governed_by`. Guards read `decomposes_into`, `shipped_in` and `caused_by`. The overlap rule and the orchestrator read `depends_on`. The other three are stored and nothing reads them yet.

**Evidence.** A row has a kind, a source, an optional `signed_by`, a JSON payload and a time. Rows are never updated or deleted. Kinds: `tests_failing_first`, `tests_frozen`, `verify_stamp`, `acceptance`, `ratchet`, `review_approval`, `ci_run`, `pr_url`, `merge_sha`, `ready_poll`, `smoke`, `guardrail_metric`, `metrics_snapshot`, `triage`, `assumptions`, `superseded`, `contract_updated`, `graph_provisioned`, and `conformance` from the orchestrator. Source `local` is advisory. A row is binding only when its source starts with `ci:` and `signed_by` is set.

**Decisions.** A question on a node with options and an optional recommendation. Anyone may propose one. Only a `@handle` may resolve it; an agent or CI actor gets `PermissionError`. The orchestrator's escalations are decisions with a severity of P0, P1 or P2.

**Runs.** One row per agent run: stage, requested and resolved model, tier, tokens, cost, outcome. `ledger/routing_policy.json` maps stages to tiers (planner `fable`, orchestrator `opus`, executor `sonnet`, utility `haiku`) and allows 3 planner runs per epic (`fable_budget_per_epic`). A planner run over an epic's budget is recorded with outcome `refused` and then refused. A human raises an epic's budget by resolving a decision on it whose question is `fable_budget_increase`, with an integer choice. Two plugin hooks pin each Agent spawn's model to this policy and record the run. A SessionStart hook runs `sdlc-graph prime` when `.claude/work/current.json` exists, and prints the contract, its state and the next step.

**Guards.** Each transition in `state_machines.json` lists guard strings. `evidence:<kind>` holds when a row of that kind exists; the newest wins, and a `verify_stamp` that carries the kind as a sub-object counts. `evidence:<kind>.<field>` needs that payload field to be truthy, and `==<json>` needs it to equal a value. The segments `binding`, `green` and `non_author` test the row itself. `actor:human|ci` lists the actor classes allowed to make the move. Named checks: `decisions_resolved`, `acceptance_present`, `allowed_paths_present`, `defaults_present`, `capabilities_present`, `capabilities_approved`, `ticket_text_reviewed`, `acceptance_covered`, `failing_first_reasons`, `frozen_tests_match`. `edge_out:<rel>` and `edge_in:<rel>` test edges.

Take `in_review -> merged`. Its guards are `evidence:merge_sha` and `actor:human`. The first holds when a `merge_sha` row (payload `sha`) is on the node. The second holds when the move is made by a `@handle`. Both must hold, so an agent cannot merge by recording a SHA, and a person cannot merge on the ledger without one. `approved -> merged` takes `actor:human|ci`. `in_review -> approved` needs a review approval whose reviewer is not the author, a green and binding `ci_run`, and a human or CI actor. Whatever the guards say, an `agent:*` actor moving a node into `approved`, `merged`, `released` or `closed` is refused with `actor:not_agent`. A refusal is a normal result, `{"ok": false, "missing": [...]}`, and is logged in `node.attrs.refused_transitions`.

**Contracts.** `contract_draft` takes the whole `contract.md`, parses its blocks (Outcome, Acceptance criteria, Acceptance, Decisions, Defaults when ambiguous, Capabilities, Out of scope, Allowed paths, Risk tier, Evidence plan) and scans the ticket text for commands, credentials and injection phrases (a regex heuristic). It dedupes, then applies the overlap rule: an open contract in the same repo with overlapping `touched_symbols` must be linked by `depends_on`, or the draft returns `conflict` and creates nothing. The other statuses are `created`, `duplicate` and `refused` (strict mode and a required block is missing; the MCP tool is strict by default).

`drafted -> ready` needs resolved decisions, acceptance rows, allowed paths, the defaults and capabilities blocks, and a human for anything that widens the sandbox (egress, secrets, shell commands, a test command). `contract_update` re-reads a changed `contract.md` while the contract is `drafted` or `ready`; from `tests_frozen` on, paths and tests are frozen.

## The code graph and impact (`codegraph/`, `impact/`, `index/`)

The graph is derived and never written through an API. `index` runs the extractors over `git archive` of one commit and stores a full snapshot; the checkout is only read. `delta` stores a later commit as a delta against a stored snapshot, and replaying it reproduces the extraction exactly. `verify-snapshot` re-extracts a commit, diffs it against the stored rows and exits 1 on a difference. Tables: `snapshot`, `sym`, `sym_edge`, `sym_delta`, `owner`.

Extractors cover Python (the standard library `ast`), Python package manifests, CODEOWNERS, frontend `fetch` and `axios` calls, and TypeScript for Next.js and Foldkit apps. The last two are regex based and take their paths from `.claude/sdlc.json` (`paths.app`, `frontend`). There is no Go or Rust extractor. Symbol kinds: `module`, `class`, `function`, `method`, `route`, `model`, `env_var`, `package`, `frontend_call`, `test`, `message`, `command`, `view`, `route_fe`. A symbol is named `kind:qualname@path`. Edge relations: `imports`, `calls`, `defines`, `exposes_route`, `includes_router`, `reads_env`, `uses_model`, `depends_on_package`, `fetches`, `tests`, `handles`, `calls_api`. A guess carries `attrs.confidence = "low"`. A call that cannot be resolved is dropped.

`graph_impact` (CLI: `query impact`) walks `imports`, `calls`, `tests`, `handles` and `calls_api` backwards from changed paths or symbols, to depth 4 by default. It adds tests whose path mirrors a touched module, and returns symbols, routes, tests and owners, with a reason for each test and route. Tests come closest first: direct, transitive, module-level closure, then mirror-only. A path or symbol the snapshot lacks is listed under `unresolved`. Owners come from CODEOWNERS, last matching rule wins, stored per snapshot.

`impact predict` stores a prediction for a contract, and `impact outcome --prediction ID --junit FILE` scores it against the tests that failed (precision and recall). Predictions are proposals and never change ledger state. `capabilities compile SLUG` turns a contract's capabilities into a Claude Code settings fragment and says what settings cannot enforce (`data`, `secrets`). `index build` is optional: it embeds ledger node sections and symbol summaries with OpenAI or Voyage (`OPENAI_API_KEY`, `VOYAGE_API_KEY`, `EMBED_PROVIDER`). Without a key nothing is embedded or sent, and search falls back to name and title matching. Source code is never embedded.

## The orchestrator (`orchestrator/`)

`sdlc-graph orchestrate FEATURE` takes a `feature` node and works the contracts it `decomposes_into`. The loop is deterministic Python. Its only state is the ledger and one `orchestrator_lock` row per feature, so it can be stopped and restarted at any point. With the graph off it exits 5 (`graph_off`); with an unreachable or uninitialised database it exits 5 (`ledger_unavailable`). The default executor starts real, paid `claude -p` sessions, so it also needs `--live`.

A tick, every `--tick` seconds (60):

1. Refresh the lock. A second daemon on the same feature exits with `lock_held`; a lock goes stale after three ticks. A `ready` feature moves to `building`.
2. Visit the contracts in `depends_on` order. A finished session goes through the gates. A session with no new evidence, transition or log output for `--stall-minutes` (30) is warned, and one window later its process group is stopped.
3. Skip contracts that are done (`in_review`, `approved` or `merged`; with `--refinery` only `merged`) or that a human skipped. Wait on unmerged dependencies, open decisions and `drafted` contracts. With `--stack`, one unmerged dependency that is in review is enough: the dependent branches from it and opens a stacked pull request. A contract with no defaults or no capabilities block is never dispatched; it gets a P1 decision.
4. Dispatch the one step the contract's state maps to, if `--max-runs` (2) and the cost cap allow:

| State | Step | The session must record | Moves to |
|---|---|---|---|
| `ready` | `/factory:tests` | `tests_failing_first`, `tests_frozen` | `tests_frozen` |
| `tests_frozen` | `/factory:implement` | a change inside the allowed paths | `implementing` |
| `implementing` | `/factory:verify` | `verify_stamp` | `verified` |
| `verified` | `/factory:ship` | `pr_url` | `in_review` |

**Worktrees.** Each contract gets a git worktree `<repo>-<slug>` next to the checkout, on branch `sdlc/<slug>`. Before each step the orchestrator writes `.claude/work/current.json` from the ledger and, if `commands.setup` is set in `.claude/sdlc.json`, installs dependencies. The session runs `claude -p` with `--permission-mode dontAsk --permission-prompts none`. Its tool list is compiled from the pack's base list, the stack's commands and the contract's `## Capabilities`; anything outside it is denied and reported, never prompted for. It cannot call `contract_transition`, `decision_resolve` or the `sdlc-graph` CLI. Its environment is an allowlist: `ANTHROPIC_*` and `CLAUDE_*` variables, the ledger URL for recording evidence, and the GitHub token for `ship` only. Anything else needs `ORCHESTRATOR_ENV_PASSTHROUGH`.

**Gates.** After a session ends the orchestrator does not take its word. In order:

1. No one but the orchestrator or a human moved the ledger state during the run.
2. Drift: changed files stay inside `allowed_paths`. If not, the tree is saved under `refs/sdlc/discarded/run-<id>` and the worktree is reset.
3. Frozen test files still match their recorded sha256.
4. The expected evidence exists and is true. The orchestrator runs the acceptance tests itself (`--test-command`, else `commands.test` in `.claude/sdlc.json`): they must fail after `tests` and pass after `verify`. The pull request must be the one `gh pr view sdlc/<slug>` reports on `origin`.
5. `tests` and `implement` left a change inside the allowed paths.
6. The ratchet reports nothing new.
7. The defaults the session applied (`assumptions.md`) are well formed and contradict no decision. After `implement`, a reviewer scores the diff against the acceptance criteria; the score must reach 0.8, and a low one re-enters `implement` with the findings, twice at most.
8. Only then does it call `transition` as `agent:orchestrator`, so the ledger's own guards apply.

**Failures and escalations.** A failed step is retried with a fresh session and the reasons in its prompt. Among the things that open an escalation: the third failure of a step, a third low conformance score, a session that stopped to ask a question, a permission denial, a vanished worktree and a `depends_on` cycle. An escalation is a decision with severity P0, P1 or P2. A contract with an open decision is not dispatched. `sdlc-graph escalations` lists them, P0 first. A human answers with `sdlc-graph decision resolve ID --choice retry --actor @you`. `retry` restarts the failure count, `skip` counts the contract as done, and a session's question takes a plain-text answer. Notices print to stderr; `ORCHESTRATOR_NOTIFY=none` silences them.

**Budgets.** `--budget` USD per session (5), `--max-turns` per session (40), `--max-cost` per feature (50; `--no-cost-limit` removes it). At the cost limit a P2 decision asks `raise` or `stop`.

**Other options.** `--executor local` (default) uses the machine's own Claude Code login. `--executor runner` refuses to start unless `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, Claude Platform on AWS or `CLAUDE_CODE_USE_BEDROCK=1` is set. Every run row records `executor` and `auth_source`. `--judge` adds a bounded judge call after a step fails twice. `--refinery` merges a pull request on GitHub only when GitHub already reports it approved, and records `merge_sha`; the ledger's `approved` and `merged` stay with a human or CI. `--once` runs one tick. `--stop` releases the lock and leaves worktrees alone.

## The MCP server (`mcp_server.py`)

Server `graph`, started by the plugin's `.mcp.json` as `uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph mcp` (`--http --port N` serves Streamable HTTP instead of stdio). Claude Code names the tools `mcp__plugin_factory_graph__<tool>`. The server starts with no database, opens a connection per call, and reads the connection string on every call.

- Read-only (11): `graph_status`, `graph_deps`, `graph_impact`, `graph_owners`, `graph_search`, `graph_snapshot`, `node_get`, `node_search`, `node_neighbours`, `workflow_next`, `metrics_snapshot`.
- Guarded writes (8): `contract_draft`, `contract_update`, `contract_transition`, `evidence_add`, `decision_propose`, `decision_resolve`, `run_start`, `run_finish`.
- Resources: `contract://{slug}`, `epic://{slug}`, `rules://{repo}/{glob}`.

Identity comes from the server, not the caller. It runs as one actor from `SDLC_ACTOR` (the plugin sets `agent:claude`, the default is `agent:mcp`, and anything that is not `agent:*` refuses to start). No tool takes an actor, and one sent anyway is ignored. `evidence_add` takes no `source` or `signed_by`, so it cannot write binding CI evidence. `contract_transition` refuses `approved`, `merged`, `released` and `closed`. `decision_resolve` always refuses. A human or CI does those through the CLI.

Replies are JSON: a list is wrapped as `{"ok": true, "result": [...]}`, an error is `{"ok": false, "error": {"type", "message"}}`, and a refused guard is `{"ok": false, "missing": [...]}`. With no reachable, initialised database every tool but `graph_status` answers `graph not configured` and a hint; with the graph off it answers `graph off` and opens nothing.

The skills are the callers. `/factory:ticket` and `/factory:contract` use `graph_snapshot`, `graph_impact`, `contract_draft` and `contract_update`. `/factory:tests`, `/factory:verify` and `/factory:ship` use `evidence_add` and `contract_transition`. `/factory:implement` and `/factory:next` use `workflow_next`. `/factory:impact` uses `graph_impact`, `graph_deps`, `graph_owners` and `graph_search`. An orchestrated session has `graph_impact`, `node_get`, `workflow_next` and `evidence_add` in its allow list, and its settings deny `contract_transition` and `decision_resolve`. The session records evidence and the orchestrator moves the state.

## The CLI (`cli.py`)

`sdlc-graph COMMAND`, run as `uv run --project plugins/factory sdlc-graph COMMAND`. The examples below leave out that prefix. Every leaf command takes `--json`; without it the result prints as a table or text. Exit codes: 0 ok, 1 a refusal or a diff returned as a normal result, 2 bad input or a forbidden actor, 3 not implemented, 4 not found, 5 database unavailable.

- Database: `db init`, `db reset` (both take `--schema`).
- Code graph: `index --repo PATH [--sha S] [--name N]`, `delta --repo PATH --from A --to B`, `verify-snapshot --repo PATH`, `query deps|impact|owners|search|snapshots`. In `index`, `delta`, `verify-snapshot` and `orchestrate`, `--repo` is a checkout path. In `query` it is the stored repository name.
- Retrieval: `index build [--dry-run]`, `index search TEXT [--scope node|sym]`, `index status`.
- Ledger: `node create|draft|get|neighbours|link|transition`, `contract update|parse`, `evidence add`, `decision propose|resolve`, `workflow next`, `run start|finish|budget`, `metrics snapshot`.
- Impact: `impact affected|predict|outcome|record|summary`, `capabilities compile`.
- Orchestrator: `orchestrate FEATURE`, `feed`, `escalations`, `prime`.
- Server and status: `mcp`, `status` (exit 1 when not configured), `provisioned`, `hooks settings`.
- Spanner: `spanner ddl|smoke|export|query`.

## Provisioning and configuration (`graphcfg.py`, `status.py`, `../docker-compose.yml`)

`/factory:init` asks two questions once (headless: the defaults) and writes the `graph` block of `.claude/sdlc.json`. `--graph on|off` and `--graph-provider` answer them up front.

```json
"graph": {"enabled": true, "provider": "local", "url_env": "DATABASE_URL",
          "spanner": {"project": "", "instance": "", "database": ""}}
```

`enabled` turns the graph on or off. `provider` is `local`, `neon`, `aws` or `spanner`. `url_env` names the variable that holds the connection string, `DATABASE_URL` by default. The string itself is never in that file or in git. The first of these that is set wins:

1. The variable `url_env` names, in the shell (the plugin's own local default counts as unset).
2. `env.<url_env>` in `.claude/settings.local.json`.
3. `<url_env>=...` in `.env`. Both files are gitignored.
4. The local default `postgresql://sdlc:sdlc@localhost:5435/sdlc`.

The files are read on every call, so a URL stored mid-session needs no restart. `SDLC_SCHEMA` picks a schema (default `public`). The default credentials are for a throwaway local database bound to `127.0.0.1`; use a secret store for anything shared.

Later, `/factory:graph status|on|off|url|provision <provider>` shows or changes it. `provision` is idempotent, ends with `sdlc-graph db init` and `status`, and records `graph_provisioned` evidence on the `repo` node once per database.

- `local`: `docker compose up -d db` on the plugin's compose file (`pgvector/pgvector:pg16`, `127.0.0.1:5435`; `SDLC_PG_PORT` and `SDLC_PG_CONTAINER` override). No decisions.
- `neon`: a Neon project `<repo>-graph` on Postgres 17, through the Neon MCP or `neonctl`. Asks the region.
- `aws`: `aws-demo` is RDS for PostgreSQL 16, reachable from your IP only. `aws-private` writes a CDK app for Aurora Serverless v2 under `infra/graph-db/` for your pipeline to deploy. Asks the shape, region, account and who pays.
- `spanner`: see below. Needs one of the three above first.

Headless, a missing decision stops the run (`ESCALATION: graph provision <provider> needs <decision>`) and nothing is created in a cloud account. Off: skills skip graph steps with one line and `orchestrate` refuses to start. On but unreachable: `graph on but not reachable: run /factory:graph provision <provider> or set DATABASE_URL`. Costs and egress per provider: [`../docs/graph-providers.md`](../docs/graph-providers.md).

## The Spanner path (`spanner/`)

The ledger stays on PostgreSQL, because it uses pgvector and PostgreSQL semantics. Spanner holds a mirror of the graph.

- `spanner ddl [--out F]` prints GoogleSQL for nine tables (`snapshot`, `sym`, `sym_edge`, `node`, `edge`, `evidence`, `transition`, `decision`, `run`) and one property graph, `SdlcGraph`. The `embedding` and `tsv` columns, GIN and trigram indexes and enum checks are left out.
- `spanner smoke [--from-pg]` applies the DDL to the local emulator (`docker compose --profile spanner up -d spanner`, `SPANNER_EMULATOR_HOST=localhost:9110`) and runs a GQL query.
- `spanner export` copies the ledger and one snapshot (`--snapshot`, default the newest) into the emulator database `sdlc_export`, recreating it. It runs the named queries `chain`, `tests_reaching` (`--symbol`) and `contract_routes` (`--slug`).
- With `--project` and `--instance`, no `--host` and no `SPANNER_EMULATOR_HOST`, `spanner export` mirrors into a real database that gcloud created from `spanner ddl`. It replaces the rows and keeps the schema.
- `spanner query NAME_OR_GQL` runs against the emulator only.

## Using it

1. Turn it on and build the database. `/factory:init` asks both questions. Later, `/factory:graph on` flips it on and `/factory:graph provision local` builds the database (Docker must be running). `/factory:graph status` should report `reachable: yes` and a schema version.
2. Index the code with `/factory:index` (`/factory:init` does it once). It stores a full snapshot, or a delta from the newest stored ancestor, then runs `verify-snapshot`. Then `/factory:impact PATH` answers what a change touches.
3. Write contracts. `/factory:ticket TICKET` reads the impact of the paths it names, writes `contract.md`, records the whole contract in the ledger as `drafted` and stops at the Decisions table. A human resolves the decisions, then runs `sdlc-graph node transition SLUG ready --actor @you`. `/factory:next` names the single next step.
4. Run the stages. `/factory:tests` posts `tests_failing_first` and `tests_frozen`. `/factory:implement` moves `tests_frozen` to `implementing`. `/factory:verify` runs the impact tests first and posts `verify_stamp`. `/factory:ship` posts `pr_url`. Each then asks for the transition, and prints what is missing if a guard refuses. After the merge on GitHub, a human records it: `sdlc-graph evidence add SLUG --kind merge_sha --payload '{"sha": "..."}'`, then `sdlc-graph node transition SLUG merged --actor @you --evidence-ids ID`. With the graph off or unreachable, each skill prints one line and carries on without these steps.
5. To run a feature unattended, create the feature, draft each contract under it with `/factory:ticket --feature my-feature TICKET`, ready them as in step 3, then start the daemon:

```
sdlc-graph node create --kind feature --slug my-feature --title "My feature" --repo owner/name --actor @you
sdlc-graph orchestrate my-feature --live --reviewer @you
sdlc-graph feed --feature my-feature
sdlc-graph escalations --feature my-feature
```

`/factory:orchestrate my-feature` starts the daemon as a background task and prints how to watch and stop it; it is human-invoked. Start with small `--max-runs`, `--max-turns` and `--max-cost`, on a branch.

To try the CLI from a checkout of this repository, without a project (`sdlc-graph` stands for `uv run --project plugins/factory sdlc-graph`):

```
docker compose -f plugins/factory/docker-compose.yml up -d --wait db
sdlc-graph db init
sdlc-graph status
sdlc-graph index --repo ../my-app --name my-app
sdlc-graph query impact app/auth.py --repo my-app
```

**The demo.** With Postgres up, `bash plugins/factory/demo/run_demo.sh` (or `/factory:demo`) runs ten steps on a copy of `examples/sample-app` in about 15 seconds: index, delta and verify; the blast radius of `app/auth.py`; an epic, a feature and a contract from draft to merged; routing and the planner budget; a prediction scored against a real pytest run; capabilities; the Spanner export; and the MCP server over stdio. Watch the three refusals: an overlapping contract, an agent resolving a decision, and unsigned CI evidence offered for a binding guard.

The demo runs `db reset`, which drops the graph's tables in that database, so it refuses a non-local `DATABASE_URL`. `DEMO_SKIP_SPANNER=1` skips the emulator step. It needs `uv venv --python 3.11 plugins/factory/.venv && uv sync --project plugins/factory` first. Talking points: [`../demo/DEMO.md`](../demo/DEMO.md).

## How it is tested

`../tests/graph/` has 29 test files and about 460 test functions. Run them with `uv run --project plugins/factory python -m pytest plugins/factory/tests/graph -q`.

- Tests that need Postgres skip when it is unreachable. Each gets its own schema `t_<random>`, and the session fails if the `public` schema was touched.
- Extractors run on a synthetic repository, a TypeScript fixture app and `examples/sample-app`. The MCP server is driven by an in-memory client session.
- The orchestrator runs against a fake executor, a fake `claude` and a fake `gh` in temporary git repositories. The tests cover the gates, drift and frozen-test resets, retries, stalls, the lock, dependencies and the autonomy rules.
- Spanner tests check the DDL always and use the emulator when Docker can supply it.
- Local provisioning runs for real. The `neon`, `aws` and `spanner` procedures run against fake CLIs in `../tests/sdlc/test_graph_option.py`.
- One live orchestrator run is opt-in (`SDLC_ORCH_LIVE=1`, `ORCH_LIVE_REMOTE`, a scratch GitHub repository) and costs money.
- CI runs the graph suite against a pgvector service container and runs the demo with `DEMO_SKIP_SPANNER=1`.

## Limits

- CI evidence is binding only with a verified GitHub OIDC token from the trusted `workflow_run` evidence workflow (`SDLC_OIDC_WORKFLOW_REF`), for the node's exact `owner/name` repo (docs/graph-decisions.md D9 says what that proves and what it still trusts).
- The CLI's `--actor` and `--source` are strings the operator types. An agent that can run shell commands as you can type `@you`. So the guards hold against an agent with only the MCP tools, not against one with a shell.
- By default an orchestrated session gets the daemon's ledger URL, and a session holding the owner's URL can bypass the actor rules with raw SQL. For real enforcement give sessions a restricted role (`../sql/orchestrator_session_role.sql.example`) through `--session-database-url`.
- The contract machine has no rework edge from `in_review` back to `implementing`.
- Impact is structural reachability, not coverage. TypeScript extraction is regex based, and Go and Rust are not extracted.
- Ticket screening is a heuristic a determined author can evade. Ticket-derived acceptance rows need a human to ready the contract.
- The live executor has been exercised mostly with fakes and short real runs, not on long runs against other teams' repositories. There is no hosted scheduler: you start the daemon yourself, on one machine.
- No test creates a resource in a Neon, AWS or Google Cloud account. The Spanner export is tested on the emulator, and the repository records no run on a real instance.
- Not built: multi-repository features, auto-merge beyond `--refinery`, Slack notices. Python is pinned to 3.11 and `mcp` to below 2.

More: [`../docs/graph.md`](../docs/graph.md), [`../docs/graph-architecture.md`](../docs/graph-architecture.md), [`../docs/orchestrator.md`](../docs/orchestrator.md), [`../docs/graph-decisions.md`](../docs/graph-decisions.md), [`../../../docs/DESIGN.md`](../../../docs/DESIGN.md).
