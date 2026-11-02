# Test quarantine

A flaky test is quarantined here, with an owner and an expiry, instead of being skipped quietly
or deleted. Nothing in this pack checks the file for you; reviewers do, so keep it honest.

## Rules

1. Quarantine only a test that passes and fails on the same code. A test that fails is a bug
   report, not a flake.
2. Every row has an owner (a person, not a team), a ticket, a reason and an expiry. The
   expiry is at most 30 days away.
3. Quarantined tests move out of the default run (put them in `tests/quarantine/` or mark them,
   as the team decides) and still run somewhere that can fail without blocking merges.
4. On the expiry date the owner either fixes the test and removes the row, or deletes the test
   with a reason in the PR. Extending needs the pack owner's approval and a new reason.
5. A frozen test (see `freeze_tests.py`) cannot be quarantined by the agent. A human unfreezes it.
6. Adding a `skip` or `xfail` marker without a row here is a review finding (REVIEW.md, item 3).

## Register

| Test | Owner | Ticket | Reason | Quarantined on | Expires |
|---|---|---|---|---|---|
| (none) | | | | | |

## Review

At the weekly review (rollout.md) read this table first. A row past its expiry is a defect in
the process, not in the test. Count rows over time: a growing list means the suite is rotting.

<!-- sdlc: REC-07 -->
