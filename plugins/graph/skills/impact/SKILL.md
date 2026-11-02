---
name: impact
description: Blast radius of changed files or symbols from the code graph: affected symbols, routes, tests (closest first) and CODEOWNERS owners. Use before editing a shared module, when scoping a contract, or to order which tests to run first.
argument-hint: "<path|kind:qualname@path> ..."
allowed-tools:
  - mcp__plugin_graph_graph__graph_impact
  - mcp__plugin_graph_graph__graph_deps
  - mcp__plugin_graph_graph__graph_owners
  - mcp__plugin_graph_graph__graph_search
  - mcp__plugin_graph_graph__graph_snapshot
  - Bash(uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph *)
  - Bash(git diff --name-only *)
---

Answer "what does this change touch?" from the derived graph. Read-only; nothing here changes ledger state, and the result is a proposal.

The allow rules match the literal command text: a shell variable, a pipe, a redirection or a second command on the same line is denied in unattended runs. Run each command exactly as written, on its own. Prefer the MCP tools; the CLI forms are the fallback, each starting with `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph`, written out in full.

In every CLI query command `--repo` is the repository NAME as stored (`owner/name` from the origin, else the directory name), never a path. Get it from `graph_snapshot` (or `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query snapshots --json`, which lists every indexed repo) rather than guessing.

1. Targets: `$ARGUMENTS` (repo-relative paths, or symbol keys `kind:qualname@path`). With none, use the working tree's changed files: `git diff --name-only HEAD`.
2. Confirm a snapshot exists: MCP `graph_snapshot` (or CLI `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query snapshots --json`). If the repo has none, or HEAD is newer than the newest snapshot, run `/graph:index` first.
3. Call MCP tool `graph_impact` (server `graph`; the full tool name is `mcp__plugin_graph_graph__graph_impact`) with `paths` and/or `symbols`, `depth` (default 4), `repo` = this repository's name from step 2's row (always: without it the tool reads the newest snapshot of any repository, which may be another tree), and `sha` when the user named one. CLI equivalent:
   `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query impact [PATH|KEY ...] [--paths ...] [--symbols ...] [--depth N] [--repo NAME] [--sha S] --json`
4. For one symbol's neighbourhood use `graph_deps` (`symbol`, `depth`, `direction` out|in|both, `rels`) or `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query deps SYMBOL [--repo NAME] [--direction {out,in,both}] [--rel REL] --json`. To find a symbol first: `graph_search` / `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query search TEXT [--repo NAME] [--kind K] [--limit N] --json`. Ownership: `graph_owners` / `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query owners PATH... --repo NAME --json`.
5. Present, in this order: tests to run first (the list is ordered closest first), affected routes, owners to ask for review, then the other affected symbols. Quote the `unresolved` entries: paths or symbols the snapshot does not contain.

The test list is structural reachability, not a guarantee. Say that when recommending to skip anything.
