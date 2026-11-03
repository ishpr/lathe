# Changelog

Versions are the `version` field of the plugin's `.claude-plugin/plugin.json`. The marketplace
entry in `.claude-plugin/marketplace.json` carries the same number.

## factory 0.4.0: one plugin

The four plugins (`sdlc` 0.3.0, `graph` 0.1.0, `epic-writer` 0.1.0, `factory` 0.1.0) are one plugin,
`factory` ("Lathe factory"). The install is two lines: `/plugin marketplace add ishpr/lathe`, then
`/plugin install factory@lathe`. The marketplace lists only `factory`.

Renames:

- Every skill is `/factory:<name>`: `/sdlc:onboard`, `audit`, `tests`, `implement`, `verify`, `ship`,
  `deploy`, `rollback`, `migrate`, `review`, `review-pr`, `maturity` and `security` keep their names under
  `/factory:`; `/graph:index`, `impact`, `contract`, `next`, `feed` and `orchestrate` likewise;
  `/graph:demo` is `/factory:demo`; `/epic-writer:epic-writer` is `/factory:epic`. Agents are
  `factory:reviewer`, `factory:security-reviewer` and `factory:test-author`.
- `/factory:init` is one skill (it was the umbrella calling `/sdlc:init`): the working agreement, then
  the graph index, the audit and `docs/onboarding-report.md`; the graph steps are skipped with one
  line when the graph is not configured. `/factory:ticket` is one skill: branch, impact, contract, the
  ledger draft with the whole contract, a stop at the Decisions. `/factory:feature` is one skill:
  impact, the stages with their stops, `pr_url` evidence at the end.
- MCP tools are `mcp__plugin_factory_graph__<tool>` (were `mcp__plugin_graph_graph__<tool>`);
  `mcp__graph__<tool>` stays as the second spelling. Server name `graph` is unchanged.
- Paths: `plugins/sdlc`, `plugins/graph` and `plugins/epic-writer` are gone. Scripts, hooks, lib,
  stacks, templates and agents are under `plugins/factory/`; the graph package `sdlc_graph`,
  `pyproject.toml`, `uv.lock`, `sql/` and `demo/` too; the graph's README, architecture and decisions
  are `docs/graph.md`, `docs/graph-architecture.md` and `docs/graph-decisions.md`; the epic writer's
  references are `docs/epic/`, its lint `scripts/lint_epic.py`, its evals and `check_examples.sh`
  `tests/epic/`. The suites are `tests/sdlc` and `tests/graph`; the harness is `tests/e2e`.
- One `hooks/hooks.json`: the working agreement's guards plus the graph's Agent-spawn guard, run
  recorder and session prime.
- The orchestrator's `SDLC_PACK_ROOT` is the plugin root; `ORCHESTRATOR_PLUGIN_DIRS` defaults to that
  one root, so `run_headless.sh` and `step_executor.sh` get one `--plugin-dir`. The CI gate templates
  check out `plugins/factory` of `ishpr/lathe` (the checkout directory stays `.sdlc`, the workflow
  files stay `sdlc-gate.yml` and `sdlc-claude-review.yml`).
- Unchanged on purpose: `.claude/sdlc.json`, `.sdlc-stage/`, `lib/sdlc.py`, the `SDLC_*` and
  `ORCHESTRATOR_*` variables, the CLAUDE.md markers and the ratchet keys. A repository initialised
  with 0.3.0 keeps working; run `/factory:init --upgrade` to rewrite its CI gate's checkout path.

Changes:

- The graph's MCP server starts with no reachable database. Every tool then answers
  `{"error": "graph not configured", "hint": ...}`, and the new read-only tool `graph_status` (CLI
  `sdlc-graph status`) reports `configured`, `provider`, `reachable`, `schema_version`, `snapshots`
  and `nodes`. `sdlc-graph orchestrate` refuses to start, with the reason, when the ledger is
  unreachable or not initialised.
- The graph is an option: `/factory:init` asks once (on or off; local, neon, aws or spanner; headless: on
  and local under Assumptions) and writes `"graph": {"enabled", "provider", "url_env", "spanner"}` to
  `.claude/sdlc.json` (`init_project.py --graph on|off --graph-provider <p>`; `--upgrade` adds the block).
  Off: every graph tool answers `{"error": "graph off"}` without connecting, the skills skip with one line,
  and `sdlc-graph orchestrate` refuses (`graph_off`, exit 5). On but unreachable: the hint is "graph on but
  not reachable: run /factory:graph provision <provider> or set DATABASE_URL"; headless runs record it under
  Assumptions. The connection string is read from the variable `url_env` names, then
  `.claude/settings.local.json` env, then `.env` (the `.mcp.json` default counts as unset), never from
  `sdlc.json`.
- New skill `/factory:graph` (`scripts/graph.py`): `status`, `on`, `off`, `url` (from a secret manager, a
  staged file or a hidden prompt; never printed) and `provision local|neon|aws|spanner`: local through the
  plugin's `docker-compose.yml`, neon through the Neon MCP or neonctl, aws as `aws-demo` (RDS for PostgreSQL
  16) or `aws-private` (an Aurora Serverless v2 CDK stack written under `infra/graph-db/`), spanner through
  gcloud with the DDL of `sdlc-graph spanner ddl` and the new real-instance mirror of `sdlc-graph spanner
  export --project --instance`. Each run is idempotent and records `graph_provisioned {provider, where,
  schema}` once on the repository's new `repo` node (`sql/010_repo_node.sql`). The procedures are in
  `docs/graph-providers.md`, with the Spanner limit: the ledger stays on PostgreSQL.
- `docker-compose.yml`: the container name takes `SDLC_PG_CONTAINER`, so a second compose project beside the
  first does not collide.
- `contract_draft` under a slug that already exists returns `duplicate` (with `same_slug`) instead of a
  database error on `node_slug_key`.
- The ledger's ready guard requires a human confirmation for a decision that touches money, security,
  data or what customers see at every tier, as `acceptance.py --ready` already did (it checked only
  tiers medium and high).
- Headless smoke fixes. `provision local` inside the pack's sandbox: a docker started by the script is
  sandboxed and exits 125, so the script writes `.sdlc-stage/graph-compose.vars` and prints two `next:`
  commands (a top-level `docker compose --env-file ... up -d db`, which the sandbox excludes, then
  `provision local --no-docker`, which waits without Docker); the sandbox settings are unchanged.
  `/factory:epic` leaves a protected branch (`docs/epic-<slug>`) before its first write instead of being
  refused by the edit guard. `/factory:audit` names its TODO-age command literally (`git log -S "<text>"` first,
  as its allow rule needs). `/factory:init` no longer lists an unreachable graph or missing Grep/Glob tools
  under Failures. The epic lint knows more imperative verbs and their inflections, so "Decode every response
  ..." is not a vague default. `/factory:epic` opens with two rules for a headless run: a denial is one
  command (plugin files are read with Read, never `sed` in a compound line), and the edit guard's
  `Blocked: you are on main` means the allowed `git switch -c docs/epic-<slug>` has not run yet, so it runs
  and the write is retried; one smoke run had taken the denial for "Bash is denied" and left the epic unsaved.
- The `sdlc-graph` Python package is versioned with the plugin (0.4.0).
- The commit guard lets the skeletons `/factory:tests` writes ride in the tests-only commit: a new file whose
  every body only raises `NotImplementedError` or throws `new Error("not implemented")` (`lib/skeleton.py`:
  Python by its syntax tree; TypeScript and JavaScript by their text with comments and strings blanked, no
  statement keywords, no calls, arrows with bodies). Anything else keeps needing the verify stamp, and the
  refusal names the file and the line. Live orchestrator run 5 had stopped on that refusal as a P1.
- `init_project.py --upgrade` survives a settings file it cannot write (Claude Code's sandbox protects a
  settings file it loaded at session start): the merged document is staged under `.sdlc-stage/` with the `cp`
  line for a human, and the upgrade goes on with `KIT_VERSION`, the CLAUDE.md block and the rest. The
  console's headless upgrade had died there.

The entries below are the history of the four plugins before the merge.

## sdlc 0.3.0

First public release of the working agreement.

- Skills: `init`, `onboard`, `audit`, `maturity`, `security`, `ticket`, `tests`, `implement`,
  `verify`, `ship`, `deploy`, `feature`, `review`, `review-pr`, `rollback`, `migrate`.
- Guards that fail closed and log every block to `.claude/work/blocks.jsonl`: scope, secrets,
  protected files, destructive git, data-store commands, credential reads, frozen tests,
  tier-high paths and verify-before-commit, including shell-write detection through redirects,
  `tee`, `sed -i`, `cp`/`mv` and interpreter one-liners.
- A ratchet over today's debt: `.claude/baseline.json` stores counts per key, only goes down, and
  `baseline_diff.py` checks it in CI. Checkers for lint, route auth, module state, stubs, dead code
  and API contract drift (frontend calls and README endpoints against the route table).
- Contract format with executable acceptance lines, frozen tests, one verify stamp (database
  backend, tool versions, acceptance, frozen-test status).
- Maturity and security assessments that offer to turn findings into an epic and to execute the fix.
- Supply-chain checks (lock files, new dependencies, licences), PR size, agent-config scan, ticket
  intake screening, commit-message rules, an unattended-run wrapper, a canary run, a route sweep.
- Templates copied by `init`: project settings with sandbox and ask rules, rules, CI gate and
  advisory review workflow, CODEOWNERS, issue templates, `dependabot.yml`, and one-page docs for
  managed settings, MCP and data policy, unattended runs, rollout, cost and worktrees.
- Adapters for Python, Node/TypeScript, Go and Rust.

## graph 0.1.0

- Code graph derived from `git archive` of each commit (symbols, routes with mounted state, models,
  env reads, packages, frontend calls, tests, CODEOWNERS); later commits stored as replayable deltas.
- Intent ledger: epics, features, contracts, releases and incidents as state machines whose guards
  cite evidence rows; local evidence is advisory, CI evidence is binding.
- Impact analysis: affected tests, routes and owners, recorded as a prediction and scored against a
  JUnit report.
- MCP server `graph` with read tools and guarded writes under one fixed `agent:*` identity.
- Model-routing hooks on Agent spawns, run records and a per-epic planner budget.
- Orchestrator with `local` and `runner` executors, and an optional Spanner Graph export.
- Python package `sdlc_graph` and CLI `sdlc-graph`.

## epic-writer 0.1.0

- Skill `epic-writer`: outcome-first epics, releasable features, INVEST stories with
  Given/When/Then criteria, agent-ready stories, tracker exports, a lint script and eval cases.

## factory 0.1.0

- Umbrella skills `init`, `ticket`, `feature`, `assess` and `demo`, calling `/sdlc:...` and
  `/graph:...`. No scripts or hooks of its own.
