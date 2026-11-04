---
name: onboard
description: Explain how this repository is put together and how work flows here under the SDLC working agreement, for someone new — from the code, the routes and CLAUDE.md, not from design docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git ls-files *)
  - Bash(git log --oneline *)
  - Bash(head *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
---
Build the picture from what runs, not from what a design document says (REC-82): an
ARCHITECTURE.md or README may describe a design that was never finished, so treat it as a claim to
check against the code, and say where the two disagree.

Read CLAUDE.md, `.claude/sdlc.json` and `.claude/rules/sdlc-*.md`. Then read the tree:
`git ls-files <app paths> | head -200` for the layout, the entry point (`entry_modules` in the
config, else the stack's usual main file), every route or handler it actually mounts (follow the
includes from the entry point; grep the route decorators or registrations), where auth is applied,
where state is stored, and `git log --oneline -20` for what is moving.

Then explain, in under 400 words, in this order:

1. The shape of the code: the main modules, where requests enter, the routes that are mounted (and
   any defined but not mounted), where auth happens, where state lives.
2. The working agreement: `/factory:design` → `tests` → `implement` → `verify` → `ship`, with
   `feature` running them in order; the human stops (Decisions, "Ship it?", the PR review); and the
   human-only commands (`ship`, `deploy`, `rollback`) plus `review-pr` and `migrate`.
3. The non-negotiables and which hook enforces each (edit guard with frozen tests and tier, bash
   guard, post-edit ratchet, stop nudge).
4. How to run the tests and the app locally, with the exact commands from `.claude/sdlc.json`.
5. The three biggest pieces of debt from `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all`.

Offer to run `/factory:design` on their first task.
