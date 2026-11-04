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
## How this skill runs

This conversation orchestrates and never executes: every step that does work runs in a subagent through the Agent tool; here you load the skill, plan the hand-offs, brief each subagent, collect what comes back, ask the human and summarise.
What stays here: step 0 (the graph tools belong to this conversation, a subagent has none); reading
`.claude/work/current.json`, the contract and the frozen list; splitting the work (step 1); writing assumptions.md,
questions.md and any contract.md edit from what the subagents report; the finish. Every code edit and every test run
happens in a `factory:implementer`: one per concern (step 2), then one runner (step 3). Headless or not, the hand-offs
are the same.

- **Hand-offs.** One Agent call per hand-off: `subagent_type` `factory:implementer`, a `description` of a few words naming the hand-off (the hand-off log records it), and the brief as the `prompt`. Do not pass a `model` on the Agent call: the spawn guard pins it from the routing policy by agent type.
- **Parallel.** The implementers of step 2 own separate files and do not wait for each other: launch them together, in one message. The runner waits for every one of them; the owners of one round of step 4 go out together the same way.
- **Narrate.** Before each hand-off say in one line what is handed off and why; when the subagents return say in one line what came back and what it means.
- **Background.** If the Agent tool answers that the agents run in the background, end your turn with one line saying what is awaited and carry on when every report has arrived; never sleep, poll or read an output file.
- **Questions.** A question for the human is asked here, in the open: a subagent cannot ask. This stage asks none (Never ask, below): its one stop is an escalation, which this conversation writes to questions.md from a subagent's `ESCALATION:` line.
- **One owner per file.** Parallel subagents never edit the same file. Every file the change needs has exactly one owner; a file two concerns need goes to one of them, and the other reports the seam it needs there instead of editing it. The e2e script keeps one record and serves one app, so no two subagents run it at once.
- **Hooks.** The allowed paths, the hooks and the Never ask rules apply inside a subagent exactly as here. A subagent refused by a hook reports the refusal; it does not work around it.
- **Briefs.** A subagent gets no context it is not given. Every brief holds, in this order: the task in one sentence; the commands, copied from this skill with the plugin path as this text shows it (no `echo "exit=$?"`, no redirection); the files it may touch and the ones it must not; what to report, in the shape its step gives; and the line "You cannot ask a question: when something cannot be done, report it and stop."

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

Read `.claude/work/current.json` and the contract. The contract's tests are the files in current.json `frozen_tests`;
`.claude/work/<slug>/failing_first.json` records why each one failed when it was frozen. Read the frozen tests far
enough to see which criterion each pins and what it imports. Running them is each implementer's first command
(step 2), not yours: give each the list of failures it starts from.

**Never ask.** Every question was asked when the contract was written; this stage runs on the
contract. When something is ambiguous, do not ask and do not stop (rules 1 and 2 go word for word into every brief;
rule 3 is this conversation's, from the rows the subagents report):
1. If a Decision's Value answers it, follow the Value.
2. Otherwise apply the contract's `## Defaults when ambiguous` in order (no such section: the standard
   four — the smallest change that satisfies the acceptance; the nearest existing pattern; the option
   the Decisions imply; the reversible option — and say in the assumption that the contract had none).
3. Record it in `.claude/work/<slug>/assumptions.md` and carry on. Read the file if it exists, Write the
   whole text with the new rows (every row the subagents reported) appended to `.sdlc-stage/<slug>/assumptions.md`, then run
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
command and the allow rule that would have covered it. Inside a subagent the escalation is its report: an
`ESCALATION: <one line>` with the question, its options and its recommendation, and it stops there; this conversation
writes questions.md from it.

While implementing (these rules, and the end-to-end paragraph after them, go word for word into every brief of step 2):
- Edit only inside `allowed_paths`. The edit guard blocks the rest; needing a path outside them is an
  escalation — the contract changes first, the code second. A path the guard says "forces tier high" means the
  contract's tier is wrong: escalate so `/factory:ticket` is re-run. Inside the allowed paths, edit only the files
  your brief gives you: a change you need in any other file is a seam, reported as
  `SEAM: <file>: <what you need there>`, and the file is left alone.
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
  assumptions.md (a subagent reports it as a row; this conversation writes the file).
- A schema change goes through a migration under the allowed paths, then `/factory:migrate`
  to render its SQL for a human; never apply it here.
- Loop: run the contract's tests your brief names → smallest change → rerun, until green.

A frozen end-to-end spec (Playwright) runs through `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run <the spec files>`
as its own Bash call, never through the suite's own command: no browser launches inside the sandbox, so that command is
excluded from it; it builds the app once (or reuses this checkout's own running server), serves it at the `e2e` block's
url, runs the specs and records the run under `.claude/work/e2e/` for verify. Rerun it after every change you want to
see in the browser; the recorded run is only read by verify when the tree has not changed since. One subagent at a
time runs it: the one implementer whose files the spec drives (its brief alone names it), and the runner, last.

1. **Split the work by concern.** From the contract's allowed paths and its Defaults (where a new file goes), the
   frozen tests and the skeleton modules the tests stage left (their bodies only raise), list every file the change
   needs, then split the list by concern (the store, the dependency wiring, the response headers, the config, ...)
   into `factory:implementer` subagents whose files do not overlap: one owner per file, a whole concern per owner. A
   file two concerns need goes to one of them; the other's brief names the seam it needs there (a name, a signature, a
   registration) and says to report it rather than edit it. Name each seam in both briefs (what one provides, what
   the other may count on), so neither waits for the other. A change that is one concern is one implementer; when it
   splits, prefer several narrow implementers to one broad one.
2. **Brief the implementers, then launch them together, in one message** (description `implement: <concern>`). Each
   brief holds, in this order:
   - the task in one sentence: the criteria it makes pass and the concern it owns;
   - the commands: the stack's test command (`commands.test` in `.claude/sdlc.json`) narrowed to its frozen tests,
     run first (the failures it starts from) and after every change; the e2e script only in the one brief whose
     files a frozen spec drives; each command as its own Bash call;
   - its files: the paths it owns (a new file by its path, or a directory only it writes in); not its to touch: every
     other path, the other implementers' files, the frozen tests, current.json, contract.md, assumptions.md and
     questions.md;
   - its criteria, word for word, with the frozen tests that pin them and the failures they start from; the Decisions
     rows and the `## Defaults when ambiguous` lines that bear on them; its seams;
   - the rules, word for word: Never ask rules 1 and 2, the escalation paragraph, the While implementing rules and
     the end-to-end paragraph, with the list of `.claude/rules/sdlc-*.md` files for its paths;
   - the report: the files changed; each assumption as a row
     `| A? | implement | <where> | <what was ambiguous> | <default applied> | <what I did> | <decision> |` (this
     conversation numbers them); its tests passing and failing, by name (a test that fails only for a seam another
     implementer provides says so, and the seam is left to its owner); each `SEAM:` line; `ESCALATION: <one line>`
     with the question, its options and its recommendation, for anything on the escalation list, after which it
     stops; a hook refusal or a denied command, verbatim, with the allow rule that would have covered it;
   - the line "You cannot ask a question: when something cannot be done, report it and stop."
3. **The runner.** When every implementer has reported, launch one `factory:implementer` (description
   `implement: run the suite`) that edits no file and fixes nothing. Its commands, each as its own Bash call: the
   contract's tests (current.json `frozen_tests`), with the stack's test command narrowed to them; the whole suite
   with the stack's test command (`commands.test` in `.claude/sdlc.json`; for Node `pnpm test`, `npm test`, `yarn test`
   or `bun test`); `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/freeze_tests.py" --check`; and, when `frozen_tests`
   holds an end-to-end spec, last, the e2e script on the spec files (the end-to-end paragraph above). Its report: each
   command's summary line; each failing test with its name, the assertion or error, and the app file and line its
   traceback points at; the `--check` result; the e2e line; the line "You cannot ask a question: when something
   cannot be done, report it and stop."
4. **Failures go back to their owner.** A failing test goes back to the subagent that owns the file it fails in (the
   app file the runner named; a failure in a file nobody owns goes to the owner whose change it exercises), and a
   `SEAM:` line goes to the owner of its file (a file nobody owns yet goes to one owner, the one that asked for it
   when no other concern needs it): one more Agent call, `factory:implementer` (description
   `implement: fix <concern>, round <n>`) with that owner's brief and the failures and seams pasted verbatim. The
   owners of one round are launched together, in one message (their files do not overlap); then the runner again.
   At most three rounds: a test still red after the third, or a fix that needs a path outside `allowed_paths`, is an
   escalation (the failing tests, what each round changed, the options, your recommendation). A failed
   `freeze_tests.py --check` means a frozen test changed: an escalation at once.
5. **Record what came back.** Merge every assumption row the subagents reported, every round's, into assumptions.md
   (Never ask, rule 3). A row that contradicts a Decision's Value is no assumption: it goes back to its owner like a
   failure, with the Decision row pasted. An `ESCALATION:` line or a reported hook refusal: once every subagent
   launched with it has returned, record their rows, then write questions.md (the escalation paragraph above), say
   `ESCALATION: <one line>` and stop.

Finish, once the runner reports the contract's tests and the whole suite green, the `--check` clean and no seam is
open, by listing the files changed (from the reports; a migration among them goes to `/factory:migrate` for its SQL)
and the assumptions you recorded (their numbers and one line each), then say: "Run /factory:verify."
