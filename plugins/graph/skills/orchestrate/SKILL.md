---
name: orchestrate
description: Start the sdlc-graph orchestrator daemon for one feature in the background and say how to watch and stop it. Human-invoked only; it starts paid Claude sessions.
argument-hint: "<feature-slug> [--reviewer @handle] [--max-runs N] [--max-cost USD]"
disable-model-invocation: true
allowed-tools:
  - Bash(uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph *)
  - Bash(nohup uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph *)
  - Bash(nohup uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph *)
  - Bash(git rev-parse *)
  - Bash(git status --porcelain)
  - mcp__plugin_graph_graph__node_get
  - mcp__plugin_graph_graph__workflow_next
  - Read
---

The orchestrator walks every contract of a feature through the ledger's state machine: one fresh
`claude -p` session per step (`/sdlc:tests`, `:implement`, `:verify`, `:ship`) in the
contract's own worktree `<repo>-<slug>`, then gates (evidence, drift, frozen tests, ratchet,
conformance) before it moves the state. It stops at `in_review` with a draft PR per contract; a human
reviews and merges. Full description: `docs/orchestrator.md` in the plugin.

The allow rules match the literal command text: a shell variable, a pipe, a redirection or a second command on the same line is denied in unattended runs. Run each command exactly as written, on its own.

Every CLI command starts with `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph`; write it out in full, never as a shorthand or variable. For the daemon, `--repo` is a PATH to the checkout (an absolute path, not `.` and not `$PWD`); `feed`, `escalations` and the other ledger commands take no repo.

1. Feature slug from `$ARGUMENTS`; no slug → stop and ask for one. Check it with MCP `node_get`
   (it must be a `feature` with `decomposes_into` contracts). For each contract, `workflow_next`:
   list the ones still `drafted` (a human readies them first; the loop will not) and any open
   decisions. Say all this before starting.
2. Get the repository root with `git rev-parse --show-toplevel` and use the path it prints, typed
   out literally, as `--repo` below. A dirty tree is fine: the daemon never edits this checkout,
   only the worktrees next to it.
3. Start it in the background with the Bash tool's `run_in_background` option (not `&`, and not a
   redirection), with the reviewer the human named (ask if none was given), as one plain command:

   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph orchestrate <feature> --repo <absolute repo root> --live --reviewer @<handle> [--max-runs N] [--max-cost USD]
   ```

   `--live` is required: without it the default executor refuses to start real sessions. A second
   daemon on the same feature exits at once with `lock_held`. The daemon's own output stays in that
   background task; per-run logs are written under `.claude/work/orchestrator/<contract>/`.
   A human who wants a log file for the daemon starts it from their own terminal instead, with
   `nohup uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph orchestrate <feature> --repo <root> --live --reviewer @<handle> > .claude/work/orchestrator/<feature>.log 2>&1 &`
   (after `mkdir -p .claude/work/orchestrator`); do not run that line yourself, it has a redirection.
4. Print, verbatim, how to watch and stop it (each is its own command):
   - progress: read the background task's output, or the per-run logs below
   - the ledger as a timeline: `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph feed --feature <feature>`
   - what needs a human (P0 first): `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph escalations --feature <feature>`; answer one with
     `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph decision resolve <id> --choice retry|skip --actor @you`
   - per-run logs, prompts and settings: `.claude/work/orchestrator/<contract>/`
   - stop safely: `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph orchestrate <feature> --stop` (releases the lock; the daemon exits at its next
     tick; worktrees and any running session are left; the next start gates what they left)
5. Do not run the daemon in the foreground of this session, do not answer its escalations for the
   human, and do not merge anything.
