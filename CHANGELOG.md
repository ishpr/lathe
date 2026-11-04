# Changelog

Versions are the `version` field of the plugin's `.claude-plugin/plugin.json`. The marketplace
entry in `.claude-plugin/marketplace.json` carries the same number.

## factory 1.0.1: clean onboarding output

- **The generated CI gate passes its own pin check.** The gate refuses any plugin ref that is not a 40-character
  commit SHA, but init wrote `main`. Init now resolves `--marketplace-ref` (a tag or branch, by `git ls-remote`) to a SHA,
  and without the flag writes the commit the plugin was installed from; when nothing resolves it warns. Test:
  `test_clean_onboarding.py`.
- **Generated files carry no template markers.** Init renders `<!-- sdlc:if -->` blocks (kept or dropped by what the
  repository has), `{tier_high_paths}`, `__TIER_HIGH_PATHS__`, `__APP_DB__`, `sdlc: REC-nn` comments and the gate's
  route-sweep markers; only the `sdlc:start`/`sdlc:end` pair around the CLAUDE.md block stays. `dependabot.yml` gets a
  `pip` entry per Python manifest (at `/` for a root `pyproject.toml` or `requirements.txt`) and an `npm` entry only
  for a `package.json` with dependencies.
- **`maturity.py` judges only what is committed.** It listed untracked files, so an onboarding run scored the
  CLAUDE.md, `.claude/` and workflows init had just written (test 3, deploy 3, AI practice 3). It now reads the tree at
  HEAD.

## factory 1.0.0: measure, then free: stage evals, stage-named commands, skills as goals

1.0.0 is released by the owner's decision ahead of two of DESIGN §19's exit criteria: the after-rewrite eval
`compare` (criterion 3) and the self-hosting increment, L3 through the loop (criterion 2), which moves to 1.1. The
baseline it will be compared against is recorded (`plugins/factory/docs/baselines/2026-11-04-02586f6fb0d3.json`).

Measurement first (DESIGN §18.6), then the freeing it measures: per-stage outcome evals and a baseline harness, the
commands renamed to their lifecycle stages, the six loop skills rewritten as goals, inputs, outputs and guards, and
the evidence of what each human decider was shown.

- **Skills rewritten as goal, inputs, outputs, guards (DESIGN §18.4, §18.7 step 4).** brief, design, tests,
  implement, verify and ship state their goal, inputs, outputs as a contract (every artifact, line and evidence field
  another step reads), guards and non-binding hints. Scripted fan-out and numbered steps are gone where a guard holds
  the outcome; exact command forms stay where the sandbox or an allow rule matches text. `test_skill_form.py` pins
  the form and a length ceiling. Measured against the recorded baseline with `baseline.py compare`.
- **Pushes from a session (finding #47).** The settings template excludes exactly `git push -u origin HEAD` from the
  sandbox, so ship can push without a human terminal; the Bash guard still refuses force, mirror, delete and
  protected-branch pushes. Repository rulesets should target the default branch only (finding #49).
- **Install guide** (`docs/install.md`): prerequisites, install, auto-update, onboarding, branch protection,
  checks, uninstall and troubleshooting, including that an unattended init needs a trusted folder (finding #50).
- **`review_pack` evidence (DESIGN §17.2, X2).** What a human decider was shown is ledger evidence: `init`, `assess`, `brief` and `design` post the saved `docs/reviews/<date>-<pack>.html` with its sha256 and what it was shown for; `decision resolve` stores the newest matching pack as `meta.pack` (`sdlc-graph decision show <id>` prints it) and a transition cites the newest pack shown for it since the previous one. It is cited, never required: no guard reads it.
- **Per-stage outcome evals.** `tests/evals` is now the one eval root (`experimental.evals` in plugin.json): the
  epic cases moved from `tests/epic/evals` to `tests/evals/epic`, and `tests`, `implement`, `verify` and `ship` each
  have two cases built on `examples/sample-app` by `tests/evals/_fixtures/build.sh` (two small contracts,
  `item-get` and `rate-limit`, at the state the stage starts from: ready, tests frozen, implemented, verified with a
  local bare origin). Each case's graders read only artifacts (the failing-first record, the verify stamp, the PR
  body), and `tests/evals/_fixtures/check.py` judges the workspace each run leaves, never the steps it took: the
  tests fail first for an intended reason and are frozen; frozen tests unchanged and passing, the diff inside the
  allowed paths, the ratchet clean; a current stamp with `frozen_ok` and no failed acceptance, and no stamp over a
  tree holding a planted out-of-scope edit; the change committed and pushed, main untouched locally and on origin,
  the work state cleared and the PR body carrying the Evidence section and the `Open decisions:` line.
- **New metrics in `metrics.snapshot`:** `merged_contracts`, `turns_per_merged_contract` (with `runs_with_turns`),
  `guard_trips` (frozen-test, scope, secret and other refusals, from the orchestrator's gate denials and a new
  `guard_blocks` copy of `.claude/work/blocks.jsonl` that `hooks/record_agent_run.py` puts on each hand-off's run,
  timed by a new `at` in the spawn guard's note), `denials` (the share of gate-checked runs with any denial, split
  into form and capability) and `model_overrides` (runs that asked for a model whose tier differs from the routing
  default).
- **`scripts/baseline.py`:** `prepare` builds the eval Python, `run` runs the eval set (scaffolds on, Bash, Write and
  Edit granted, the workspaces kept for `check.py`, an unreachable ledger for the sessions) and writes
  `docs/baselines/<date>-<sha>.json` with each run's outcome, cost and turns and the metric snapshot; `compare`
  reports per stage held, improved or regressed (`PASS_RATE_TOLERANCE` 0.20, `SCORE_TOLERANCE` 0.05, a case that
  always passed and now never does) and whether cost and turns stayed within 1.25x.
- **Fixed:** two epic cases (`agent-ready-headless-owner-missing`, `agent-ready-story-no-questions-left`) did not
  load ("case.yaml must be a YAML object"): their prompt frontmatter sat in case.yaml as a second YAML document; it
  is in prompt.md now.
- **verify refuses a change outside the contract.** `verify.py` has a new `scope` step: every file that differs from
  the contract's start (current.json `start_commit`, else the merge base with the protected branch; commits since,
  staged, unstaged and untracked) must be inside current.json `allowed_paths`, judged by the orchestrator drift gate's
  own matcher and work-state rules, now in `lib/sdlc.py` (`outside`, `work_exempt`) and re-exported by
  `sdlc_graph/orchestrator/gitops.py`. A file outside is a red step naming it, and no stamp; without a contract the
  step is skipped. The `verify-refuses-scope-violation` case now expects exactly that: refused, no stamp.

- **Breaking: commands renamed (DESIGN §16.7, lifecycle increment 1).** Commands carry their stage names, with no aliases:
  `/factory:epic` is `/factory:brief`, and `/factory:ticket` and `/factory:contract` are one command, `/factory:design`
  (the contract skill's steps are now step 8 of design). `skills/epic/` is `skills/brief/`; `skills/ticket/` and
  `skills/contract/` are `skills/design/`. The ledger's next step for a drafted contract, the headless wrapper's
  `--headless` flag, the hook messages, the templates and the docs use the new names. Unchanged on purpose, because they
  name artifacts, kinds or scripts, not commands: `docs/epics/`, `docs/epic/`, `tests/epic/`, `scripts/lint_epic.py`,
  `scripts/ticket_intake.py`, the MCP tools `epic_decompose` and `contract_*`, the routing stage `contract.design` and
  the node kind `epic`. `tests/sdlc/test_rename.py` fails on any remaining old command name outside this file.

## factory 0.8.0: routing is a default, the sandbox leftovers, lifecycle stages

- **Routing as a default with ceilings (DESIGN §4, §18 step 2; finding #42).** The spawn guard no
  longer rewrites a subagent's model to the stage default. It records the request, the default and
  whether it was an override (spawn log and run row, `run start --meta`), and refuses only a model
  outside `availableModels`, a spent Fable budget or unusable input. `metrics.runs_by_tier` reports
  `model_overrides`; the skills no longer say "never pass a model".
- **Sandbox leftovers (#44, #45, #46, #48).** `run_headless.sh` uses a named pipe under `$TMPDIR`
  instead of process substitution. The Spanner emulator probe is a gRPC handshake with a 2 s
  deadline and no proxy for localhost, and emulator tests are capped at 90 s, so a stray listener
  skips in seconds instead of hanging a suite. Process identity uses `os.kill`, `/proc` or sysctl
  instead of `ps`, and never kills a process it cannot identify. `ship` and `rollback` pass PR
  bodies with `--body-file`.
- **Lifecycle L2 (DESIGN §16.1, §16.9).** A registry conformance test holds the kinds, machines,
  evidence kinds, migrations, Spanner DDL, export and index in lock step, and fixed the eleven
  places they disagreed. Migration `013_lifecycle.sql` adds the finding and idea kinds,
  `promoted_to`, four state machines, the guard grammar and seven evidence kinds; the
  `lifecycle_view` MCP tool and `sdlc-graph lifecycle view|backfill|resolve` project the stages.
  `parent_stage` is a stub, pinned by a strict xfail.

## factory 0.7.0: lathe onboarded itself, and fixed what it found

Factory 0.6.0 was installed in its own repository and run end to end: `/factory:init`, the audits,
both assessments, the verify gate, with several git worktrees carrying parallel work. It found 48
gaps in itself, recorded with their causes in `docs/dogfood-findings.md`. This release fixes them.

- **Init on real repositories.** Monorepos are detected one and two levels down (`projects`), with
  app and test paths from each project's own layout; paths that match nothing are named. Project
  settings register the marketplace and plugin, the allow list fits the detected stack plus the
  exact test command, and plugin rules survive an upgrade (a version wildcard). Existing worktrees
  join the sandbox's write list; `.claude/work/*` is ignored; generated files leave the PR size
  count; a sandbox-refused baseline prints the human command; pending graph migrations are
  reported and applied. Init now writes two review artifacts, one per decision: an onboarding
  page with a system map from the code graph, and an assessment page with the A/B/C decision.
- **CI templates built from what was detected.** The gate runs `commands.test` after installing
  the project, the route sweep appears only when routes exist, tool versions are pinned, the
  plugin checkout is pinned to a commit, and the ledger secret reaches only the step that posts
  evidence. The Claude review workflow runs from the base branch's definition, so no pull request
  can read the API key. Generated docs, rules, the licence allow-list and Dependabot no longer
  assume a FastAPI app.
- **Checkers that measure the repository, not its virtualenvs.** Virtualenvs and gitignored paths
  are skipped; entry points come from `hooks.json`, console scripts and `scripts/`; config modules
  and constant tables are recognised; ruff uses the project's config; test-fixture keys are not
  secrets; tools resolve from the project's environment or `uvx`.
- **Guards that are precise and worktree-aware.** A commit is judged in its own worktree; reads
  and comments are not writes. Human-owned files (the baseline, `sdlc.json`, settings) get `ask`
  in an interactive session and `deny` unattended, and the three human-only commands run outside
  the sandbox once approved. `verify` runs without a contract.
- **Permissions by category** (DESIGN §18 step 3): read-only inspection, read-only git and the
  stack's own commands are allowed by category; every deny rule stays, checked by a deny-list lint.
- **Security.** MCP writes record their actor and a run closes only by its opener; the OIDC
  verifier refuses a token without a key id and refetches once on an unknown one; OAuth write
  tools need a scope; test fixtures refuse a non-local database; the settings template denies
  writes to settings, `.mcp.json` and `.github/`, and narrows the `gh` exclusions.
- **Graph.** Tests that drive hooks and scripts through subprocess count as test edges;
  `graph_impact` has a cap, a counts-only mode and a whole-suite flag; the security assessor finds
  the graph CLI from a subagent.
- **Review artifacts in unattended runs.** Without the Artifact tool, the model designs the same
  artifact as a self-contained HTML file in `docs/reviews/`, which is exempt from a contract's
  scope rule.

Known, not fixed here (in the findings log): emulator tests hang under the sandbox (#45), process
identity needs `ps` (#46), `run_headless.sh` uses process substitution (#44), and the spawn guard
still rewrites models by stage (#42, DESIGN §18 step 2).

## factory 0.6.0: trusted CI evidence, review artifacts, and the design caught up

### Review artifacts at each human gate

- At the decision point of `init`, `assess`, `maturity`, `security`, `epic` and `ticket`, the skill
  asks the model to create a review artifact for a customer, PMO and management audience with the
  Artifact tool, from the step's reports and using only their figures. It is published privately,
  the human gets its claude.ai link to open it inline, and its HTML is saved as `docs/reviews/<date>-<kind>.html` for the
  portal's evidence. One instruction per skill; no renderer, template or checker.

### CI evidence bound to the trusted workflow

A review found that any workflow in a repository, including one a pull request adds, could mint an OIDC token
the ledger accepted as binding CI evidence, and that the PR chose which contract it applied to. These close it.

- **Breaking:** `in_review -> approved` has a new guard, `ci_matches_approval`: the newest binding `ci_run` must be
  green and its `sha` must equal the newest `review_approval.commit`. `review_approval` gains `commit` (the commit
  the reviewer approved); rows without it cannot satisfy the guard, so re-record approvals with `commit`. Closes
  the gap where CI went green on commit A, the author pushed B and a reviewer approved B. `approved -> merged` and
  `in_review -> merged` gain `merge_head_matches_ci`: an optional `merge_sha.head` must equal the sha CI verified
  (absent: unchanged, as a squash merge makes a new sha). No CI writer posts `review_approval` yet.
- **Breaking:** binding `ci_run` evidence is posted only by the new `templates/ci/sdlc-evidence.yml`, a
  `workflow_run` job on the gate that GitHub runs from the default branch and that runs no PR code. The
  `evidence` job is gone from the four gate templates. `init_project.py` installs the new file as
  `.github/workflows/sdlc-evidence.yml`; existing projects rerun `init_project.py --upgrade` and take the
  `.new` files. It posts the real conclusion (red runs too), the run's head sha and the gate run id, and skips
  a run that is not the gate file, a PR into another branch, a PR that changes the gate file, and a stale head.
- **Breaking:** `sdlc-graph evidence add --source ci:...` refuses every token unless `SDLC_OIDC_WORKFLOW_REF`
  is set and the token's `job_workflow_ref` equals it exactly (`{repository}` is substituted, so
  `{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main` serves every repo), and unless
  `event_name` is `workflow_run` (`SDLC_OIDC_EVENTS`; never widen it).
- **Breaking:** a node's repo must be `owner/name` and equal the token's `repository` exactly; a bare name no
  longer matches `anyone/<name>`, and an empty repo no longer skips the check.
- **Breaking:** the template reads `SDLC_DATABASE_URL` from the `sdlc-ledger` environment, which you restrict
  to the default branch (templates/docs/admin-setup.md, "CI evidence"); a repository secret is readable by a
  same-repo PR's workflows.
- The slug comes from the ledger, not the PR: `sdlc-graph evidence find --kind pr_url --repo R --pr N` (or
  `--url`) names the contract whose `pr_url` is that PR, so a shipped PR (no `current.json`) gets its evidence.
- The ledger writes the verified claims into the row (`payload.oidc`, `payload.evidence_run_id`); a caller
  sending either key is refused. `green` must equal `conclusion == "success"`. The token is verified before
  any database access, and the CLI verifies it before opening a connection.
- **Breaking:** MCP over HTTP with OAuth acts as `agent:oauth/<sub>` per request (the verified token's subject)
  instead of one fixed identity; still agent-class. A call with no verified subject is refused. stdio unchanged.
- MCP OAuth: JWKS fetched off the event loop and cached; an unknown `kid` refetches at most once per 60 s and
  otherwise fails at once. PS384, PS512, ES512 and EdDSA accepted (asymmetric only).
- New `.github/workflows/oidc-smoke.yml` in this repository checks the verifier against a real GitHub token.
- docs/graph.md, docs/graph-decisions.md (D9) and templates/docs/admin-setup.md say what binding evidence now
  proves and what it still trusts.

### The design and the plugin agree again

An audit of docs/DESIGN.md against the plugin found gaps; these close them.

- CI evidence is binding only with a verified GitHub OIDC token (signature, issuer, audience,
  expiry, repository); a `ci:` row without one is refused and `--signed-by` is gone. The CI
  templates post `ci_run` from a separate job that holds `id-token: write`.
- The index does all four of its jobs: dedupe at 0.15 cosine distance (trigram fallback), the
  precedent of earlier contracts on the same paths, the rules whose `paths:` match, and similar
  past incidents (`intake_context`, `incident_similar`).
- New MCP tools `epic_decompose` (proposed features, walking skeleton first, nothing written) and
  `release_request`; OAuth on `--http`; `metrics_snapshot` adds intent survival, repeat incidents
  and cost per merged contract by tier.
- Model routing: `planner` and `triage` agents, the orchestrator pinned to Opus in the project
  settings, alias pins and `availableModels` in the managed-settings example, a model-switch log
  hook, and `templates/docs/sdk-routing.md`.
- `/deploy` and `/rollback` drive the release state machine; `sdlc-graph incident open --band`
  logs, runs a read-only diagnosis, or opens a P1 decision; the PR template shows the open
  decisions and `/ship` keeps the PR a draft while any remain.
- Foldkit: the `submodel` kind and `emits`, `renders` and `routes_to` edges (migration 011);
  auth and payment views join `tier_high_paths`.
- Declared versus observed capabilities: a never-blocking hook records accesses outside the
  contract's capabilities, and the verify stamp counts them as findings.
- docs/DESIGN.md now describes the deliberate deviations (state machines as a file, the spawn
  guard rewriting rather than refusing, `/ticket` stopping at `drafted`, credential types on run
  rows, and others) and its sections are in order.

## factory 0.5.0: the main conversation orchestrates, subagents execute

- The six working skills (`init`, `ticket`, `tests`, `implement`, `verify`, `ship`), `assess` and `epic` now
  orchestrate: the conversation that runs a skill loads it, plans the hand-offs, briefs each subagent
  through the Agent tool, collects what comes back, asks the human and summarises; every step that does
  work (scripts, audits, assessments, drafting, tests, implementation, the gate, the reviews, the commit)
  runs in a subagent, and independent steps run as parallel subagents launched in one message. Before
  each hand-off the skill says in one line what is handed off and why; when the subagents return, what
  came back and what it means. A question for the human is always asked in the conversation, since a
  subagent cannot ask. Parallel subagents never edit the same file.
- Five agents join `test-author`, `reviewer` and `security-reviewer` under `agents/`: `auditor` (one audit
  area per brief: routes and auth, services and workers, tests, docs against the code, TODO ages from
  git history), `maturity-assessor` and `security-assessor` (the maturity and security scripts,
  report-only; the security assessor runs the judgment pass itself), `drafter` (the onboarding report,
  the epic, the contract draft, through the same staging paths the skills use) and `implementer` (edits
  inside the files its brief lists, runs the test command; the hooks bind it like anyone). Each has its
  own tool list; the spawn guard routes them (`audit` and `assess` at the executor tier, `draft` at the
  orchestrator tier) and `check_skill_permissions.py` now checks the commands in agent bodies against
  the settings rules too, since a subagent's tool calls are approved by the session's rules alone.
- `/factory:init` includes the two assessments: after the working agreement, five auditors and the two
  assessors run together in one message, then one drafter writes the onboarding report; nothing is
  committed and no epic is drafted there. The epic is a separate, visible step: `/factory:epic from the
  security assessment` reads `docs/security-assessment.md` and `.claude/work/security-assessment.json`,
  drafts one feature per subagent in parallel, merges them through one drafter into `docs/epics/<slug>.md`,
  asks any question in the open and ends by naming the file. `/factory:assess` keeps its own offer.
- `run_headless.sh` puts the Agent tool on its base allow list so unattended runs fan out too, and the
  run recorder appends each hand-off (agent, brief title, outcome, duration, tool uses) to
  `.claude/work/<slug>/handoffs.md`, so the evidence trail names who did what.

## factory 0.4.4: end-to-end tests in the gate

- `lib/test_reasons.py` reads `playwright test` output (the `list` reporter's text and the `json`
  reporter's report) into the same per-test reasons as pytest and vitest: a failed expectation and an
  element or page state that never came (`expect(locator)` timeouts, `locator.click`, `waitForFunction`)
  are intended first failures; a web server that did not start, a page that could not be reached
  (`page.goto` refused or timed out), a spec that does not parse or import, and a run that found no test
  are wrong reasons `freeze_tests.py` refuses. `detect_runner` knows `playwright test` and `pnpm test:e2e`.
- `.claude/sdlc.json` gains an `e2e` block (`command`, `build`, `serve`, `url`, timeouts); `/factory:init`
  writes it for a Node repo with a `playwright.config.*`, with a `sandbox.excludedCommands` entry for the
  runner below. Nothing with a browser runs inside the sandbox on macOS (Chromium's Mach bootstrap
  check-in is denied; a dev server with a file watcher dies too), so the suite runs through one top-level
  command, `scripts/e2e.py run [specs]`, which the human lets out of the sandbox and the orchestrator
  excludes when the contract's approved commands name the e2e command. It makes the app listen at `url`
  (`lib/e2e.py`: a server already listening is reused only when it was started from this checkout,
  otherwise `build` runs once and `serve` is started for the run and stopped after it; a port held by
  another checkout is refused rather than grading the wrong code), runs the suite with
  `--reporter=list,json` and records the run under `.claude/work/e2e/` with the tree's fingerprint.
- `tests_failing_first.py` reads a Playwright spec (it imports `@playwright/test`) from that recorded run
  and refuses one the tree has moved past or that did not cover the spec; the stack's test command never
  sees it (vitest cannot collect one, which recorded `collection_error` and stalled the console's
  lifecycle contract). `--from-output` accepts `--runner playwright` for a saved list run or report. A
  skipped test is noted.
- `verify.py`'s `e2e` step reads the same recorded run (red when there is none, when the tree changed
  since it, or when it was narrowed to some files), records it in the stamp (`e2e: {command, url, server,
  recorded_at, passed, failed, skipped}`) and hands its report to `freeze_tests.py --check --e2e-report`,
  which fails when a frozen Playwright spec did not run or was skipped.
- The orchestrator's drift gate no longer counts a test runner's gitignored output (`test-results/`,
  `playwright-report/`, `blob-report/`, `coverage/`) as a planted file, nor the recorded e2e run under
  `.claude/work/e2e/`: the lifecycle contract lost two tests attempts to those. A file the session
  commits inside the allowed paths is its change even when an escalated attempt had left it untracked
  (two more attempts were judged `no_change` for committing the spec attempt 1 wrote).
- The node test-quality checker reads a Playwright test's body after the arrow, not the `{` of its
  destructured `({ page })` fixture: every e2e test had been a `no-assert` finding (twelve on one spec).
- `freeze_tests.py` recognises tests in JavaScript and TypeScript (`test(`, `it(`, `describe(`), Go
  (`func Test`) and Rust (`#[test]`): 0.4.3 read only Python, so every `.spec.ts` and `.test.ts` was
  left unfrozen with a note.
- The capability compiler sets `sandbox.network.allowLocalBinding` when a contract's egress names
  `localhost` or `127.0.0.1`, so an orchestrated step can serve the app its e2e suite drives.

## factory 0.4.3: a frozen file that holds no test

- `freeze_tests.py --freeze` no longer freezes a test-path file that defines no test function or
  class (an empty package `__init__.py`, a helper module): it says so and freezes the rest. The
  `--check --junit` pass no longer expects such a file, frozen earlier, to have run. Found in an
  interactive run: the tests step froze `tests/<pkg>/__init__.py` and verify refused the whole
  run with "frozen test did not run" until a human unfroze it.

## factory 0.4.2: what a quality review of the rehearsal's outputs found

Graded output by output (the onboarding report, a contract, a tests commit, an orchestrated run to a
pull request, the assessment prompts), then fixed at the prompt that caused each weakness:

- ticket: ask which responses a change covers (errors included) and what identifies the caller; a
  criterion on an error response when the Outcome promises something of every response; the stack
  rules the change falls under become criteria; Paths and Data lines written as data (globs, store
  entries), Values copied from Options.
- tests and the test author: pin only what the contract states, fake external clients broadly
  (a counter fake accepts incr, incrby, expire, set, get, ttl and pipelines), keep implementation
  internals out of the brief, wait for a backgrounded agent instead of ending the turn; freeze the
  existing test files the step edited; the freeze prints the `tests_frozen` evidence payload with
  full hashes.
- the spawn guard: a known agent type (`test-author`, `reviewer`, with or without a plugin prefix)
  names its own stage, so the test author and the reviewers run on the executor tier again instead
  of inheriting the contract's `contract.design` stage from current.json.
- verify: the reviewers get `git diff HEAD` verbatim, never a summary; a fix after review stays
  inside the allowed paths and gets a second look; no new smoke scripts from the gate.
- init: the audit prints nothing when another skill invokes it; "what to do first" ranks by
  severity, then reach, then blast radius; init may call `graph_snapshot` and `graph_impact`.
- index: a sha already stored still returns its counts; the MCP snapshot docstring promises no
  counts.
- workfile: `assumptions.md` is append-only across stages (implement had overwritten the tests
  stage's rows).
- the stop hook's nudge names assumptions.md, not Decisions, as the place for a reason.
- assess: the top three findings are the file's first three; a missing blast radius is said once.

## factory 0.4.1: what a timed rehearsal found

- maturity, security and assess run each script as one plain command; a chained `; echo "exit=$?"`
  matched no allow rule in an unattended run and both assessments were denied.
- index may call `graph_status` and `graph_snapshot` over MCP.
- init goes on to the report and the summary after the audit, whatever the audit printed.
- ticket: every new name the tests import lives in a new module named in Defaults, so the tests
  step can write its import skeleton without editing an existing module.
- The demo ticket names its decision owner, which the ticket skill requires.
- guard_edit refuses a Write or Edit outside the project directory while a contract is active
  (the temp directory stays writable).
- The live orchestrator test deletes the previous run's branch on the scratch remote first.

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
- The vitest failure-reason parser reads the output of a config with named projects (`test.projects`,
  workspaces), where every file line and `FAIL` header carries a `|project|` prefix. The console's first
  tests session had twelve good tests recorded as a collection error and the freeze refused them.
- Orchestrator sessions run with `PYTHONDONTWRITEBYTECODE=1`, and the tests skill says the test run is the
  syntax check: live run 6's tests session chained `py_compile` and a `__pycache__` cleanup into one denied
  command, which no contract can grant, and the step ended as a P1.
- The orchestrator's drift gate counts `*.tsbuildinfo`, `.eslintcache` and `.DS_Store` as tool state, not as
  files a session planted outside its paths: the console's third contract lost an implement attempt because
  `tsc --incremental` wrote `tsconfig.tsbuildinfo`. The stop hook's "no test changed" nudge stays quiet
  while the contract has frozen tests, since in a test-first run the implement step changes no test.
- A step resumed after a human's answer, a reported refusal or an attempt that changed nothing is judged on the
  whole step's work: what the earlier attempts committed or left in the worktree inside the allowed paths counts
  (files identical to the base branch do not, so a merge of it is not drift), and evidence counts from the first
  attempt of the chain. A drift reset or a low conformance score ends the chain, so a retry after those still has
  to do its own work. The console's second contract had been judged "no change" three times in a row after its
  implementation was already in the worktree.
- The commit guard's skeleton refusal ("not a skeleton here") is a denial of form: the session takes the logic out
  and commits again, as the console's first contract did; and a refusal the block log repeats with a truncated
  command is one denial, not two.
- An assumption a session records as evidence keeps its Decision column: the payload the verify skill prescribes
  now names a `decision` key, and the ledger's normaliser reads `decision`, `relies_on`, or a `decision: none` /
  `decision: D1` text written into any other field. The console's verify step had scored conformance 0 three
  times because its own recorded assumptions, which carried the decision under `choice`, looked decision-less.
- A row of assumptions a session records on top of a well-formed `assumptions.md` that lacks keys is a note on the
  run, not a conformance failure (the file is the record the skills mandate; with no usable file the row's gaps
  still count), and a conformance failure that came from assumption bookkeeping alone does not end a step's
  resume chain. The console's third contract had lost its implementation's conformance to items recorded as
  bare strings, then retried three times with nothing to change.
- `ratchet.py --fast --files <new files>` runs the project's typecheck too, filtered to those files. The tests
  step ratchets its new test files that way, and vitest runs a test file with a type error without complaint,
  so twice a frozen test reached verify with a type error only a human could then fix.
- The verify skill records the frozen tests' results as `acceptance` evidence on its first attempt when the
  contract has no runnable acceptance lines, since the ledger's `verified` guard counts a passed test per
  criterion; two console contracts had been refused twice each before their sessions worked that out.
- `docs/frontend-contracts.md` says how a scene test drives a view helper that takes no focus Message:
  `Scene.focus` needs an `OnFocus` handler in Foldkit 0.170, so keyboard reachability is the key pressed on
  the located element, which the helper handles. The console's interactive helpers had eleven frozen tests
  that could not pass for that reason.
- The node CI gate is written for the repository's package manager: corepack puts pnpm or yarn on the runner at
  the version `package.json`'s `packageManager` pins, `actions/setup-node` caches for it, the lockfile pair check
  watches the right lockfile and the install is that manager's lockfile-only form (`npm ci`, `pnpm install
  --frozen-lockfile`, `yarn install --frozen-lockfile`, `bun install --frozen-lockfile`). The console's gate had
  died in setup-node with "Dependencies lock file is not found" on its first three pull requests, so it never ran.
- The advisory Claude review workflow skips with a notice when the repository has no `ANTHROPIC_API_KEY`
  secret instead of failing the check. The node gate's install step is the project's `commands.setup` when it
  has one (the same command the orchestrator runs in a fresh worktree): the console's gate, once it ran,
  reported Next's generated route types as unknown names because CI had never run `next typegen`.
- A pull request a human merged on GitHub has a ledger path: `in_review -> merged` with a `merge_sha` evidence
  row and a human actor (`sdlc-graph evidence add <slug> --kind merge_sha --payload '{"sha": ...}'`, then
  `sdlc-graph node transition <slug> merged --actor @you --evidence-ids <id>`). Until now only `approved ->
  merged` existed, behind CI-signed evidence, so a dependent contract waited forever on a hand-merged one.
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
