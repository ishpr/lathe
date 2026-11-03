---
name: index
description: Index a git commit of the current repo into the sdlc-graph code graph (full snapshot or delta) and verify it. Use before asking impact, deps or search questions, or after new commits land.
argument-hint: "[sha]"
allowed-tools:
  - Bash(uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} sdlc-graph *)
  - Bash(git rev-parse *)
  - Bash(git rev-list *)
  - Bash(git config --get remote.origin.url)
---

Index a commit into the derived code graph. The graph is written only by `index` and `delta`; no MCP tool writes it, so this runs through the CLI.

The allow rules match the literal command text: a shell variable, a pipe, a redirection or a second command on the same line is denied in unattended runs. Run each command exactly as written, on its own.

Every CLI command below starts with `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph`. Write it out in full each time; do not define a shorthand or a variable for it. It needs `uv` installed and `DATABASE_URL` pointing at the graph's Postgres (default `postgresql://sdlc:sdlc@localhost:5435/sdlc`). If the first call exits 5, the database is down: tell the user, and stop. The first call may take longer while `uv` creates or syncs the plugin's environment (a `.venv` under the plugin root, packages from the uv cache); that is normal.

`--repo` means two different things, so keep them apart:
- `query snapshots --repo` takes the repository NAME: `owner/name` from the origin URL when there is a remote, else the directory name of the checkout. It is not a path.
- `index`, `delta` and `verify-snapshot` take `--repo` as a PATH to the checkout. Use `.` (the session's working directory, which must be the repository root).

Steps, one command per Bash call:

0. Run `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph status --json`. `enabled` false: say `graph off: the graph
   steps are skipped` and stop. `configured` false otherwise: say its `hint` as one line (`graph on but not reachable:
   run /factory:graph provision <provider> or set DATABASE_URL`; headless, record it under Assumptions too) and stop.
1. Pick the commit: `$ARGUMENTS` if given, else `HEAD`. Run `git rev-parse --verify <sha>` and keep the full sha it prints; the checkout is only read.
2. Find the repo NAME. Run `git config --get remote.origin.url`: if it prints a URL, the name is its last two path parts without `.git` (`https://github.com/acme/app.git` is `acme/app`); if it prints nothing (exit 1) there is no origin, so run `git rev-parse --show-toplevel` and the name is the last directory of that path.
3. List what is stored for that name: `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query snapshots --repo <name> --json`. To see every indexed repo instead, run `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph query snapshots --json`. Read the `repo` and commit fields of the rows rather than guessing.
4. Choose the cheapest correct path:
   - no snapshot for this repo, or the stored parent is not an ancestor (`git rev-list` can check): `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph index --repo . --sha <sha> --json`
   - the newest stored snapshot is an ancestor of `<sha>`: `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph delta --repo . --from <stored-sha> --to <sha> --json` (stores `<sha>` as a delta that replays exactly)
   The result's `repo` field is the stored name; use it for later `--repo <name>` arguments.
5. Always finish with `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph verify-snapshot --repo . --sha <sha> --json`. Exit 0 means the stored rows equal a fresh extraction; exit 1 returns a diff, which you must show the user rather than hide.
6. Report: repo name, sha, snapshot kind (full or delta), and the counts the result carries (symbols, edges, owners; `verify-snapshot` returns `ok` and the missing/extra/changed lists).

Rules: never run `db reset` or `db init` from this skill; never index a path other than the user's repo; if `index` fails, show the error as printed. Do not append `2>&1`, `| head`, `; echo $?` or similar: the exit code is reported by the tool call itself.
