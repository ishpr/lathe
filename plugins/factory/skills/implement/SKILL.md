---
name: implement
description: Make the contract's tests pass inside the allowed paths, with the /tests files frozen. Never asks — ambiguity is settled by the contract's defaults and logged as an assumption. Hooks lint, run the stack's checkers and keep the change in scope.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --check)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --list)
  - mcp__plugin_factory_graph__workflow_next
  - mcp__graph__workflow_next
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
  - Bash(pnpm test *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" *)
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
Editing contract.md (Decisions, Follow-ups, Evidence and the like): Claude Code refuses Edit and Write under `.claude/`
in an unattended (`--permission-mode dontAsk`) run, whatever the allow rules say. Read the contract, Write the whole
updated text to `.sdlc-stage/<slug>/contract.md`, then run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/contract.md`
(it moves the file into place). If the edit guard denies the staged Write as out of scope, say so in one line
(its `.sdlc-stage/**` exemption is missing) and, in an interactive session only, Edit contract.md directly (Claude Code
asks the human to approve). Never use a heredoc, `echo`, `printf` or `cat` to write a file. Run each script as one plain command: no `echo "exit=$?"`, `$?` or redirection after it (under `dontAsk` that
fails the allow rule); the tool result already reports a non-zero exit.

0. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`): once you have read the slug from `.claude/work/current.json`, and before anything else, call `workflow_next` with it.
   If its `state` is `tests_frozen`, move the node with `contract_transition` to `implementing` (that
   step has no guard) and call `workflow_next` again. Proceed only when the state is `implementing`;
   any other state (`drafted`, `ready`, `verified`, `in_review`, unknown slug) means this is the wrong
   step: refuse, print the state, `command` and `missing_evidence` it returned, and stop. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.

Read `.claude/work/current.json` and the contract. Run the contract's tests first and keep the
list of failures in front of you.

**Never ask.** Every question was asked when the contract was written; this stage runs on the
contract. When something is ambiguous, do not ask and do not stop:
1. If a Decision's Value answers it, follow the Value.
2. Otherwise apply the contract's `## Defaults when ambiguous` in order (no such section: the standard
   four — the smallest change that satisfies the acceptance; the nearest existing pattern; the option
   the Decisions imply; the reversible option — and say in the assumption that the contract had none).
3. Record it in `.claude/work/<slug>/assumptions.md` and carry on. Read the file if it exists, Write the
   whole text with your new rows appended to `.sdlc-stage/<slug>/assumptions.md`, then run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/assumptions.md`. A new file
   starts with `# Assumptions: <slug>` and the table header
   `| # | Stage | Where | What was ambiguous | Default applied | What I did | Decision |`; number rows `A1`, `A2`, …
   across stages, Stage is `implement`, Default applied is the default's number or per-contract line, Decision is
   the Decisions row the assumption relies on (`D1`, or `#12` from the ledger) or `none`: never blank.
   Write the rows before you finish the stage (several at once is fine).
An assumption never contradicts a Decision's Value. When the only way forward would, or the question
is one no default may settle — a path outside `allowed_paths`, a capability the contract's
`## Capabilities` does not list, a frozen test that is wrong, a path the guard says forces tier high,
money, security, data loss — that is an **escalation**, the one stop this stage makes: Write the
question, its options and your recommendation to `.sdlc-stage/<slug>/questions.md`, move it with
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/questions.md`, say
`ESCALATION: <one line>` and stop. A denied tool call you did not expect is an escalation too: name the
command and the allow rule that would have covered it.

While implementing:
- Edit only inside `allowed_paths`. The edit guard blocks the rest; needing another path is an
  escalation — the contract changes first, the code second. A path the guard says "forces tier
  high" means the contract's tier is wrong: escalate so `/factory:ticket` is re-run.
- The test files in current.json `frozen_tests` are read-only
  (`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --list` shows them). Do not edit,
  rename, skip or delete them, and do not add a conftest, fixture or override that changes what
  they assert. If a frozen test is wrong, escalate: say which assertion and why, and ask a human to run
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --unfreeze <path>` (REC-07).
- Make the code meet the behaviour, not the test's literals: no branch on a test's input value, no
  constant copied from a test into app code.
- Follow `.claude/rules/sdlc-*.md` for the paths you touch. The post-edit hook runs the stack's
  formatter and the ratchet on every file you save; fix what it reports before moving on.
- New state goes behind an interface with a real store and an in-memory fake. Inject time.
- Delete what you replace. Add nothing "for later".
- Do not add rows to **Decisions** yourself: those are a human's answers. What you chose goes in
  assumptions.md.
- A schema change goes through a migration under the allowed paths, then `/factory:migrate`
  to render its SQL for a human; never apply it here.

Loop: run the contract's tests → smallest change → rerun, until green. Then the whole suite with
the stack's test command (`commands.test` in `.claude/sdlc.json`; for Node `pnpm test`, `npm test`, `yarn test`
or `bun test`), and `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --check`.
A frozen end-to-end spec (Playwright) runs through `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run <the spec files>`
as its own Bash call, never through the suite's own command: no browser launches inside the sandbox, so that command is
excluded from it; it builds the app once (or reuses this checkout's own running server), serves it at the `e2e` block's
url, runs the specs and records the run under `.claude/work/e2e/` for verify. Rerun it after every change you want to
see in the browser; the recorded run is only read by verify when the tree has not changed since.

Finish by listing the files changed and the assumptions you recorded (their numbers and one line
each), then say: "Run /factory:verify."
