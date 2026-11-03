---
name: feed
description: Record evidence on a contract (test results, verify stamp, PR URL) through the guarded ledger writer, and move the node forward when the guard allows. Use after tests or verify have run.
argument-hint: "<slug> <kind> [json payload]"
allowed-tools:
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
  - mcp__plugin_factory_graph__node_get
  - mcp__graph__node_get
  - mcp__plugin_factory_graph__workflow_next
  - mcp__graph__workflow_next
  - mcp__plugin_factory_graph__run_start
  - mcp__graph__run_start
  - mcp__plugin_factory_graph__run_finish
  - mcp__graph__run_finish
  - Read
---

Feed the ledger what actually happened. Evidence you add is local and advisory; CI evidence is binding and is ingested by CI, never by you. Any graph tool that answers `{"error": "graph off", ...}` or `{"error": "graph not configured", ...}` means the graph is off or has no reachable, initialised database: say its `hint` as one line (`graph off: ...` when `graph.enabled` is false, `graph on but not reachable: run /factory:graph provision <provider> or set DATABASE_URL` otherwise; headless, record that line under Assumptions too) and stop (`graph_status` says which part is missing).

1. Slug and kind from `$ARGUMENTS` (kinds the guards look for include `tests_failing_first`, `tests_frozen`, `verify_stamp`, `acceptance`, `ratchet`, `pr_url`). Payload: JSON built from a real result you can point to (the verify output, the JUnit counts, the PR URL). If you have no such result, stop and say so; never invent a payload to satisfy a guard.
2. Call MCP `evidence_add` (`slug`, `kind`, `payload` as an object; full name `mcp__plugin_factory_graph__evidence_add`). A human's terminal equivalent is the CLI's evidence add subcommand (default source local; flags in `${CLAUDE_PLUGIN_ROOT}/docs/graph.md`); this skill allows MCP tools only, so do not run it.
3. Ask `workflow_next` what the next transition needs. If every guard is now satisfied, call `contract_transition` (`slug`, `to_state`, optionally `evidence_ids`, `reason`). A guard failure returns `{"ok": false, "missing": [...]}`: report it and stop.
4. `contract_transition` into `approved`, `merged`, `released` or `closed` is refused for this server's agent identity; that is the design. Tell the user a human must do it from their own terminal with the CLI's node transition subcommand and their own `@handle` as the actor, and do not attempt it.
5. To record a model run: `run_start` before and `run_finish` after (the PostToolUse hook already records Agent spawns, so do this only for work outside an Agent call).
