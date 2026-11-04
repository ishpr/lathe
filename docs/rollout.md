# Rollout: the first month

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/factory/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

The pack changes how a team works. Roll it out to a few people first, pair them with a
champion, and review weekly for six weeks. Ask for evidence before widening.

## Before day one

- Managed settings delivered and confirmed with `/status` (admin-setup.md).
- `/factory:design` tried on one real ticket by the champion. CODEOWNERS handles are real people, and
  owner teams have at least two members.
- The default branch protected (`scripts/protect_main.sh`, or a repository ruleset). A ruleset's pull-request,
  deletion and force-push rules must target the default branch only. A ruleset that targets all branches refuses
  the pushes that every PR branch needs.
- A baseline: run `scripts/metrics_baseline.py` and file it. This is the "before" number.
- A working agreement the team has read (`the working-agreement section of CLAUDE.md`), and the data policy.

## Weeks 1 to 2: pilot

Two or three volunteers, one champion who has already shipped one ticket through the flow.
Each pilot developer pairs with the champion for their first ticket: the champion watches
the contract step and the first failing test, not the code. Everyone reads `day-one.md`.
Optional but useful: the developer writes the first failing test by hand, so the habit of
saying what "done" means in code stays with the human.

## Weeks 3 to 6: weekly review

Thirty minutes, once a week, same people. Bring:

- the guard blocks by rule (`.claude/work/blocks.jsonl`) and who hit them;
- one PR the reviewer found a real problem in, and how the flow missed it;
- one false positive, filed with the false-positive issue template;
- one thing nobody should have to do by hand any more.

Fix at least one rule or message each week. A guard that confuses people gets rewritten, not
explained forever.

## Keeping skills sharp

People who lean on an assistant learn less: in one study, learners with AI help scored 50%
against 67% without, with the widest gap on debugging. So: authors explain the change in their
own words in the PR; reviewers write what they checked; on a rotating basis a developer fixes a
bug without the assistant. Treat this as a team habit, not a hook.

## Exit criteria for widening (week 6)

- Median time to merge is no worse than the baseline, and the review comment rate is not falling.
- No secret in a transcript, and no block of a destructive action that was unexplained.
- Every false positive is fixed or closed with a reason.
- The owner and a second reviewer agree. Then repeat for the next team, starting at day one.

## Decisions to take early

Who the pack owner is; whether to run the advisory Claude review in CI comments-only for two
weeks first; whether unattended runs are allowed at all (unattended-runs.md); the spend limit
(cost.md). A clear, communicated AI stance is a known DORA capability, so write the answers down.
