# Light path and hotfixes

Not every change needs the full ceremony, and an outage cannot wait for it. This page says what
may be skipped, what may not, and who signs a hotfix.

## Tiers

`current.json` records a `tier`: `trivial`, `low`, `medium` or `high`. `/factory:design` sets it from
the ticket and the allowed paths.

- Paths that force tier `high` are the `tier_high_paths` in `.claude/sdlc.json` (REVIEW.md lists them for this repo). If a contract touches
  one of them with a lower tier, the edit guard blocks the edit ("this path forces tier high")
  until `/factory:design` is re-run. This is mechanical, not a judgment call.
- `/factory:design --trivial` is for typo, comment, copy and config-value fixes that touch none of those
  paths. It records tier `trivial` only when no allowed path matches the high list.

## What `--trivial` skips

Only the Decisions stop: the point where `/factory:design` waits for a human to answer open questions.
It does not skip the contract, the failing test (or a stated reason there is none), the
scope guard, `/factory:verify`, the stamp, the commit-message check or the PR. A trivial change that
needs a decision is not trivial; re-run without the flag.

## Hotfix route

For a production fault that cannot wait for the normal queue:

1. A named approver, a human who is not the author, agrees in the incident channel before work
   starts. Put their handle in the contract's `reviewer` field and in the PR.
2. Branch `hotfix/<slug>`. Run `/factory:design`; tier is `high`. The contract shortens, the gate does not:
   `/factory:verify` still runs and the stamp is still required.
3. The smallest change that stops the harm: no refactors, no new dependencies, no migrations
   unless the fault is the migration. If a data fix is needed, a human runs it.
4. The approver reviews the diff and the evidence, merges in GitHub, and a human runs `/factory:deploy`.
   State the rollback step before deploying (runbook-agent-incident.md, step 5).
5. Within two working days: a follow-up ticket for the proper fix and a test that would have
   caught it. If the fault involved an agent, run the runbook's post-mortem.

A hotfix is never a reason to use `--no-verify`, edit the baseline, or switch off a hook.

## Other paths

- Frontend: once `package.json` is real, add `tsc`, `eslint` and `vitest` to the gate through
  the stack config. Until then frontend changes are reviewed by a person, and the pack says so.
- Infrastructure: `.devcontainer/**` and compose files are under CODEOWNERS and tier high,
  because a `privileged: true` container once landed unreviewed.

<!-- sdlc: REC-54 -->
