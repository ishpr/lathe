# Permission modes and the allow list

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/sdlc/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

## Modes

| Mode | Set with | Use for |
|---|---|---|
| `default` (shown as Manual) | `permissions.defaultMode` | normal work; the starting point |
| `plan` | `--permission-mode plan` | `/sdlc:ticket`: read, ask, write the contract; no edits |
| `acceptEdits` | `--permission-mode acceptEdits` | `/sdlc:implement` inside an approved contract, on a branch |
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

## The allow list (what `/sdlc:ship` and `/sdlc:deploy` actually run)

Allow, so the flow does not prompt at every step: `Bash(git commit *)`,
`Bash(git push -u origin HEAD)`, `Bash(gh pr create *)`, `Bash(git add *)`, `Bash(git status *)`,
`Bash(git diff *)`, `Bash(git log *)`, the stack's test, lint, type-check and format commands,
and `Bash(python3 -I scripts/*)`.

Ask, so a human sees each one: `Bash(docker compose up *)`, `Bash(docker compose down *)`, package
installs (`pip install`, `npm install`), and `WebFetch`. A bare `WebFetch` rule and
`WebFetch(domain:*)` behave differently, and the sandbox's domain allowlist does not limit
WebFetch at all, which is why it asks.

Deny (also in the managed floor): force push, push to main, `gh pr merge`, `--no-verify`,
`ratchet.py --update` and `--prune`, `freeze_tests.py --unfreeze`, reads of credential files.

Syntax: write `Bash(git commit *)`, with a space before the star. The older `:*` form only works
at the end of a pattern. A Bash rule matches the command text after Claude Code splits compound
commands; it is not a boundary around the program, so `guard_bash` stays.

## Skills

A skill's `allowed-tools` pre-approves tools only for the turn that runs the skill. Give each
skill the least it needs: `/sdlc:ticket` needs reads and writes under `.claude/work`, `/sdlc:verify` needs
the test runner and `scripts/`, `/sdlc:ship` needs git and `gh pr create`. A skill's `allowed-tools`
is ignored from user and project files when managed settings set `allowManagedPermissionRulesOnly`.

<!-- sdlc: REC-51 -->
