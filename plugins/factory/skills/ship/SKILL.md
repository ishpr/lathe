---
name: ship
description: Commit with a message that matches the diff, clear the work state, push the branch, open the PR with the evidence. Never merges. Human-invoked only.
disable-model-invocation: true
allowed-tools:
  - Read
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_message.py" *)
  - Bash(git status --porcelain)
  - Bash(git add -u)
  - Bash(git add -- *)
  - Bash(git diff --cached --name-only)
  - Bash(git commit *)
  - Bash(git push -u origin HEAD)
  - Bash(gh pr create *)
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
---
Precondition: `.claude/work/last_verify.json` exists and its fingerprint equals
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py"`. If not, stop and say to run
`/factory:verify`. The commit guard checks this too.

1. Read `.claude/work/current.json` (slug, reviewer) and `.claude/work/<slug>/contract.md`, and
   draft the PR body now, from the repository's PR template: the contract's Outcome, the acceptance
   criteria as a checked list, the acceptance table, the Evidence section verbatim, the Decisions
   table with each Value and who decided it (rows marked `recommended, unconfirmed` listed again under
   "Decisions nobody confirmed — reviewer, confirm or change"), an **Assumptions** section with every row of
   `.claude/work/<slug>/assumptions.md` (the defaults the agent applied instead of asking; "None" when the
   file does not exist) headed "Assumptions — reviewer, confirm or reverse", the reviewers' "Not reviewed"
   lists, the Follow-ups, and the line "AI-assisted: what I verified; any copied code and its licence."
   Read assumptions.md now: step 2 deletes it.
2. Clear the work state before the final commit, so a stale contract never reaches the protected
   branch (CI fails if one does; REC-137): run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --clear <slug>` as one plain command (no `rm`: nothing allows it
   under `dontAsk`). The slug comes from a file the session can write, so the script checks it: it must match
   `^[a-z0-9][a-z0-9-]{0,40}$` and `.claude/work/<slug>` must be a plain directory directly under `.claude/work`, and
   the verify stamp must still match the tree. It removes `current.json` and that directory and nothing else. If it
   refuses, delete nothing and tell the human what it said.
   Leave `last_verify.json` alone. The fingerprint ignores `.claude/work`, so the stamp still holds.
3. Write the commit message from the diff, not from the ticket. Subject under 72 characters as
   `<type>(<scope>): <what changed>`; the body says what changed and why, names the decisions from
   the contract, and claims nothing the diff does not do. No `Signed-off-by` trailer on an agent
   commit. Stage and check it, one plain command each: `git add -u`, then `git add -- <each new file you made>`,
   then `git diff --cached --name-only`, then
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_message.py" --message "<msg>" --files <the files it listed>`.
   Stage tracked changes with `git add -u` and name each new file you created; never `git add -A` or
   `git add .`, which would stage a developer's `.env`. Read `git status --porcelain` first: if `.env` or any
   `.env.*` other than `.env.example` shows as staged or untracked-and-unignored, unstage it and tell
   the human to add it to `.gitignore`.
4. Commit, then `git push -u origin HEAD`.
5. Open the PR with the body from step 1. Write the body with the Write tool to
   `.sdlc-stage/<slug>/pr-body.md` (never a heredoc or a pipe), then
   `gh pr create --title "<subject>" --reviewer <reviewer from current.json> --body-file .sdlc-stage/<slug>/pr-body.md`
   (the reviewer handle without its `@`), then remove the staged copy with
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --discard .claude/work/<slug>/pr-body.md`. The reviewer is not the author.
   Do not merge; do not enable auto-merge.
5a. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), post the PR as evidence, using the slug you read from current.json in step 1
   (current.json is gone after step 2): `evidence_add` kind `pr_url`, payload `{"url": "<the PR url gh
   printed>"}`, then `contract_transition` to `in_review` with that evidence id. Approval and merge stay
   with a human or CI; this server cannot do them. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
6. Say: "PR <url>. Waiting for review. /factory:deploy after merge."
