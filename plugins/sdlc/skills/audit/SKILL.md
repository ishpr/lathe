---
name: audit
description: List the repository's current issues — the ratchet's known and new violations, unmounted entry points, TODOs, unused modules, unverified inbound calls, secrets read in handlers — ranked, with file:line.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
  - Bash(git log -S *)
---
1. `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all` — every current violation, known and new, by check.
   On Python the `dead|` keys are unmounted routers and unreachable modules, `stubs|` keys are
   TODOs without a ticket id, stub bodies, env reads outside config and class-level state, and
   `contract|` keys are endpoints the frontend or README names that do not exist; steps 2–4 confirm
   those by hand and cover the stacks without these checkers.
2. Entry points nothing mounts: list the routers / handlers / modules defined under the app paths
   and compare with what the entry point actually registers.
3. `grep -rn "TODO\|FIXME\|XXX" <app paths>` — what each one admits, and how long it has been there
   (`git log -S` on the line).
4. Unused modules: for each module under the app paths, grep the app and the tests for an import of
   it; list those with none.
5. Inbound calls that verify nothing (webhooks without signature checks), identity taken from a
   request body, secrets read inside a handler, SQL or shell built from strings.
6. Tests that assert existence, test files with no negative case (from step 1), and shared
   fixtures that would touch a real database.

Rank: security first, then correctness (state that does not survive a second process, contracts
that lie), then debt. Output one table: #, severity, file:line, the problem in one line, the fix in
one line. End with the three you would fix this week and why those three.

For a maturity level and a security assessment, with an offer to create and execute the fix, run `/sdlc:maturity` and `/sdlc:security`.
