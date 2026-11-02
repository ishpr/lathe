---
name: rollback
description: Revert a merged change on a new branch and open a PR for it — never a force-push, never a push to the protected branch. Human-invoked only, for an incident or a bad deploy.
disable-model-invocation: true
argument-hint: <PR number | merge commit sha> [reason]
allowed-tools:
  - Read
  - Bash(git status --porcelain)
  - Bash(gh pr view *)
  - Bash(git show -s *)
  - Bash(git fetch origin *)
  - Bash(git switch -c rollback/*)
  - Bash(git revert --no-commit *)
  - Bash(git revert --abort)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_message.py" *)
  - Bash(git diff --cached --name-only)
  - Bash(git commit *)
  - Bash(git push -u origin HEAD)
  - Bash(gh pr create *)
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
---
Roll back: $ARGUMENTS (REC-133). A revert is a normal change: branch, gate, PR, human merge.

0. `git status --porcelain` must be empty and `.claude/work/current.json` absent; otherwise stop
   and say to start a session in a clean worktree (`claude -w rollback`). If an agent caused the
   incident, say first: stop that session, revoke its keys at the source and freeze merges, as the
   agent-incident runbook says (`docs/runbook-agent-incident.md` where the repo has it).
1. Find the commit. A PR number → `gh pr view <n> --json mergeCommit,title,baseRefName`. A sha →
   `git show -s --format='%H %P %s' <sha>`. Two parents → a merge commit (revert with `-m 1`); one
   parent → a squash or rebase merge (plain revert). Show the subject and ask "Revert this?" if the
   human has not named it exactly.
2. `git fetch origin <base>` then `git switch -c rollback/<short sha> origin/<base>`.
3. `git revert --no-commit [-m 1] <sha>`. A conflict → `git revert --abort`, report the files,
   stop: a human resolves it.
4. Gate the reverted tree like any change: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py"`.
   Red → report what failed and stop; the revert may need a human decision (a migration that
   already ran, data written in the new shape). Do not edit around it.
5. Message: `revert(<scope>): <original subject> (<short sha>)`, body with the reason and the
   incident or ticket link the human gave. Run `git diff --cached --name-only` on its own, then check the message with
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_message.py" --message "<msg>" --files <the files it listed>`
   (no command substitution: `dontAsk` denies `$(...)`), then `git commit`.
6. `git push -u origin HEAD`. Never `--force` or `--force-with-lease`, never a push to the protected
   branch, never a reset of it.
7. Write the PR body with the Write tool to `.sdlc-stage/rollback/pr-body.md` (never a heredoc or a pipe):
   what is reverted and why, verify's summary line, whether a schema or data change needs its own step, and
   "After merge: /sdlc:deploy". Then `gh pr create --title "<subject>" --body-file .sdlc-stage/rollback/pr-body.md`,
   remove the staged copy with `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --discard .claude/work/rollback/pr-body.md`,
   and request a reviewer. Do not merge.
8. Say: "Rollback PR <url>. Merge it after review, then /sdlc:deploy." End with the
   post-mortem prompt: which new rule or test would have caught this.
