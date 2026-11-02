---
name: init
description: Set this repository up for the SDLC working agreement — detect the stack, write .claude/sdlc.json, rules, a CI gate and today's debt baseline. Run once per repo, before any other sdlc command.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo] [--headless]"
allowed-tools:
  - Read
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
---
Initialise this repository for the working agreement. Arguments: $ARGUMENTS

The run is **headless** when the arguments hold `--headless` or the prompt says no human will answer (an
orchestrated or `claude -p` run); then nothing is asked: steps 1 and 3 say what to do instead. Interactive,
the questions below are the "ask before" moment: asked once, here, never during later work.

1. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --dry-run $ARGUMENTS` and show the
   plan: detected stack, app and test paths, the files it will create, and what it will append to
   CLAUDE.md. If it reports several stacks or none, ask which and rerun with `--stack`.
   **Headless:** if it reports several stacks or none, print `ESCALATION: stack ambiguous: <list>; rerun with --stack <one>`
   (`<list>` is the stacks it named, or `none`) and stop with nothing written.
2. Run it for real: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" $ARGUMENTS`.
   It writes `.claude/sdlc.json`, `.claude/rules/sdlc-*.md`, `.claude/work/`, a PR template, a
   CI workflow (`.github/workflows/sdlc-gate.yml`), gitignore lines, the working-agreement section
   in CLAUDE.md, and records the current violations as `.claude/baseline.json`. Nothing is committed.
3. Open `.claude/sdlc.json` and check: app paths point at the code, test paths at the tests, the
   test command is the one the team runs, and `paths.protected` lists anything humans own (generated
   files, a dead-code sink). Ask the human to confirm or change these; do not guess.
   **Headless:** do not ask. Accept the detected app paths, test paths and test command and the empty protected
   paths, and print one `Assumptions:` block that lists them (app paths, test paths, test command, protected paths),
   ending with the line `confirm in .claude/sdlc.json`.
4. Report the baseline numbers by check: copy the `baseline by check:` line step 2 printed (keys, violations,
   and the count per key prefix, from `.claude/baseline.json`); do not count them again with a shell pipe. No
   such line (no baseline was recorded): say so and why, from step 2's output. Then say plainly which checks this stack gets: Python has route auth, module state and test
   quality; Node, Go and Rust have module state and test quality as text heuristics; lint and types
   come from the stack's own tools when they are installed.
5. Tell the human what to commit and what to do next: `/sdlc:onboard` to read the agreement,
   `/sdlc:audit` for the debt, `/sdlc:ticket <ticket>` to start work. Mention the
   escape hatch: `/plugin disable sdlc` turns every hook off.
