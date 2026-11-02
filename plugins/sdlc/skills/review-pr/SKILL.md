---
name: review-pr
description: Read-only help for the human approving a PR — scope against the PR's contract, and whether each new test fails on the base code (a test that passes without the change tests nothing). Never comments, approves or merges.
argument-hint: <PR number>
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/review_pr.sh" *)
  - Bash(gh pr view *)
  - Bash(gh pr diff *)
---
You are helping a human approve PR $ARGUMENTS (REC-53). Read-only: no edits, no commits, no
`gh pr review`, `gh pr comment` or `gh pr merge`.

1. Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/review_pr.sh" <n>`. It fetches the PR head into
   FETCH_HEAD only, checks the changed files against the PR's own `allowed_paths` when the PR
   carries a current.json, and runs each new or changed test file in a throwaway worktree at the
   base commit. Exit 0 clean, 1 out-of-scope files or suspicious tests, 2 it could not read the PR
   (report the error and stop). Show its report.
2. Read it for the human: a test that **FAILS on base** is good (it needs the PR's code). One that
   **PASSES on base** is suspicious: it would pass without the change — name it and what it should
   assert instead. A failure on base that is a collection or import error is weaker evidence; say so.
3. Read `gh pr diff <n>` and `gh pr view <n> --json title,body,files`. Check the body carries the
   contract's Outcome, acceptance criteria, acceptance table and Evidence, and that they match the
   diff (tests named exist; a real request shown is for a route in the diff). For each changed
   public symbol, grep its callers and say whether any was missed.
4. Flag, with file:line: weakened or deleted assertions, new `skip`/`xfail` markers, literals copied
   from a test into app code, an auth dependency override with no 401/403 test, a grown
   `.claude/baseline.json`, edits under `.claude/`, `.github/` or CI config.
5. Output: the review_pr.sh verdict; at most 10 findings as `severity | file:line | what | check`,
   highs first, no style notes; the three places to read by hand and why; and a **Not reviewed**
   section listing what you did not look at (generated files, large data, anything you skimmed).
6. One-line verdict for the human: approve-ready / needs changes. The human approves and merges.
