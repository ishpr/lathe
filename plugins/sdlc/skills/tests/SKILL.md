---
name: tests
description: Write the failing tests for the current contract, run them, confirm they fail for the right reason, commit them, then freeze them so /implement cannot edit them. Tests only — no app code.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --list)
  - Bash(git add *)
  - Bash(git commit -m *)
  - mcp__plugin_graph_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_graph_graph__contract_transition
  - mcp__graph__contract_transition
  - Bash(git rev-parse HEAD)
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
---
Read `.claude/work/current.json` and the contract it points to. No contract → stop and say to run
`/sdlc:ticket`.

Never ask: a criterion that could be pinned more than one way is settled by the contract's Decisions,
then its `## Defaults when ambiguous`, and recorded as an assumption (Stage `tests`) exactly as
`/sdlc:implement` describes; tell the test-author agent the same, and record what it reports
it chose. The only stop is an escalation (implement's list), written to `.claude/work/<slug>/questions.md` the way implement writes it: the Write tool to `.sdlc-stage/<slug>/questions.md`, then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/questions.md` (assumptions.md the same way).

1. Delegate to the `sdlc:test-author` agent with the contract's acceptance criteria and the
   rules in `.claude/rules/sdlc-*.md`. It writes under the test paths only. Do not pass a `model`
   on the Agent call; the agent's own `model` applies only when the call leaves it unset.
2. Run the new tests with the stack's test command (from `.claude/sdlc.json`, narrowed to the new
   files where the runner allows). Every new test must fail, and fail because the behaviour is
   missing: an import of a module that does not exist yet, a 200 where 429 is expected. A test that
   passes before the implementation is not testing the change — fix it or delete it.
3. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files <the new files>` and fix
   what it flags.
4. Commit only the tests: `git add <test files>` then `git commit -m "test(<slug>): <what the tests pin down>"`.
   The commit guard allows a tests-only commit without a verify stamp, so test-first is visible in
   the history.
5. Freeze them: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze <the same test files>`.
   It stores each file's sha256 in current.json `frozen_tests` and the commit in `frozen_at`. From
   now on the edit guard refuses edits to those files and verify fails if one changed. Only a human
   unfreezes (`--unfreeze`); the agent is blocked from it (REC-07). Check with `--list`.
5a. **If the graph MCP is available** (your tool list has `mcp__plugin_graph_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), post the test-first evidence after the freeze, using the slug from current.json.
   Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   - `evidence_add` kind `tests_failing_first`, payload `{"tests": [<the node id of each new test that
     failed in step 2>], "commit": "<git rev-parse HEAD, the tests-only commit>"}`.
   - `evidence_add` kind `tests_frozen`, payload `{"files": {<path>: <sha256>}, "commit": "<frozen_at>"}`,
     both read from current.json `frozen_tests` and `frozen_at` after the freeze.
   - `contract_transition` to `tests_frozen` with the two evidence ids. The guard cites that evidence;
     if it answers `missing`, list what is missing in the report and carry on.
6. Report every test name with its expected failure reason and the frozen list, then say:
   "Run /sdlc:implement."
