---
name: impact
description: Blast radius of changed files or symbols from the code graph: affected symbols, routes, tests (closest first) and CODEOWNERS owners. Use before editing a shared module, when scoping a contract, or to order which tests to run first.
argument-hint: "<path|kind:qualname@path> ..."
allowed-tools: mcp__plugin_graph_graph__graph_impact, mcp__plugin_graph_graph__graph_deps, mcp__plugin_graph_graph__graph_owners, mcp__plugin_graph_graph__graph_search, mcp__plugin_graph_graph__graph_snapshot, Bash(sdlc-graph *), Bash(uv run *), Bash(git diff --name-only *)
---

Answer "what does this change touch?" from the derived graph. Read-only; nothing here changes ledger state, and the result is a proposal.

1. Targets: `$ARGUMENTS` (repo-relative paths, or symbol keys `kind:qualname@path`). With none, use the working tree's changed files: `git diff --name-only HEAD`.
2. Confirm a snapshot exists: MCP `graph_snapshot` (or CLI `query snapshots`). If the repo has none, or HEAD is newer than the newest snapshot, run `/graph:index` first.
3. Call MCP tool `graph_impact` (server `graph`; the full tool name is `mcp__plugin_graph_graph__graph_impact`) with `paths` and/or `symbols`, `depth` (default 4), and `repo`/`sha` when the user named them. CLI equivalent:
   `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query impact [PATH|KEY ...] [--paths ...] [--symbols ...] [--depth N] [--repo R] [--sha S] --json`
4. For one symbol's neighbourhood use `graph_deps` (`symbol`, `depth`, `direction` out|in|both, `rels`) or `sdlc-graph query deps SYMBOL [--direction {out,in,both}] [--rel REL]`. To find a symbol first: `graph_search` / `query search TEXT [--kind K] [--limit N]`. Ownership: `graph_owners` / `query owners PATH... --repo R`.
5. Present, in this order: tests to run first (the list is ordered closest first), affected routes, owners to ask for review, then the other affected symbols. Quote the `unresolved` entries: paths or symbols the snapshot does not contain.

The test list is structural reachability, not a guarantee. Say that when recommending to skip anything.
