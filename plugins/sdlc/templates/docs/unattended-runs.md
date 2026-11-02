# Unattended runs

An unattended run is `claude -p` with nobody watching. The only supported way to start one is
`scripts/run_headless.sh`. Do not run `claude -p` by hand against a real checkout.
Scripts named `scripts/NAME` live in the plugin, not in this repo. Run one from a marketplace checkout as
`<checkout>/plugins/sdlc/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

## What the wrapper refuses without

`run_headless.sh <repo> <ticket-file> --reviewer @handle` stops with exit 2 unless:

- `.claude/work/current.json` exists with a non-empty `allowed_paths` (run `/sdlc:ticket` first, as a
  human, so the scope was approved before the run);
- a verify command is configured (`commands.test` or `verify` in `.claude/sdlc.json`, or `scripts/verify.py`);
- `--reviewer` names a human. That person is requested on the draft PR and written into its body.

## What it does

1. Creates a git worktree at `../<repo>-<slug>` on branch `headless/<slug>` and carries
   `current.json` into it. It never runs in your checkout.
2. Runs `claude -p` there with `--permission-mode dontAsk` (anything not pre-approved is
   denied), an explicit `--allowedTools` list (reads, edits, `git status|diff|log|add|commit`,
   `python3`, `bash scripts/*`), `--max-turns` (default 40) and `--max-budget-usd` (default 5),
   with `--output-format json`. The prompt runs `/sdlc:feature` on the ticket.
3. Pushes the branch and opens a DRAFT PR with `gh`. It never merges and never pushes to a
   protected branch.
4. Logs under `.claude/work/headless/`.

Useful options: `--no-push`, `--dry-run`, `--base BRANCH`, `--max-turns N`, `--budget USD`.
Environment: `HEADLESS_ALLOWED_TOOLS`, `HEADLESS_MODEL` (default `sonnet`), `HEADLESS_FEATURE_CMD`.

## Why not --bare

`--bare` skips hooks, skills, subagents, plugins, MCP, auto memory and CLAUDE.md. The hooks
are the guard, so a bare run has no guard. It also ignores OAuth and keychain logins and needs
`ANTHROPIC_API_KEY`. The wrapper never passes it. Without `--bare`, a `-p` session runs the
project's hooks and connects `.mcp.json` servers even in a folder you have not trusted, so run
it only on repositories you own.

## Limits you should know

- `--max-budget-usd` is a client-side estimate. Spend can pass the cap, and cap enforcement
  needs Claude Code v2.1.217 or later. `--max-turns` works with `-p` but may be missing from
  `claude --help`; confirm against the current docs. The wrapper records the turn count in
  its log.
- `--allowedTools` is ignored if managed settings set `allowManagedPermissionRulesOnly`.
- Replace any `privileged: true` devcontainer first. Do not run unattended sessions
  inside a privileged container, and do not use `bypassPermissions` for them.
- No production credentials, no database you cannot rebuild. See db-roles.sql.

## After the run

A person reads the draft PR as if a stranger wrote it. `scripts/review_pr.sh <n>` checks
scope and runs the PR's new tests against the base code in a throwaway worktree (a new test
that passes on the base is suspicious). Then follow rollout.md. Reviewers are accountable,
and "the agent ran unattended" is not an excuse for a thinner review.

<!-- sdlc: REC-52 -->
