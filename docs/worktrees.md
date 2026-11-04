# Worktrees: one session per worktree

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/factory/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

Two sessions in one checkout fight over the same files, the same `.claude/work/current.json`
and the same verify stamp. Give each session its own worktree.

## Rules

1. One Claude Code session per worktree. Never switch branches, stash or reset in a checkout
   another session might be using.
2. Start with `claude -w <name>` (or `--worktree`), which creates `<repo>/.claude/worktrees/<name>`.
   `-w` also takes `#<number>` or a PR URL. `scripts/run_headless.sh` makes its own
   worktree at `../<repo>-<slug>`.
3. Each worktree has its own `current.json`: the contract is committed with the change, so
   a worktree on another branch sees its own. `/factory:ship` deletes it before the final commit, and
   CI fails on a push to a protected branch if the file exists there. A stale copy on main
   would block the next person's edits.
4. The verify stamp and ratchet baseline belong to a tree. Run `/factory:verify` in the worktree you
   will commit from.
5. Remove finished worktrees: `git worktree remove <path>`, then delete the merged branch.

## Files a new worktree needs: .worktreeinclude

A worktree is a fresh checkout, so git-ignored files are missing. List the ones you need in a
`.worktreeinclude` file at the repo root, in .gitignore syntax. Only files that match a pattern
and are also git-ignored are copied. For example:

```
.env.test
.venv-cache/
```

Do not list real credentials. List the test settings. If you use a `WorktreeCreate` hook, it
replaces the default behaviour and `.worktreeinclude` is not processed.

## Separate data per worktree

Parallel test runs share nothing but the machine, so give each its own:

- A test database per worktree: a different database name in the test `DATABASE_URL`, created and dropped by the test fixtures.
- If you use Redis, a different DB index in the test `REDIS_URL` per worktree.
- A compose project name per worktree (`COMPOSE_PROJECT_NAME`) and non-clashing host ports, so
  `compose down` in one tree does not stop another's stack. Never use `down -v` (the guard
  blocks it), and never point a worktree at a database that holds real data.
- Local servers on different ports (`--port 8001`, and so on).

## Checks

- `git worktree list` shows what exists. `git status` in each should be clean before you
  start a session there.
- If two sessions report a stale stamp or a contract they did not write, they share a tree.
  Stop one of them.
