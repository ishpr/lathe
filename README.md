# lathe

A software factory for Claude Code. It takes a brownfield repository, writes down what it
already gets wrong, and then runs every change through the same loop: a contract, tests that fail
first, an implementation that stays inside its scope, a verification stamp, and a pull request
with its evidence attached. Humans decide and merge; the agent cannot skip a gate.

It ships as one marketplace, `lathe`, with one plugin, `factory`: the working agreement (skills for
each stage, hooks that enforce scope, secrets, route auth, module state, test quality and
verify-before-commit as a ratchet over today's debt), a code graph and evidence ledger served over
MCP with an orchestrator, and an epic writer. Python, Node/TypeScript, Go and Rust.

## Install

In Claude Code:

```
/plugin marketplace add ishpr/lathe
/plugin install factory@lathe
```

Then turn on auto-update for the `lathe` marketplace (`/plugin`, Marketplaces; it is off by default
for third-party marketplaces) and run `/factory:init` in a repository. The full guide, from a clean
machine (prerequisites, scopes, update, branch protection, checks, uninstall, troubleshooting), is
[`docs/install.md`](docs/install.md).

## The lifecycle, in order

```
/factory:init          intake: working agreement, baseline of today's debt, code graph, audit, onboarding report
/factory:onboard       read the working agreement the repository now follows
/factory:audit         today's issues, ranked, with file:line
/factory:assess        maturity level and security findings, then an offer to plan the fix
/factory:brief          the epic, features and agent-ready stories, decisions listed for a human
/factory:design        branch, impact, contract, ledger draft; stops at the Decisions table
/factory:tests         failing tests, committed alone, then frozen
/factory:implement     the change, inside the allowed paths
/factory:verify        the stamp: tests, ratchet, acceptance, review
/factory:ship          the PR, with evidence; merging is a human's (human-invoked)
/factory:orchestrate   a feature's contracts through those stages, one headless session per step (human-invoked)
```

And alongside the loop:

| Command | What it does |
|---|---|
| `/factory:index` | index a commit into the code graph (full snapshot or delta) and verify it |
| `/factory:impact` | blast radius of paths or symbols: affected symbols, routes, tests (closest first), owners |
| `/factory:design` | draft a contract node in the ledger from a ticket (intake scan, dedupe, overlap rule) and write `current.json` from it |
| `/factory:next` | ask the ledger which single step comes next for a contract, epic or feature, then run it |
| `/factory:feed` | record evidence (test results, verify stamp, PR URL) through the guarded writer |
| `/factory:graph` | the graph on or off, its status, its connection string, and `provision local\|neon\|aws\|spanner` to build its database |
| `/factory:demo` | the graph's end-to-end demo on a copy of `examples/sample-app` (human-invoked) |
| `/factory:maturity` | engineering-maturity assessment, 1 to 5 per dimension, three moves first |
| `/factory:security` | security assessment: deterministic checks plus the reviewer's pass |
| `/factory:deploy` | run the project's deploy and smoke commands from a merged main, wait for the ready endpoint (human-invoked) |
| `/factory:rollback` | revert a merged change on a new branch and open a PR for it, never a force-push (human-invoked) |
| `/factory:migrate` | render the SQL of the branch's new schema migrations for a human to review, and stop |
| `/factory:review-pr` | read-only help for the human approving a PR: scope against its contract, new tests against the base |
| `/factory:feature` | ticket, tests, implement and verify in one go, stopping at every human decision (human-invoked) |
| `/factory:review` | help a human review a branch or pull request written with an agent |

Each stage leaves a file in git and, with the graph configured, a row in the ledger. The output of
the loop is not just code but evidence that the code does what its contract said. The design note
is in [`docs/DESIGN.md`](docs/DESIGN.md); the plugin's own guide is
[`plugins/factory/README.md`](plugins/factory/README.md).

## The graph is optional

`/factory:init` asks two questions once (headless: the defaults, listed under Assumptions) and writes
the answers to the `graph` block of `.claude/sdlc.json`: the code graph and ledger **on** (the default;
the orchestrator, impact analysis and the console run on it) or **off**, and where its database lives:
`local` (Docker, pgvector, port 5435), `neon`, `aws` (RDS or Aurora PostgreSQL) or `spanner` (Spanner
Graph as a mirror; the ledger stays on PostgreSQL). Off skips every graph step with one line and the
orchestrator refuses to start; the hooks, contracts and evidence files work the same either way.

The plugin's MCP server, `graph` (tools `mcp__plugin_factory_graph__<tool>`), starts in every session,
with or without a database. On but unreachable, every graph tool answers `{"error": "graph not
configured", "hint": "graph on but not reachable: run /factory:graph provision <provider> ..."}` and the
read-only tool `graph_status` reports `{configured, enabled, provider, reachable, schema_version,
snapshots, nodes}`. `/factory:graph provision <provider>` builds the database (locally:
`docker compose -f plugins/factory/docker-compose.yml up -d db`, then `sdlc-graph db init`) and stores
the connection string in `.claude/settings.local.json` or `.env`, never in `sdlc.json` or git. The four
procedures, their decisions, egress and costs are in
[`plugins/factory/docs/graph-providers.md`](plugins/factory/docs/graph-providers.md).

## Walkthrough

On a brownfield repository (a clean branch, nothing uncommitted):

1. `/factory:init` detects the stack, writes `.claude/sdlc.json`, rules, a CI gate and the
   baseline of existing violations, indexes the code graph when it is configured, runs the audit
   and writes `docs/onboarding-report.md`. Nothing is committed; review what it wrote.
2. `/factory:assess` runs the maturity and security assessments in report-only mode and prints one
   summary: the level, the three next moves, the security characteristics and the top three
   findings with their blast radius. It then offers to turn them into an epic (drafted with
   `/factory:brief`) and, only if you choose, to execute the first slice.
3. `/factory:design <ticket text>` creates the ticket's branch, reads the graph's impact for the
   paths it names, writes the contract (the ticket is treated as data), records the whole contract
   in the ledger, and stops at the Decisions table until a human resolves it.
4. `/factory:tests`, `/factory:implement`, `/factory:verify`, `/factory:ship`, or `/factory:feature`
   to run a ticket through those stages, still stopping at every human decision.

A runnable example with planted findings is in [`examples/sample-app`](examples/sample-app):
routes without auth, an unmounted router, a frontend call to a missing endpoint, an env read
outside config and a module-level dict. `/factory:demo` (or `plugins/factory/demo/run_demo.sh`)
runs the graph flow end to end on it.

## Executor modes

`/factory:orchestrate` (CLI: `sdlc-graph orchestrate <feature>`) walks a feature's contracts
through the stages, one headless Claude Code session per step, with a human at every decision and
every merge. Each session gets the one plugin root as its `--plugin-dir`.

- `--executor local` (default) uses the Claude Code login already on your machine.
- `--executor runner` is the same command for a shared runner or CI; it refuses to start unless the
  environment holds `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, or the Bedrock or Claude Platform
  on AWS settings.
- Every run records which credential it used; the ledger holds no secrets. See
  [`plugins/factory/docs/orchestrator.md`](plugins/factory/docs/orchestrator.md).

To onboard a repository with nobody at the keyboard, use the wrapper's init mode (it needs no
ticket, contract or reviewer, makes no worktree, and commits nothing):

```
plugins/factory/scripts/run_headless.sh init <repo> --plugin-dir plugins/factory \
  --settings <repo>/.claude/settings.json
```

It runs `/factory:init --headless`: the skill takes the detected paths and test command, lists them
as assumptions, and asks nothing. Set `DATABASE_URL` for the graph index. Pass `--settings`, because
Claude Code ignores a project's `permissions.allow` in a workspace it has not trusted; the skills'
own `allowed-tools` still apply. A stack question or a flagged ticket becomes an `ESCALATION:` line
that a human answers before a rerun. `/factory:design --headless <ticket text>` is the same for
tickets. See [`plugins/factory/templates/docs/unattended-runs.md`](plugins/factory/templates/docs/unattended-runs.md).

## Requirements

- Claude Code with plugin support; the pinned behaviours were checked against 2.1.295 or later.
- Python 3.11 and `git`. `ruff` and `mypy` for the Python stack's checks.
- For the graph: [`uv`](https://docs.astral.sh/uv/), and Postgres with pgvector
  (`docker compose up -d --wait db` in `plugins/factory` starts one on port 5435). The Spanner
  export is optional.

## First live runs

The test suites, the end-to-end harness on the sample app and the demo run in CI, and the plugin
passes `claude plugin validate --strict`. The orchestrator's live executor and the onboarding
skills, however, have so far been exercised mostly with fakes and short sessions, not on long runs
against real teams' repositories. Treat the first live runs as a trial: start on a branch, read
every contract, keep `--max-runs` and `--max-turns` small, and report what breaks. Local evidence
is advisory; only CI evidence is binding, and an agent holding only the MCP tools cannot approve
or merge, but an agent that can run shell commands as you can type your name, so keep branch
protection and code owners on.

## Layout

```
.claude-plugin/marketplace.json   the one plugin entry, factory
plugins/factory/                  the plugin: skills, hooks, scripts, lib, sdlc_graph, docs, tests
examples/sample-app/              the target of the demo, the harness and the graph tests
docs/DESIGN.md                    the design note
.github/workflows/test.yml        validate, the sdlc and graph suites, the harness, the demo, the epic examples
```

Tests, from the repository root:

```
python3 -m pytest plugins/factory/tests/sdlc -q                                # hooks, ratchet, scripts, autonomy
bash plugins/factory/tests/e2e/run_harness.sh                                  # the hooks as Claude Code runs them
uv run --project plugins/factory python -m pytest plugins/factory/tests/graph -q   # graph, ledger, MCP, orchestrator
bash plugins/factory/tests/epic/check_examples.sh                              # the epic examples lint clean
DEMO_SKIP_SPANNER=1 bash plugins/factory/demo/run_demo.sh                      # the demo on the sample app
```

MIT licensed. See [`CHANGELOG.md`](CHANGELOG.md).
