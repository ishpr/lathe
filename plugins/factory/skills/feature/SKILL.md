---
name: feature
description: Run a ticket end to end — impact, contract, tests, implement, verify — with every question asked up front by /factory:ticket, and the PR URL recorded as ledger evidence at the end. Refuses to start a contract with an empty decision or no defaults; after that it stops only for an escalation the contract did not anticipate. Use only when the user explicitly asks to run a whole ticket as a feature. Human-invoked.
argument-hint: "[--trivial] [--headless] <ticket text or id>"
disable-model-invocation: true
allowed-tools:
  - Skill
  - Read
  - Glob
  - Grep
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --ready *)
---
Ask before, never during: every question is asked when the contract is written, and the stages after
it run on the contract with nothing left to ask. Run the stages in order. Invoke each one with the
Skill tool (`factory:impact`, `factory:ticket`, `factory:tests`, `factory:implement`, `factory:verify`, `factory:feed`)
so its own `allowed-tools` apply; where the Skill tool is unavailable, read that SKILL.md and follow it.

Unless the branch is already this ticket's, `/factory:ticket` first creates `feature/<slug>` (nothing is written on `main`, and one ticket gets one branch); the later stages stay on it.

0. **Impact first (optional).** Pick out the repo paths the ticket names (files, modules, routes you can map to files
   with Glob or Grep) and invoke `factory:impact` with them; keep the affected routes, tests and owners and give them
   to step 1 for the contract's allowed paths. The graph answers "graph off" or "graph not configured" (say
   its hint as one line; headless, under Assumptions too), the skill fails, or the ticket names no paths: say so in one
   line and go on.
1. **Contract.** No `.claude/work/current.json`, or one for another ticket: run
   `/factory:ticket $ARGUMENTS`. It asks its questions in one batch and writes the contract only after
   the answers (headless: it takes its recommendations and marks them unconfirmed). A `REJECTED`, an
   `ESCALATION` or a quoted-back intake block ends the run here. A current.json for this ticket that
   already exists: use its contract as it is.
2. **Refuse to start an unready contract.** Run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --ready` before any other stage. Exit 1 means
   a Decision has no Value or the Defaults block is missing: do not start. Print, verbatim:

   "NOT STARTED: the contract is not ready to run unattended. Fill these in
   .claude/work/<slug>/contract.md, then run /factory:feature again:"

   followed by every `FILL` line it printed (each says exactly what to write; a missing Defaults block
   comes with the four standard lines to paste). Then stop. This is the only place this command refuses
   for something a human could have answered: stopping here costs nothing, stopping midway costs the run.
   `WARN` lines (no Capabilities block) and `NOTE` lines (decisions recorded as recommended, unconfirmed)
   do not stop the run; show them and carry on.
3. `/factory:tests` — the tests-only commit, then the tests are frozen.
4. `/factory:implement` — until the whole suite is green, without touching a frozen test.
   It never asks: ambiguity is settled by the contract's defaults and logged in
   `.claude/work/<slug>/assumptions.md`.
5. `/factory:verify` — until the stamp exists, every acceptance line passes and no high
   finding remains. It lists the assumptions in its summary.
6. STOP: show the Evidence section and the assumptions, and tell the human: "Run /factory:ship when
   you are happy with it." `/factory:ship` is human-invoked only (`disable-model-invocation`), so this
   command ends here.
7. **PR evidence, when you are resumed after the ship.** Only once a PR exists and you have its real URL (the human
   ran `/factory:ship`, or the URL is in the stage output): `/factory:ship` records `pr_url` itself when the graph is
   configured; if its output says it did not (the graph was down or not configured then, and is configured now),
   invoke the skill `factory:feed` with the contract slug, kind `pr_url` and the payload `{"url": "<that URL>"}`.
   Never invent a URL or payload. No PR yet: say "no pr_url to record yet". Then print the Evidence section and the
   PR URL, and remind the human that merging is theirs.

`/factory:deploy` is not part of this command; a reviewer merges first.

The one legitimate stop after step 2 is an **escalation**: something the contract did not anticipate
and its defaults cannot settle without contradicting a Decision — a path outside the allowed paths, a
capability the contract does not list, a frozen test that is wrong, a tier the guard says is too low,
anything touching money, security or data loss. The stage writes the question, with options and a
recommendation, to `.claude/work/<slug>/questions.md` and says `ESCALATION: <one line>`; report where
the run stopped and that question, and end. Do not improvise around a gate: no edits outside the
contract, no commits without a stamp, no baseline updates, no unfreezing tests.
