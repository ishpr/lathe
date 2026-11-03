---
name: deploy
description: Run the project's local deploy and smoke commands from a merged main, wait for the ready endpoint with a timeout, and report. States the rollback step first. Human-invoked only; the real deploy is CI/CD.
disable-model-invocation: true
argument-hint: "[base-url]"
allowed-tools:
  - Read
  - Bash(git log -1 *)
  - Bash(git rev-parse HEAD)
  - Bash(curl -s -o /dev/null -w *)
  - Bash(docker compose up --build -d)
  - Bash(docker compose down)
  - Bash(docker compose logs *)
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" *)
---
Only from the protected branch at a commit that arrived through a reviewed PR
(`git log -1 --format=%s` is a merge or squash commit, not a local one). Otherwise stop and say why.

0. Before anything starts, print the rollback step for this commit (REC-133):
   "Rollback: `/factory:rollback <sha from git rev-parse HEAD>` opens a revert PR; until it
   merges, stop the local stack with the project's stop command (`docker compose down`, never
   with `-v`)."
1. Run `commands.deploy_local` from `.claude/sdlc.json` (default `docker compose up --build -d`).
2. Poll the ready endpoint with a timeout: the ready endpoint the project documents (`/ready`, else
   `/health`) at the base URL (argument, default `http://localhost:8000`), every 2 s for at most
   60 s, each try `curl -s -o /dev/null -w '%{http_code}' --max-time 2 <base-url>/ready`. 200 →
   go on. A 404 on `/ready` → poll `/health` the same way and say the project has no ready
   endpoint. No 200 within 60 s → show the service logs, print the rollback step again, stop.
3. Run `commands.smoke` if configured, otherwise the smoke check the contract's evidence plan names
   (for a rate limit: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" <base-url> <key>`; use
   it only when the merged change was the rate limiter, and ask the human for the key rather than
   reading it from `.env`). With no smoke check named, the ready answer from step 2 is the result;
   say so. The sandbox can block `curl` to a host server (Linux) and `docker`; run the step from a human
   terminal if it fails with a connection error.
4. Print the summary: commit, time to ready, smoke result. If the smoke check fails: stop the
   stack, report, print the rollback step, stop. A fix goes back through `/factory:ticket`;
   nothing is patched on a deployed tree.

In a real project this step is CI/CD on merge — `.github/workflows/sdlc-gate.yml` is the gate and
the deploy job targets the team's platform. Keep this command as the local proof.
