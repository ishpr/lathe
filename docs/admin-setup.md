# Admin setup: the settings floor nobody can remove locally

Project settings and hooks can be edited by the people they constrain. Managed settings
cannot. Everything the pack enforces from `.claude/` is a default; this page is the floor.

## What to deliver

Deliver `managed-settings.example.json` (same folder) after editing it for your team.
Pick one route:

- Team or Enterprise: paste it into the admin console as server-managed settings.
- MDM: macOS domain `com.anthropic.claudecode`, Windows `HKLM\SOFTWARE\Policies\ClaudeCode`.
- A file: macOS `/Library/Application Support/ClaudeCode/managed-settings.json`, Linux and
  WSL `/etc/claude-code/managed-settings.json`, Windows `C:\Program Files\ClaudeCode\managed-settings.json`.
  Optional drop-ins go in `managed-settings.d/*.json` beside it and merge alphabetically.

Confirm on a developer machine: run `/status`; the "Setting sources" line should show
"Enterprise managed settings (file)".

## What the example sets

- `permissions.disableBypassPermissionsMode: "disable"`. It is nested under `permissions`, and the
  value is the string `"disable"`. It rejects `--dangerously-skip-permissions`.
- `requiredMinimumVersion: "2.1.295"`. Managed scope only, and it is ignored without warning
  elsewhere. 2.1.295 is the first release that honours `onFailure: "block"`, which the guards
  use so a crashed or missing guard stops the call instead of letting it through.
- A `permissions.deny` floor that is a subset of the plugin's `hooks/guard_bash.py` (the hook parses the whole command and is the stronger check; a pattern is a floor that survives a hook someone disables): force push, push to main, `gh pr merge`,
  `--no-verify`, `ratchet.py --update` and `--prune`, `freeze_tests.py --unfreeze`, and `Read`
  rules for `.env*`, `~/.ssh`, `~/.aws` and `~/.config/gh`. Arrays merge across sources, so
  developers can add rules but cannot remove these. Flag patterns use mid-pattern wildcards (`git commit *--no-verify*`) so the flag matches in any position. Path prefixes matter: `~/` is home, `./` is
  the project, and a bare `/path` is relative to the settings source, not absolute.
- `sandbox` with `failIfUnavailable` and `allowUnsandboxedCommands: false`, a credential
  `denyRead`, and a PyPI and GitHub domain allowlist. Edit the domains for your stack.
- `env`: `DISABLE_FEEDBACK_COMMAND` and `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY` (see data-policy.md).
- `cleanupPeriodDays`: a number the team chooses (whole number, 1 or more).

## What it deliberately leaves out

- `allowManagedHooksOnly`. It runs only managed hooks, so the pack's guards (project hooks, or
  plugin hooks that are not force-enabled) stop running and nothing replaces them. Set it only
  after the same guards ship as managed hooks.
- `allowManagedPermissionRulesOnly`. It ignores every allow rule in project and user settings,
  so the pack's own allow list stops working. Use it only if you are ready to move that list
  into managed settings too.
- Telemetry. See cost.md; it needs a collector first.

Optional locks: `strictKnownMarketplaces` (an empty array blocks every marketplace, including
the official one), `allowManagedMcpServersOnly` (see mcp-policy.md), `disableAutoMode`.

## Sandbox and the devcontainer

The sandbox covers Bash commands. It does not cover the Read tool (`denyRead` does not stop it,
which is why the example repeats the paths as `Read(...)` deny rules), WebFetch, hooks or local
MCP servers. A deny rule is not a security boundary around a program, so the guards and the
sandbox back each other up. The sandbox does not run on native Windows, and
`failIfUnavailable` stops Claude Code from starting there; use WSL2.

Run tests and compose inside the devcontainer. We believe database drivers do not follow the
sandbox's HTTP proxy, but the docs do not say so: confirm that on your machines. Keep
`DATABASE_URL` and `REDIS_URL` in the settings `env` block pointed at compose values, never at a
shared database. If a devcontainer sets `privileged: true`, replace it before enabling
`bypassPermissions` anywhere (see permission-modes.md). Whether `docker compose` works inside
the sandbox on macOS (`sandbox.network.allowUnixSockets`, macOS only) or Linux
(`allowAllUnixSockets`) is untested here; confirm against the current docs.

## Where inference runs

Claude Code reaches Claude through three channels; the floor above is the same on all of them.

- Claude Platform on AWS: Anthropic operates it; AWS supplies identity and billing. Access is IAM
  (SigV4) or an AWS-managed API key; OAuth does not work. Claude Code needs `CLAUDE_CODE_USE_ANTHROPIC_AWS=1`,
  `ANTHROPIC_AWS_WORKSPACE_ID`, `AWS_REGION`, and `ANTHROPIC_AWS_API_KEY` or ambient SigV4 credentials.
  Once per AWS account, run `aws iam enable-outbound-web-identity-federation` or every request fails.
- Amazon Bedrock: AWS operates it; identity is IAM; Claude Code needs `CLAUDE_CODE_USE_BEDROCK=1`.
  HIPAA or FedRAMP customers use it: Claude Platform on AWS is outside AWS compliance programs.
- Anthropic direct: Anthropic organisations and OAuth; subscription seats and the admin console.

What changes here: on Claude Platform on AWS server-managed settings are not available, so deliver
the floor as a file (MDM-managed `managed-settings.json`, or the container image). Subscription logins
do not exist there, so people and headless runs hold an AWS-managed API key or IAM role.

Sources: https://docs.aws.amazon.com/claude-platform/latest/userguide/prerequisites.html and
https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws

## CI evidence (the intent ledger)

Only `.github/workflows/sdlc-evidence.yml` posts the gate's result to the ledger as binding `ci_run` evidence, and
`approved` needs it. It is a `workflow_run` job that GitHub runs from the default branch, so a pull request cannot
change it, and it runs no code from the PR. Configure, once per repository:

| Where | Setting | Value |
|---|---|---|
| GitHub environment `sdlc-ledger`, restricted to the default branch (Settings > Environments > Deployment branches) | secret `SDLC_DATABASE_URL` | the ledger's connection string. Not a repository secret: a same-repo PR's workflows can read those, and this URL is write access to the ledger |
| repository variable (optional) | `SDLC_OIDC_WORKFLOW_REF` | the trusted `job_workflow_ref`; default `<owner>/<repo>/.github/workflows/sdlc-evidence.yml@refs/heads/<default branch>`. Change it only if you rename the file |
| wherever else `sdlc-graph evidence add --source ci:...` runs | `SDLC_OIDC_WORKFLOW_REF` | the same value; `{repository}/.github/workflows/sdlc-evidence.yml@refs/heads/main` serves every repo. Unset: every `ci:` row is refused |
| the same | `SDLC_OIDC_EVENTS` | leave unset (`workflow_run`). Never widen it: `pull_request` tokens are what it keeps out |
| the same | `SDLC_OIDC_AUDIENCE` | only if you change the template's audience (default `sdlc-graph`) |

The contract node's repo must be `owner/name` (index the repo with its origin, or set `repo` to `owner/name`): a
bare name is refused. The gate's workflow name must stay `sdlc-gate` and its file `.github/workflows/sdlc-gate.yml`;
the evidence workflow skips a PR that changes that file. Fork PRs are skipped (the event names no pull request).

## The local hatch

`claude plugin disable factory@lathe` turns every plugin hook off and `claude plugin enable factory@lathe` turns them back on. Managed settings are what stops a person from switching the guards off; the hatch is
for demos and for debugging the pack itself.
