---
name: feature
description: Run a ticket end to end — contract, tests, implement, verify — with every question asked up front by /ticket. Refuses to start a contract with an empty decision or no defaults; after that it stops only for an escalation the contract did not anticipate.
argument-hint: "[--trivial] [--headless] <ticket text or id>"
disable-model-invocation: true
allowed-tools:
  - Read
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --ready *)
---
Ask before, never during: every question is asked when the contract is written, and the stages after
it run on the contract with nothing left to ask. Run the stages in order. Invoke each one with the
Skill tool (`sdlc:ticket`, `sdlc:tests`, `sdlc:implement`, `sdlc:verify`)
so its own `allowed-tools` apply; where the Skill tool is unavailable, read that SKILL.md and follow it.

On a protected branch `/sdlc:ticket` first creates `feature/<slug>` (nothing is written on `main`); the later stages stay on it.

1. **Contract.** No `.claude/work/current.json`, or one for another ticket: run
   `/sdlc:ticket $ARGUMENTS`. It asks its questions in one batch and writes the contract only after
   the answers (headless: it takes its recommendations and marks them unconfirmed). A `REJECTED`, an
   `ESCALATION` or a quoted-back intake block ends the run here. A current.json for this ticket that
   already exists: use its contract as it is.
2. **Refuse to start an unready contract.** Run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --ready` before any other stage. Exit 1 means
   a Decision has no Value or the Defaults block is missing: do not start. Print, verbatim:

   "NOT STARTED: the contract is not ready to run unattended. Fill these in
   .claude/work/<slug>/contract.md, then run /sdlc:feature again:"

   followed by every `FILL` line it printed (each says exactly what to write; a missing Defaults block
   comes with the four standard lines to paste). Then stop. This is the only place this command refuses
   for something a human could have answered: stopping here costs nothing, stopping midway costs the run.
   `WARN` lines (no Capabilities block) and `NOTE` lines (decisions recorded as recommended, unconfirmed)
   do not stop the run; show them and carry on.
3. `/sdlc:tests` — the tests-only commit, then the tests are frozen.
4. `/sdlc:implement` — until the whole suite is green, without touching a frozen test.
   It never asks: ambiguity is settled by the contract's defaults and logged in
   `.claude/work/<slug>/assumptions.md`.
5. `/sdlc:verify` — until the stamp exists, every acceptance line passes and no high
   finding remains. It lists the assumptions in its summary.
6. STOP: show the Evidence section and the assumptions, and tell the human: "Run /sdlc:ship when
   you are happy with it." `/sdlc:ship` is human-invoked only (`disable-model-invocation`), so this
   command ends here.

`/sdlc:deploy` is not part of this command; a reviewer merges first.

The one legitimate stop after step 2 is an **escalation**: something the contract did not anticipate
and its defaults cannot settle without contradicting a Decision — a path outside the allowed paths, a
capability the contract does not list, a frozen test that is wrong, a tier the guard says is too low,
anything touching money, security or data loss. The stage writes the question, with options and a
recommendation, to `.claude/work/<slug>/questions.md` and says `ESCALATION: <one line>`; report where
the run stopped and that question, and end. Do not improvise around a gate: no edits outside the
contract, no commits without a stamp, no baseline updates, no unfreezing tests.
