---
name: ship
description: Commit with a message that matches the diff, clear the work state, push the branch, open the PR with the evidence. Never merges. Human-invoked only.
disable-model-invocation: true
allowed-tools:
  - Read
  - Agent
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
This conversation orchestrates and never executes: every step that does work runs in a subagent through the Agent tool; here you load the skill, plan the hand-offs, brief each subagent, collect what comes back, ask the human and summarise.
Here that means the precondition (one plain command), reading current.json, the hand-off, the question when the
branch has no remote, the ledger post (step 5a: the MCP tools belong to this conversation) and the summary
(step 6). The commit, the push and the PR (steps 1 to 5) run in one subagent, the shipper.
Nothing in this skill runs in parallel: each of steps 1 to 5 needs the one before, so one shipper runs them in
order. The skills that fan out launch them together, in one message; here that message holds one Agent call.
Before each hand-off say in one line what is handed off and why; when the subagents return say in one line what came back and what it means.
That one rule holds at both hand-offs this skill can make: the shipper, and a second one after a remote is added.
A question for the human is asked here, in the open: a subagent cannot ask.
Parallel subagents never edit the same file. There is one subagent here, and never a second shipper beside it.
A subagent refused by a hook reports the refusal; it does not work around it.
The commit guard, the edit guard and the deny rules hold inside the shipper exactly as they do here.

**Launching the shipper.** The Agent tool with `subagent_type` `factory:implementer`, the description
`[sdlc stage=ship node=<slug>] ship: commit and PR` (the tag first, so the spawn guard and the ledger record the
hand-off under the ship stage; the hand-off log records the description) and the brief as the `prompt`. Never pass a `model`: the
spawn guard pins it from the routing policy by agent type. If the Agent tool answers that the agent runs in the
background, end the turn with one line saying the shipper's report is awaited and carry on when it arrives;
never sleep or poll. A subagent gets no context it is not given, so the brief holds, in this order: the task in
one sentence (commit this branch, push it and open its PR; merge nothing); steps 1 to 5 below, verbatim, with the
slug and the reviewer filled in and every command as this skill text shows it (a command that names
`${CLAUDE_PLUGIN_ROOT}` keeps the path as shown here), one plain command per Bash call: no chaining, no `echo "exit=$?"`, no redirection;
the paths: it writes only `.sdlc-stage/<slug>/pr-body.md` and edits no other file (the tree is verified: an edit
breaks the stamp, and the commit guard then refuses the commit); the report it returns (below); and, last, this
line: "You cannot ask a question: when something cannot be done, report it and stop."

Precondition: `.claude/work/last_verify.json` exists and its fingerprint equals
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py"`. If not, stop and say to run
`/factory:verify`. The commit guard checks this too. Check it here, before the hand-off: Read the stamp, run the
script as one plain command (the only command this conversation runs) and compare the two. Then Read
`.claude/work/current.json` and keep the slug and the reviewer: the brief needs both, and step 5a needs the slug
after step 2 has deleted the file.

**The shipper's steps** (its brief carries them verbatim):

1. Read `.claude/work/current.json` (slug, reviewer) and `.claude/work/<slug>/contract.md`, and
   draft the PR body now, from the repository's PR template: the contract's Outcome, the acceptance
   criteria as a checked list, the acceptance table, the Evidence section verbatim, the Decisions
   table with each Value and who decided it (rows marked `recommended, unconfirmed` listed again under
   "Decisions nobody confirmed — reviewer, confirm or change"), an **Assumptions** section with every row of
   `.claude/work/<slug>/assumptions.md` (the defaults the agent applied instead of asking; "None" when the
   file does not exist) headed "Assumptions — reviewer, confirm or reverse", the reviewers' "Not reviewed"
   lists, the Follow-ups, a **Hand-offs** section with every row of `.claude/work/<slug>/handoffs.md`
   (who did what: the subagent, the brief, the outcome; "None" when the file does not exist), and the
   line "AI-assisted: what I verified; any copied code and its licence."
   Read assumptions.md and handoffs.md now: step 2 deletes them.
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
4. Commit, then `git push -u origin HEAD`. When the push answers that `origin` does not appear to be a git
   repository, the branch has no remote and nothing was pushed: do not add a remote, do not push anywhere else
   and do not go on to step 5; report `NO REMOTE`, the line git printed, the PR title (the commit subject) and
   the PR body from step 1, verbatim (step 2 removed the contract, so the body lives only in your report). Any
   other push failure: report what git printed and stop.
5. Open the PR with the body from step 1. Write the body with the Write tool to
   `.sdlc-stage/<slug>/pr-body.md` (never a heredoc or a pipe), then
   `gh pr create --title "<subject>" --reviewer <reviewer from current.json> --body-file .sdlc-stage/<slug>/pr-body.md`
   (the reviewer handle without its `@`), then remove the staged copy with
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --discard .claude/work/<slug>/pr-body.md`. The reviewer is not the author.
   Do not merge; do not enable auto-merge.

**The shipper's report**: the commit sha and subject as `git commit` printed them; the files it committed (the
`git diff --cached --name-only` list); what `check_commit_message.py` and `workfile.py --clear` said; the push
result or the `NO REMOTE` block; the PR url gh printed; whatever steps 2 and 3 say to tell the human (a refusal of
the clear, an `.env` it unstaged), which you then tell them; any refusal by a hook, verbatim.

**No remote.** Ask the human here, one question: add a remote and push, or keep the commit local only? In a
headless run nothing is asked: the commit stays local; say so. A human adds the remote; ship never does. Once
they say it is there, hand the rest to a second shipper: step 4's `git push -u origin HEAD` and step 5, with the
title and the body from the first report, verbatim, and the reviewer. Local only: nothing more runs, step 5a is
skipped (there is no PR) and the summary is "Committed locally at <sha>; no remote, so no PR."

5a. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), post the PR as evidence, using the slug you read from current.json before the hand-off
   (current.json is gone after step 2): `evidence_add` kind `pr_url`, payload `{"url": "<the PR url gh
   printed>"}`, then `contract_transition` to `in_review` with that evidence id. Approval and merge stay
   with a human or CI; this server cannot do them. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
6. Say: "PR <url>. Waiting for review. /factory:deploy after merge." When the graph MCP was available, add:
   "After merging on GitHub, record it so dependent contracts can start: a human runs
   `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph evidence add <slug> --kind merge_sha --payload '{"sha": "<merge commit>"}'`
   and then `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph node transition <slug> merged --actor @<handle> --evidence-ids <id>`"
   (the ledger moves `in_review` to `merged` for a human; CI-signed approval is the other path).
