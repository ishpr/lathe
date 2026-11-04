# templates

What `/factory:init` copies or fills into a repository, and the rule behind it: the agreement a repository receives on day
one is versioned and reviewable, so onboarding is a pull request like any other. Init writes plain files and commits
nothing. A person reads the diff on a branch (the `/factory:init` skill first leaves a protected branch for
`chore/onboarding`), edits what does not fit and merges it through review. An upgrade never overwrites a file that differs:
it writes `<file>.new` beside it, and `.claude/KIT_VERSION` records the plugin version the repository took.

## What each template becomes

Entries are grouped by destination: what the file does, who edits it later, its placeholders (none unless named). "A human
edits it" means the edit guard refuses an agent (`ALWAYS_PROTECTED` in `lib/sdlc.py`). Other files are edited by pull request.
Scripts are not templates: nothing under `scripts/` is copied, and the gate checks them out of the marketplace repository.

### `.claude/`

- `.claude/sdlc.json`: the config the hooks and scripts read (`paths.app`, `paths.tests`, `paths.protected`, `commands`,
  `checkers`, `marketplace_repo`, `graph`). Not in `templates/`: init builds it from `stacks/<stack>.json`. A human edits it.
- `.claude/rules/sdlc-common.md` and `.claude/rules/sdlc-<stack>.md`, from `rules/common.md` and `rules/<stack>.md`: the
  rules for any change, then the language's. Only the stack file has a `paths:` list. A human edits them.
- `.claude/public_routes.txt`, from `public_routes.txt`, Python only: routes allowed without the auth dependency, one
  `METHOD PATH reason` per line. The `route_auth` checker and `route_sweep.py` read it. A human edits it.
- `.claude/work/README.md`, from `work-README.md`: what `.claude/work/` holds. Yours after the first write.
- `.claude/settings.json`, merged from `settings.project.json`: sandbox on, allow rules for the stack's commands and the
  graph tools, `ask` for installs and `WebFetch`, `deny` for force pushes, `gh pr merge`, `--no-verify`,
  `ratchet.py --update`, `freeze_tests.py --unfreeze` and `.env` and credential reads. A human edits it. Placeholders:
  `{test_command}`, `{lint_command}`, `{typecheck_command}`, `{format_command}`, `{install_command}`, `{package_domains}`,
  `{cleanup_days}`, `{plugin_root}`.
- `.claude/settings.local.json`, split off the same template: allow rules that embed this machine's plugin path, such as
  `Bash(python3 -I "{plugin_root}/scripts/*)`. Gitignored, so each machine gets its own when init runs there. A human edits it.

### `.github/`

- `.github/workflows/sdlc-gate.yml`, from `ci/python.yml`, `ci/node.yml`, `ci/go.yml` or `ci/rust.yml`: workflow
  `sdlc-gate`, job `gate`, the check `scripts/protect_main.sh` requires. It moves the factory's scripts out of the
  workspace, so a pull request cannot edit the checks that grade it. Steps include the ratchet, baseline growth, change
  size, new dependencies, tests, an audit, frozen tests, acceptance lines, a secret scan and commit messages. The Python
  gate adds a route sweep and the licence allowlist. Placeholders: `__MARKETPLACE_REPO__` and `__MARKETPLACE_REF__`; the
  Node gate adds `__NODE_PM_SETUP__`, `__NODE_CACHE__`, `__NODE_LOCKFILE__`, `__NODE_INSTALL__`, `__NODE_TEST__` and
  `__NODE_AUDIT__`.
- `.github/workflows/sdlc-claude-review.yml`, from `ci/claude-review.yml`: an advisory review with one sticky comment. It
  never approves, is not required, skips drafts and forks, and skips with a notice without an `ANTHROPIC_API_KEY` secret.
- `.github/pull_request_template.md`: the author's own words, evidence, an `AI-assisted` line and a reviewer checklist.
- `.github/ISSUE_TEMPLATE/agent-task.yml`: the form for a ticket an agent may pick up. It requires a runnable acceptance check.
- `.github/ISSUE_TEMPLATE/hook-false-positive.yml`: the form for a guard that blocked a legitimate action or missed a bad one.
- `.github/dependabot.yml`: weekly updates. Init keeps the stack's block (`pip`, `npm`, `gomod`, `cargo`) and `github-actions`.
- `.github/CODEOWNERS`, only with `--codeowners`: everything to the reviewers; auth, models, migrations and `alembic/**` add
  the security owner; `.mcp.json`, `.devcontainer/**`, compose files and `Dockerfile*` go to the security owner alone.
  Placeholders: `@your-org/reviewers` and `@your-org/security-owner`.

### `docs/`

Copied from `templates/docs/`, except the two files under the repository root. Init fills no placeholder in them. Three
hold `to be named` lines for a human: `pack-governance.md` (the owner), `runbook-agent-incident.md` (three roles) and
`licence-allowlist.txt` (the legal owner and an exception's approver).

- `admin-setup.md`: how an admin delivers managed settings, the floor nobody can remove locally.
- `cost.md`: which model for which job, limits, billing by channel, telemetry and measurement.
- `data-policy.md`: where code and prompts go, the settings that back the rules, and offboarding.
- `day-one.md`: each `Blocked:` message from the guards, its cause and its fix.
- `db-roles.sql`: roles `app_owner`, `app_rw` and `app_ro` for a throwaway development database, run once by hand.
- `frontend-contracts.md`: contracts for Foldkit frontends, and the five checkers added when `package.json` has `foldkit`.
- `licence-allowlist.txt`: `tier|name` lines that the Python gate's licence step reads, and `exception|` lines.
- `light-path-and-hotfix.md`: what `/factory:ticket --trivial` skips, and the hotfix route.
- `managed-settings.example.json`: example managed settings for an admin to edit and deliver. Nothing applies it.
- `mcp-policy.md`: untrusted input, and the register a new MCP server must enter.
- `pack-governance.md`: the owner, versions, the quarterly prune and the change flow.
- `permission-modes.md`: the permission modes, bypass, and the allow, ask and deny lists.
- `rollout.md`: the first month, from pilot to exit criteria.
- `runbook-agent-incident.md`: six steps for when an agent did something it should not have.
- `unattended-runs.md`: running `claude -p` only through `scripts/run_headless.sh`, and unattended init.
- `worktrees.md`: one session per worktree, `.worktreeinclude`, and separate data per worktree.

### Repository root

- `CLAUDE.md`, from `CLAUDE.working-agreement.md`: a block appended once between `<!-- sdlc:start -->` and
  `<!-- sdlc:end -->`: the stages, the non-negotiables, the untrusted-input rules and what to keep when a conversation is
  compacted. A human edits it. Placeholders: `{stack}` (the stack) and `{test_command}` (`commands.test`).
- `.mcp.json`, from `mcp.json`: `{"mcpServers": {}}`, empty on purpose. A human edits it; a new server is a reviewed pull request.
- `REVIEW.md`, from `docs/REVIEW.md`: what reviewers and the advisory review always flag, skip and report.
- `tests/QUARANTINE.md`, from `docs/tests/QUARANTINE.md`: flaky tests with owner and expiry. Yours; only reviewers check it.

Two templates are not copied. `contract.template.md` is the shape of a contract: `/factory:ticket` writes contracts in it and
the ledger parser reads them. `maturity_rubric.json` is the default rubric of `scripts/maturity.py`; `--rubric` takes a copy.

## How init fills and places them

Init detects the stack from a marker file such as `pyproject.toml`, `package.json`, `go.mod` or `Cargo.toml`. With none or
several it exits 2 and asks for `--stack`. `--dry-run` writes nothing.

- `fill_settings` fills the settings placeholders. The command ones hold the command prefix from the config, so
  `{test_command}` becomes `python3 -m pytest` and the rule `Bash(python3 -m pytest *)`. An array element or object entry
  whose placeholder has no value is dropped. `{cleanup_days}` comes from `--cleanup-days` (default 30, at least 1).
- `{plugin_root}` is the plugin's installed directory. Rules that embed it go to `.claude/settings.local.json`, because an
  absolute path cannot be committed; `scripts/run_headless.sh` merges both files. The path moves when the plugin updates:
  rerun init, which adds the new rules and leaves the old ones.
- `__MARKETPLACE_REPO__` and `__MARKETPLACE_REF__` come from `--marketplace-repo` (default `ishpr/lathe`) and
  `--marketplace-ref` (default `main`; pin a release tag or a SHA). `--no-ci` writes neither workflow.
- The Node placeholders follow the lockfile (`pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `bun.lock`, else npm).
  `__NODE_INSTALL__` is `commands.setup` when the project has one, and `__NODE_TEST__` is `commands.test`. After you edit
  `commands.setup`, `--upgrade` proposes the new install step in `sdlc-gate.yml.new`.
- `--codeowners '@a,@b'` needs two distinct handles, compared without case, and refuses placeholders such as `@owner`. The
  first is the reviewer, the second the security owner. A line that named only the reviewers gets every owner, since GitHub
  counts an approval from any one owner on a line.

When Claude Code's sandbox refuses a write to a settings file the session loaded at start, init does not fail. It writes
the merged content to `.sdlc-stage/settings.json` (or `.sdlc-stage/settings.local.json`), finishes its other steps and
prints `A human runs: cp .sdlc-stage/settings.json .claude/settings.json`. A human reads the staged file and runs the `cp`.
`.sdlc-stage/` is gitignored. Only the two settings files are staged.

What a rerun keeps. Init prints one line per file: `+` created, `~` merged, `!` a `.new` file written, `= kept`.

- A missing file is created. A file equal to the template is left alone. A file that differs is kept; `--upgrade` also
  prints a diff and writes `<file>.new`, and `--force` replaces it.
- `tests/QUARANTINE.md`, `.claude/public_routes.txt`, `.claude/work/README.md` and an existing `.mcp.json` are yours: never
  replaced, never given a `.new`, even with `--force`.
- `.claude/sdlc.json` is kept. `--upgrade` adds missing keys and changes no value; `--force` replaces it.
- Settings merge: objects merge, arrays union, an existing scalar wins. Only machine-specific rules leave `settings.json`.
- The `CLAUDE.md` block is never rewritten. If the agreement changed, `--upgrade` writes `CLAUDE.working-agreement.md.new`.
- A rerun with another `--marketplace-repo` fills it, and the ref, into a gate that is still the unedited template. An
  edited gate is never rewritten.
- `.gitignore` gets the lines it lacks from a fixed list: the work files, `.claude/settings.local.json` and `.sdlc-stage/`.
- The baseline is recorded once; `--upgrade` never runs `ratchet.py --update`. `.claude/KIT_VERSION` moves with `--upgrade`.

Init copies most templates by name. It walks `templates/docs/` and `templates/github/` and copies every file in them, so a
file added there ships to every repository. A file added elsewhere in `templates/`, this README included, is not copied.

## How they are tested, and the limits

The tests are in `plugins/factory/tests/sdlc/`; run `python3 -m pytest plugins/factory/tests/sdlc -q`.

- Fill and placement: `test_init_writes_a_node_gate_for_the_repositorys_package_manager` (pnpm, npm, yarn, bun, then a
  `.new` after `--upgrade`), `test_init_rerun_with_marketplace_repo_fills_the_placeholder_in_an_unedited_gate`,
  `test_init_puts_machine_specific_rules_in_settings_local_and_portable_ones_in_settings` and, for staging,
  `test_init_upgrade_stages_a_settings_file_the_sandbox_refuses_and_finishes_the_rest`.
- Content: `test_plugin_gate_templates_keep_the_plugin_out_of_the_workspace_and_pin_a_ref` (all four gates),
  `test_autonomy_settings_keep_every_deny_and_cover_the_plugin_root_form` and
  `test_a_freshly_initialised_repo_passes_the_claude_md_path_check`.
- `tests/e2e/run_harness.sh` runs init on `examples/sample-app` with `--codeowners '@alice,@bob'` and checks the gate file,
  the settings split, the `.gitignore` line and that every skill command is pre-approved.

The limits:

- The workflows are checked as text. No test parses them as YAML, lints them or runs one on GitHub. A Node gate once died
  in `setup-node` because it cached with npm in a pnpm repository; the Node placeholders exist because of it.
- Only the Python and Node gates are rendered through init in a test. The Go and Rust gates get string checks, and the
  harness initialises Go and Rust with `--no-ci`. The licence-step test skips when `pip-licenses` is missing.
- No test asserts the content of the issue templates, `dependabot.yml`, the PR template, `tests/QUARANTINE.md`,
  `.claude/work/README.md` or `sdlc-claude-review.yml`. Init writes them every run. Only the `--codeowners` handle check is
  tested (`test_init_owner_check_is_case_insensitive_and_placeholders_are_exact`), not the rendering. The `test_install_*`
  tests belong to an installer that is not in this repository and skip here.
- The staging test makes `settings.json` read-only. It does not run inside Claude Code's sandbox, and it skips as root.
- The docs are prose. `test_docs_do_not_cite_unverified_plugin_slash_commands` scans the docs pages for three banned
  strings and nothing else checks them. Where they quote vendor behaviour they say to confirm it; read those as open.
- A template can only ask for a control. CODEOWNERS gates a merge only when branch protection requires a code-owner review
  (`scripts/protect_main.sh`, run once by a human). The sandbox is not available on native Windows, and `failIfUnavailable`
  stops Claude Code there. Deny rules are a floor, not a boundary around a program.
