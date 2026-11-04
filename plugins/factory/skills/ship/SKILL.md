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
  - mcp__plugin_factory_graph__workflow_next
  - mcp__graph__workflow_next
---

# Ship

## Goal

Turn the verified tree into one commit whose message matches the diff, clear the work state, push the branch and open
its PR with the evidence, for a reviewer who is not the author. Never merges. Human-invoked only.

## Inputs

- The stamp `.claude/work/last_verify.json`. **Precondition:** it exists and its fingerprint equals
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py"`; otherwise stop and say to run
  `/factory:verify`. The commit guard checks this too.
- `.claude/work/current.json` (the slug and the reviewer) and, under `.claude/work/<slug>/`, `contract.md`,
  `assumptions.md` and `handoffs.md` (the hand-off log the hooks wrote). Read all of them before the clear: it
  deletes them, and the ledger post needs the slug afterwards.
- The repository's PR template, the diff, `git status --porcelain`.
- The ledger, when the graph tools are in your tool list: `workflow_next` on the slug, for its `open_decisions`.

## Outputs

Order: read assumptions.md and handoffs.md, then `workfile.py --clear`, stage and check, commit with
`git commit -m "<subject>" -m "<body>"` (subject under 72 characters), push with exactly `git push -u origin HEAD`, open
the PR with `--body-file` (the body is composed before the clear).

- **The PR body**, from the repository's PR template: the contract's Outcome; the acceptance criteria as a checked
  list; the acceptance table; the Evidence section verbatim; the Decisions table with each Value and who decided it,
  the rows marked `recommended, unconfirmed` listed again under "Decisions nobody confirmed — reviewer, confirm or
  change"; a section "Assumptions — reviewer, confirm or reverse" with every row of assumptions.md ("None" without the
  file); the reviewers' "Not reviewed" lists; the Follow-ups; a **Hand-offs** section with every row of handoffs.md
  ("None" without the file); the template's `Open decisions: N` line, `N` the count of `open_decisions` with each
  question listed under the Decisions table (graph off, no graph tools or an error: `Open decisions: n/a (graph off)`);
  and the line "AI-assisted: what I verified; any copied code and its licence."
- **The cleared work state**: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --clear <slug>` before the final
  commit, so a stale contract never reaches the protected branch (CI fails if one does; REC-137). It checks the slug
  (`^[a-z0-9][a-z0-9-]{0,40}$`, a plain directory directly under `.claude/work`) and that the stamp still matches,
  then removes `current.json` and that directory and nothing else. `last_verify.json` stays (the fingerprint ignores
  `.claude/work`). A refusal: delete nothing and tell the human what it said.
- **The commit**: the message written from the diff, not from the ticket: subject under 72 characters as
  `<type>(<scope>): <what changed>`; a body that says what changed and why, names the contract's decisions and claims
  nothing the diff does not do; no `Signed-off-by` trailer on an agent commit. Staged with `git add -u` and
  `git add -- <each new file you made>`, the list from `git diff --cached --name-only` checked with
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_message.py" --message "<msg>" --files <the files it listed>`.
- **The push**: `git push -u origin HEAD`, exactly that text as its own command (the sandbox excludes only that exact
  form, so git's credential helper can run). When git answers that `origin` does not appear to be a git repository,
  nothing was pushed: report `NO REMOTE`, the line git printed, the PR title (the commit subject) and the PR body
  verbatim (the contract is gone, so the body lives only there). Any other push failure: report what git printed and
  stop.
- **The PR**: the body written with the Write tool to `.sdlc-stage/<slug>/pr-body.md`, then, with `gh` as the first
  word of its own command:

  ```
  gh pr create --title "<subject>" --reviewer <reviewer from current.json> --body-file .sdlc-stage/<slug>/pr-body.md
  ```

  (the handle without its `@`; `--draft` added when `N` is above 0, and the summary says the PR stays a draft until a
  human resolves the listed decisions and marks it ready). Then
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" --discard .claude/work/<slug>/pr-body.md`.
- **Ledger evidence** (graph tools available; without them skip silently, an error is one line): `evidence_add` kind
  `pr_url` with `{"url": "<the PR url gh printed>"}`, then `contract_transition` to `in_review` with that evidence id.
- **The summary**: "PR <url>. Waiting for review. /factory:deploy after merge." With the graph, add: "After merging on
  GitHub, record it so dependent contracts can start: a human runs
  `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph evidence add <slug> --kind merge_sha --payload '{"sha": "<merge commit>"}'`
  and then `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph node transition <slug> merged --actor @<handle> --evidence-ids <id>`"
  (the ledger moves `in_review` to `merged` for a human; CI-signed approval is the other path). No remote, local
  only: "Committed locally at <sha>; no remote, so no PR." and no `pr_url` evidence.

## Guards

- **Never merge.** No merge, no auto-merge, no approval; the reviewer is not the author. Approval and merge stay with
  a human or CI.
- **No remote is a human's question.** Ask once: add a remote and push, or keep the commit local only? Headless:
  nothing is asked and the commit stays local. A human adds the remote; ship never adds one and never pushes
  anywhere else. Once it is there, the push and the PR run with the title and body already written.
- **The tree stays verified.** Edit no file but `.sdlc-stage/<slug>/pr-body.md`: an edit breaks the stamp and the
  commit guard refuses the commit. The commit guard, the edit guard and the deny rules (no force push, no push to a
  protected branch, no `--no-verify`) hold throughout.
- **Never stage everything.** Tracked changes with `git add -u` and each new file by name; never `git add -A` or
  `git add .`, which would stage a developer's `.env`. If `git status --porcelain` shows `.env` or any `.env.*` other
  than `.env.example` staged or untracked-and-unignored, unstage it and tell the human to add it to `.gitignore`.
- **No shell-made text.** The work state goes through workfile.py (no `rm`: nothing allows it under `dontAsk`); the PR
  body through the Write tool and `--body-file`, never a heredoc, a pipe or `--body` with the text inline; never
  `echo "exit=$?"` or a redirection after a script.
- **Subagents.** A subagent spawned for this stage carries `[sdlc stage=ship node=<slug>]` first in its
  `description`. A subagent cannot ask: what it would ask (no remote, a refused clear, an unstaged `.env`) comes back
  in its report and is asked or told here. A subagent refused by a hook reports the refusal; it does not work around
  it. Parallel subagents never edit the same file. There is never a second shipper beside the first.

## Hints (non-binding)

- What worked before: one `factory:implementer` (tagged as above) running the clear, the commit, the push and the PR
  in order, each needing the one before, handed the slug, the reviewer and the PR body; its report carries the commit
  sha and subject, the files committed, what check_commit_message.py and the clear said, the push result or the
  `NO REMOTE` block, and the PR url.
