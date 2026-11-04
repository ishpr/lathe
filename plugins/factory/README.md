# factory

The lathe factory as one Claude Code plugin: a working agreement for teams that build with coding
agents (skills for each stage, hooks that enforce it, a ratchet over today's debt), a code graph and
evidence ledger served over MCP with an orchestrator, and an epic writer. Stack-neutral core;
adapters for Python, Node/TypeScript, Go and Rust. Version 0.5.0; see `CHANGELOG.md` in the
repository root for what changed (0.4.0 merged the `sdlc`, `graph`, `epic-writer` and umbrella
`factory` plugins into this one).

| Part | Where | Guide |
|---|---|---|
| Working agreement: skills, hooks, scripts, checkers, templates | `skills/`, `hooks/`, `scripts/`, `lib/`, `stacks/`, `templates/`, `agents/` | this file |
| Code graph, ledger, MCP server `graph`, orchestrator (package `sdlc_graph`, CLI `sdlc-graph`) | `sdlc_graph/`, `sql/`, `demo/`, `pyproject.toml` | [`docs/graph.md`](docs/graph.md), [`docs/orchestrator.md`](docs/orchestrator.md) |
| Epic writer (`/factory:brief`) | `skills/brief/`, `scripts/lint_epic.py`, `docs/epic/` | [`skills/brief/SKILL.md`](skills/brief/SKILL.md) |

`/factory:init` onboards a brownfield repository in one command: the working agreement below, then
the graph index, the audit, the maturity and security assessments and `docs/onboarding-report.md`.
It commits nothing and drafts no epic; `/factory:brief from the security assessment` is the next,
separate step. `/factory:assess` runs the two assessments together again and offers to plan the fix.
The graph is optional: init asks once whether it is on and where its database lives (`local`,
`neon`, `aws`, `spanner`) and writes the `graph` block of `.claude/sdlc.json`; `/factory:graph`
turns it on or off and provisions the database
([`docs/graph-providers.md`](docs/graph-providers.md)). Off, every graph tool answers `{"error":
"graph off"}`; on without a reachable database, the MCP server still starts, every graph tool
answers `{"error": "graph not configured", "hint": "graph on but not reachable: ..."}`,
`graph_status` says why, and the skills skip their graph steps with one line.

## What it does

| Command | Produces | Human stop? |
|---|---|---|
| `/factory:init` | `.claude/sdlc.json`, rules, CI gate, PR template, docs, today's debt as the ratchet baseline, the onboarding report and the maturity and security assessments (no epic) | review and commit |
| `/factory:design <ticket>` | the contract: acceptance criteria as test names and runnable `## Acceptance` lines, decisions, allowed paths, tier, evidence plan | approve the decisions |
| `/factory:tests` | failing tests, each failing for the intended reason (`tests_failing_first.py`), committed on their own, then frozen in `current.json` | — |
| `/factory:implement` | green inside the allowed paths; frozen tests untouched | — |
| `/factory:verify` | the gate (tests, the end-to-end suite of the `e2e` block, ratchet, secret scan, scope check, agent-config scan, frozen tests, acceptance lines) + two reviewer agents → the verify stamp + Evidence | — |
| `/factory:ship` | commit (message checked against the diff), push, PR with evidence; deletes `current.json` first | human-invoked; a reviewer merges |
| `/factory:deploy` | local deploy + smoke check from a merged main | human-invoked |
| `/factory:feature <ticket>` | the chain 1–4: refuses to start a contract with an open decision, then runs without asking | the decisions, before it starts |
| `/factory:review-pr <n>` | read-only help for the human approving a PR: scope against its contract, and whether each new test fails on the base code (`scripts/review_pr.sh`) | — |
| `/factory:migrate` | renders the SQL of this branch's new schema migrations for review, then stops; never applies one | review the SQL |
| `/factory:rollback <pr or sha>` | reverts a merged change on a new branch and opens a PR; human-invoked only | yes |
| `/factory:maturity` | maturity level and the three moves; writes `docs/maturity-assessment.md` | — |
| `/factory:security` | security characteristics and ranked findings with blast radius; writes `docs/security-assessment.md`; offers to create an epic or feature and to execute the fix, asking you and recommending an option | you choose each step |
| `/factory:audit`, `review`, `onboard` | the debt list; help for a human reviewing an agent PR; a tour | — |
| `/factory:assess` | the maturity and security assessors in parallel (`factory:maturity` and `factory:security` in report-only mode), one combined summary, then the offer; a drafter writes the epic or feature you choose | you choose each step |
| `/factory:brief <ask>` | an outcome-first epic, releasable features, INVEST stories with runnable criteria, `from the security assessment` turns `docs/security-assessment.md` into its agent-ready epic; `scripts/lint_epic.py` checks it | the decisions it lists |
| `/factory:index`, `impact`, `contract`, `next`, `feed` | the code graph and the ledger (see `docs/graph.md`) | — |
| `/factory:graph status\|on\|off\|url\|provision <provider>` | the graph on or off, its status and connection string; builds its database locally, on Neon, on AWS or (as a mirror) on Spanner | the provider's decisions, asked once |
| `/factory:orchestrate <feature>`, `/factory:demo` | the orchestrator daemon for one feature; the graph's end-to-end demo | human-invoked |

The six loop skills (`brief`, `design`, `tests`, `implement`, `verify`, `ship`) each state a goal, the inputs they
read, the outputs another step or a guard depends on, and the guards that apply (DESIGN §18.4); which subagents they
use and in what order is the model's, and the hooks below hold the outcome.

Hooks, always on while the plugin is enabled. The PreToolUse guards fail closed: unreadable
input or a crash blocks, and the hook entries set `onFailure: "block"` (Claude Code 2.1.295 or later).

| Hook | Blocks or nudges |
|---|---|
| session start (`startup`, `resume`, `clear`, `compact`) | prints branch, stack, active contract, frozen tests, stamp state, and a `MISSING TOOL: <name> — install: <cmd>` line for each tool the config needs and the machine lacks |
| before Edit/Write/MultiEdit | secrets in content; edits on main/master; protected files; paths outside the contract; frozen tests and their `current.json` entries; paths that force tier high when the contract is lower; an unreadable `current.json` |
| before Bash | force push, push to main, `gh pr merge`/`--approve`, `--no-verify`, destructive git, baseline updates and `ratchet.py --prune` and `freeze_tests.py --unfreeze` (humans only); destructive data-store commands (`dropdb`, DROP/TRUNCATE, DELETE with no WHERE, `alembic downgrade` and `stamp`, `FLUSHALL`, `compose down -v`); reads of `~/.ssh`, `~/.aws`, `~/.config/gh` and `.env*`; `git commit` without a matching verify stamp (tests-only commits exempt) or with a message that does not describe the diff |
| after Edit/Write | the stack's lint-fix and formatter on the file, then the ratchet for that file; new or grown violations come back as feedback |
| stop | one nudge when app code changed without a test change (contract with `require_tests`) |
| before Agent | the graph's spawn guard: records each Agent spawn's model against the routing default and enforces `availableModels` and the planner budget |
| after Agent | the graph's run recorder: one ledger row per Agent run when the graph is configured; graph or not, one row per hand-off in `.claude/work/<slug>/handoffs.md`, which `ship` puts in the PR body |
| session start, with an active contract | the graph's prime: the contract, its ledger state and the next step |

Every deny appends one JSON line to `.claude/work/blocks.jsonl` (git-ignored). `docs/day-one.md`
lists each message, its cause and its fix.

The **ratchet** is the idea that makes the gates survivable on a real codebase: `/init` records
today's violations once, as counts per file and rule (`baseline.json` version 2); from then on a
new key or a count that grows fails. Lower counts pass, and `ratchet.py --prune` (humans only)
lowers the baseline to match. `ratchet.py --all` lists the debt. `baseline_diff.py` compares a
PR's baseline with the base branch's so a PR cannot grade itself clean.

## Scripts

All run as `python3 -I scripts/<name>` (or `bash`) from the plugin directory or a repo that
installed them; each has `--help`, a `--root` option and exit codes 0 ok, 1 findings, 2 misuse.

| Script | What it does |
|---|---|
| `ratchet.py`, `baseline_diff.py` | the gate and its CI comparison; `--update`, `--prune`, `--allow-growth` |
| `verify.py`, `worktree_fingerprint.py` | run every check, write the stamp (backend, tool versions, acceptance, checks) |
| `freeze_tests.py`, `tests_failing_first.py`, `acceptance.py` | freeze the `/tests` files by hash, refusing one whose recorded failure reason is wrong; classify why each new test fails (pytest, vitest); run the contract's acceptance lines |
| `check_agent_config.py`, `check_claude_md_paths.py` | invisible characters, base64 blobs and `curl` piped to a shell in agent config; paths in CLAUDE.md that do not exist |
| `ticket_intake.py` | scan a ticket for commands, exfiltration words and injection phrases; quote them back |
| `check_pr_size.py`, `check_new_deps.py` | lines changed against a base; new packages: exist, age, licence |
| `lock_deps.sh` | hash-pinned lock for the stack; `--check` in CI |
| `route_sweep.py` | unauthenticated sweep of an OpenAPI spec against a running app |
| `run_headless.sh` | the one wrapper for unattended runs |
| `review_pr.sh` | read-only PR helper: scope check and new tests against the base in a throwaway worktree |
| `metrics_baseline.py` | PR size, time to merge, time to first review, reverts, deletion ratio, guard blocks |
| `baseline.py` | the measurement baseline: `prepare`, `run`, `score`, `compare` over `tests/evals`; run from a normal terminal, never inside a sandboxed session |
| `canary.sh` | live check that a Claude Code session is still blocked from each guarded action |
| `check_commit_message.py`, `check_commit_range.py` | message against the diff; rejects `Signed-off-by` beside an agent trailer |
| `protect_main.sh`, `smoke_rate_limit.sh`, `init_project.py` | branch protection (a human, once); a rate-limit smoke test; the init engine |

## What v2 adds

- **A floor nobody removes locally**: `docs/managed-settings.example.json` and `admin-setup.md`
  (bypass mode off, a version floor, a deny floor, sandbox and credential denies).
- **Guards that fail closed and keep a log**, a data-store guard, a credential-read guard, humans-only
  commands, frozen tests, and tier-high path enforcement.
- **A ratchet that only goes down**: counts per file and rule, `--prune`, stale-key warnings, a CI diff.
- **Rules that were prose, now checks** (Python): unmounted routers and unreachable modules, TODOs
  with no ticket id, stub bodies, `os.environ` outside config, credential-named route params,
  class-level mutable state, frontend and README routes missing from the app, ruff `S608`.
- **Executable acceptance** lines in the contract and a live route sweep.
- **Supply chain**: hash-pinned locks, a new-dependency check, a licence allowlist.
- **Untrusted-input and MCP policy**, a data policy, an unattended-run wrapper, a permission-mode
  policy, an incident runbook, rollout and day-one pages, a cost page, worktree rules, the light
  path and hotfix route, `REVIEW.md` and a test-quarantine register.
- **Pack governance**: a version, a changelog, an owner, and a quarterly prune.

The pages ship under `templates/docs/` (and `REVIEW.md`, `tests/QUARANTINE.md`) and are copied
into the repository's `docs/` by `/init`. Each is one page and cites the recommendation it
implements.

## Stack adapters

| | Python | Node/TS | Go | Rust |
|---|---|---|---|---|
| route auth checker | FastAPI (AST; auth names from config) | — | — | — |
| module-state checker | AST | text heuristic | text heuristic | text heuristic |
| test-quality checker | AST (pytest) | text heuristic (jest/vitest/mocha) | text heuristic | text heuristic |
| stubs, dead code, API contract | AST | — | — | — |
| lint in the ratchet | ruff (JSON, with `S608` and friends) | eslint (JSON) | go vet | cargo clippy (JSON) |
| types in the ratchet | mypy | tsc | go build | — |
| format on save | ruff | eslint --fix + prettier | gofmt | rustfmt |

Heuristic means regex over text, not a parser: it catches the common shapes (`let` at module scope,
an empty `Map`, a `Test` func with no `t.Error`, a `#[test]` with no `assert!`) and can be silenced
per line with `// state: ok <reason>`. The Python checkers take `# stubs: ok <reason>` and
`# dead: ok <reason>`; `api_contract.strip_prefix` in the config handles a proxy that rewrites
`/api`. Every default command lives in `stacks/<stack>.json` and is copied into
`.claude/sdlc.json` by `/init`, where the team edits it.

End-to-end tests (Playwright) have their own block in `.claude/sdlc.json`, which `/factory:init` writes for a Node
repo with a `playwright.config.*`:

```json
"e2e": {"command": "pnpm test:e2e", "build": "pnpm build", "serve": "pnpm start", "url": "http://localhost:3100"}
```

Nothing with a browser runs inside Claude Code's sandbox on macOS: Chromium registers a Mach bootstrap service when
it starts and the sandbox denies it (`bootstrap_check_in ... Permission denied`), and a dev server with a file watcher
(`next dev`) dies there too. So the suite runs through one top-level command, `scripts/e2e.py run [spec files]`, which
the human lets out of the sandbox (`sandbox.excludedCommands` in `.claude/settings.json`; init writes the entry next to
the block, and the orchestrator compiles it into a step's settings when the contract's approved commands name the e2e
command). The script makes the app listen at `url` (a server already listening is reused when it was started from this
checkout, a developer's own `pnpm dev`, which serves the current source; a production server you started yourself
serves the build it started with, so stop it before a gate run; otherwise `build` runs once and `serve` is started for
the run and stopped after it; a port held by another checkout is refused instead of testing the wrong code), runs the suite with the
`list` and `json` reporters and records the run under `.claude/work/e2e/` with the tree's fingerprint.
`tests_failing_first.py` and `verify.py` read that run and refuse one the tree has moved past, the way the commit guard
refuses a stale verify stamp. A failed expectation or an element that never appears is an intended first failure; a web
server that did not start, a page that could not be reached, a browser that did not launch, a spec that does not parse
and a run that found no test are wrong reasons the freeze refuses.

## Install

```
/plugin marketplace add ishpr/lathe
/plugin install factory@lathe
```

or from a terminal: `claude plugin marketplace add ishpr/lathe` then `claude plugin install factory@lathe`.

## Requirements

- Claude Code 2.1.295 or later. That is the first release that honours `onFailure: "block"` on
  the guards; on an older one a crashed or missing guard lets the call through.
- `python3` on the PATH the session starts with (the hooks are Python 3 standard library only).
- The stack's own tools for lint, types and tests; session start prints a `MISSING TOOL` line for
  any that are missing, and the ratchet skips what it cannot run rather than blocking.
- Hooks are tested on macOS and Linux; on Windows they need Git Bash or a `python3` alias.

## Escape hatch

`claude plugin disable factory@lathe` turns every hook off in one step; `claude plugin enable factory@lathe`
turns them back on. Nothing in the repository changes. This is for demos and for debugging the
pack. It is not an enforcement boundary: a team that needs one delivers managed settings
(`templates/docs/admin-setup.md`), and a managed floor stays in force when the plugin is off.

## Tests

```
python3 -m pytest plugins/factory/tests/sdlc -q          # hooks, ratchet, scripts, autonomy (standard library + pytest)
bash plugins/factory/tests/e2e/run_harness.sh            # the hooks as Claude Code runs them, on examples/sample-app
uv run --project plugins/factory python -m pytest plugins/factory/tests/graph -q   # graph, ledger, MCP, orchestrator (Postgres)
bash plugins/factory/tests/epic/check_examples.sh        # the epic examples lint clean
```

The harness copies the sample app into a throwaway git repository, runs init, drives every guard
with the JSON Claude Code sends, checks each planted finding lands in the baseline, and initialises
generated Node, Go and Rust repositories when those tools are installed. It needs `python3` with the
sample app's dependencies (fastapi, sqlalchemy, httpx, pytest) first on the PATH, and ruff and mypy
for a full baseline. `--keep` leaves the work directory for inspection.

## Layout

```
.claude-plugin/plugin.json   .mcp.json (server graph)   hooks/hooks.json + hooks/*.py   skills/<name>/SKILL.md
agents/*.md   scripts/*.py|*.sh   scripts/checkers/<stack>/*.py   lib/sdlc.py   stacks/<stack>.json
templates/{rules,ci,github,docs,...}   sdlc_graph/   sql/   demo/   pyproject.toml   uv.lock
docs/{graph,graph-architecture,graph-decisions,graph-providers,orchestrator}.md   docs/epic/
tests/sdlc/   tests/graph/   tests/e2e/run_harness.sh   tests/epic/   tests/evals/
```

## Component guides

One README per component, written against the code at this version:

- [`sdlc_graph/README.md`](sdlc_graph/README.md): the code graph, the ledger, the orchestrator, the MCP server and the CLI
- [`hooks/README.md`](hooks/README.md): what each hook refuses, the message a person sees, and what to do about it
- [`skills/README.md`](skills/README.md): the lifecycle stage by stage, the other skills, and the conventions every skill follows
- [`docs/agents.md`](docs/agents.md): the subagent definitions and why the work is split between them
- [`scripts/README.md`](scripts/README.md): the scripts behind each stage, and the stack adapters
- [`lib/README.md`](lib/README.md): the shared modules the hooks and scripts import
- [`templates/README.md`](templates/README.md): what init places in a repository, and what fills the placeholders
- [`demo/README.md`](demo/README.md): the graph demo, what it prints and what it resets
