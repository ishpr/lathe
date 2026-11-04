# Permission modes and the allow list

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/factory/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

## Modes

| Mode | Set with | Use for |
|---|---|---|
| `default` (shown as Manual) | `permissions.defaultMode` | normal work; the starting point |
| `plan` | `--permission-mode plan` | `/factory:design`: read, ask, write the contract; no edits |
| `acceptEdits` | `--permission-mode acceptEdits` | `/factory:implement` inside an approved contract, on a branch |
| `dontAsk` | `--permission-mode dontAsk` | unattended runs only, with `--allowedTools` (unattended-runs.md) |
| `auto` | `--permission-mode auto` | not used here; does not take effect from project settings |
| `bypassPermissions` | `--dangerously-skip-permissions` | isolated container only, see below |

`deny` rules still apply in every mode, bypass included. In `dontAsk`, a call that would have
prompted is denied, so an `ask` rule becomes a deny there.

## Bypass

The vendor's guidance is to use `bypassPermissions` only in isolated environments such as
containers, VMs or dev containers without internet access. Our rule: never on a laptop, never
with credentials mounted, never in a privileged container. The managed settings set
`permissions.disableBypassPermissionsMode` to `"disable"`, which rejects the flag and ignores a
subagent's `permissionMode: bypassPermissions`. If a team needs bypass in a throwaway
container, the container's owner deploys a different managed file there, with a reason in the PR.

## The allow list: categories, not a script of commands

The settings allow commands by category, so a model can choose its own commands (`rg`, `find`, `jq`,
`pytest -k`, `git log -p`) without each one being denied and escalated in an unattended run:

| Category | Allowed |
|---|---|
| Read-only inspection | `cat`, `head`, `tail`, `wc`, `ls`, `rg`, `grep`, `find`, `sed -n`, `jq`, `sort`, `uniq` on a pipe (`uniq`, `uniq -c`, `-d`, `-u`, no file operands), `diff`, `file`, `stat`, `tree` |
| Read-only git | `git status`, `log`, `show`, `diff`, `blame`, `rev-parse`, `ls-files`, `grep`, `describe`, `stash list`, `stash show`, `remote -v`, and the listing forms of `git branch` (`-a`, `-r`, `-v`, `-vv`, `--merged`, `--show-current`) |
| The stack's own tools | the test, lint, type-check and format commands of `.claude/sdlc.json` (from the stack's `stacks/<stack>.json`), plus the runner forms the pack knows (`uv run pytest *`, `pnpm exec eslint *` ...) |
| The flow | `Bash(git add *)`, `Bash(git commit *)`, `Bash(git push -u origin HEAD)`, `Bash(gh pr create *)`, the pack's scripts (`python3 -I <plugin>/scripts/*`) |

Never allowed: a blanket runner (`uv run *`, `npx *`, `make *`, `bash *`, `sh -c`, `python3 *`). A runner runs
anything, and `guard_bash` cannot see through it. `awk` is left out too, since its `system()` makes a read-only
rule impossible to write.

Chains and pipes are fine. Claude Code checks every part of `a && b` or `a | b` and runs the whole only when every
part is allowed, so `rg -n limit app | head -20` runs and `rg x | frob` is denied for `frob`. A redirection to a
file is checked against the Edit rules, and a `$(...)`, heredoc or loop may still be refused for its shape.

Ask, so a human sees each one: `Bash(docker compose up *)`, `Bash(docker compose down *)`, package
installs (`pip install`, `npm install`), and `WebFetch`. A bare `WebFetch` rule and
`WebFetch(domain:*)` behave differently, and the sandbox's domain allowlist does not limit
WebFetch at all, which is why it asks.

## Always denied

The deny rules are listed in the pack's `templates/required_denies.json`. Some are also in the managed floor. A deny
rule wins over every allow rule in every mode, and it applies to each part of a chain. The list covers:

- history and publishing: force push (every spelling), `--mirror`, `--prune`, `--delete`, `git reset --hard`,
  `--no-verify` and `commit -n`, `gh pr merge`, and the human-only ledger transitions (`merged`, `released`,
  `verified`, `rolled_back`, `merge_sha` evidence);
- the quality ratchet: `ratchet.py --update` and `--prune`, `freeze_tests.py --unfreeze`;
- credentials: reads of `.env` files (by name, so `.env.example` stays readable), `~/.ssh`, `~/.aws`, `~/.config/gh`,
  `~/.npmrc`, `~/.netrc`, `~/.docker`, `~/.git-credentials`. A Read deny also stops `cat`, `head`, `tail` and `sed`
  on those files;
- the write and run forms of the read-only categories: `find -delete`, `-exec`, `-execdir`, `-ok`, `-fprint*`,
  `-fls`; `sed -i`; `rg --pre` and `--hostname-bin`; `sort -o` and `--compress-program`; `jq` with `env`/`$ENV`;
  `tree -o` and `-R`; `file -C`; `git grep -O`; `git diff|log|show --output`. `guard_bash` also refuses a sed script
  that runs a command (`e`, `s///e`) and a `sed -n` that writes (`w`, `W`, `s///w`, in place).

`check_skill_permissions.py` fails CI when the settings lack any required deny rule or allow a blanket runner. It
also fails when a skill or an agent names a command that no rule allows or denies. `run_headless.sh` passes the
required denies to every unattended run as `--disallowedTools`, and the orchestrator merges them into every
executor's settings, so a repo whose settings predate a rule is still covered.

## When a command is denied anyway

An unattended run never waits on a prompt. A denied call ends the step as an escalation (a P1 decision) that names
the tool, the exact command and the rule that would have allowed it. For a chain, that is the first part no rule
allows. The escalation also says where to declare it: the contract's `## Capabilities` commands, or `.claude/sdlc.json`
`commands` when every step needs it. For a forbidden form, it says that a contract cannot pre-approve it. A human
adds the rule or refuses it, then answers `retry`. Denials of form (a redirection, heredoc or loop whose parts are
all allowed, or the commit guard's message rules) do not escalate: the session redoes the call in an allowed form.
See unattended-runs.md.

Syntax: write `Bash(git commit *)`, with a space before the star. The older `:*` form only works
at the end of a pattern. A Bash rule matches the command text after Claude Code splits compound
commands; it is not a boundary around the program, so `guard_bash` stays.

## Skills

A skill's `allowed-tools` pre-approves tools only for the turn that runs the skill. Give each
skill the least it needs: `/factory:design` needs reads and writes under `.claude/work`, `/factory:verify` needs
the test runner and `scripts/`, `/factory:ship` needs git and `gh pr create`. A skill's `allowed-tools`
is ignored from user and project files when managed settings set `allowManagedPermissionRulesOnly`.


## Human-owned changes: ask when interactive, deny when unattended

The baseline, `.claude/sdlc.json`, the settings files, the public routes file, whatever `paths.protected`
lists, `ratchet.py --update` and `freeze_tests.py --unfreeze` belong to a human. The edit and Bash guards
answer them differently by session kind:

- **Interactive** (permission mode `default`, `acceptEdits` or `plan`): the guard returns
  `permissionDecision: "ask"` with the exact change, Claude Code shows it, and one approval applies it.
  The guards never answer `allow`.
- **Unattended** (`--permission-mode dontAsk` or `bypassPermissions`, a hook event with no permission mode,
  `claude -p`/SDK sessions via `CLAUDE_CODE_ENTRYPOINT=sdk-*`, or `CLAUDE_HEADLESS=1`): deny, log to
  `blocks.jsonl`, and escalate as before. Unknown counts as unattended.

Claude Code's `!` prefix runs inside the same OS sandbox, so it cannot do what the sandbox blocks (the
baseline is `denyWrite`; a checkout writes `.mcp.json`). For those, `sandbox.excludedCommands` lists the exact
prefixes `ratchet.py --update`, `freeze_tests.py --unfreeze` and `git worktree add`, nothing broader: after the
human approves the ask, the command runs outside the sandbox. Use `! <command>` only for commands the sandbox
does not block.
