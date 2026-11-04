---
name: verify
description: The gate. Tests, ratchet (checkers, lint, types vs baseline), secret scan, agent-config scan, frozen tests, the contract's acceptance lines, then two independent reviewers over the diff and the recorded assumptions. Never asks. Stamps the tree so ship can commit.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Agent
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py")
  - Bash(git diff HEAD)
  - Bash(git ls-files --others --exclude-standard)
  - mcp__plugin_factory_graph__graph_impact
  - mcp__graph__graph_impact
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
  - mcp__plugin_factory_graph__contract_update
  - mcp__graph__contract_update
  - Bash(git diff --name-only HEAD)
  - Bash(python3 -m pytest *)
  - Bash(uv run pytest *)
  - Bash(uv run python -m pytest *)
  - Bash(uv run python3 -m pytest *)
  - Bash(poetry run pytest *)
  - Bash(poetry run python -m pytest *)
  - Bash(poetry run python3 -m pytest *)
  - Bash(pdm run pytest *)
  - Bash(pdm run python -m pytest *)
  - Bash(pdm run python3 -m pytest *)
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
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" *)
---
This conversation orchestrates and never executes: every step that does work runs in a subagent through the Agent tool; here you load the skill, plan the hand-offs, brief each subagent, collect what comes back, ask the human and summarise.
Here that means the graph calls (steps 0, 0a, 1a and 2: the MCP tools belong to this conversation and a subagent
has none), the reads that brief the subagents, the stage writes through workfile.py (assumptions.md,
questions.md, contract.md) and the summary. The gate (step 1: every test, e2e, verify and acceptance command),
the two reviews (step 3) and every fix (step 4) run in subagents. Steps 1 and 3 do not wait for each other:
launch them together, in one message. A step that needs a report waits for it; nothing else waits.
Before each hand-off say in one line what is handed off and why; when the subagents return say in one line what came back and what it means.
That one rule holds at every hand-off below: the fan-out, each fix, each rerun.
A question for the human is asked here, in the open: a subagent cannot ask.
This stage has none to ask (**Never ask** below): an escalation comes back in a subagent's report, and you write
it to questions.md.
Parallel subagents never edit the same file.
The gate and the reviewers edit nothing, so they run together; the fixer edits, so it runs alone: never two
fixers at once, and never a fixer beside a gate run.
A subagent refused by a hook reports the refusal; it does not work around it.
The allowed paths, the hooks and the never-ask rules hold inside a subagent exactly as they do here.

**Launching a subagent.** The Agent tool with a `subagent_type` (`factory:implementer`, `factory:reviewer` or
`factory:security-reviewer`), a `description` of a few words naming the hand-off (`verify: gate`; the hand-off
log records it; an implementer's description starts with the tag `[sdlc stage=verify node=<slug>]`, so the spawn guard
and the ledger record the hand-off under this stage, not the implementer's default) and the brief as the `prompt`. Never pass a `model`: the spawn guard pins it from the routing
policy by agent type. If the Agent tool answers that an agent runs in the background, end the turn with one line
saying what is awaited and carry on when the reports arrive; never sleep or poll. A subagent gets no context it
is not given, so every brief holds, in this order: the task in one sentence; the commands, copied from this skill
(a command that names `${CLAUDE_PLUGIN_ROOT}` keeps the path as this skill text shows it), one plain command per
Bash call: no chaining, no `echo "exit=$?"`, no redirection; the slug, the paths it may touch and the ones it
must not; the shape of its report; and, last, this line:
"You cannot ask a question: when something cannot be done, report it and stop."

Editing contract.md (Decisions, Follow-ups, Evidence and the like): Claude Code refuses Edit and Write under `.claude/`
in an unattended (`--permission-mode dontAsk`) run, whatever the allow rules say. Read the contract, Write the whole
updated text to `.sdlc-stage/<slug>/contract.md`, then run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/contract.md`
(it moves the file into place). If the edit guard denies the staged Write as out of scope, say so in one line
(its `.sdlc-stage/**` exemption is missing) and, in an interactive session only, Edit contract.md directly (Claude Code
asks the human to approve). Never use a heredoc, `echo`, `printf` or `cat` to write a file. Run each script as one plain command: no `echo "exit=$?"`, `$?` or redirection after it (under `dontAsk` that
fails the allow rule); the tool result already reports a non-zero exit.

**Never ask.** This stage runs on the contract. Ambiguity (which evidence a vague evidence plan means,
whether a low finding is worth fixing) is settled by the contract's `## Defaults when ambiguous` and
recorded as an assumption, exactly as `/factory:implement` says (Stage `verify`, appended to
`.claude/work/<slug>/assumptions.md` through `.sdlc-stage/` and workfile.py). The only stop is an
escalation the contract did not anticipate (implement's list), written to
`.claude/work/<slug>/questions.md` the same way, then `ESCALATION: <one line>`.
A subagent writes neither file: its assumption rows and its `ESCALATION:` line come back in its report, and you
number the rows on from the file's last `A<n>` and write both files here.

**Order.** Steps 0 and 0a here; then the brief inputs (below); then steps 1 and 3 together, in one message; when
the three reports are in, step 4, until the last gate run came after the last fix, is green, and no high finding
is open; then steps 1a and 2 from that last green gate report (a contract with no acceptance lines: post step 2's
`acceptance` evidence before 1a's `contract_transition`, since that is the first attempt step 2 means); then 5
and 6.

0. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), put the affected tests first. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   Call `graph_impact` with `paths` = the changed files (`git diff --name-only HEAD` plus
   `git ls-files --others --exclude-standard`, run here, one plain command each: the call needs them, and a
   subagent has no graph tools). The tests it lists go to the gate as its first command (step 1): the stack's
   test command narrowed to them, before the full run, so a break in the blast radius shows up in seconds. A
   failure there is the first thing the fixer gets (step 4). The full `verify` run in step 1 still happens; the
   impact set only orders the work and never replaces it. Say which tests go first and what `unresolved` held.
0a. **If the graph MCP is available**, keep the ledger's copy of the contract current: Read
   `.claude/work/<slug>/contract.md` and call `contract_update` with `slug` and its WHOLE text as `contract_md` (a
   human may have confirmed a decision or added a path since `/factory:ticket`). `unchanged` or `updated`: say which,
   with the `changed` fields. A non-empty `blocks.missing`: print it and say the contract needs those blocks. A
   refusal because the node is past `ready` is expected once `/factory:tests` froze the tests (from `tests_frozen` the
   paths and tests are frozen): say so in one line and carry on. Optional like every graph step: without the tools
   skip it; an error is one line, never a block.

**Brief inputs** (here, before the fan-out). Read `.claude/work/current.json` (slug, `allowed_paths`,
`frozen_tests`), the contract and `.claude/work/<slug>/assumptions.md` if it exists, and run `git diff HEAD` and
`git ls-files --others --exclude-standard` here, one plain command each (step 0 may have run the second already).
That is how the reviewers get the diff: you gather it and paste the output, as it printed, into both review
prompts (step 3). The gate does not return it: the reviewers start in the same message as the gate, so they
cannot wait for its report, and the gate edits nothing, so the tree it checks is the tree they read.

1. **The gate**: one `factory:implementer`, description `[sdlc stage=verify node=<slug>] verify: gate`. It runs the checks and reports them; it
   fixes nothing. Its brief, in this order:
   - Task: run the gate on this tree and report what it printed; edit nothing.
   - Commands, each its own Bash call, in this order:
     - When step 0 listed tests: the stack's test command (`commands.test` in `.claude/sdlc.json`) narrowed to
       them (name them).
     - When `.claude/sdlc.json` has an `e2e` block, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run`
       (no file: the whole suite) as its own Bash call: no browser launches inside the sandbox, so that command
       is excluded from it; it builds the app once, serves it at the block's url (or reuses this checkout's own
       running server; a port held by another checkout is exit 2, never tested), runs the suite and records the
       run under `.claude/work/e2e/`.
     - Then run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py"`. It runs, in order and all of them even
       after a failure: the stack's test command, the e2e step (the recorded run; red when there is none, when
       the tree changed since it, or when it was narrowed to some files), the ratchet against
       `.claude/baseline.json`, a secret scan of the diff, the agent-config scan, `freeze_tests.py --check` (a
       frozen test changed → red; a frozen e2e spec that did not run in the suite → red), and the contract's
       `## Acceptance` lines through `acceptance.py`. Its last line names what failed. It writes
       `.claude/work/last_verify.json` only when everything is green.
     - When that last line is red: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --root .`, to show
       the acceptance lines on their own.
     - Only when the brief names a base URL (step 5): `bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" <base url>`;
       a refused connection means no server listens there: report it, never start one.
   - Paths: it edits and writes no file; the scripts keep their own records under `.claude/work/`. Never edit
     `.claude/baseline.json`, the frozen tests or the plugin's scripts to make it green; a frozen test that is
     wrong goes to a human (`/factory:implement` says how). **Fix nothing**: a red step is reported as it
     printed, never repaired; the fix is its own hand-off (step 4).
   - Report, each part as it printed, never a summary: the narrowed run's command and summary line; the e2e
     line (`e2e: N passed, M failed, K skipped; server started|reused at <url>`) and the reason per failing
     spec, when the e2e block exists; the test command and its summary line; the acceptance table verify
     printed (command, expected, got, PASS/FAIL) and its `ACCEPTANCE passed=N failed=M` line; the ratchet
     line; the `frontend` step's four lines when verify printed one; when verify found no acceptance lines in
     the contract, each frozen test named after a criterion (`AC1: ...`) and whether it passed; for each red
     step its failing lines (test ids and assertions, new ratchet keys, scan hits, frozen-check lines), which
     the fixer works from; the smoke output when it ran; and verify's last line.
   - The line "You cannot ask a question: when something cannot be done, report it and stop."
   Red → the fixer (step 4), then this gate again with the same brief, its first command the tests the fixer
   named. A fix after a red e2e step means the e2e script runs again before verify does (the tree changed);
   the brief's order does that on every run.
1a. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), and step 1 wrote `.claude/work/last_verify.json` (it only does when all green), post
   the stamp of the last green gate run (**Order**), reading that file here with the Read tool: `evidence_add` kind `verify_stamp`, payload from that file: `frozen_ok`, `acceptance`
   (`passed`, `failed`), `db_backend`, `tool_versions`, and `ratchet` as `{"new": 0, "known": <the known
   count in the ratchet line verify printed, 0 if none>}` (new is 0 because the stamp is only written
   when the ratchet is clean). Then `contract_transition` to `verified` with that evidence id. If the
   guard answers `missing`, show the list and carry on. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   Also post the assumptions as evidence (the reviewer and the conformance gate read them): `evidence_add` kind `assumptions`, payload
   `{"path": ".claude/work/<slug>/assumptions.md", "count": <rows>, "items": [{"id": "A1", "stage": ...,
   "where": ..., "ambiguity": ..., "default": ..., "choice": ..., "decision": ...}]}` read from that file, one
   key per table column (`decision` is the Decision column: `none`, `D1` or `#12`, never left out: the
   conformance gate reads it) (no file: count 0, items `[]`). A server that does not know the kind answers with an error: say so in one line and carry on.
2. Report the acceptance table verify printed (command, expected, got, PASS/FAIL) and its
   `ACCEPTANCE passed=N failed=M` line, from the gate's report. To rerun only those lines while fixing (the fixer's brief carries it):
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --root .` (REC-09). A contract with no
   acceptance lines: say so; that is a finding against the contract, not a pass. When the graph MCP is
   available, record what stands in for them on this first attempt, not after a refusal: the frozen tests
   named after the criteria (`AC1: ...`) are the executable acceptance there is, so post `evidence_add` kind
   `acceptance` with `{"passed": <frozen tests that passed>, "failed": <that failed>, "source": "frozen tests",
   "note": "the contract has no runnable acceptance lines"}`; the ledger's `verified` guard counts a passed
   test per criterion, and two console contracts were refused twice each before their sessions did this.
3. **The two reviewers**, in the same message as step 1: the `factory:reviewer` and `factory:security-reviewer`
   agents (descriptions `verify: diff review` and `verify: security review`). They
   have no shell, so give each in its prompt: the contract, the output of `git diff HEAD` pasted verbatim (never a summary of it, focus hints or your own account of the change: a reviewer handed the author's summary reviews the summary), the
   list from `git ls-files --others --exclude-standard` (they Read those files) and
   `.claude/work/<slug>/assumptions.md` if it exists, with the instruction that an assumption which
   contradicts a Decision's Value is a **high** finding (it fails conformance), and, last, the line
   "You cannot ask a question: when something cannot be done, report it and stop." Do not pass a
   `model` on the Agent call. Each returns a criterion table, at most 10 findings as
   `severity | file:line | what | fix`, and a "Not reviewed" section (REC-53).
   If the Agent tool answers that they run in the background, end your turn with one line saying you are waiting for the gate and the two reviews and carry on at step 4 once all three reports are in; never sleep or poll.
4. **Fix, then look again.** What gets fixed: the gate's red steps (a failure in step 0's tests first), every
   **high** finding, and a lower finding that takes under five minutes. A lower finding that takes longer is
   listed under **Follow-ups** in contract.md. A high finding that is wrong (the code it names does not do what
   it says) gets a line under **Review notes** in contract.md saying why it is not a problem. You write both
   here, through the stage. Build every path you write from the repository root; never type an absolute path
   outside it. Everything to fix goes to ONE `factory:implementer`, description `[sdlc stage=verify node=<slug>] verify: fix`: never two at
   once, and never a fix made here. Its brief, in this order:
   - Task: fix these findings and red steps inside the allowed paths, and nothing else.
   - What to fix: each finding as the reviewer wrote it (`severity | file:line | what | fix`) and the gate's
     failing lines as they printed.
   - Commands, one plain command per Bash call: the stack's test command (`commands.test` in
     `.claude/sdlc.json`) narrowed to the tests that pin the findings and the ones that failed, and
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --root .` to rerun only the acceptance lines.
     The gate reruns the whole check after it.
   - Paths: it edits only the files the findings and the red lines name, each inside the contract's
     `allowed_paths` (list them), and makes a new file only for the test that pins a finding, under the test
     paths (`paths.tests` in `.claude/sdlc.json`). The test files in current.json `frozen_tests` are read-only:
     it does not edit, rename, skip or delete them, and does not add a conftest, fixture or override that
     changes what they assert; a frozen test that is wrong is an ESCALATION line naming the assertion and why.
     It never edits `.claude/baseline.json` or the plugin's scripts, and builds every path it writes from the
     repository root.
   - Report: the files changed (a new test file named as such); each finding and red step, fixed (file:line)
     or not and why; the tests it ran and their summary lines; each assumption as a row
     `| A? | verify | <where> | <ambiguous> | <default applied> | <what I did> | <decision> |`
     (you number them); an ESCALATION line when a path outside its list, a frozen test or a capability is
     needed; a hook refusal, verbatim.
   - The line "You cannot ask a question: when something cannot be done, report it and stop."
   When it returns, its assumption rows go into assumptions.md and an ESCALATION into questions.md (**Never
   ask**); an escalation ends the stage: `ESCALATION: <one line>`. Otherwise launch the gate again (step 1's
   brief, its first command the tests the fixer named) and give the reviewer that raised each high finding a
   second look: the contract, its finding, the new `git diff HEAD` of the fixed files, verbatim (run it here
   again and paste those files' hunks), and a fresh `git ls-files --others --exclude-standard` list when the
   fixer made a file (a new test is untracked, so the diff does not show it). The gate edits nothing and the
   reviewers only read: launch them together, in one message. Loop until the last gate run came after the last
   fix, is green, and no high finding is open. Carry both "Not reviewed" sections into the
   Evidence so the human reviewer knows what nobody looked at.
5. Append an **Evidence** section to contract.md, here, through the stage (the contract.md paragraph above), from
   the gate's last green report and the two reviews: the test command and its summary line, the e2e line verify
   printed when the repo has an `e2e` block (`e2e: N passed, M failed, K skipped; server started|reused at <url>`, and the
   reason per failing spec), the
   ratchet line, the acceptance table, both reviewers' verdicts and "Not reviewed" lists, an
   **Assumptions** list (each row of assumptions.md, one line, or "None"), and — for
   anything with an HTTP or UI surface — the output of a real request or a screenshot
   (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" <base url>` is there for rate limits; the gate runs it (step 1) only against a server already listening at a URL the contract names, so put that URL in the gate's brief, since this stage cannot start one, and otherwise name under Not reviewed the request that would show it. A new smoke script is implement's to write, not the gate's).
   **Frontend (a Foldkit app, `"frontend"` in `.claude/sdlc.json`):** verify printed a `frontend` step
   and the stamp holds a `frontend` block. Put its four lines in the Evidence: stories (Message tags
   sent by a story test, covered/tags), scenes (scene files that assert `toExist`, with_exist/files),
   api (Command calls that go through the API call table, matched/calls) and oxlint (findings, or why
   it did not run). The step is red only for `fe-story` / `fe-scene` keys that are new versus the
   baseline: add the story that sends the tag, or give the element a role or label (the fixer's work, step 4); never re-baseline.
   For the live evidence, the contract's `- mcp: foldkit_dispatch_messages [...]` lines with their
   `=> model.<path> == <value>` results stand in for the screenshot (they need the dev-only relay,
   `FOLDKIT_DEVTOOLS_URL` on localhost). `docs/frontend-contracts.md` explains each key.
6. Summarise: "Verified at <fingerprint>. Assumptions: <N> — <A1: one line>; <A2: one line>; … (the
   reviewer confirms or reverses each). Decisions recorded as recommended, unconfirmed: <their numbers, or
   none>. Run /factory:ship." If a fix removed the stamp, say so and loop.
