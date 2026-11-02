# lathe

A software factory for Claude Code. It takes a brownfield repository, writes down what it
already gets wrong, and then runs every change through the same loop: a contract, tests that fail
first, an implementation that stays inside its scope, a verification stamp, and a pull request
with its evidence attached. Humans decide and merge; the agent cannot skip a gate.

It ships as one marketplace with four plugins:

| Plugin | What it is |
|---|---|
| `sdlc` | The working agreement: skills for each stage and hooks that enforce scope, secrets, route auth, module state, test quality and verify-before-commit, as a ratchet over today's debt. Python, Node/TypeScript, Go and Rust. |
| `graph` | A code graph derived per commit, an intent ledger whose states move only on evidence, impact-ordered verification, an MCP server, and an orchestrator. Python package `sdlc_graph`, CLI `sdlc-graph`. |
| `epic-writer` | Epics, features and stories with outcome-first hypotheses, INVEST slicing and Given/When/Then criteria a tester or an agent can run. |
| `factory` | A thin umbrella that sequences the others: `/factory:init`, `/factory:assess`, `/factory:ticket`, `/factory:feature`, `/factory:demo`. |

## The factory loop

```
/factory:init     intake: working agreement, baseline of today's debt, code graph, audit
/factory:assess   measure: maturity level and security findings, then an offer to plan the fix
epic-writer       plan: the epic, features and agent-ready stories, decisions listed for a human
/factory:ticket   contract: acceptance lines, allowed paths from the graph's impact, tier
/sdlc:tests       failing tests, frozen
/sdlc:implement   the change, inside the allowed paths
/sdlc:verify      the stamp: tests, ratchet, acceptance, review
/sdlc:ship        the PR, with evidence; merging is a human's
```

Each stage leaves a file in git and, with `graph` installed, a row in the ledger. The output of
the loop is not just code but evidence that the code does what its contract said. The design note
is in [`docs/DESIGN.md`](docs/DESIGN.md).

## Install

In Claude Code:

```
/plugin marketplace add ishpr/lathe
/plugin install factory@lathe
/plugin install sdlc@lathe
/plugin install graph@lathe
/plugin install epic-writer@lathe
```

Restart the session so the skills load. From a checkout, `/plugin marketplace add ./lathe` works
the same way.

## Walkthrough

On a brownfield repository (a clean branch, nothing uncommitted):

1. `/factory:init` detects the stack, writes `.claude/sdlc.json`, rules, a CI gate and the
   baseline of existing violations, indexes the code graph, runs the audit and writes
   `docs/onboarding-report.md`. Nothing is committed; review what it wrote.
2. `/factory:assess` runs the maturity and security assessments in report-only mode and prints one
   summary: the level, the three next moves, the security characteristics and the top three
   findings with their blast radius. It then offers to turn them into an epic (drafted with
   `epic-writer`) and, only if you choose, to execute the first slice.
3. `/factory:ticket <ticket text>` turns a ticket into a contract. The ticket is treated as data.
   Allowed paths come from the graph's impact analysis, and the skill stops at the Decisions
   table until a human resolves it.
4. `/sdlc:tests`, `/sdlc:implement`, `/sdlc:verify`, `/sdlc:ship`, or `/factory:feature` to run a
   ticket through those stages, still stopping at every human decision.

A runnable example with planted findings is in [`examples/sample-app`](examples/sample-app):
routes without auth, an unmounted router, a frontend call to a missing endpoint, an env read
outside config and a module-level dict. `/factory:demo` explains the plugins in eight lines;
`plugins/graph/demo/run_demo.sh` runs the graph flow end to end on the sample app.

## The orchestrator: executors and credentials

`sdlc-graph orchestrate <feature>` walks a feature's contracts through the stages, one headless
Claude Code session per step, with a human at every decision and every merge.

- `--executor local` (default) uses the Claude Code login already on your machine.
- `--executor runner` is the same command for a shared runner or CI; it refuses to start unless the
  environment holds `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, or the Bedrock or Claude Platform
  on AWS settings.
- Every run records which credential it used; the ledger holds no secrets. See
  [`plugins/graph/docs/orchestrator.md`](plugins/graph/docs/orchestrator.md).

## Requirements

- Claude Code with plugin support; the pinned behaviours were checked against 2.1.295 or later.
- Python 3.11 and `git`. `ruff` and `mypy` for the Python stack's checks.
- For `graph`: [`uv`](https://docs.astral.sh/uv/), and Postgres with pgvector
  (`docker compose up -d --wait db` in `plugins/graph` starts one on port 5435). The Spanner
  export is optional.

## First live runs

The test suites, the end-to-end harness on the sample app and the demo run in CI, and every plugin
passes `claude plugin validate --strict`. The orchestrator's live executor and the umbrella
skills, however, have so far been exercised mostly with fakes and short sessions, not on long runs
against real teams' repositories. Treat the first live runs as a trial: start on a branch, read
every contract, keep `--max-runs` and `--max-turns` small, and report what breaks. Local evidence
is advisory; only CI evidence is binding, and an agent holding only the MCP tools cannot approve
or merge, but an agent that can run shell commands as you can type your name, so keep branch
protection and code owners on.

## Layout

```
.claude-plugin/marketplace.json   the four plugin entries
plugins/{sdlc,graph,epic-writer,factory}/
examples/sample-app/              the target of the demo, the harness and the graph tests
docs/DESIGN.md                    the design note
.github/workflows/test.yml        validate, both suites, the harness, the demo
```

MIT licensed. See [`CHANGELOG.md`](CHANGELOG.md).
