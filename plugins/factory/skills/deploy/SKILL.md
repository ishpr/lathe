---
name: deploy
description: Run the project's local deploy and smoke commands from a merged main, wait for the ready endpoint with a timeout, and report. States the rollback step first. Human-invoked only; the real deploy is CI/CD.
disable-model-invocation: true
argument-hint: "[base-url] [feature-or-contract-slugs...]"
allowed-tools:
  - Read
  - Bash(git log -1 *)
  - Bash(git rev-parse HEAD)
  - Bash(curl -s -o /dev/null -w *)
  - Bash(docker compose up --build -d)
  - Bash(docker compose down)
  - Bash(docker compose logs *)
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" *)
  - mcp__plugin_factory_graph__release_request
  - mcp__graph__release_request
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
---
Only from the protected branch at a commit that arrived through a reviewed PR
(`git log -1 --format=%s` is a merge or squash commit, not a local one). Otherwise stop and say why.

0. Before anything starts, print the rollback step for this commit (REC-133):
   "Rollback: `/factory:rollback <sha from git rev-parse HEAD>` opens a revert PR; until it
   merges, stop the local stack with the project's stop command (`docker compose down`, never
   with `-v`)."
0a. **If the graph MCP is available** (your tool list has `mcp__plugin_factory_graph__*` or `mcp__graph__*` tools; if
   tools are deferred, search for `release_request`) and the human named a feature or contract slugs after the
   base URL: call `release_request` with `feature_slug` (one slug that is a feature) or `contract_slugs`. It opens a
   `release` in `requested` with a `shipped_in` edge from each merged contract, and refuses any contract the ledger
   does not show as `merged` (then say which, and that the merge is recorded with `evidence add --kind merge_sha`
   as /ship says). Keep the release `slug` it returns for steps 2 to 4. Graph off (`{"error": "graph off", ...}` or
   `{"error": "graph not configured", ...}`), no tools, no slugs named, or any error: say its `hint` as one line and
   carry on exactly as below. The deploy never waits on the graph.
1. Run `commands.deploy_local` from `.claude/sdlc.json` (default `docker compose up --build -d`).
2. Poll the ready endpoint with a timeout: the ready endpoint the project documents (`/ready`, else
   `/health`) at the base URL (argument, default `http://localhost:8000`), every 2 s for at most
   60 s, each try `curl -s -o /dev/null -w '%{http_code}' --max-time 2 <base-url>/ready`. 200 →
   go on (with a release: `evidence_add` on its slug, kind `ready_poll`, payload
   `{"url": "<the polled url>", "status": <last code>, "attempts": <tries>}`, and keep the evidence id; post it for a
   failed poll too, with the code it ended on). A 404 on `/ready` → poll `/health` the same way and say the project has no ready
   endpoint. No 200 within 60 s → show the service logs, print the rollback step again, stop.
3. Run `commands.smoke` if configured, otherwise the smoke check the contract's evidence plan names
   (for a rate limit: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" <base-url> <key>`; use
   it only when the merged change was the rate limiter, and ask the human for the key rather than
   reading it from `.env`). With no smoke check named, the ready answer from step 2 is the result;
   say so. The sandbox can block `curl` to a host server (Linux) and `docker`; run the step from a human
   terminal if it fails with a connection error.
3a. With a release and a smoke check that ran: `evidence_add` kind `smoke`, payload
   `{"command": "<the command run>", "exit": <its exit code>}`, keep the id. Then `contract_transition` the release to
   `staged`. The ledger moves it only on a binding green `ci_run` (CI posts that, never this session); when it
   answers `missing`, say so in one line and leave the release in `requested`. Never try `released`, `verified`
   or `rolled_back`: this server acts as an agent and the ledger refuses it.
4. Print the summary: commit, time to ready, smoke result. If the smoke check fails: stop the
   stack, report, print the rollback step, stop. A fix goes back through `/factory:ticket`;
   nothing is patched on a deployed tree.

In a real project this step is CI/CD on merge — `.github/workflows/sdlc-gate.yml` is the gate and
the deploy job targets the team's platform. Keep this command as the local proof.

With a release, finish the summary with the human's two steps (this session cannot take them): after the deploy is
live, `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph node transition <release slug> released --actor @<handle>`; and
once the ready poll and smoke evidence above are in,
`uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph node transition <release slug> verified --actor @<handle> --evidence-ids <ready_poll id> <smoke id>`.
A failed ready poll or smoke check leaves the release where it is; `/factory:rollback` carries it to `rolled_back`.
