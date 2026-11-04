# Maturity assessment

Assessed `b7da2cf` (commit of 2026-11-04), python stack, history: all history.
Overall: **level 2 of 5, Individuals get results, nothing is shared** (mean 2.00 across 7 dimensions).

| Dimension | Level | Evidence | The move that raises it one level |
| --- | --- | --- | --- |
| **Plan** | **2** of 5<br>*Changes trace to a pull request or ticket id, so a reader can find the ask behind a commit.* | No PR or issue template<br>21 distinct pull requests named in commit subjects<br>5 of 88 commits cite a ticket or PR id | **To 3:** Add a pull-request template and an issue template that ask for outcome, an acceptance check a command can run, and an owner; commit one intent file per piece of work (docs/epics/<slug>.md or .claude/work/<slug>/contract.md).<br>*Not yet true:* a PR or issue template is committed (now: no) |
| **Design** | **2** of 5<br>*A README and an architecture note exist.* | No decision records (ADRs)<br>8 paths named in CLAUDE.md or .claude/rules do not exist<br>README is 143 lines | **To 3:** Record each hard-to-reverse decision as an ADR (docs/adr/NNNN-title.md) and fix or delete every endpoint, path and command that the README, the UI or CLAUDE.md names but the code does not have.<br>*Not yet true:* at least one decision record (ADR) (now: 0); no path named in CLAUDE.md is missing (now: 8) |
| **Build** | **1** of 5<br>*No shared standards: little or no lint, format or type config, and commit messages do not describe the change.* | 0 of 3 standards have config in the repo: none<br>57 of 88 commit messages fail the pack's message check (vague subject, body missing, or claims the diff does not show)<br>0 of 19 pull-request merges are self-merges (the merger is the author); a second reviewer is not visible in git | **To 2:** Add lint, format and type-check config, and fix the commit habit: a subject that says what changed, and a body when four or more files move.<br>*Not yet true:* two of lint, format and type config present (now: 0); 60% of commit messages pass the message check (now: 35%) |
| **Test** | **3** of 5<br>*Tests assert behaviour, not existence, and CI runs them on every pull request.* | No coverage floor configured<br>1452 tests in 82 files<br>3.2 assertions per test (4698 across 1452) | **To 4:** Add a negative case to every test file (a 4xx, an exception, a refusal) and set a coverage floor in CI that can only rise.<br>*Not yet true:* 20% or fewer test files lack a negative case (now: 21%); a coverage floor is configured (now: no) |
| **Deploy** | **3** of 5<br>*CI runs on every pull request: install, tests, lint and types.* | CI does not run a deploy or release step<br>0 release tags<br>CI: GitHub Actions (2 files) | **To 4:** Automate the deploy from the default branch behind a human approval, tag each release, and write the rollback step down.<br>*Not yet true:* CI runs a deploy or release step (now: no); releases are tagged (now: 0) |
| **Maintain** | **1** of 5<br>*No dependency manifest, or a secret in the tree; the system cannot be rebuilt safely from the repo.* | 2 secret-like strings in tracked files (AWS access key id in plugins/factory/tests/e2e/run_harness.sh; AWS access key id in plugins/factory/tests/sdlc/test_hooks.py)<br>Lock file: plugins/factory/uv.lock<br>deps.pinned_share: not measured (no parsable dependency list) | **To 2:** Declare every dependency in one manifest and remove any committed secret: rotate it first, then purge it from history.<br>*Not yet true:* no secret-like string in tracked files (now: 2) |
| **AI practice** | **2** of 5<br>*Individuals use assistants and get results; nothing is shared.* | Shared agent config committed: AGENTS.md<br>88 of 88 non-merge commits carry an AI trailer, from 2 of 2 authors (a floor: trailers can be turned off)<br>No hooks configured in the repo, so rules are advisory (nothing refuses a bad edit) | **To 3:** Commit CLAUDE.md and .claude/rules (how to run, what not to touch, house conventions), keep the trailers on, and get a second and third engineer using them on real tickets.<br>*Not yet true:* no path named in CLAUDE.md is missing (now: 8) |

## Three moves first

Ranked by impact over effort (each 1 low to 3 high, from the rubric); ties go to the step that clears more unmet conditions, then the lower level, then the order of the table. Steps that clear the same conditions are one piece of work and are listed together.

1. **Maintain, 1 to 2** (effort low, impact high). Declare every dependency in one manifest and remove any committed secret: rotate it first, then purge it from history.
2. **AI practice, 2 to 3** (effort low, impact high). Commit CLAUDE.md and .claude/rules (how to run, what not to touch, house conventions), keep the trailers on, and get a second and third engineer using them on real tickets.
3. **Plan, 2 to 3** (effort low, impact medium). Add a pull-request template and an issue template that ask for outcome, an acceptance check a command can run, and an owner; commit one intent file per piece of work (docs/epics/<slug>.md or .claude/work/<slug>/contract.md).

## What we did not measure

- Whether branch protection or required reviews are switched on in the host (GitHub, GitLab): only the files in the repo are read.
- Whether the tests pass: no project code is executed, so a suite that fails on rerun looks the same as one that passes.
- Review quality: a merge by a second person is counted, what they looked at is not.
- AI use that leaves no trailer: trailers can be turned off, so the share of commits with one is a floor, and they say nothing about quality.
- Runtime behaviour: production incidents, latency, cost and the people's habits are outside the repo.
- Lint and type error counts: not run (pass --run-tools to run ruff on a Python repo; mypy is never run, it can execute project plugins).
- deps.pinned_share: no parsable dependency list
- docs.contract_lies: api_contract found nothing to look at (no matching source files or routes)
- auth.unprotected: no routes found

Scored against `templates/maturity_rubric.json` in the plugin (edit the thresholds there; `--dump-rubric` prints a copy to start from). Re-run: `/factory:maturity` (the script's `--json` gives the data, `--epic 1` the epic prompt of the first move).
