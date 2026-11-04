---
name: implement
description: Make the contract's tests pass inside the allowed paths, with the /tests files frozen. Never asks — ambiguity is settled by the contract's defaults and logged as an assumption. Hooks lint, run the stack's checkers and keep the change in scope.
allowed-tools:
  - Agent
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

# Implement

## Goal

Make the contract's frozen tests and the whole suite pass by changing only code inside the allowed paths, without
touching a frozen test and without asking: ambiguity is settled by the contract and logged as an assumption.

## Inputs

- `.claude/work/current.json` (slug, `allowed_paths`, `frozen_tests`) and `.claude/work/<slug>/contract.md`
  (criteria, Decisions, `## Defaults when ambiguous`, `## Capabilities`).
- `.claude/work/<slug>/failing_first.json`: why each frozen test failed when it was frozen.
- The frozen tests (read far enough to see which criterion each pins and what it imports), the skeleton modules the
  tests stage left (their bodies only raise), `.claude/rules/sdlc-*.md` for the paths touched, and
  `.claude/sdlc.json` (`commands.test`, the `e2e` block).
- The ledger, when the graph tools are in your tool list: `workflow_next` with the slug, before anything else. State
  `tests_frozen`: `contract_transition` to `implementing` (no guard) and ask again. Proceed only at `implementing`;
  any other state (`drafted`, `ready`, `verified`, `in_review`, an unknown slug) is the wrong step: print the state,
  `command` and `missing_evidence` it returned, and stop. Without the tools skip it; an error is one line.

## Outputs

- **The change**: code inside `allowed_paths` that makes the frozen tests pass for the behaviour, not the tests'
  literals. A schema change is a migration under the allowed paths, handed to `/factory:migrate` for its SQL, never
  applied here.
- **Green evidence**, from this tree: the contract's tests (current.json `frozen_tests`) with the stack's test command
  narrowed to them; the whole suite with the stack's test command; and
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --check` clean. A frozen end-to-end spec (Playwright)
  runs through `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run <the spec files>` as its own Bash command, never
  the suite's own command (no browser launches inside the sandbox, which excludes that exact command); it records
  the run under `.claude/work/e2e/` for verify, which reads it only while the tree is unchanged since.
- **`.claude/work/<slug>/assumptions.md`**: read it if it exists and keep every row an earlier stage wrote. A new file
  starts with `# Assumptions: <slug>` and the header
  `| # | Stage | Where | What was ambiguous | Default applied | What I did | Decision |`; rows are numbered `A1`,
  `A2`, ... across stages; Stage is `implement`; Default applied is the default's number or per-contract line;
  Decision is the Decisions row it relies on (`D1`, or `#12` from the ledger) or `none`, never blank. Written before
  the stage ends.
- **`.claude/work/<slug>/questions.md`** on an escalation: the question, its options and a recommendation; then
  `ESCALATION: <one line>`, and the stage stops.
- **The report**: the files changed (a migration among them goes to `/factory:migrate`), the assumptions recorded
  (number and one line each), then "Run /factory:verify."

## Guards

- **Never ask.** Every question was asked when the contract was written. When something is ambiguous: (1) a
  Decision's Value that answers it is followed; (2) otherwise the contract's `## Defaults when ambiguous` apply in
  order (none in the contract: the standard four, the smallest change that satisfies the acceptance, the nearest
  existing pattern, the option the Decisions imply, the reversible option, and the assumption says the contract had
  none); (3) the choice is recorded in assumptions.md and the work carries on. An assumption never contradicts a
  Decision's Value.
- **Escalation**, the one stop: when the only way forward would contradict a Value, or the question is one no default
  may settle: a path outside `allowed_paths`, a capability `## Capabilities` does not list, a frozen test that is
  wrong, a path the guard says forces tier high (the contract's tier is wrong: `/factory:design` is re-run), money,
  security, data loss. A denied tool call you did not expect is one too: name the command and the allow rule that
  would have covered it. A test still red after three rounds of fixes is one too.
- **Scope.** Edit only inside `allowed_paths`; the edit guard blocks the rest, and the contract changes first, the
  code second.
- **Frozen tests are read-only**: never edit, rename, skip or delete one, and never add a conftest, fixture or
  override that changes what it asserts. A wrong one is an escalation naming the assertion and why, asking a human to
  run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --unfreeze <path>` (REC-07). A failed `--check` is
  an escalation at once.
- **Decisions are a human's.** Never add or change a Decisions row; what you chose is an assumption.
- **The code.** No branch on a test's input value and no constant copied from a test into app code. New state goes
  behind an interface with a real store and an in-memory fake; time is injected. Delete what you replace; add nothing
  "for later". The post-edit hook runs the formatter and the ratchet on every saved file: fix what it reports.
- **Work files.** assumptions.md, questions.md and contract.md edits are written whole with the Write tool to
  `.sdlc-stage/<slug>/<file>`, then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/<file>`
  (Claude Code refuses Edit and Write under `.claude/` in an unattended run). If the edit guard denies the staged
  Write as out of scope, say so (its `.sdlc-stage/**` exemption is missing); only in an interactive session, Edit
  contract.md directly so Claude Code asks the human. Never a heredoc, `echo`, `printf` or `cat` to write a file, and
  never `echo "exit=$?"` or a redirection after a script: under `dontAsk` that fails the allow rule.
- **Subagents.** A subagent cannot ask: its choices come back as assumption rows (this conversation numbers and
  writes them; a row that contradicts a Value goes back as a failure), its stop as an `ESCALATION:` line, and a change
  it needs in a file it does not own as `SEAM: <file>: <what you need there>`, with the file left alone. A subagent
  refused by a hook reports the refusal; it does not work around it. Parallel subagents never edit the same file.
  Every file has one owner, and one subagent at a time runs the e2e script.

## Hints (non-binding)

- What worked before: split the files by concern (the store, the wiring, the headers, the config) into
  `factory:implementer` subagents with no file in common, in parallel, each starting from its tests' failures and
  looping test, smallest change, test; then one runner that edits nothing runs the contract's tests, the suite,
  `--check` and the e2e script; failures and `SEAM:` lines go back to the owner of the file. Prefer several narrow
  implementers to one broad one, and name each seam in both briefs.
- An implementer needs, verbatim: its criteria and frozen tests, the failures it starts from, the Decisions and
  Defaults that bear on them, the files it owns, and the guards above.
