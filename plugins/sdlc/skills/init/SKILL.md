---
name: init
description: Set this repository up for the SDLC working agreement — detect the stack, write .claude/sdlc.json, rules, a CI gate and today's debt baseline. Run once per repo, before any other sdlc command.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo]"
allowed-tools:
  - Read
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
---
Initialise this repository for the working agreement. Arguments: $ARGUMENTS

1. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --dry-run $ARGUMENTS` and show the
   plan: detected stack, app and test paths, the files it will create, and what it will append to
   CLAUDE.md. If it reports several stacks or none, ask which and rerun with `--stack`.
2. Run it for real: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" $ARGUMENTS`.
   It writes `.claude/sdlc.json`, `.claude/rules/sdlc-*.md`, `.claude/work/`, a PR template, a
   CI workflow (`.github/workflows/sdlc-gate.yml`), gitignore lines, the working-agreement section
   in CLAUDE.md, and records the current violations as `.claude/baseline.json`. Nothing is committed.
3. Open `.claude/sdlc.json` and check: app paths point at the code, test paths at the tests, the
   test command is the one the team runs, and `paths.protected` lists anything humans own (generated
   files, a dead-code sink). Ask the human to confirm or change these; do not guess.
4. Report the baseline numbers by check (`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all | tail -1`)
   and say plainly which checks this stack gets: Python has route auth, module state and test
   quality; Node, Go and Rust have module state and test quality as text heuristics; lint and types
   come from the stack's own tools when they are installed.
5. Tell the human what to commit and what to do next: `/sdlc:onboard` to read the agreement,
   `/sdlc:audit` for the debt, `/sdlc:ticket <ticket>` to start work. Mention the
   escape hatch: `/plugin disable sdlc` turns every hook off.
