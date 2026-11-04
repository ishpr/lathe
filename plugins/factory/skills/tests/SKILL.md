---
name: tests
description: Write the failing tests for the current contract, run them, confirm from the runner's output that each fails for the intended reason (the behaviour is missing, not the test broken), commit them, then freeze them so /implement cannot edit them. Tests and NotImplementedError skeletons only — no app code.
allowed-tools:
  - Agent
  - Read
  - Grep
  - Glob
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --list)
  - Bash(git add *)
  - Bash(git commit -m *)
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
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

# Tests

## Goal

Pin the current contract's acceptance criteria as tests that fail for the intended reason (the behaviour is missing,
not the test broken), commit them with the skeletons they import, and freeze them so `/factory:implement` cannot edit
them. Tests and `NotImplementedError` skeletons only, no app code.

## Inputs

- `.claude/work/current.json` and the contract it points to (`.claude/work/<slug>/contract.md`): the acceptance
  criteria with their ids, the Decisions, `## Defaults when ambiguous`, the allowed paths. No contract: stop and say
  to run `/factory:design`.
- `.claude/sdlc.json`: the test paths (`paths.tests`), the stack's test command, the `e2e` block.
- `.claude/rules/sdlc-*.md` and the code the tests will drive.

## Outputs

- **Test files** under the test paths, one test per criterion named after it, each failing for an intended reason.
- **Skeletons** for every module the new tests import that does not exist yet: new files inside the contract's
  allowed paths and outside the test paths, each function, method or class the tests call with its signature and a
  body that only raises (Python `raise NotImplementedError`; TypeScript or JavaScript
  `throw new Error("not implemented")`), nothing else. Never an edit to an existing module.
- **The failing-first record**, `.claude/work/<slug>/failing_first.json`, written by
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" <the new test files>`. It runs the stack's test
  command narrowed to those files and records one reason per test: `not_implemented`, `assertion`, `status_mismatch`
  or `other` (intended) or `import_error`, `attribute_error`, `name_error`, `syntax_error`, `collection_error` (the
  test is broken and would never pass). Exit 1 lists each wrong reason; a `NOTE ... already pass` line is a test that
  does not test the change. Exit 2: the runner is not one it reads; run the stack's test command narrowed to the new
  files and read every failure the same way. An end-to-end spec (one that imports `@playwright/test`) is read from a
  recorded run: first `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run <the new spec files>` as its own Bash
  command (the sandbox excludes that exact command), then tests_failing_first.py on the same files; never the suite's
  own command in its place, because the freeze reads that record.
- **One tests-only commit**: `git add <test files> <skeleton files>`, then
  `git commit -m "test(<slug>): <what the tests pin down>"`, subject under 72 characters (detail and trailers in a
  second `-m`). The commit guard allows it without a verify stamp and lets a skeleton ride when the file is new and
  every body only raises; `not a skeleton here: <file> (<why>)` means logic to take out. A skeleton that cannot be
  reduced is app code: leave it uncommitted for `/factory:implement` and commit the tests alone.
- **The freeze**: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze <the same test files> <each existing test file this step edited>`
  stores each file's sha256 in current.json `frozen_tests` and the commit in `frozen_at`
  (`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --list` shows them). It prints
  `tests_failing_first payload: {...}` and `tests_frozen payload: {...}`; use both lines whole.
- **Ledger evidence** (graph tools in your tool list; without them skip silently, an error is one line): `evidence_add`
  kind `tests_failing_first` with the `tests_failing_first payload:` JSON exactly,
  `{"tests": [{"test": "<node id>", "reason": "<reason>"}, ...], "commit": "<the tests-only commit>"}` (exit-2 path:
  built from the reasons read and `git rev-parse HEAD`); `evidence_add` kind `tests_frozen` with the
  `tests_frozen payload:` JSON exactly (full 64-character hashes); then `contract_transition` to `tests_frozen` with
  both evidence ids. A `missing` answer is listed in the report.
- **Assumptions and questions** in the formats `/factory:implement` gives (Stage `tests`).
- **The report**: every test name with its recorded failure reason, the frozen list and the assumptions recorded,
  then "Run /factory:implement."

## Guards

- **Never ask.** A criterion that could be pinned more than one way is settled by the contract's Decisions, then its
  `## Defaults when ambiguous`, and recorded as an assumption. What no default may settle (implement's escalation
  list; here also a criterion that cannot be tested as written, a test still wrong after three attempts, or an exit 2
  naming the `e2e` block, the recorded run or a held port after one rerun of the e2e script, quoting its message) is an
  escalation: the question, its options and a recommendation written to `.claude/work/<slug>/questions.md`, then
  `ESCALATION: <one line>` and stop. Never a weaker test in place of one the contract states.
- **Frozen means frozen.** freeze_tests.py refuses, freezing nothing, a file whose latest recorded reason is wrong or
  that changed since it was classified: classify again first. Only a human unfreezes; the agent is blocked from it
  (REC-07). From the freeze on, the edit guard refuses edits to those files and verify fails if one changed.
- **Scope.** The edit guard holds the contract's allowed paths; a skeleton outside them is an escalation, not a
  skeleton. Everything under `.claude/` other than the work files below is not this step's to touch.
- **Work files.** assumptions.md and questions.md are written with the Write tool to `.sdlc-stage/<slug>/<file>`,
  then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/<file>` (Claude Code refuses Write
  under `.claude/` unattended). Files are made with the Write tool, never through the shell: a loop, a heredoc or a
  redirection is refused unattended.
- **E2E specs are written, never run by the author.** A Playwright spec is not run while writing it; `e2e.py run` runs
  once, in the tail (no browser launches in the sandbox).
- **The test run is the syntax check.** Never `python3 -m py_compile`, `node --check` or a linter over the new tests:
  no allow rule covers them, and the runner already reports a file that does not parse as `syntax_error`.
- **Subagents.** A subagent spawned for this stage carries the tag `[sdlc stage=tests node=<slug>]` first in its
  `description`, so the spawn guard and the ledger record it under this stage. A subagent cannot ask: its choices come
  back as assumption rows and its stop as an `ESCALATION:` line, which this conversation writes to the work files. A
  subagent refused by a hook reports the refusal; it does not work around it. Parallel subagents never edit the same
  file. The e2e script and tests_failing_first.py each keep one record, so one runner at a time.

## Hints (non-binding)

- What worked before: one `factory:test-author` per module or route, each owning one test file, in parallel; then one
  `factory:implementer` (tagged as above) for the skeletons, the classification, the commit and the freeze. Hand an
  author the public names the skeleton will hold and nothing about how the code works inside: the brief becomes
  frozen tests, and a later fix to an internal detail would need a human to unfreeze them.
- A file two groups would need (a conftest, a shared fixture module) goes to one of them.
- The name-shadowing case: `import app.auth`, then `from app.main import app`, rebinds `app`, so `app.auth` raises
  AttributeError. An `import_error` of the module under test means its skeleton is missing.
- `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files <the new files>` before the commit catches
  what the gate would.
