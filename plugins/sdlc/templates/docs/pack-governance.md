# Pack governance

> Scripts named `scripts/NAME` below live in the plugin, not in this repo. Run one from a marketplace checkout as
> `<checkout>/plugins/sdlc/scripts/NAME` (the CI gate runs them the same way), or ask a session to run it.

This pack is a set of rules that cost every developer a little time. It stays only while each
rule earns its place. Keep the enforced set small.

## Owner

One named owner per repository: **to be named** (write the name here before rollout). The owner reviews every change to hooks, scripts,
rules, `CLAUDE.md`, settings, CI and this folder, answers false-positive reports, and runs the
quarterly prune. Changes to `.claude/**`, `docs/` and `REVIEW.md` also need an approval from a code owner who is not the author, because the pack
grades everyone else's work. GitHub accepts an approval from any one owner named on a CODEOWNERS line, so two names on a line do not mean both must approve: the
first owner cannot approve their own pack PR, and a path that needs the security owner lists that owner alone (or use a team, or a ruleset that requires two approvals).

## Every rule cites its reason

A rule in `CLAUDE.md`, a rules file, a hook or a checker carries a reason: an ISS id from the
issue list, a section of the history report, or an incident. A rule with no citation is removed
at the next prune. When you add a check, add the case that proves it (a hook test or a canary
case) in the same PR.

## Versions and changelog

- the plugin `version` in `.claude-plugin/plugin.json` holds the pack version. The changelog (`CHANGELOG.md` in the marketplace) gets an entry for every
  release: what was added, changed or removed, and which REC or incident drove it.
- Versions follow major.minor.patch. A new guard or a stricter check is a minor version. A change
  that makes previously green work fail is called out under "Behaviour changes".
- An upgrade shows a diff before it writes, and never re-baselines. It must not run
  `ratchet.py --update`. A pack upgrade that raises the baseline is a stop-and-ask.
  Confirm that your installer's upgrade mode behaves this way before relying on it.
- The baseline file may shrink freely (`ratchet.py --prune`, human only). It may grow only with
  an owner's label on the PR (`baseline-approved`); CI compares against the base branch.

## Quarterly prune

Once a quarter the owner and two developers read `blocks.jsonl` and the false-positive issues
and decide, for each rule: keep, tune, or remove. Remove a rule that never fires, fires mostly
falsely, or duplicates a managed deny. Record the decision in the changelog.

## Measuring

Run `scripts/metrics_baseline.py` before rollout, at week 6 and at week 12; file the output
under `docs/metrics/`. Watch median PR size, time to merge, time to first review, reverts, the
deletions-to-additions ratio and guard blocks by rule. See cost.md. A rule is working if the
block count falls as people learn it, not if it stays high.

## Change flow

1. Open an issue (false-positive or rule request) using the issue templates.
2. PR to the pack paths; tier high; the owner and one other code owner review (one approval from any listed owner is what GitHub counts).
3. Merge, bump the version, add the changelog line, announce in the team channel.

<!-- sdlc: REC-38 -->
