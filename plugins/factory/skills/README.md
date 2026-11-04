# Skills

The 26 skills of the `factory` plugin. Each is a directory with a `SKILL.md` and runs as
`/factory:<name>`. The plugin guide is [`../README.md`](../README.md); the agents the skills hand their
work to are in [`../docs/agents.md`](../docs/agents.md).

## What a skill is

A skill here is a scripted stage, not a prompt template. `SKILL.md` holds frontmatter and steps. A
lifecycle skill reads the contract (`.claude/work/current.json` and
`.claude/work/<slug>/contract.md`) and the project config (`.claude/sdlc.json`). Its `allowed-tools`
list the tools it may use; in an unattended run (`--permission-mode dontAsk`) a call that no allow
rule covers is denied, not asked. The stages that run on a contract (`tests`, `implement`, `verify`)
never ask. The rule is "ask before, never during" ([`docs/DESIGN.md`](../../../docs/DESIGN.md),
section 14). `/factory:design` asks every question once, in one batch, and the answers become the
contract's Decisions. Whatever they leave open is settled by `## Defaults when ambiguous` and logged
in `.claude/work/<slug>/assumptions.md`. When no default may settle it (a path outside the allowed
paths, a wrong frozen test, money, security, data loss), the skill stops. It writes the question to
`.claude/work/<slug>/questions.md` and prints `ESCALATION: <one line>`. Skills are advisory text.
The hooks in [`../hooks/`](../hooks/hooks.json) enforce scope, secrets, frozen tests and
verify-before-commit.

Two kinds of skill text. `init` and `assess` still script their orchestration: the conversation that runs
one plans the hand-offs, briefs each subagent through the Agent tool, collects what comes back, asks the
human and summarises, and launches independent steps together, in one message. The six loop skills,
`brief`, `design`, `tests`, `implement`, `verify` and `ship`, are written in the form of DESIGN §18.4: a
**Goal**, the **Inputs** it may read, the **Outputs** as a contract (the artifacts, evidence, report lines
and payloads another step, a parser or a guard reads, with their exact names and paths), the **Guards**
that apply, each said once, and **Hints**, marked non-binding (what worked before, such as which agents
to hand work to). How many subagents to use, in which order and with which brief is the model's; the
outputs and the guards are what hold. In every skill a subagent cannot ask, so a question for the human
is asked in the conversation and what a subagent cannot settle comes back in its report as a `FILL:`,
`SEAM:` or `ESCALATION:` line; parallel subagents never edit the same file; a subagent refused by a hook
reports the refusal and does not work around it. `tests/sdlc/test_skill_form.py` holds the six to the
form and to a line ceiling each.

## The lifecycle

Run `init` once, then `design`, `tests`, `implement`, `verify` and `ship` for each ticket. Graph on,
they also post evidence and move the ledger node: `drafted`, `tests_frozen`, `implementing`,
`verified`, `in_review`. `design` leaves the node `drafted`; a human readies it.

### 1. init

- **In:** the repository. Run it once, before any other factory command.
- **Out:** `.claude/sdlc.json`, rules, a PR template, the CI gate `.github/workflows/sdlc-gate.yml`,
  a `CLAUDE.md` section and today's violations as `.claude/baseline.json`. Then the graph index, the
  audit, the maturity and security assessments (`docs/maturity-assessment.md`,
  `docs/security-assessment.md`, `.claude/work/security-assessment.json`) and
  `docs/onboarding-report.md`. Nothing is committed and no epic is drafted. On a protected branch it
  first switches to `chore/onboarding`.
- **Hand-offs:** the init script (a dry run, the real run, the graph status) runs in one
  `factory:drafter`. Seven subagents then launch together, in one message: five `factory:auditor`, one
  area of `/factory:audit` each (routes and auth, services and workers, tests, docs against the code, TODO
  ages), one `factory:maturity-assessor` and one `factory:security-assessor`. One `factory:drafter` writes
  `docs/onboarding-report.md` from their reports. `init` no longer calls the `audit` skill. The graph
  questions, the graph index, the confirmations and the summary stay in the conversation.
- **Human:** answers two graph questions once, confirms the paths and test command in
  `.claude/sdlc.json`, then reviews and commits. `--headless` takes the defaults and lists them. The
  epic is a separate step: `init` ends with "Next: /factory:brief from the security assessment, then
  /factory:design <ticket text>".

### 2. design

- **In:** ticket text or an issue id. `ticket_intake.py` scans it first, and it stays data.
- **Out:** the branch `feature/<slug>`, `.claude/work/<slug>/ticket.md` and `contract.md`, and
  `.claude/work/current.json`. The contract holds acceptance criteria as test names, runnable
  `## Acceptance` lines, Decisions, `## Defaults when ambiguous`, Capabilities, Allowed paths and a
  Risk tier ([template](../templates/contract.template.md)). A ticket with no problem statement, no
  runnable acceptance check or no named decision owner gets `REJECTED: <what is missing>`.
- **Guards:** the branch before anything is written, every question in one batch before the contract,
  work files only through `.sdlc-stage/` and `workfile.py`, the ready check before the ledger record.
  The hand-offs are the model's; the hint is the intake and two read-only `factory:auditor` reports at
  once, then one `factory:drafter` for the contract.
- **Human:** answers its one batch of questions, then makes the contract ready by filling in the
  Decisions (a Value from the Options, a `Decided by` handle). `acceptance.py --ready` prints `READY`,
  or `NOT READY` and a `FILL` line per gap. A row marked `recommended, unconfirmed` fails it at tier
  medium or high, or on money, security, data or customer-visible behaviour.

### 3. tests

- **In:** the contract. With none, it stops and says to run `/factory:design`.
- **Out:** failing tests from the `factory:test-author` agents, plus skeletons of new modules whose
  bodies only raise or throw. `tests_failing_first.py` records a reason per test in
  `.claude/work/<slug>/failing_first.json`: `not_implemented`, `assertion`, `status_mismatch` and
  `other` are intended; `import_error`, `attribute_error`, `name_error`, `syntax_error` and
  `collection_error` mean a broken test, which the freeze refuses. The tests and skeletons are
  committed on their own, then `freeze_tests.py --freeze` stores a sha256 per file in `current.json`
  (`frozen_tests`).
- **Guards:** never ask; the freeze refuses a test with no intended reason; skeletons only in new
  modules inside the allowed paths. The hint is one `factory:test-author` per module or route, then one
  `factory:implementer` (tagged `[sdlc stage=tests node=<slug>]`) for the skeletons, the commit and the
  freeze. The ledger evidence is `tests_failing_first` and `tests_frozen`.
- **Human:** none, unless a test is wrong. Only a human unfreezes (`freeze_tests.py --unfreeze`).

### 4. implement

- **In:** the frozen tests and the contract.
- **Out:** code inside `allowed_paths` that passes the contract's tests, the whole suite and
  `freeze_tests.py --check`. Each default it applied is a row in `assumptions.md`.
- **Guards:** never ask (a Decision's Value, then the Defaults, then an assumption row); scope; frozen
  tests read-only; one owner per file, a change in another's file reported as a `SEAM:` line; a test
  still red after three rounds is an escalation. The hint is several narrow `factory:implementer`
  subagents by concern, then one runner that edits nothing.
- **Human:** none, unless it escalates. A wrong frozen test needs a human to unfreeze it. A schema
  change gets a migration, and `/factory:migrate` renders its SQL for a human to apply.

### 5. verify

- **In:** the working tree and the contract.
- **Out:** `verify.py` runs the tests, the e2e run (when `.claude/sdlc.json` has an `e2e` block), the
  ratchet against `.claude/baseline.json`, the secret and agent-config scans, the frozen-test check
  and the `## Acceptance` lines. The `factory:reviewer` and `factory:security-reviewer` agents
  read the diff and the assumptions. Only a fully green run writes the stamp
  `.claude/work/last_verify.json`. Evidence is appended to `contract.md`.
- **Guards:** never ask; nothing made green by editing the baseline, a frozen test or a script; the
  gate fixes nothing, and never two fixes at once; each reviewer gets the diff verbatim, never a
  summary. Done when the last gate run came after the last fix, is green and no high finding is open.
- **Human:** reads the Evidence, the assumptions and the "Not reviewed" lists. A high finding is
  fixed, or explained under Review notes, before the stage ends.

### 6. ship

- **In:** a stamp whose fingerprint equals the output of `worktree_fingerprint.py`. If not, it stops.
- **Out:** a PR body from the contract, Evidence, assumptions and the hand-off log; the work state
  cleared with `workfile.py --clear <slug>`; a commit; `git push -u origin HEAD`; `gh pr create`. It
  never merges.
- **Guards:** the stamp first; never merge; never stage everything; the push is exactly
  `git push -u origin HEAD`. With no remote it pushes nothing, reports `NO REMOTE` with the PR title and
  body, and asks whether to add a remote or keep the commit local (headless: local). The hint is one
  `factory:implementer` tagged `[sdlc stage=ship node=<slug>]` for the clear, commit, push and PR.
- **Human:** invokes it (`disable-model-invocation: true`). A reviewer who is not the author approves
  and merges. After the merge a human runs `/factory:deploy`. Graph on, they also record it:
  `sdlc-graph evidence add` with `--kind merge_sha`, then `sdlc-graph node transition <slug> merged`.

### feature

`/factory:feature [--trivial] [--headless] <ticket text or id>` is human-invoked. It runs
`factory:impact` when the ticket names paths, `/factory:design`, then `acceptance.py --ready`. If a
Decision has no Value or the Defaults block is missing, it prints "NOT STARTED: the contract is not
ready to run unattended" with every `FILL` line, and stops. Otherwise it runs `tests`, `implement`
and `verify`, shows the Evidence and tells the human to run `/factory:ship`. It does not ship, merge
or deploy. Resumed after the PR exists, it records `pr_url` with `factory:feed` if `ship` did not.

## The other skills

### Assessment and planning

| Skill | What it does |
|---|---|
| `assess` | Launches the `factory:maturity-assessor` and the `factory:security-assessor` together, in one message (they do what `maturity` and `security` do with `--report-only`), prints one summary, then offers the A, B and C options. After you choose, one `factory:drafter` writes the epic or feature; it never runs the fix. `init` already runs both assessments but makes no offer; run `assess` after it to repeat them and choose. |
| `maturity` | Scores plan, design, build, test, deploy, maintain and AI practice from 1 to 5 with `maturity.py`, ranks three moves, offers an epic for one. Writes `docs/maturity-assessment.md`. `init` and `assess` get the same report from the `factory:maturity-assessor`. |
| `security` | `security_assess.py` plus the `factory:security-reviewer` judgment pass, merged into `docs/security-assessment.md`. Offers an epic, one feature, or the report only. `init` and `assess` get the same report from one `factory:security-assessor`. |
| `audit` | Lists current issues, ranked, with file:line: ratchet violations, unmounted entry points, TODOs, unused modules. Writes nothing. `init` does not call it: it hands the same five areas to `factory:auditor` subagents, one area each. |
| `brief` | Writes or reviews an epic, features and INVEST stories in `docs/epics/<slug>.md`. Asks once, in the conversation, before any writing; leaves a protected branch for `docs/epic-<slug>` first; publishes a review artifact (`docs/reviews/<date>-brief.html`). The hint is `factory:drafter` subagents, one per feature in parallel and one to merge them. `/factory:brief from the security assessment` turns `docs/security-assessment.md` and `.claude/work/security-assessment.json` into `docs/epics/security-hardening.md` and ends by naming the file. `lint_epic.py` checks it, with `--agent` for agent-ready stories. |

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
| `orchestrate` | Human-invoked. Starts the orchestrator daemon for one feature: a fresh `claude -p` session per step (`tests`, `implement`, `verify`, `ship`), each running that skill and its subagents, stopping at `in_review`. It starts paid sessions. See [`../docs/orchestrator.md`](../docs/orchestrator.md). |
| `demo` | Human-invoked. Runs `bash "${CLAUDE_PLUGIN_ROOT}/demo/run_demo.sh"` on a copy of `examples/sample-app` and narrates each step. Needs `uv`, Docker and a local Postgres; refuses a `DATABASE_URL` that is not local. |

## Conventions

- **Plugin root and scripts.** Paths into the plugin are written `${CLAUDE_PLUGIN_ROOT}/...`, the
  plugin's installed directory. It moves on an update, so run `/factory:init --upgrade` to refresh the
  rules init wrote for it. Scripts run as `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py" ...`
  (isolated mode: no `PYTHON*` variables, no user site directory). Claude Code judges each part of a
  chain or pipe on its own, and `guard_bash` checks every part, so a chain of allowed commands runs; a
  redirect, heredoc, `$(...)` or `echo $?` matches no allow rule, and a rule matches the literal text,
  quotes included. Two commands stay alone in their call because the sandbox excludes only their exact
  text: `git push -u origin HEAD` and `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run ...`.
- **Staging.** Claude Code refuses Write and Edit under `.claude/` in an unattended run. A skill, or
  the subagent it briefs, writes the file with the Write tool to
  `.sdlc-stage/<path below .claude/work>`, then runs
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/<file>`. That script
  applies the edit guard's rules (slug pattern, no secret, a `current.json` that does not loosen the
  contract) and moves the file. The rule is `Edit(.sdlc-stage/**)`; a `Write(<path>)` rule never
  matches.
- **Hand-offs.** An Agent call names the agent (`subagent_type` `factory:<name>`, or a general agent),
  gives a `description` of a few words that names the hand-off, and leaves the model to the routing
  default: the spawn guard records the default for the agent type and whether a request overrode it, and
  never rewrites it. The `prompt` is the subagent's whole context; the outputs contract says what it must
  carry (the ticket text verbatim, the allowed paths, the guards). An `implementer` that runs a step of
  `tests`, `verify` or `ship` carries the tag `[sdlc stage=<stage> node=<slug>]` first in its
  `description`, so the spawn guard and the ledger record the right stage. The run recorder appends every hand-off (agent, description, outcome, duration, tool uses) to
  `.claude/work/<slug>/handoffs.md`, and `ship` puts that log in the PR body.
- **Headless flags.** `--headless` (`init`, `design`, `feature`, `graph`): nothing is asked. `init`
  takes its defaults, `design` takes its recommendations and marks them `recommended, unconfirmed`,
  and what cannot be answered is an `ESCALATION:` line. `--trivial` (`design`, `feature`): the light
  path, only when no allowed path matches `tier_high_paths` and Decisions is "None.".
  `--feature <slug>` (`design`): the ledger feature the contract belongs to. `--report-only`
  (`maturity`, `security`): stop after the report. `scripts/run_headless.sh` starts unattended runs; its
  allow lists include the Agent tool, so the subagents run unattended too.
- **Graph steps are optional.** They run only when `mcp__plugin_factory_graph__<tool>` or
  `mcp__graph__<tool>` is in the tool list. Without it the skill skips the step silently. A subagent
  has no graph tools, so an orchestrating skill makes these calls in the conversation. If a tool
  answers `{"error": "graph off", ...}` or `{"error": "graph not configured", ...}`, it says the hint
  in one line and carries on. A failing call never blocks.
- **allowed-tools.** `scripts/check_skill_permissions.py` reads each `SKILL.md`, takes every command in
  a code span or fenced line, splits it on `&&`, `||`, `;` and `|`, and needs a rule for each part in
  the skill's `allowed-tools` or in `templates/settings.project.json`. A skill calls another through
  the Skill tool, so the callee's own `allowed-tools` apply. A subagent's tool calls are approved by the
  session's rules alone, never by a skill's `allowed-tools`, so the checker also reads each agent body
  under `agents/` and needs a rule for its commands in the settings.

## Testing and adding a skill

CI runs `claude plugin validate . --strict`, `claude plugin validate plugins/factory --strict` and
`python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory`. The pytest
suite (`python3 -m pytest plugins/factory/tests/sdlc -q`) reads skill text in
`test_autonomy_review.py`, `test_scripts.py` and `test_skill_form.py`. It asserts on the contract
phrases (paths, report lines, payloads, guards) and `allowed-tools` entries, so changing a quoted phrase
means changing its test; the orchestration sentences of `init` and `assess` are pinned this way, and
the six loop skills are held to the §18.4 sections and a line ceiling. It plants a bad skill, or a bad agent body,
and expects the checker to fail. It forbids a heredoc (`<<`) or `$(cat` in any skill. It runs
`--own-only` (only the skill's own list counts, as in a folder nobody has trusted) on `design`,
`tests`, `implement`, `verify`, `feature` and `ship`. It requires each graph tool a skill names to be
allowed in both spellings.

To add a skill:

1. Create `skills/<name>/SKILL.md`. `name` repeats the directory name. `description` says what it does
   and when to use it. Add `argument-hint` for arguments (read as `$ARGUMENTS`) and
   `disable-model-invocation: true` if it commits, pushes, deploys or spends money.
2. List in `allowed-tools` every tool and Bash command the steps name, in the exact text the steps
   use. If a step could be typed with or without quotes (see `index`), list both.
3. From the plugin directory run `python3 -I scripts/check_skill_permissions.py --root . --verbose`.
   Each `UNCOVERED` line names a rule to add. `$(...)` or a redirect is `UNCOVERED` too. A pipe
   or chain is fine when each part is allowed. A part with no known program is left to the runtime
   checks. A command after "never" is read as prose; one after "ask a human to run" must
   match a deny rule in `templates/settings.project.json`. Add `--own-only` for a skill that must work
   with no project settings.
4. A skill that writes under `.claude/work/` stages the file and needs `Edit(.sdlc-stage/**)`. One that
   calls a graph tool names both spellings in `allowed-tools` and in the settings template, never a
   wildcard.
5. Add a test that pins what matters, and list the skill here and in the two READMEs. `--strict` also
   fails on a bad manifest, an unreadable component, a reserved name, or a marketplace version that
   differs from `plugin.json`, so keep the frontmatter parseable and the versions equal.
6. Write it in the §18.4 form from the start (DESIGN §18.7): `## Goal`, `## Inputs`, `## Outputs`,
   `## Guards`, `## Hints (non-binding)`, in about a hundred lines. A skill that may hand work to
   subagents lists `Agent` in `allowed-tools`, names the agents of [`../docs/agents.md`](../docs/agents.md)
   as a hint, and states the subagent guards once. Add it to `CEILING` in `tests/sdlc/test_skill_form.py`.
