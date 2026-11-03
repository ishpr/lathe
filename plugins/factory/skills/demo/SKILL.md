---
name: demo
description: Run the sdlc-graph end-to-end demo (index, delta, impact, contract lifecycle with refusals, routing and Fable budget, prediction scoring, capabilities, Spanner export, MCP) on a copy of the repository's sample app (or a repository you name), and narrate each step.
allowed-tools:
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/demo/run_demo.sh")
  - Bash(bash ${CLAUDE_PLUGIN_ROOT}/demo/run_demo.sh)
  - Bash(docker compose *)
  - Read
disable-model-invocation: true
---

Run the demo and narrate it. About 15 seconds when Postgres is up.

1. Prerequisites: `uv` installed; Docker running; Postgres on `localhost:5435` (`docker compose up -d --wait db` in `${CLAUDE_PLUGIN_ROOT}`); optionally the Spanner emulator (`docker compose --profile spanner up -d spanner`; `DEMO_SKIP_SPANNER=1` skips step 9). The demo runs `db reset`, so it refuses a non-local `DATABASE_URL`; never set `DEMO_ALLOW_REMOTE_DB`.
2. The fixture is the repository's `examples/sample-app` (two levels above the plugin root in a checkout of the repository). `SAMPLE_REPO` in the environment (set by the human before the session starts; you cannot set it on the command line, an inline `VAR=value` prefix is denied) names a different repository instead; it is only read, and the demo copies it. If the demo says there is no fixture, say so and stop; do not search the disk for one.
3. Run exactly `bash "${CLAUDE_PLUGIN_ROOT}/demo/run_demo.sh"`, on its own: the allow rules match the literal command text, so a variable prefix, a pipe, a redirection or a second command on the same line is denied in unattended runs.
4. As the steps print, narrate each in one or two sentences using `${CLAUDE_PLUGIN_ROOT}/demo/DEMO.md` (read it for the talking point per step). Call out the three refusals, since they are the point: an overlapping contract is refused, a human-only decision is refused for the agent, and unsigned evidence cannot satisfy a binding guard.
5. Success is exit 0 and a closing summary. On failure, print the step number and message, check the "if a step fails" notes in DEMO.md, and report the cause; do not retry in a loop or edit the demo to make it pass.
