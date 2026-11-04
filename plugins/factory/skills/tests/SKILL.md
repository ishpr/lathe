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
## How this skill runs

This conversation orchestrates and never executes: every step that does work runs in a subagent through the Agent tool; here you load the skill, plan the hand-offs, brief each subagent, collect what comes back, ask the human and summarise.
What stays here: reading `.claude/work/current.json`, the contract and the list of rules files; grouping the criteria (step 1);
writing assumptions.md and questions.md from what the subagents report (Never ask, below); the ledger evidence of
step 5a (the graph tools belong to this conversation, a subagent has none); the report of step 6. The tests are
written by one `factory:test-author` per group of criteria (step 1); steps 1a to 5 run in one `factory:implementer`,
the tail. Headless or not, the hand-offs are the same.

- **Hand-offs.** One Agent call per hand-off: `subagent_type` `factory:test-author` or `factory:implementer`, a `description` of a few words naming the hand-off (the hand-off log records it), and the brief as the `prompt`.
- **Parallel.** The test authors do not depend on each other: launch them together, in one message. The tail needs every file they wrote, so it waits for all of them; its steps depend on each other, so it is one subagent.
- **Narrate.** Before each hand-off say in one line what is handed off and why; when the subagents return say in one line what came back and what it means.
- **Background.** If the Agent tool answers that the agents run in the background, end your turn with one line saying what is awaited and carry on when every report has arrived; never sleep, poll or read an output file.
- **Questions.** A question for the human is asked here, in the open: a subagent cannot ask. This stage asks none (Never ask, below): its one stop is an escalation, which this conversation writes to questions.md from a subagent's `ESCALATION:` line.
- **One owner per file.** Parallel subagents never edit the same file. Each test file belongs to one author; a file two groups would need (a conftest, a shared fixture module) goes to one group, and the others keep what they need inside their own test file. The e2e script and tests_failing_first.py each keep one record under `.claude/work/`, so only the tail runs them.
- **Hooks.** The contract's allowed paths, the hooks and the Never ask rule apply inside a subagent exactly as here. A subagent refused by a hook reports the refusal; it does not work around it. Doing what the refusal itself says (step 4's skeleton case) is not working around it.
- **Briefs.** A subagent gets no context it is not given. Every brief holds, in this order: the task in one sentence; the commands, copied from this skill with the plugin path as this text shows it (no `echo "exit=$?"`, no redirection); the files it may touch and the ones it must not; what to report, in the shape this skill gives; and the line "You cannot ask a question: when something cannot be done, report it and stop."

Read `.claude/work/current.json` and the contract it points to. No contract → stop and say to run
`/factory:design`.

Never ask: a criterion that could be pinned more than one way is settled by the contract's Decisions,
then its `## Defaults when ambiguous`, and recorded as an assumption (Stage `tests`) exactly as
`/factory:implement` describes; tell every subagent the same in its brief, and record what each reports it chose. The only stop is an escalation (implement's list), written to `.claude/work/<slug>/questions.md` the way implement writes it: the Write tool to `.sdlc-stage/<slug>/questions.md`, then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/questions.md` (assumptions.md the same way). This conversation writes both files, from the assumption rows and the `ESCALATION:` lines the subagents report; a subagent never writes them.

1. **The test authors, one per group.** Group the contract's acceptance criteria by the module or route they touch:
   one group per module or route, one test file per group (a new file under the test paths, `paths.tests` in
   `.claude/sdlc.json`, or an existing test file that group alone edits), never two authors on one file. Brief one
   `factory:test-author` per group (description `tests author: <module or route>`) with:
   - the task: write the failing tests for these criteria, in this file;
   - the group's criteria, word for word with their ids, and the Decisions rows and `## Defaults when ambiguous`
     lines that bear on them;
   - the public names the skeleton will hold (module, class, function, signature), where the contract's Defaults put them, and nothing about how the code works inside (which client methods it calls and in what order, when it reads a setting, where it sets a header): the brief becomes frozen tests, and a reviewer's later fix to any of it then needs a human to unfreeze them;
   - the test paths and its one test file, the only file it writes: app modules, the other groups' test files and
     everything under `.claude/` are not its to touch. An end-to-end spec (a Playwright file) is written, not run:
     the tail runs it;
   - the rules: the list of `.claude/rules/sdlc-*.md` files;
   - the Never ask rule: what could be pinned more than one way is settled by the Decisions, then the
     `## Defaults when ambiguous`, and reported as an assumption row; what no default may settle (implement's
     escalation list) is reported as `ESCALATION: <one line>` with the question, its options and a recommendation;
   - the report: `test name → expected failure reason`, the files it wrote, each choice it made as a row
     `| A? | tests | <where> | <what was ambiguous> | <default applied> | <what I did> | <decision> |`, each
     criterion it cannot test as written (said so, instead of writing a weaker test), and a hook refusal, verbatim;
   - the line "You cannot ask a question: when something cannot be done, report it and stop."

   Launch them together, in one message. When they return, a criterion an author could not test as written
   (implement's list: a test that cannot be written as the contract states), an `ESCALATION:` line or a hook
   refusal is an escalation: record the assumption rows reported, write questions.md (Never ask, above), say
   `ESCALATION: <one line>` and stop. Otherwise brief the tail with every file they wrote.

**The tail: steps 1a to 5, one `factory:implementer`.** When every author has reported, launch one
`factory:implementer` (description `[sdlc stage=tests node=<slug>] tests tail`: the tag first, so the spawn guard and the ledger record this hand-off under the tests stage, not the implementer's default) with this brief:
   - the task: give the new tests the skeletons they import, prove each fails for the intended reason, commit them
     with their skeletons and freeze them;
   - steps 1a to 5 below, copied verbatim: they hold every command, each run as its own Bash call;
   - the slug, the new test files (and each existing test file an author edited), the public names the authors were
     given, the contract's allowed paths and the test paths;
   - the files it may touch: new skeleton modules inside the contract's allowed paths and outside the test paths,
     and the test files the authors wrote or edited, only to fix what step 2 reports and never to weaken what a
     test asserts; not its to touch: any existing module, current.json (only freeze_tests.py writes it),
     contract.md, assumptions.md and questions.md;
   - the report: each test with its node id and the reason step 2 recorded (on step 2's exit 2, the reason it read);
     each test it fixed or deleted, and why; the files committed and any skeleton left uncommitted (step 4); the
     commit, from `git rev-parse HEAD`; the `frozen:` and `NOTE` lines; the `tests_failing_first payload:` and
     `tests_frozen payload:` lines exactly as step 5 printed them, whole; each choice it made as an assumption row
     in the authors' shape (Stage `tests`); `ESCALATION: <one line>` with the question, its options and a recommendation wherever a step
     below says to escalate, after which it stops; a hook refusal, verbatim;
   - the line "You cannot ask a question: when something cannot be done, report it and stop."

1a. **Skeletons.** When a module the new tests import does not exist yet, create a skeleton of it with the Write
   tool, inside the contract's allowed paths and outside the test paths, so the imports succeed and the run shows
   what the test itself does: each function, method or class the tests call, with its signature, and a body that
   only raises, nothing else (Python `raise NotImplementedError`; TypeScript or JavaScript
   `throw new Error("not implemented")`). No logic, no return values, no constants beyond what an import needs.
   Never edit an existing module for this. A path outside the allowed paths is an escalation, not a skeleton.
1b. **The test run is the syntax check.** Never run `python3 -m py_compile`, `node --check` or a linter over
   the new tests to see whether they parse: the runner reports a file that does not parse as `syntax_error`
   (step 2), and no allow rule covers those commands, so under the orchestrator the call ends the step. Byte-code
   caches are not yours to clean either: the session's environment writes none (`PYTHONDONTWRITEBYTECODE=1`),
   and `__pycache__/` is ignored by git in any repository init prepared.
2. Run the new tests and classify every failure:
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/tests_failing_first.py" <the new test files>`. It runs the stack's test
   command from `.claude/sdlc.json` narrowed to those files (pytest, or vitest through `pnpm test`, `npm test` and the
   like), reads the output and records, per test, one reason: `not_implemented`, `assertion`, `status_mismatch` or
   `other` (the intended reasons: the behaviour is missing) or `import_error`, `attribute_error`, `name_error`,
   `syntax_error`, `collection_error` (the wrong reasons: the test is broken, and would never pass). It writes
   `.claude/work/<slug>/failing_first.json` and prints `payload tests: [...]`.
   An end-to-end spec (a Playwright file, one that imports `@playwright/test`) is read from a recorded run instead of
   the stack's test command, because no browser launches inside the sandbox: first run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run <the new spec files>` as its own Bash call (the sandbox
   excludes that command; it builds the app once, serves it at the `e2e` block's url, or reuses this checkout's own
   running server, runs the specs and records the run under `.claude/work/e2e/`), then run tests_failing_first.py on
   the same files: it reads the recorded run and refuses one the tree has moved past. A failed expectation and an
   element or page state that never came are intended reasons there; a web server that did not start, a page that
   could not be reached, a browser that did not launch, a spec that does not parse or import and a run that found no
   test are wrong. Never run the suite's own command yourself in place of the script: the record it writes is what the
   freeze reads.
   - Exit 1 lists each wrong reason with what to fix. Fix the test (or add the missing function to the skeleton)
     and rerun until it exits 0; never freeze before. The name-shadowing case it catches: `import app.auth`, then
     `from app.main import app`, rebinds `app`, so `app.auth` raises AttributeError. An `import_error` of the module
     under test means its skeleton is missing (step 1a). After three attempts on the same test, escalate (implement's
     list: a test that cannot be written as the contract states).
   - A `NOTE ... already pass` line: a test that passes before the implementation is not testing the change —
     fix it or delete it, then rerun.
   - Exit 2: the runner is not one it reads (a stack other than pytest, vitest or Playwright). Run the stack's test command
     yourself, narrowed to the new files, read every failure the same way, and report the reason per test.
     Exit 2 naming the `e2e` block (a Playwright spec in a repo whose `.claude/sdlc.json` has none), the recorded run
     (none yet, stale, or narrowed to other files: run the e2e script again, then this one) or an app that could not be
     made to listen (the e2e script's exit 2) is, once the e2e script has been rerun, an escalation, not something to
     work around: report it as the `ESCALATION:` line, quoting the script's message, which says the block to add or
     the port that is held.
3. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --fast --files <the new files>` and fix
   what it flags.
4. Commit the tests with their skeletons: `git add <test files> <skeleton files>` then
   `git commit -m "test(<slug>): <what the tests pin down>"`. The commit guard allows a tests-only commit without a
   verify stamp, so test-first is visible in the history, and it lets a skeleton ride in it when the file is new
   and every body in it only raises or throws (a constant, a type, an empty constructor are fine; a return value, a
   branch, a call or a computed value are not). If it refuses the commit saying `not a skeleton here: <file> (<why>)`,
   the skeleton has logic in it: take that logic out as the reason says, then run the same commit again. A skeleton
   you cannot reduce that way is app code: leave it uncommitted in the worktree, commit the test files alone
   (`git add <test files>`, the same message), and `/factory:implement` finishes it inside the allowed paths. It refuses a subject of 72 characters or more: keep the first `-m` short and put
   the detail (and any trailer) in a second `-m`. In an unattended session a loop, a heredoc, a redirection, or a chain
   holding any command outside the allow-list is refused, so files are made with the Write tool, never through the shell.
5. Freeze them: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --freeze <the same test files> <each existing test file this step edited>`.
   It stores each file's sha256 in current.json `frozen_tests` and the commit in `frozen_at`. From
   now on the edit guard refuses edits to those files and verify fails if one changed. Only a human
   unfreezes (`--unfreeze`); the agent is blocked from it (REC-07). Check with `--list`. It refuses, freezing
   nothing, a file whose latest reason in failing_first.json is wrong or that changed since step 2 ran on it: go back
   to step 2. After a freeze it prints `tests_failing_first payload: {...}`, the evidence payload for step 5a, and
   `tests_frozen payload: {...}`: report both lines whole, never shortened.

**When the tail returns.** An `ESCALATION:` line or a hook refusal: record the assumption rows reported so far, write
questions.md from it (Never ask, above), say `ESCALATION: <one line>` and stop. Otherwise record every assumption row
the authors and the tail reported, then post the evidence with the payload lines the tail quoted.

5a. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), post the test-first evidence after the freeze, using the slug from current.json.
   Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   - `evidence_add` kind `tests_failing_first`, payload exactly the `tests_failing_first payload:` JSON step 5
     printed (the tail's report quotes it): `{"tests": [{"test": "<node id>", "reason": "<reason>"}, ...], "commit": "<the tests-only commit>"}`,
     one entry per new test with the reason step 2 recorded (the ledger's guard refuses a wrong reason too). No
     printed payload (step 2's exit-2 path): build the same shape from the reasons the tail reported, and the commit
     from the `git rev-parse HEAD` line it reported.
   - `evidence_add` kind `tests_frozen`, payload exactly the `tests_frozen payload:` JSON step 5 printed (full 64-character hashes; the `frozen:` lines show only 12).
   - `contract_transition` to `tests_frozen` with the two evidence ids. The guard cites that evidence;
     if it answers `missing`, list what is missing in the report and carry on.
6. Report every test name with its recorded failure reason (from step 2, as the tail reported it), the frozen list
   and the assumptions recorded, then say: "Run /factory:implement."
