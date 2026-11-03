# Cost: model defaults, limits and measurement

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/factory/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

## Which model for which job

| Job | Model alias | Why |
|---|---|---|
| Sweeps, greps, log triage, counting | `haiku` | cheap, mechanical |
| Writing code and tests inside a contract | `sonnet` (the session default) | good enough, fast |
| Design, cross-module refactors, review lenses | `opus` | needs judgment |
| A rare, hard question that Opus should not settle alone | `fable` | most capable, most expensive; a few calls per task |

Aliases move: on the Anthropic API `opus` is Opus 5.5, `sonnet` is Sonnet 5.5, `haiku` is Haiku
5.5 and `fable` is Fable 5.1; on Bedrock and Agent Platform the sonnet and haiku aliases resolve
older. Pin with `ANTHROPIC_DEFAULT_SONNET_MODEL` and its siblings if you need a stable version.

## Making the choice stick

A subagent's `model` frontmatter is a default, not a pin. The order is: the `model` the parent
passes on the Agent call, then the frontmatter, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the
main model. So a parent that passes `model` overrides a role's frontmatter. Three ways to
enforce:

1. `CLAUDE_CODE_SUBAGENT_MODEL` with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (v2.1.257 or later)
   puts every subagent, teammate and workflow agent on one model. Cheap and blunt: it erases the
   role split above.
2. A PreToolUse hook on the `Agent` tool that rewrites `model` from the subagent type through
   `updatedInput` (send the whole input back), or denies with exit 2. This keeps roles. This
   pack does not ship that hook in this release.
3. Managed `availableModels` to keep the expensive models out of reach (it also constrains the
   Agent tool's `model`).

Plugin subagents ignore `permissionMode`, `hooks` and `mcpServers` in their frontmatter.

## Limits

- Interactive: set the spend limit in the admin console for the organisation or workspace
  (confirm the exact controls against the current docs). Cap effort with `maxEffortLevel`.
- Unattended: `--max-budget-usd` and `--max-turns` in `scripts/run_headless.sh`. The budget is
  a client-side estimate that can be passed.
- Run `/clear` between tickets so one ticket's context is not paid for again in the next. Use
  `/compact` mid-ticket; the SessionStart guard runs again after `compact`.
- Reviewers and sweeps run on the cheaper model the table gives them. Keep prompts short and
  point at files instead of pasting them.

## Billing by channel

Pick the channel before you set a budget; each bills and limits differently.

- Claude Platform on AWS: billed through AWS Marketplace, in arrears, in Claude Consumption Units
  (CCU) at $0.01 each, at Anthropic's per-token rates. Discounts arrive as fewer CCUs. Private offers
  are accepted at sign-up and are not retroactive. `inference_geo` `us` costs 1.1×. Allocate cost by
  workspace tag through Cost and Usage Reports.
- Spend limits there: AWS's guide says there are none (use AWS billing controls); Anthropic's newer
  page describes tier caps and org and workspace limits, with spend taking about 2 hours to show and
  the overshoot billed. Confirm which applies to your account before relying on either.
- Amazon Bedrock: on the AWS bill.
- Anthropic direct: invoiced by Anthropic.

Sources: https://docs.aws.amazon.com/claude-platform/latest/userguide/billing.html and
https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws

## Telemetry, only if you have a collector

OpenTelemetry is configured in managed settings (or the shell, or `~/.claude/settings.json`),
never in the repository: Claude Code ignores the exporter variables in a repo's
`.claude/settings.json`. In the `env` block set `CLAUDE_CODE_ENABLE_TELEMETRY=1`, then
`OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`, `OTEL_EXPORTER_OTLP_PROTOCOL` and
`OTEL_EXPORTER_OTLP_ENDPOINT`. Without a collector to receive it, do not turn it on.
`OTEL_LOG_RAW_API_BODIES` records prompts; do not set it without a data review.

## Measure the pack, not just the bill

Run `scripts/metrics_baseline.py --since 90d` before rollout and at weeks 6 and 12. It reports PR
count, median lines changed, hours to merge and to first review, reverts, the
deletions-to-additions ratio and guard blocks. People tend to estimate a speedup that the clock
does not show (METR measured one), so trust the numbers over the mood.

<!-- sdlc: REC-46 -->
