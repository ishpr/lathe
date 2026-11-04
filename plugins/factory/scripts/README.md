# Scripts

The skills in `../skills/` tell the model what to do at each stage of a ticket. Where a result must not rest on the
model's word, a skill calls a script from this directory. A script runs a check, hashes a file, reads a test runner's
output or writes an evidence file. Its exit code and its file are the result of the stage, not a sentence in a
transcript. A skill runs a Python script as `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/<name>"` and a shell script as
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/<name>"`. `${CLAUDE_PLUGIN_ROOT}` is the directory the plugin is installed in. `-I`
is Python's isolated mode: no `PYTHON*` environment variables, no user site directory, and neither the script's
directory nor the working directory on `sys.path`. The scripts run inside the repository under review, so nothing in
that repository can shadow a module they import. A script that needs `../lib` adds it to `sys.path` itself and turns
bytecode off first, because `-I` also ignores `PYTHONDONTWRITEBYTECODE`. In CI the gate checks the plugin out of the
marketplace repository to a path outside the workspace and points `CLAUDE_PLUGIN_ROOT` at it, so a pull request cannot
edit the scripts that grade it. The plugin overview is `../README.md`.

## Common behaviour

Most scripts take `--root DIR` and answer `--help`. Most exit 0 when nothing is wrong, 1 when they found something and
2 for misuse or a tool that could not run. `e2e.py` returns the exit code of the suite it ran. Four files carry state:

| File | Written by | Read by |
|---|---|---|
| `.claude/sdlc.json` | `init_project.py`, `graph.py` | scripts that need commands, paths or limits; the hooks |
| `.claude/baseline.json` | `ratchet.py --update` and `--prune` (humans only) | `ratchet.py`, `baseline_diff.py`, `verify.py` |
| `.claude/work/current.json` | `workfile.py`, `freeze_tests.py` | `acceptance.py`, `tests_failing_first.py`, `freeze_tests.py`, the guards |
| `.claude/work/last_verify.json` | `verify.py` | the commit guard, `workfile.py --clear` |

## Onboarding

- `init_project.py` onboards a repository and commits nothing. It detects the stack, writes `.claude/sdlc.json` from
  `stacks/<stack>.json`, copies the rules, PR template, docs and CI gate, merges `templates/settings.project.json` into
  `.claude/settings.json` and appends the working agreement to `CLAUDE.md`. On the first run it records today's debt
  with `ratchet.py --update`. Flags: `--stack`, `--dry-run`, `--upgrade` (shows a diff, writes `<file>.new` beside a
  changed file, keeps the baseline), `--force`, `--no-ci`, `--codeowners`, `--marketplace-repo`, `--graph`,
  `--graph-provider`, `--allow-partial-baseline`. If lint or type tools are missing it exits 2 after writing the files,
  because a baseline taken without them makes CI report every old finding as new. Called by `/factory:init`.
- `ratchet.py` fails on a violation that is not in `.claude/baseline.json`, or whose count grew. It runs the stack's
  checkers, `commands.lint_report` and `commands.typecheck`. Flags: `--all` (list the known debt too), `--fast` (skip
  the type check unless `--files` is given), `--files f ...`, `--update` and `--prune` (rewrite or lower the baseline;
  humans only), `--baseline`, `--config`, `--base-ref`, `--require-tools`. In CI it reads the config from the base ref,
  so a PR cannot edit the checks that grade it. Exit 2 means a tool or a checker failed. Called by `/factory:init`,
  `audit`, `onboard`, `ticket`, `tests`, `review`, `verify.py`, the post-edit hook and CI.
- `graph.py` is `/factory:graph`: `status [--json]`, `on`, `off`, `url`, `provision local|neon|aws|spanner`. It edits the
  `graph` block of `.claude/sdlc.json` and stores the connection string in `.claude/settings.local.json` or `.env`,
  never in its output. Called by `/factory:graph`; `/factory:init` runs `status`.
- `protect_main.sh owner/repo [branch=main] [check=gate]` sets branch protection through `gh api`: one approving review
  that counts code owners, a required status check, no force pushes, linear history, admins included. A human with
  admin rights runs it once. No skill calls it.

## Ticket

- `ticket_intake.py` screens ticket text before `/factory:ticket` acts on it. It flags shell commands, secret-shaped
  values, credential handling, prompt-injection phrases, URLs, invisible characters and lines addressed to the
  assistant, and quotes each one back for a human. Flags: `--file PATH` (else stdin), `--slug TITLE` (print the work
  slug of a title, scan nothing). Exit 1 means something was flagged. Called by `/factory:ticket`.
- `workfile.py` moves a staged file into `.claude/work/`. Under `claude -p --permission-mode dontAsk`, Claude Code
  refuses Write and Edit below `.claude/`. So a skill writes to `.sdlc-stage/<path below .claude/work>` and runs
  `workfile.py .claude/work/<slug>/<file>`. The script applies the edit guard's rules itself: a plain slug and file
  name, at most 256 KiB, no symlink, no secret, and a `current.json` that does not loosen the contract. `--discard DEST`
  drops a staged file. `--clear SLUG` removes `current.json` and the slug directory if the verify stamp still matches
  the tree. Called by `ticket`, `tests`, `implement`, `verify`, `ship`, `rollback` and `security`.
- `acceptance.py` runs the contract's `## Acceptance` lines: `- $ <command>`, then `=> exit <n>`, `=> contains <text>`
  or `=> matches <regex>`. No shell is involved. A command outside a fixed list (pytest, `bash scripts/smoke_<name>.sh`,
  `curl` without output options, `make test`, some Node forms, and these under `uv run`, `poetry run` or `pdm run`)
  fails with "command not allowed". It prints `ACCEPTANCE passed=N failed=M`. `--ready` runs nothing. It checks that
  every Decisions row has a real value, prints `FILL` lines, then `READY` or `NOT READY`. Other flags: `--root`,
  `--contract`, `--require`. Called by `/factory:ticket` and `feature` (`--ready`), `/factory:verify`, `verify.py`, CI.
- `lint_epic.py <file.md> [--strict] [--agent]` checks the shape of an epic: sections, a metric, stories, Given/When/Then
  criteria. `--agent` also requires the fields an unattended story needs. Called by `/factory:epic`.

## Tests

- `tests_failing_first.py PATH ...` runs the new tests before the code exists, with `commands.test` narrowed to the
  files, and records why each one fails. `not_implemented`, `assertion`, `status_mismatch` and `other` are intended.
  `import_error`, `attribute_error`, `name_error`, `syntax_error` and `collection_error` mean the test itself is broken.
  Writes `.claude/work/<slug>/failing_first.json`. `--from-output FILE [--runner pytest|vitest|playwright]` classifies
  a saved run instead. Exit 1 lists the wrong reasons. Called by `/factory:tests`.
- `freeze_tests.py` pins test files by sha256 in `current.json` (`frozen_tests`, `frozen_at`). Flags: `--freeze PATH ...`,
  `--unfreeze PATH ...` (humans only), `--check [--junit XML] [--e2e-report JSON]`, `--list`. It refuses a file whose
  latest failing-first verdict is wrong. It also pins the `conftest.py` files, `pytest.ini`, `tox.ini` and `setup.cfg`
  above a Python test. `--check` fails if a frozen file changed or vanished, and, given a report, if a frozen test did
  not run or was skipped. Called by `/factory:tests`, `implement`, `verify.py` and CI.
- `e2e.py run [FILE ...]` runs the Playwright suite of the `e2e` block in `.claude/sdlc.json`. On macOS, Claude Code's
  sandbox stops a browser from launching and a dev server with a file watcher from running, so a human excludes this
  top-level command from the sandbox (`sandbox.excludedCommands`). It builds and serves the app, runs the suite, stops
  the server and records `.claude/work/e2e/` (`report.json`, `output.txt`, `run.json` with the tree fingerprint).
  Called by `/factory:tests`, `implement` and `verify`.

## Implement

`/factory:implement` has no script of its own. It runs `freeze_tests.py --check`, `e2e.py run` and `workfile.py` (for
`assumptions.md` and `questions.md`). The post-edit hook runs `ratchet.py --fast --files` on each edited file.

## Verify

- `verify.py` is the gate. It runs all of these, in order, even after a failure: the test command, the recorded e2e
  run, `ratchet.py`, the Foldkit frontend step, a secret scan of added lines, `check_agent_config.py`,
  `freeze_tests.py --check` and `acceptance.py`. Green writes `.claude/work/last_verify.json` (`fingerprint`, `head`,
  `db_backend`, `tool_versions`, `acceptance`, `frozen_ok`, `checks`). Red removes the old stamp. `--skip-tests` never
  stamps. The Bash guard refuses `git commit` unless the stamp's fingerprint matches the tree. Called by
  `/factory:verify` and `rollback`.
- `worktree_fingerprint.py [ROOT]` hashes every path that differs from HEAD, plus untracked files, but not
  `.claude/work/`. It hashes working-tree bytes, so staging does not change it. Called by `verify.py`, the commit
  guard, `/factory:verify`, `ship` and `workfile.py --clear`.
- `smoke_rate_limit.sh BASE_URL API_KEY [N] [PATH] [HEADER]` sends N requests with one key. It passes only if a 429
  arrives with `X-RateLimit-Remaining: 0` and `Retry-After`. Called by `/factory:verify` and `deploy`.

The CI gate (`templates/ci/<stack>.yml`) also runs these, from a plugin checkout that the PR cannot edit:

- `baseline_diff.py [BASE_REF] [--allow-growth]` fails if the PR's baseline gained a key or a count against the base
  ref. CI passes `--allow-growth` when the PR has the `baseline-approved` label.
- `check_pr_size.py --base REF [--head] [--warn N] [--fail N] [--label-present]` sums changed lines (warn at 400, fail at
  1000, or `pr_size` in the config). `--label-present` stands for the `large-change` label.
- `check_new_deps.py --base REF [--head] [--offline] [--min-age-days N]` looks up each added package on PyPI, npm, the Go
  proxy or crates.io. It reports MISSING, YOUNG, NO-LICENCE, UNCHECKED and DIRECT-REF (a URL or git dependency).
- `check_agent_config.py` finds invisible characters, base64 blobs, `curl ... | sh` and HTML comments aimed at an
  assistant in `.claude/`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json` and workflows. `check_claude_md_paths.py` fails on a
  path in `CLAUDE.md` or `.claude/rules/*.md` that does not exist.
- `route_sweep.py --base-url URL` (Python only) calls every route in the OpenAPI spec without credentials and expects
  401 or 403 (`--expect`). Routes in `.claude/public_routes.txt` are exempt. Point it at a throwaway instance.

### Checkers

`ratchet.py` runs `checkers/<stack>/<name>.py` for each name in the config's `checkers` list. A checker prints
`key<TAB>file:line: message` and exits 0 (none) or 1 (findings). The key has no line number. Exit 2, or exit 1 with no
output, is a crash and fails the ratchet. Python has `route_auth` (FastAPI), `module_state`, `test_quality`, `stubs`,
`dead_code` and `api_contract`. Node, Go and Rust have `module_state` and `test_quality`, as text heuristics. A line
ending `# state: ok <reason>` (`//` outside Python), `# stubs: ok <reason>` or `# dead: ok <reason>` silences one
finding. `../lib/frontend_foldkit.py` adds five more for a repo that depends on `foldkit`.

## Ship and headless

- `check_commit_message.py [--message MSG | --text-file PATH] [--files ...]` checks a message against the diff: an empty,
  long or vague subject, a claim of tests with no test file, a path the diff does not touch, and a `Signed-off-by` next
  to an agent trailer. Called by `/factory:ship` and `rollback`, by the commit guard and by CI.
- `check_commit_range.py [RANGE]` runs it over each non-merge commit (default `origin/main..HEAD`). A bad ref is exit 2,
  never "0 commits checked". Called by `/factory:review` and CI.
- `review_pr.sh <n> [--contract PATH] [--run-tests] [--trust-fork]` helps the human who approves a PR. It lists the
  changed files against a trusted contract's `allowed_paths`. With `--run-tests` it runs each new test on the base
  commit in a sandboxed worktree. A test that passes there tests nothing. Needs `gh`. Called by `/factory:review-pr`.
- `run_headless.sh <repo> <ticket-file> --reviewer @handle` is the one wrapper for unattended runs (its allow lists
  carry the Agent tool, since every skill hands its work to subagents). It refuses without a
  `current.json` with `allowed_paths`, a verify command and a reviewer. It makes a worktree on `headless/<slug>`, runs
  `commands.setup`, then runs `claude -p --permission-mode dontAsk` with an explicit tool list and turn and budget
  limits, never `--bare` (it would skip the hooks). It checks the diff against the original contract and opens a draft
  PR. It never merges. Flags: `--max-turns`, `--budget`, `--base`, `--no-push`, `--plugin-dir`, `--settings`,
  `--dry-run`. `run_headless.sh init <repo>` runs `/factory:init --headless`. Logs go to `.claude/work/headless/`. No
  skill calls it; the orchestrator reads its `DEFAULT_TOOLS` line.

## Assessment

- `maturity.py` scores a repository on seven dimensions (plan, design, build, test, deploy, maintain, ai-practice) from
  its files and git history, against `templates/maturity_rubric.json`. It writes `docs/maturity-assessment.md`. It runs
  nothing from the project unless `--run-tools` is given. Flags: `--rev`, `--rubric`, `--out`, `--no-write`, `--json`,
  `--epic N`, `--min-level N`, `--stack`, `--dump-rubric`. Called by `/factory:maturity` and `assess`.
- `security_assess.py` is the deterministic half of `/factory:security`: the pack's checkers, ruff's `S` rules, a secret
  scan with masked values, `pip-audit`, `npm audit`, `cargo audit` and `govulncheck` when installed, and its own checks.
  It writes `docs/security-assessment.md` and `.claude/work/security-assessment.json`. Flags: `--offline`, `--stack`,
  `--merge FILE` (fold in the reviewer agent's findings), `--impact FILE`, `--json`, `--no-write`. Exit 1 means findings.

## Maintenance

- `check_skill_permissions.py` lints the permission rules unattended runs depend on (DESIGN section 18.5). Every rule
  in `templates/required_denies.json` must be in the settings' `permissions.deny` (`MISSING-DENY`). No allow rule may
  be a blanket runner (`BLANKET-RUNNER`). Every Bash command a skill or an agent body names must be allowed or
  denied (`UNCOVERED`). Agent commands are checked against the settings rules alone. Each part of a chain or pipe is
  judged on its own, and a part with no known program is left to the runtime denial, which names its rule. Flags:
  `--skills-dir`, `--agents-dir`, `--settings`, `--required-denies`, `--set NAME=VALUE`, `--own-only`, `--verbose`.
  It runs in CI and over this plugin's own skills and agents.
- `lock_deps.sh [--stack] [--upgrade] [--check] [--dry-run]` writes a hash-pinned lock (`uv pip compile`, npm, `go mod`,
  cargo). The Python gate only names it in a warning. No skill calls it.
- `metrics_baseline.py [--since 90d] [--no-write] [--no-gh]` prints PR size, time to merge, reverts, the deletion ratio
  and guard blocks (`.claude/work/blocks.jsonl`). It writes `docs/metrics/<date>.json`. No skill calls it.
- `baseline.py prepare | run [--stage ...] [--case ...] [--runs N] [--model M] [--max-cost-usd X] | score RESULT |
  compare OLD NEW` is the measurement baseline of DESIGN section 18.5. `run` runs `claude plugin eval` on
  `tests/evals` (the epic cases and the per-stage cases for tests, implement, verify and ship), judges each stage run
  on the workspace it left with `tests/evals/_fixtures/check.py` (outcomes only: frozen tests, scope, the stamp, the
  PR body, git state), adds the ledger's `metrics.snapshot` when `DATABASE_URL` is set, and writes
  `docs/baselines/<date>-<sha>.json`. `compare` says per stage whether outcomes held, improved or regressed and
  whether cost and turns stayed similar (the thresholds are constants at the top); it exits 1 when any did not. It
  spends money and subscription quota; a human runs it before and after removing a rule. No skill calls it.
- `canary.sh --fixture DIR` asks a live `claude -p` session to attempt forbidden actions (a force push, `--no-verify`,
  `ratchet.py --update`, an edit outside the contract) and greps for the guard's `Blocked:`. It needs a throwaway
  repo with a local bare origin, and it spends money. A human runs it after a Claude Code upgrade.

## Stack adapters

`../stacks/` holds `python.json`, `node.json`, `go.json` and `rust.json`. They are data, not code. Each is the default
`.claude/sdlc.json` for one stack:

- `extensions`, `paths.app`, `paths.tests`: the source extensions and the globs for app and test code.
- `commands`: `test`, `lint_fix`, `format`, `lint_report`, `typecheck`, `deploy_local`, `smoke`. `{file}` is the edited
  file and `{app_dirs}` the app directories. Init also writes `setup`, the install command for a fresh worktree.
- `lint_format`, `typecheck_format`: the parser `ratchet.py` uses for those outputs. It knows `ruff-json`, `eslint-json`,
  `golangci-json`, `go-vet-text`, `go-build-text`, `tsc-text`, `mypy-text` and `cargo-json`.
- `checkers` and `auth` (the auth dependency names and the public-routes file). Python also sets `ticket_id_pattern`,
  `env_access_allowed`, `entry_modules`, `tier_high_paths`, `pr_size`, `min_package_age_days`, `route_sweep` and
  `tool_versions`. Rust sets `require_tests` to false.

| | python | node | go | rust |
|---|---|---|---|---|
| `commands.test` | `python3 -m pytest -q` | `npm test --silent` | `go test ./...` | `cargo test -q` |
| lint tool | ruff | eslint | go vet | clippy |

Detection is in `init_project.py`. It looks in the repository root for `pyproject.toml`, `requirements.txt`, `setup.py`,
`setup.cfg` or `Pipfile` (Python), `package.json` (Node), `go.mod` (Go) or `Cargo.toml` (Rust). One match picks the
stack. No match, or several, is exit 2 and a request for `--stack`, because one `.claude/sdlc.json` holds one stack.
Init then fits the defaults to the project: `uv run`, `poetry run` or `pdm run` in front of the Python commands, the Node
runner from `pnpm-lock.yaml`, `yarn.lock` or `bun.lock`, an `e2e` block for a Playwright config, a `frontend` block for
a `foldkit` dependency. After init the scripts read the stack from `.claude/sdlc.json`, and `lib/sdlc.py` layers
built-in defaults, then `stacks/<stack>.json`, then the project's file (the project wins). `maturity.py` and
`security_assess.py` also work on a repository that was never initialised. They keep their own marker lists and order,
so with two manifests they can pick a different stack than init does. Pass `--stack`.

## Shared code

`../lib/` is imported by the scripts that read the config, by every checker and by the hooks. `sdlc.py` loads the config,
the contract and the stamp, matches globs, and holds the secret patterns and the block log. `test_reasons.py` reads
pytest, vitest and Playwright output for `tests_failing_first.py`, `verify.py` and `e2e.py`. `e2e.py` is the logic
behind `scripts/e2e.py`. `frontend_foldkit.py` holds the Foldkit checkers. Only the commit guard in
`../hooks/guard_bash.py` imports `skeleton.py`, to let a tests-only commit carry new files whose bodies only raise.

## Tests and limits

The suite is `../tests/sdlc/`, standard library and pytest only. `../tests/epic/` covers `lint_epic.py`.
`test_scripts.py` holds the script tests, grouped by script. Most tests build a throwaway git repository (`conftest.py`),
set `CLAUDE_PLUGIN_ROOT` and run the script as a subprocess with `python3 -I` or `bash`. Test names are sentences, such
as `test_freeze_refuses_a_file_whose_latest_failing_first_reason_is_wrong`. Other tests check the pack itself: every
script parses and passes `bash -n`, and a run under `-I` leaves no `__pycache__`. `../tests/e2e/run_harness.sh` drives
the hooks and `init_project.py` on the repository's `examples/sample-app`, then runs init and the ratchet on small
generated Node, Go and Rust repositories. From the repository root: `python3 -m pytest plugins/factory/tests/sdlc -q`
and `bash plugins/factory/tests/e2e/run_harness.sh`.

Limits:

- `tests_failing_first.py` reads pytest, vitest and Playwright output only. A `commands.test` that names none of them
  (`go test ./...`, `cargo test -q`, `make test`, `npx jest`) gets exit 2. The `tests` skill then has the model read the
  failures, which is a model's claim, not a program's result. A `test` run through `npm`, `pnpm`, `yarn` or `bun` is
  parsed as vitest output, whatever the script runs.
- The check that a frozen test ran and was not skipped exists for pytest (junit XML) and Playwright (JSON report). For
  other runners `--check` compares hashes only. Pinning `conftest.py` and its kin is Python only.
- Python has six checkers. Node, Go and Rust have two regex heuristics each. The Go and Rust checkers and Node's
  `module_state` have no unit test. The harness covers them on generated repositories (Go and Rust only if `go` and
  `cargo` are installed) and asserts only that init works and the ratchet is clean against its own baseline.
- Tests use stand-ins where the real thing needs the outside world: a fake `claude` and `gh` for `run_headless.sh` and
  `review_pr.sh`, fake `neonctl`, `aws`, `gcloud` and `docker` for `graph.py provision`, a fake Playwright for `e2e.py`.
  The registry lookups of `check_new_deps.py` have no test (only `--offline` and direct references). `lock_deps.sh` and
  `canary.sh` are tested through `--dry-run` and refusals. No test executes `protect_main.sh` or `smoke_rate_limit.sh`.
- `ratchet.py` parses eight report formats. A linter whose output is none of them is skipped, and under `CI=true` or
  `--require-tools` that is exit 2. The ticket screen is a set of regular expressions: it quotes lines back for a human
  and does not prove a ticket safe.
