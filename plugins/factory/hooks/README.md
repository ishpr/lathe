# Factory hooks

Prose asks; hooks refuse. A skill is a prompt, so it is advisory: an agent can skip a step or argue past a
rule. A hook is a script that Claude Code runs on a matching tool call, and a `PreToolUse` hook can refuse the
call before it runs. These hooks stop what prose cannot: an edit outside the contract, an edit to a frozen
test, a commit of a tree nobody verified, a secret in a file, a push to `main`.

They check against the contract. `/factory:ticket` writes it twice: `.claude/work/<slug>/contract.md` for
people and skills, and `.claude/work/current.json` for the hooks. The hooks read the JSON, never the markdown.
The keys they act on are `slug`, `tier`, `allowed_paths`, `require_tests`, `frozen_tests` (path to sha256) and
`frozen_at`. `/factory:tests` adds the frozen tests and `/factory:verify` writes the stamp,
`.claude/work/last_verify.json`. The hooks stop the agent from loosening `current.json` or writing the stamp itself.

| Script | Event and tools | Can refuse | Timeout (s) |
|---|---|---|---|
| `session_start.py`, `session_prime.sh` | `SessionStart` | no, they orient | 15, 20 |
| `guard_edit.py` | `PreToolUse`: Edit, Write, MultiEdit | yes | 10 |
| `guard_bash.py` | `PreToolUse`: Bash | yes | 20 |
| `guard_agent_spawn.py`, `record_agent_run.py` | `PreToolUse`, `PostToolUse`: Agent | the first can | 30, 30 |
| `post_edit.py` | `PostToolUse`: Edit, Write, MultiEdit | feedback after the edit | 90 |
| `stop_check.py` | `Stop` | one nudge | 10 |

A refusal is exit code 2 with the reason on stderr, which Claude Code returns to the model. Messages from
`guard_edit.py` and `guard_bash.py` start with `Blocked: ` and, when the project has a `.claude` directory, add a JSON
line to `.claude/work/blocks.jsonl` (`ts`, `hook`, `tool`, `rule`, `detail`). The tables use the log's rule names.
`templates/docs/day-one.md` lists causes and fixes. If a refusal is wrong, do not work around it: file the
false-positive issue (`templates/github/ISSUE_TEMPLATE/hook-false-positive.yml`).

## session_start.py and session_prime.sh

`session_start.py` runs on `SessionStart` for `startup|resume|clear|compact` and never fails. It prints the branch, the stack,
`Active contract: <slug> (tier <tier>)` with its `allowed_paths`, the frozen tests and
`Verify stamp: current (commit allowed).` or `Verify stamp: stale`. It also says what would otherwise fail quietly:
`MISSING TOOL: <name>` (install it, restart), `CONFIG INVALID: .claude/sdlc.json` (a broken config blocks every edit
and shell command until a human repairs it), `This repo is not initialised for the working agreement: run /factory:init`
and `You are on <branch>: nothing is edited here.` A second `SessionStart` entry prints the contract when `current.json`
exists (`sdlc-graph prime` through `uv`, else `session_prime.sh`).

## guard_edit.py: before Edit, Write and MultiEdit

| Rule | Message starts | Cause and fix |
|---|---|---|
| `secret` | `this write contains what looks like a` | A known key format, a private key, a URL with a password, a hard-coded credential or a `secret_patterns_extra` match. Read it from the environment. |
| `protected-file` | `<path> is maintained by humans` | Among others the config, baseline, stamp, block log, `.env*` (not `.env.example`), `.claude/hooks`, `rules`, `agents`, `skills`, `settings*.json`, `CLAUDE.md`, `.mcp.json`, `.git/config` and `paths.protected`. Say what you need and stop. |
| `protected-branch` | `you are on <branch>. Create a branch first` | A branch in `paths.protected_branches` (default `main`, `master`). Run `git switch -c <type>/<slug>`. |
| `frozen-test`, `test-config` | `<path> is frozen by /tests`; `this edit drops or changes frozen_tests`; `<path>: test configuration is frozen with the tests` | A frozen test, its `frozen_tests` entry, or while one is frozen `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `.coveragerc` and the pytest and coverage tables of `pyproject.toml`. A human runs `freeze_tests.py --unfreeze <path>`. |
| `contract-lift`, `contract-invalid` | `this edit loosens the contract`; `this edit would leave .claude/work/current.json unreadable` | An edit of `current.json` that changes `tier`, `slug` or `frozen_at`, widens `allowed_paths`, sets `require_tests` false or breaks the JSON. Say what the ticket needs and stop. |
| `scope`, `tier` | `<path> is outside the contract's allowed_paths`; `<path>: this path forces tier high` | Ask a human to widen the contract. A path in `tier_high_paths` needs tier high: re-run `/factory:ticket`. |

Also refused: `outside-root` (a path outside the project while a contract is active), `config-invalid`,
`contract-unreadable`, `input` and `crash`. Exempt: `.claude/work/**` and `.sdlc-stage/**` skip the scope and tier
rules, but not the protected files; the `hard-coded credential` and `URL with a password` patterns are skipped in
tests, `.md` files and env templates, and a placeholder passes. With no `current.json` there is no scope or tier
check: the skill asks for a contract, this hook does not.

## guard_bash.py: before Bash

It reads the command text, unwrapping `sudo`, `env`, `xargs`, `uv run`, `npx`, `bash -c`, `eval`, `| sh` and
`find -exec`. A word inside a quoted message, a comment or a heredoc is not a command.

| Rule | Message starts | Cause and fix |
|---|---|---|
| `force-push`, `push-protected`, `destructive-git`, `no-verify` | `no force pushes.`; `no pushes to a protected branch; open a PR.`; `destructive git (`; `git hooks are not optional here` | `--force`, `-f`, a push to or from `main`, `reset --hard`, `clean -f`, `branch -D`, `checkout -f`, `commit -n`, `--no-verify`. Push a new commit, open a PR, or ask a human. |
| `hooks-path`, `git-alias`, `pr-merge`, `pr-approve` | `git hook paths are not overridden from a session.`; `PRs are merged by a reviewer in GitHub, never from this session.`; `a session does not approve PRs.` | `core.hooksPath`, `GIT_CONFIG_KEY_*`, aliases that push or force, `gh pr merge`, `gh pr review --approve`, also through `gh api`. A reviewer merges in GitHub. |
| `no-stamp`, `stale-stamp` | `no verify stamp.`; `the tree changed since the last verify` | The commit gate, below. Run `/factory:verify`. |
| `commit-message` | `commit message problems:` | Lists keys such as `commit\|subject-vague`, `commit\|claims-tests`, `commit\|claims-path:<p>` and `commit\|body-missing`. Describe the diff. |
| `frozen-test`, `protected-file`, `contract-lift`, `shell-write` | `<path> is frozen by /tests`; `<path> differs from the version /tests froze`; `<path> is maintained by humans; changing it through the shell (` | A shell write (`>`, `tee`, `cp`, `mv`, `sed -i`, `rm`, `git checkout`, `git diff --output=`, inline code) to what `guard_edit.py` protects (`contract-lift`: `current.json`; `shell-write`: pytest settings while tests are frozen), or a frozen test changed by any means. |
| `humans-only` | `` `ratchet --update` is run by a human only. `` | `ratchet.py --update` or `--prune`, `freeze_tests.py --unfreeze`, a write to `.claude/baseline.json`, however spelled. Reading the baseline is fine. Say what changed and why; stop. |
| `data-store`, `credential-read` | `dropping or truncating a database or table is for a human.`; `reading credential files from a session is blocked` | `DROP`, `TRUNCATE`, `DELETE` with no `WHERE`, `alembic downgrade`, `FLUSHALL`, `compose down -v`; `~/.ssh`, `~/.aws/credentials`, `.env*` (not `.env.example`), `gh auth token`, a bare `env`. Each message names the safe way. |
| `rm-rf`, `nesting`, `input`, `crash` | `rm -r of the filesystem root, the home directory, the repository`; `the command nests shells too deeply to check` | Name the path; run the inner command directly. Unreadable input and a crash refuse too. |

### The commit gate

`git commit` is judged on the paths it will commit (`-a`, `-o`, `-i` and a chained `git add` change them), and
`merge`, `cherry-pick`, `revert`, `am` and `rebase` need the stamp too. The stamp is `.claude/work/last_verify.json`,
written by `/factory:verify` when the gate is green. It holds `ok` and a `fingerprint`: a sha256 over every path that
differs from `HEAD` plus untracked files that are not ignored, from working-tree bytes, ignoring `.claude/work/`.
Staging does not change it; one edited byte does. No stamp gives `no verify stamp. Run /factory:verify, fix what it
reports, then commit.` An old one gives `the tree changed since the last verify` and both fingerprints. Exempt:

- A commit whose paths are all test paths (`paths.tests` and the usual names) or under `.claude/work/`. The failing
  tests are committed before the code, so test-first shows in git.
- Skeletons in a tests commit. `lib/skeleton.py` accepts a new file (not at `HEAD`) that only declares: Python
  functions whose body is `raise NotImplementedError`; TypeScript and JavaScript functions whose body is empty or
  `throw new Error("not implemented")`; no other logic. The guard judges what git will commit (the index, or the
  working tree for `-a`). A file that fails keeps the gate, and `no-stamp` adds `not a skeleton here: <file>
  (<reason>)`: take the logic out, or commit the tests alone.
- A commit with no changed paths needs no stamp. `--amend` always does.

## guard_agent_spawn.py and record_agent_run.py

`guard_agent_spawn.py` never rewrites an `Agent` spawn's `model`: the policy in `sdlc_graph/ledger/routing_policy.json` is a default, and the hook records it. The
stage comes from a tag `[sdlc stage=<stage> node=<slug>]` in the `description` (never the prompt, which can carry
ticket text), then `SDLC_STAGE`, the `subagent_type` or `current.json`, else `implement`. A stage names a tier and a
tier a model: `fable`, `opus`, `sonnet` or `haiku`. The requested model, that default and whether the request was an
override (`meta.default`, `meta.override` on the `run` row) are recorded; a spawn with no model keeps Claude Code's own
resolution. A spawn with no stage anywhere is `implement`. The ceilings are exit 2: a model outside the `availableModels`
of the managed, user or project settings (none set, no ceiling), and the Fable budget. Fable spawns draw on `fable_budget_per_epic` (3).
When it is spent the spawn is refused: `Fable budget exhausted for stage '<stage>': no planner spawns left.` A human
resolves a `fable_budget_increase` decision, or you use the orchestrator tier. `record_agent_run.py` writes a ledger
`run` row after the spawn, appends the hand-off (agent, brief title, outcome) to `.claude/work/<slug>/handoffs.md`
(`.claude/work/handoffs.md` without a contract), copies the `blocks.jsonl` lines logged since the spawn (its `at`
time in the guard's note) onto the run as `guard_blocks`, and never blocks.

## post_edit.py and stop_check.py

`post_edit.py` runs after Edit, Write and MultiEdit when `.claude/sdlc.json` exists and the file ends with one of the
stack's `extensions`. It runs `commands.lint_fix` and `commands.format` (those that hold `{file}`), then
`scripts/ratchet.py` with `--fast` and `--files <file>` against `.claude/baseline.json`. A new violation, or a count
above the baseline, exits 2 with `New violations in <file> (vs .claude/baseline.json):` and the list. The edit has
happened; the model gets the list as feedback. Fix the code; only a human updates the baseline. A missing tool or a
crashing command prints a `post-edit:` line and never blocks.

`stop_check.py` runs on `Stop`. When app code changed and no test did, it exits 2 once with `app code changed (` and
up to five paths. Add the test, or write in `.claude/work/<slug>/assumptions.md` why none is needed. The marker
`.claude/work/.stop_nudged` keeps it to one nudge. It is quiet with no contract, with `require_tests` false, and with
`frozen_tests` set: a test-first `/factory:implement` changes no test.

## Wiring, subagents and the off switch

- **Registration.** Claude Code loads `hooks/hooks.json`; `.claude-plugin/plugin.json` has no hooks key. The Python
  hooks run as `python3 -I "${CLAUDE_PLUGIN_ROOT}"/hooks/<name>.py`. `-I` is isolated mode: it ignores `PYTHON*`
  variables and puts neither the script directory nor the working directory on `sys.path`, so nothing in the project
  can shadow what a hook imports. `hooks.example.json` is not the registration; `sdlc-graph hooks settings` prints it.
- **Bytecode.** The five hooks that import `lib/sdlc.py` or `lib/skeleton.py` set `sys.dont_write_bytecode = True`
  before they add `lib` to `sys.path`, because `-I` also ignores `PYTHONDONTWRITEBYTECODE`; a test checks the order.
  That keeps `__pycache__` out of the plugin. `record_agent_run.py` sets nothing and loads `guard_agent_spawn.py` by
  path, so it can leave one here; `.gitignore` covers it.
- **Failing closed.** The three `PreToolUse` entries set `"onFailure": "block"`, honoured from Claude Code 2.1.295; on
  an older one a guard that cannot start lets the call through. The guards also fail closed in their own code. They
  need `git` and `python3` 3.11 or later (`guard_edit.py` imports `tomllib`) and root the project at `CLAUDE_PROJECT_DIR`.
- **Settings.** `/factory:init` merges `templates/settings.project.json` into `.claude/settings.json`: no hooks, but
  deny rules that back `guard_bash.py` (`Bash(git push --force *)`, `Bash(gh pr merge *)`) and `Read(...)` rules for
  `.env*`, `~/.ssh` and `~/.aws`. No hook is registered on Read, so only these rules cover it.
- **Subagents.** The hooks match tool names, not agents, so every matching call in the session runs them, from the main
  session or a subagent. A `test-author` subagent meets the same edit guard and `allowed_paths`. The hooks do not read
  which agent is calling, so a rule for one agent stays in its prompt. No `SubagentStop` hook is registered.
- **Off switch.** `claude plugin disable factory@lathe` turns every hook off; from a shell, `claude plugin disable factory@lathe`.
  It is for demos and for debugging the plugin, not a boundary: anyone in the session can run it. A team that needs one
  delivers managed settings (`templates/docs/admin-setup.md`); its `permissions.deny` floor holds with the plugin off.

## Tests and limits

Run `python3 -m pytest plugins/factory/tests/sdlc/test_hooks.py -q` and `bash plugins/factory/tests/e2e/run_harness.sh`
from the repository root. `test_hooks.py` has about 150 tests. Most run a hook as `python3 -I` in a throwaway git
repository, with the JSON Claude Code sends. `BYPASS_DENY` lists spellings that must be refused and
`FALSE_POSITIVE_ALLOW` commands that must pass. `tests/graph/test_hooks.py` covers the `Agent` hooks. The harness
drives every guard on a copy of `examples/sample-app`. `scripts/canary.sh` starts a live `claude -p` session and
checks that each guarded action still ends in `Blocked:`, which unit tests cannot show.

What they cannot catch:

- Anything outside the session. A terminal, another editor and CI never run these hooks. They are a speed bump that
  says what to do instead; the sandbox, database roles, branch protection and CI are the boundary.
- A missing contract. Scope is enforced only once `current.json` exists.
- Commands it cannot read. `guard_bash.py` works on text: a path built at run time, a program that writes by itself or a
  tool with no rule gets through. NotebookEdit, MCP write tools and Read have no hook at all.
- Secrets with no known shape, and secrets written through the shell. `/factory:verify` scans added lines.
- Weak tests. A stamp says the gate was green for this tree, not that the tests are good.
- Setup gaps: the agent can delete `.claude/work/fable_budget.json` (the ledger is the enforcement), a Claude Code older
  than 2.1.295 does not honour `onFailure`, and Windows needs Git Bash or a `python3` alias.
