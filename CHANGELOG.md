# Changelog

Versions are the `version` field of each plugin's `.claude-plugin/plugin.json`. The marketplace
entry in `.claude-plugin/marketplace.json` carries the same number.

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
