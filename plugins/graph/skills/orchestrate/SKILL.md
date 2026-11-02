---
name: orchestrate
description: Start the sdlc-graph orchestrator daemon for one feature in the background and say how to watch and stop it. Human-invoked only; it starts paid Claude sessions.
argument-hint: "<feature-slug> [--reviewer @handle] [--max-runs N] [--max-cost USD]"
disable-model-invocation: true
allowed-tools: Bash(uv run --project *), Bash(nohup uv run --project *), Bash(mkdir -p .claude/work/orchestrator), Bash(git rev-parse *), Bash(git status --porcelain), mcp__plugin_graph_graph__node_get, mcp__plugin_graph_graph__workflow_next, Read
---

The orchestrator walks every contract of a feature through the ledger's state machine: one fresh
`claude -p` session per step (`/sdlc:tests`, `:implement`, `:verify`, `:ship`) in the
contract's own worktree `<repo>-<slug>`, then gates (evidence, drift, frozen tests, ratchet,
conformance) before it moves the state. It stops at `in_review` with a draft PR per contract; a human
reviews and merges. Full description: `docs/orchestrator.md` in the plugin.

`SG` below is `uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph`.

1. Feature slug from `$ARGUMENTS`; no slug → stop and ask for one. Check it with MCP `node_get`
   (it must be a `feature` with `decomposes_into` contracts). For each contract, `workflow_next`:
   list the ones still `drafted` (a human readies them first; the loop will not) and any open
   decisions. Say all this before starting.
2. Run from the repository root (`git rev-parse --show-toplevel`). A dirty tree is fine: the daemon
   never edits this checkout, only the worktrees next to it.
3. Start it in the background, with the reviewer the human named (ask if none was given):

   ```bash
   mkdir -p .claude/work/orchestrator
   nohup uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph orchestrate <feature> \
     --repo "$PWD" --live --reviewer @<handle> [--max-runs N] [--max-cost USD] \
     > .claude/work/orchestrator/<feature>.log 2>&1 &
   ```

   `--live` is required: without it the default executor refuses to start real sessions. A second
   daemon on the same feature exits at once with `lock_held`.
4. Print, verbatim, how to watch and stop it:
   - progress, one line per tick: `tail -f .claude/work/orchestrator/<feature>.log`
   - the ledger as a timeline: `SG feed --feature <feature>`
   - what needs a human (P0 first): `SG escalations --feature <feature>`; answer one with
     `SG decision resolve <id> --choice retry|skip --actor @you`
   - per-run logs, prompts and settings: `.claude/work/orchestrator/<contract>/`
   - stop safely: `SG orchestrate <feature> --stop` (releases the lock; the daemon exits at its next
     tick; worktrees and any running session are left; the next start gates what they left)
5. Do not run the daemon in the foreground of this session, do not answer its escalations for the
   human, and do not merge anything.
