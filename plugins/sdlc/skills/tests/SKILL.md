---
name: tests
description: Write the failing tests for the current contract, run them, confirm from the runner's output that each fails for the intended reason (the behaviour is missing, not the test broken), commit them, then freeze them so /implement cannot edit them. Tests and NotImplementedError skeletons only — no app code.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" *)
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
  - Bash(pnpm test *)
  - Bash(pnpm exec vitest run *)
  - Bash(pnpm vitest run *)
  - Bash(pnpm exec playwright test *)
  - Bash(pnpm lint)
  - Bash(pnpm typecheck)
  - Bash(npm test *)
  - Bash(npm run test *)
  - Bash(npx --no-install vitest run *)
  - Bash(yarn test *)
  - Bash(yarn vitest run *)
  - Bash(bun test *)
  - Bash(bunx vitest run *)
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
1a. **Skeletons.** When a module the new tests import does not exist yet, create a skeleton of it with the Write
   tool, inside the contract's allowed paths and outside the test paths, so the imports succeed and the run shows
   what the test itself does: each function, method or class the tests call, with its signature, and a body that
   only raises, nothing else (Python `raise NotImplementedError`; TypeScript or JavaScript
   `throw new Error("not implemented")`). No logic, no return values, no constants beyond what an import needs.
   Never edit an existing module for this. A path outside the allowed paths is an escalation, not a skeleton.
2. Run the new tests and classify every failure:
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" <the new test files>`. It runs the stack's test
   command from `.claude/sdlc.json` narrowed to those files (pytest, or vitest through `pnpm test`, `npm test` and the
   like), reads the output and records, per test, one reason: `not_implemented`, `assertion`, `status_mismatch` or
   `other` (the intended reasons: the behaviour is missing) or `import_error`, `attribute_error`, `name_error`,
   `syntax_error`, `collection_error` (the wrong reasons: the test is broken, and would never pass). It writes
   `.claude/work/<slug>/failing_first.json` and prints `payload tests: [...]`.
   - Exit 1 lists each wrong reason with what to fix. Fix the test (or add the missing function to the skeleton)
     and rerun until it exits 0; never freeze before. The name-shadowing case it catches: `import app.auth`, then
     `from app.main import app`, rebinds `app`, so `app.auth` raises AttributeError. An `import_error` of the module
     under test means its skeleton is missing (step 1a). After three attempts on the same test, escalate (implement's
     list: a test that cannot be written as the contract states).
   - A `NOTE ... already pass` line: a test that passes before the implementation is not testing the change —
     fix it or delete it, then rerun.
   - Exit 2: the runner is not one it reads (a stack other than pytest or vitest). Run the stack's test command
     yourself, narrowed to the new files, read every failure the same way, and report the reason per test in step 6.
3. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files <the new files>` and fix
   what it flags.
4. Commit the tests with their skeletons: `git add <test files> <skeleton files>` then
   `git commit -m "test(<slug>): <what the tests pin down>"`. The commit guard allows a tests-only commit without a
   verify stamp, so test-first is visible in the history. If it refuses the commit because a skeleton is not a test
   path ("no verify stamp"), commit the test files alone (`git add <test files>`, the same commit) and leave the
   skeletons uncommitted in the worktree; `/sdlc:implement` replaces them inside the allowed paths. It refuses a subject of 72 characters or more: keep the first `-m` short and put
   the detail (and any trailer) in a second `-m`. Run each command as its own Bash call: in an unattended
   session a loop, a heredoc, a redirection, or a chain holding any command outside the
   allow-list is refused, so files are made with the Write tool, never through the shell.
5. Freeze them: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze <the same test files>`.
   It stores each file's sha256 in current.json `frozen_tests` and the commit in `frozen_at`. From
   now on the edit guard refuses edits to those files and verify fails if one changed. Only a human
   unfreezes (`--unfreeze`); the agent is blocked from it (REC-07). Check with `--list`. It refuses, freezing
   nothing, a file whose latest reason in failing_first.json is wrong or that changed since step 2 ran on it: go back
   to step 2. After a freeze it prints `tests_failing_first payload: {...}`, the evidence payload for step 5a.
5a. **If the graph MCP is available** (your tool list has `mcp__plugin_graph_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), post the test-first evidence after the freeze, using the slug from current.json.
   Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   - `evidence_add` kind `tests_failing_first`, payload exactly the `tests_failing_first payload:` JSON step 5
     printed: `{"tests": [{"test": "<node id>", "reason": "<reason>"}, ...], "commit": "<the tests-only commit>"}`,
     one entry per new test with the reason step 2 recorded (the ledger's guard refuses a wrong reason too). No
     printed payload (step 2's exit-2 path): build the same shape from your own reading, and the commit from
     `git rev-parse HEAD`.
   - `evidence_add` kind `tests_frozen`, payload `{"files": {<path>: <sha256>}, "commit": "<frozen_at>"}`,
     both read from current.json `frozen_tests` and `frozen_at` after the freeze.
   - `contract_transition` to `tests_frozen` with the two evidence ids. The guard cites that evidence;
     if it answers `missing`, list what is missing in the report and carry on.
6. Report every test name with its recorded failure reason (from step 2) and the frozen list, then say:
   "Run /sdlc:implement."
