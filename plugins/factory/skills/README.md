# Skills

The 26 skills of the `factory` plugin. Each is a directory with a `SKILL.md` and runs as
`/factory:<name>`. The plugin guide is [`../README.md`](../README.md).

## What a skill is

A skill here is a scripted stage, not a prompt template. `SKILL.md` holds frontmatter and steps. A
lifecycle skill reads the contract (`.claude/work/current.json` and
`.claude/work/<slug>/contract.md`) and the project config (`.claude/sdlc.json`). Its `allowed-tools`
list the tools it may use; in an unattended run (`--permission-mode dontAsk`) a call that no allow
rule covers is denied, not asked. The stages that run on a contract (`tests`, `implement`, `verify`)
never ask. The rule is "ask before, never during" ([`docs/DESIGN.md`](../../../docs/DESIGN.md),
section 14). `/factory:ticket` asks every question once, in one batch, and the answers become the
contract's Decisions. Whatever they leave open is settled by `## Defaults when ambiguous` and logged
in `.claude/work/<slug>/assumptions.md`. When no default may settle it (a path outside the allowed
paths, a wrong frozen test, money, security, data loss), the skill stops. It writes the question to
`.claude/work/<slug>/questions.md` and prints `ESCALATION: <one line>`. Skills are advisory text.
The hooks in [`../hooks/`](../hooks/hooks.json) enforce scope, secrets, frozen tests and
verify-before-commit.

## The lifecycle

Run `init` once, then `ticket`, `tests`, `implement`, `verify` and `ship` for each ticket. Graph on,
they also post evidence and move the ledger node: `drafted`, `tests_frozen`, `implementing`,
`verified`, `in_review`. `ticket` leaves the node `drafted`; a human readies it.

### 1. init

- **In:** the repository. Run it once, before any other factory command.
- **Out:** `.claude/sdlc.json`, rules, a PR template, the CI gate `.github/workflows/sdlc-gate.yml`,
  a `CLAUDE.md` section and today's violations as `.claude/baseline.json`. Then the graph index,
  `/factory:audit` and `docs/onboarding-report.md`. Nothing is committed. On a protected branch it
  first switches to `chore/onboarding`.
- **Human:** answers two graph questions once, confirms the paths and test command in
  `.claude/sdlc.json`, then reviews and commits. `--headless` takes the defaults and lists them.

### 2. ticket

- **In:** ticket text or an issue id. `ticket_intake.py` scans it first, and it stays data.
- **Out:** the branch `feature/<slug>`, `.claude/work/<slug>/ticket.md` and `contract.md`, and
  `.claude/work/current.json`. The contract holds acceptance criteria as test names, runnable
  `## Acceptance` lines, Decisions, `## Defaults when ambiguous`, Capabilities, Allowed paths and a
  Risk tier ([template](../templates/contract.template.md)). A ticket with no problem statement, no
  runnable acceptance check or no named decision owner gets `REJECTED: <what is missing>`.
- **Human:** answers its one batch of questions, then makes the contract ready by filling in the
  Decisions (a Value from the Options, a `Decided by` handle). `acceptance.py --ready` prints `READY`,
  or `NOT READY` and a `FILL` line per gap. A row marked `recommended, unconfirmed` fails it at tier
  medium or high, or on money, security, data or customer-visible behaviour.

### 3. tests

- **In:** the contract. With none, it stops and says to run `/factory:ticket`.
- **Out:** failing tests from the `factory:test-author` agent, plus skeletons of new modules whose
  bodies only raise or throw. `tests_failing_first.py` records a reason per test in
  `.claude/work/<slug>/failing_first.json`: `not_implemented`, `assertion`, `status_mismatch` and
  `other` are intended; `import_error`, `attribute_error`, `name_error`, `syntax_error` and
  `collection_error` mean a broken test, which the freeze refuses. The tests and skeletons are
  committed on their own, then `freeze_tests.py --freeze` stores a sha256 per file in `current.json`
  (`frozen_tests`).
- **Human:** none, unless a test is wrong. Only a human unfreezes (`freeze_tests.py --unfreeze`).

### 4. implement

- **In:** the frozen tests and the contract.
- **Out:** code inside `allowed_paths` that passes the contract's tests, the whole suite and
  `freeze_tests.py --check`. Each default it applied is a row in `assumptions.md`.
- **Human:** none, unless it escalates. A wrong frozen test needs a human to unfreeze it. A schema
  change gets a migration, and `/factory:migrate` renders its SQL for a human to apply.

### 5. verify

- **In:** the working tree and the contract.
- **Out:** `verify.py` runs the tests, the e2e run (when `.claude/sdlc.json` has an `e2e` block), the
  ratchet against `.claude/baseline.json`, the secret and agent-config scans, the frozen-test check
  and the `## Acceptance` lines. The `factory:reviewer` and `factory:security-reviewer` agents then
  read the diff and the assumptions. Only a fully green run writes the stamp
  `.claude/work/last_verify.json`. Evidence is appended to `contract.md`.
- **Human:** reads the Evidence, the assumptions and the "Not reviewed" lists. A high finding is
  fixed, or explained under Review notes, before the stage ends.

### 6. ship

- **In:** a stamp whose fingerprint equals the output of `worktree_fingerprint.py`. If not, it stops.
- **Out:** a PR body from the contract, Evidence and assumptions; the work state cleared with
  `workfile.py --clear <slug>`; a commit; `git push -u origin HEAD`; `gh pr create`. It never merges.
- **Human:** invokes it (`disable-model-invocation: true`). A reviewer who is not the author approves
  and merges. After the merge a human runs `/factory:deploy`. Graph on, they also record it:
  `sdlc-graph evidence add` with `--kind merge_sha`, then `sdlc-graph node transition <slug> merged`.

### feature

`/factory:feature [--trivial] [--headless] <ticket text or id>` is human-invoked. It runs
`factory:impact` when the ticket names paths, `/factory:ticket`, then `acceptance.py --ready`. If a
Decision has no Value or the Defaults block is missing, it prints "NOT STARTED: the contract is not
ready to run unattended" with every `FILL` line, and stops. Otherwise it runs `tests`, `implement`
and `verify`, shows the Evidence and tells the human to run `/factory:ship`. It does not ship, merge
or deploy. Resumed after the PR exists, it records `pr_url` with `factory:feed` if `ship` did not.

## The other skills

### Assessment and planning

| Skill | What it does |
|---|---|
| `assess` | Runs `maturity` and `security` with `--report-only`, prints one summary, then offers the A, B and C options. Creates an epic only after you choose, never runs the fix. Run it after `init`. |
| `maturity` | Scores plan, design, build, test, deploy, maintain and AI practice from 1 to 5 with `maturity.py`, ranks three moves, offers an epic for one. Writes `docs/maturity-assessment.md`. |
| `security` | `security_assess.py` plus the `factory:security-reviewer` judgment pass, merged into `docs/security-assessment.md`. Offers an epic, one feature, or the report only. |
| `audit` | Lists current issues, ranked, with file:line: ratchet violations, unmounted entry points, TODOs, unused modules. Writes nothing. `init` calls it. |
| `epic` | Writes or reviews an epic, features and INVEST stories in `docs/epics/<slug>.md`. Asks once. `lint_epic.py` checks it, with `--agent` for agent-ready stories. |

### Review and operations

| Skill | What it does |
|---|---|
| `review` | Read-only help for a human reviewing an agent's branch or PR: message against diff, evidence, contract, ratchet. Ends "approve-ready" or "needs changes". |
| `review-pr` | Read-only. `review_pr.sh` checks scope against the PR's own `allowed_paths` and runs each new test at the base commit; a test that passes there is suspicious. Never comments, approves or merges. |
| `deploy` | Human-invoked. From the protected branch at a reviewed commit: prints the rollback step, runs `commands.deploy_local`, polls `/ready` (else `/health`) for up to 60 s, runs `commands.smoke`. |
| `rollback` | Human-invoked. Reverts a merged change on `rollback/<short sha>`, gates it with `verify.py`, opens a PR. Never force-pushes, never pushes to the protected branch. |
| `migrate` | Renders the SQL of the branch's new migrations (`commands.migrate_sql`, Alembic by default), flags what is not additive, stops. Never applies, downgrades or stamps. |
| `onboard` | Explains the repository and its working agreement in under 400 words, from the code, the routes and `CLAUDE.md`. |
| `next` | Asks the ledger (`workflow_next`) for the one next step and runs that one slash command. Refuses a `human_only` step. Graph off, it only suggests the command. |
| `feed` | Records evidence with `evidence_add` and moves the node when the guard allows. Never into `approved`, `merged`, `released` or `closed`. Needs the graph. |

### Graph skills and demo

The graph skills need `uv` and a Postgres with pgvector ([`../docs/graph.md`](../docs/graph.md)).

| Skill | What it does |
|---|---|
| `graph` | `status`, `on`, `off`, `url` and `provision` for `local`, `neon`, `aws` or `spanner`. Flips `graph.enabled` in `.claude/sdlc.json`, stores the connection string without printing it, builds the database. |
| `index` | Indexes a commit into the code graph (full snapshot or delta) with `sdlc-graph`, then runs `verify-snapshot`. |
| `impact` | Blast radius of paths or symbols: tests (closest first), routes, owners. Read-only. |
| `contract` | Drafts the contract node with `contract_draft` and writes `.claude/work/current.json` from it. Never marks it ready. |
| `orchestrate` | Human-invoked. Starts the orchestrator daemon for one feature: a fresh `claude -p` session per step (`tests`, `implement`, `verify`, `ship`), stopping at `in_review`. It starts paid sessions. See [`../docs/orchestrator.md`](../docs/orchestrator.md). |
| `demo` | Human-invoked. Runs `bash "${CLAUDE_PLUGIN_ROOT}/demo/run_demo.sh"` on a copy of `examples/sample-app` and narrates each step. Needs `uv`, Docker and a local Postgres; refuses a `DATABASE_URL` that is not local. |

## Conventions

- **Plugin root and scripts.** Paths into the plugin are written `${CLAUDE_PLUGIN_ROOT}/...`, the
  plugin's installed directory. It moves on an update, so run `/factory:init --upgrade` to refresh the
  rules init wrote for it. Scripts run as `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py" ...`
  (isolated mode: no `PYTHON*` variables, no user site directory), one plain command per Bash call:
  no chain, pipe, redirect, heredoc, `$(...)` or `echo $?`. A rule matches the literal text, quotes
  included, so a chained line matches none.
- **Staging.** Claude Code refuses Write and Edit under `.claude/` in an unattended run. A skill
  writes the file with the Write tool to `.sdlc-stage/<path below .claude/work>`, then runs
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/<file>`. That script
  applies the edit guard's rules (slug pattern, no secret, a `current.json` that does not loosen the
  contract) and moves the file. The rule is `Edit(.sdlc-stage/**)`; a `Write(<path>)` rule never
  matches.
- **Headless flags.** `--headless` (`init`, `ticket`, `feature`, `graph`): nothing is asked. `init`
  takes its defaults, `ticket` takes its recommendations and marks them `recommended, unconfirmed`,
  and what cannot be answered is an `ESCALATION:` line. `--trivial` (`ticket`, `feature`): the light
  path, only when no allowed path matches `tier_high_paths` and Decisions is "None.".
  `--feature <slug>` (`ticket`): the ledger feature the contract belongs to. `--report-only`
  (`maturity`, `security`): stop after the report. `scripts/run_headless.sh` starts unattended runs.
- **Graph steps are optional.** They run only when `mcp__plugin_factory_graph__<tool>` or
  `mcp__graph__<tool>` is in the tool list. Without it the skill skips the step silently. If a tool
  answers `{"error": "graph off", ...}` or `{"error": "graph not configured", ...}`, it says the hint
  in one line and carries on. A failing call never blocks.
- **allowed-tools.** `scripts/check_skill_permissions.py` reads each `SKILL.md`, takes every command in
  a code span or fenced line, splits it on `&&`, `||`, `;` and `|`, and needs a rule for each part in
  the skill's `allowed-tools` or in `templates/settings.project.json`. A skill calls another through
  the Skill tool, so the callee's own `allowed-tools` apply.

## Testing and adding a skill

CI runs `claude plugin validate . --strict`, `claude plugin validate plugins/factory --strict` and
`python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory`. The pytest
suite (`python3 -m pytest plugins/factory/tests/sdlc -q`) reads skill text in
`test_autonomy_review.py` and `test_scripts.py`. It asserts on exact phrases, step order and
`allowed-tools` entries, so changing a quoted phrase means changing its test. It plants a bad skill
and expects the checker to fail. It forbids a heredoc (`<<`) or `$(cat` in any skill. It runs
`--own-only` (only the skill's own list counts, as in a folder nobody has trusted) on `ticket`,
`tests`, `implement`, `verify`, `feature` and `ship`. It requires each graph tool a skill names to be
allowed in both spellings.

To add a skill:

1. Create `skills/<name>/SKILL.md`. `name` repeats the directory name. `description` says what it does
   and when to use it. Add `argument-hint` for arguments (read as `$ARGUMENTS`) and
   `disable-model-invocation: true` if it commits, pushes, deploys or spends money.
2. List in `allowed-tools` every tool and Bash command the steps name, in the exact text the steps
   use. If a step could be typed with or without quotes (see `index`), list both.
3. From the plugin directory run `python3 -I scripts/check_skill_permissions.py --root . --verbose`.
   Each `UNCOVERED` line names a rule to add. `$(...)`, a redirect or an unknown program is
   `UNCOVERED` too. A command after "never" is read as prose; one after "ask a human to run" must
   match a deny rule in `templates/settings.project.json`. Add `--own-only` for a skill that must work
   with no project settings.
4. A skill that writes under `.claude/work/` stages the file and needs `Edit(.sdlc-stage/**)`. One that
   calls a graph tool names both spellings in `allowed-tools` and in the settings template, never a
   wildcard.
5. Add a test that pins what matters, and list the skill here and in the two READMEs. `--strict` also
   fails on a bad manifest, an unreadable component, a reserved name, or a marketplace version that
   differs from `plugin.json`, so keep the frontmatter parseable and the versions equal.
