# Runbook: an agent did something it should not have

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/factory/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

Use this for a leaked secret, a destructive command that ran, a bad change that reached a
shared branch, or a session you no longer trust. Do the steps in order. Write times as you go.

## 1. Stop

- End the session (Ctrl+C, then close the terminal). For an unattended run, kill the
  `claude -p` process and remove its worktree later; leave the branch for evidence.
- Do not ask the agent to undo its own damage. Do not run `/clear` or `claude purge`
  yet: the transcript is evidence.

## 2. Contain the credentials

- Revoke any key the session could read or print at its source (the cloud console, the
  GitHub token page, the database), not by deleting it from a file. A deleted secret
  keeps working until it is revoked.
- Rotate every secret the session had access to: `.env`, `~/.aws`, `~/.ssh`, `gh auth`
  tokens, and any value printed in the transcript.
- Check the audit log of each system for use between session start and revocation.

## 3. Freeze

- Pause merges on the affected repository (branch protection or the merge queue) and say so
  in the team channel.
- If a deploy happened, stop further deploys until step 5 is done.

## 4. Preserve evidence

Copy these before anyone cleans up: the session transcript under `~/.claude/projects/`, the
repository's `.claude/work/blocks.jsonl`, `.claude/work/headless/` logs, `git reflog`, and the
PR. Transcripts contain secrets; store the copy where only the incident team can read it.

## 5. Revert

- Revert on a branch with `git revert`, through a normal PR. No force push, no history rewrite.
- Data: restore from backup into a new database, compare, then switch. Do not repair in place.
- Deploys: use the rollback step stated in `/factory:deploy`; check the ready endpoint before declaring done.

## 6. Post-mortem (within 3 working days)

Blameless. Cover what the agent was asked, which guard should have stopped it and why it did
not, how long keys were exposed, and what changed. It must end with a new rule, test or hook
(cite the ticket or ISS id in the rule, as pack-governance.md requires), or an explicit note
that none is possible. Add the case to `scripts/canary.sh` if a guard failed to fire. Record
the incident in the changelog if the pack changed.

## Who does what

| Role | Name | Does |
|---|---|---|
| Incident lead | to be named | runs this page, owns the timeline |
| Secrets owner | to be named | revokes and rotates |
| Repo admin | to be named | freezes merges, restores |
| Pack owner | see pack-governance.md | fixes the guard |

<!-- sdlc: REC-133 -->
