---
name: index
description: Index a git commit of the current repo into the sdlc-graph code graph (full snapshot or delta) and verify it. Use before asking impact, deps or search questions, or after new commits land.
argument-hint: "[sha]"
allowed-tools: Bash(sdlc-graph *), Bash(uv run *), Bash(git rev-parse *), Bash(git rev-list *)
---

Index a commit into the derived code graph. The graph is written only by `index` and `delta`; no MCP tool writes it, so this runs through the CLI.

Run the CLI as `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph ...` (call it `SG` below). It needs `uv` installed and `DATABASE_URL` pointing at the graph's Postgres (default `postgresql://sdlc:sdlc@localhost:5435/sdlc`). If the first call exits 5, the database is down: tell the user, and stop.

1. Pick the commit: `$ARGUMENTS` if given, else `HEAD`. Resolve it with `git rev-parse --verify <sha>`; the checkout is only read.
2. List what is stored: `SG query snapshots --repo <name> --json`. `<name>` defaults to the origin owner/name or the directory name; pass `--name N` to `index` to choose one.
3. Choose the cheapest correct path:
   - no snapshot for this repo, or the stored parent is not an ancestor: `SG index --repo . --sha <sha> --json`
   - the newest stored snapshot is an ancestor of `<sha>`: `SG delta --repo . --from <stored-sha> --to <sha> --json` (stores `<sha>` as a delta that replays exactly)
4. Always finish with `SG verify-snapshot --repo . --sha <sha> --json`. Exit 0 means the stored rows equal a fresh extraction; exit 1 returns a diff, which you must show the user rather than hide.
5. Report: repo name, sha, snapshot kind (full or delta), and the counts from the result (symbols, edges, routes, tests, owners).

Rules: never run `db reset` or `db init` from this skill; never index a path other than the user's repo; if `index` fails, show the error as printed.
