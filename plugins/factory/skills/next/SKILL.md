---
name: next
description: Ask the ledger which single step comes next for a contract, epic or feature, then run the one slash command it names. Use to advance work without guessing the state.
argument-hint: "[slug]"
allowed-tools:
  - mcp__plugin_factory_graph__workflow_next
  - mcp__graph__workflow_next
  - mcp__plugin_factory_graph__node_get
  - mcp__graph__node_get
  - Read
  - Bash(uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph *)
---

The ledger knows the state; do not infer it. If `workflow_next` answers `{"error": "graph off", ...}` or `{"error": "graph not configured", ...}`, there is no ledger here: say its `hint` as one line (`graph off: ...` when `graph.enabled` is false, `graph on but not reachable: run /factory:graph provision <provider> or set DATABASE_URL` otherwise; headless, record that line under Assumptions too) and suggest the stage command the contract's `stage` in `.claude/work/current.json` implies, without running it.

The allow rules match the literal command text: a shell variable or a redirection is denied in unattended runs. Run each command exactly as written.

1. Slug: `$ARGUMENTS`, else the `slug` in `.claude/work/current.json`. If neither exists, ask the user, or suggest `/factory:design`.
2. Call MCP `workflow_next` with the slug (full name `mcp__plugin_factory_graph__workflow_next`). CLI equivalent: `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph workflow next SLUG --json`.
3. Read the result: current state, next state, the `command`, the stage, tier and model, `missing` guards (evidence not yet recorded), and the Fable budget.
4. Act:
   - `missing` is non-empty: report what is missing; do not run the command, and do not try to fabricate evidence.
   - the step is `human_only` (approve, merge): tell the user it needs a human or CI; stop. The MCP server cannot do it, and you must not try through the CLI with a human's `--actor`.
   - `command` is null: nothing to run (done, or waiting on a human).
   - otherwise run exactly the one slash command named (for example `/factory:tests`), once, and no other. If the plugin that owns it is not installed, say so rather than improvising its steps.
5. When the stage's tier is `planner` (Fable) and the budget is spent, say so and use the next tier down only if the user agrees.
6. After the command finishes, call `workflow_next` again and report the new state in one line. Do not chain a second command.
