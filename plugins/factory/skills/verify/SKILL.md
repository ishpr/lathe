---
name: verify
description: The gate. Tests, ratchet (checkers, lint, types vs baseline), secret scan, scope check, agent-config scan, frozen tests, the contract's acceptance lines, then two independent reviewers over the diff and the recorded assumptions. Never asks. Stamps the tree so ship can commit.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Agent
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py")
  - Bash(git diff HEAD)
  - Bash(git ls-files --others --exclude-standard)
  - mcp__plugin_factory_graph__graph_impact
  - mcp__graph__graph_impact
  - mcp__plugin_factory_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_factory_graph__contract_transition
  - mcp__graph__contract_transition
  - mcp__plugin_factory_graph__contract_update
  - mcp__graph__contract_update
  - Bash(git diff --name-only HEAD)
  - Bash(python3 -m pytest *)
  - Bash(uv run pytest *)
  - Bash(uv run python -m pytest *)
  - Bash(uv run python3 -m pytest *)
  - Bash(poetry run pytest *)
  - Bash(poetry run python -m pytest *)
  - Bash(poetry run python3 -m pytest *)
  - Bash(pdm run pytest *)
  - Bash(pdm run python -m pytest *)
  - Bash(pdm run python3 -m pytest *)
  - Bash(pnpm test *)
  - Bash(pnpm exec vitest run *)
  - Bash(pnpm vitest run *)
  - Bash(pnpm exec playwright test *)
  - Bash(pnpm lint)
  - Bash(pnpm typecheck)
  - Bash(npm test *)
  - Bash(npm run test *)
  - Bash(npx --no-install vitest run *)
  - Bash(yarn test *)
  - Bash(yarn vitest run *)
  - Bash(bun test *)
  - Bash(bunx vitest run *)
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" *)
---

# Verify

## Goal

Prove the implemented tree meets its contract and stamp it so `/factory:ship` can commit: every gate check green on
the last tree, every acceptance line passing, two independent reviews with no high finding open, and the evidence
written into the contract and the ledger. Never asks.

## Inputs

- `.claude/work/current.json` (slug, `allowed_paths`, `frozen_tests`), `.claude/work/<slug>/contract.md` and
  `.claude/work/<slug>/assumptions.md` when it exists.
- The change: `git diff HEAD` and `git ls-files --others --exclude-standard`; `git diff --name-only HEAD` for the
  changed paths.
- `.claude/sdlc.json` (`commands.test`, `paths.tests`, the `e2e` and `frontend` blocks).
- The ledger, when the graph tools are in your tool list (without them skip silently; an error is one line, never a
  block): `graph_impact` with `paths` = the changed and untracked files, whose tests run first, narrowed, before the
  full gate (say which and what `unresolved` held); and the contract kept current: call `contract_update` with `slug`
  and its WHOLE text as `contract_md` (say `unchanged` or `updated` with the `changed` fields; a non-empty
  `blocks.missing` is printed; a refusal because the node is past `ready` is expected once the tests are frozen).

## Outputs

- **The gate**, on the tree as it stands: when `.claude/sdlc.json` has an `e2e` block,
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/e2e.py" run` (no file: the whole suite) as its own Bash command (the
  sandbox excludes that exact command; a port held by another checkout is exit 2, never tested), then
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py"`. verify.py runs every check even after a failure: the stack's
  test command, the recorded e2e run (red when none, stale or narrowed), the ratchet against `.claude/baseline.json`,
  a secret scan of the diff, the scope check (a changed file outside `allowed_paths` is red: a human reverts it or
  widens the contract), the agent-config scan, `freeze_tests.py --check` (a changed frozen test, or a frozen e2e spec
  that did not run, is red) and the contract's `## Acceptance` lines through acceptance.py. Its last line names what
  failed. It writes the stamp `.claude/work/last_verify.json` only when everything is green; any later edit removes
  it, so the last gate run comes after the last fix. Red: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --root .`
  shows the acceptance lines alone (REC-09).
- **The acceptance table** verify printed (command, expected, got, PASS/FAIL) and its `ACCEPTANCE passed=N failed=M`
  line. A contract with no acceptance lines: say so; that is a finding against the contract, not a pass. With the
  graph, record what stands in for them on this first attempt, not after a refusal: the frozen tests named after the
  criteria (`AC1: ...`), as `evidence_add` kind `acceptance` with `{"passed": <frozen tests that passed>, "failed": <that failed>, "source": "frozen tests", "note": "the contract has no runnable acceptance lines"}`;
  the ledger's `verified` guard counts a passed test per criterion. Post it before the `verified` transition.
- **Two independent reviews**: `factory:reviewer` and `factory:security-reviewer`, each given the contract, the
  output of `git diff HEAD` verbatim, the untracked list (they Read those files) and assumptions.md, never a summary
  of the change or focus hints: a reviewer handed the author's summary reviews the summary. An assumption that
  contradicts a Decision's Value is a **high** finding (it fails conformance). Each returns a criterion table, at most
  10 findings as `severity | file:line | what | fix`, and a "Not reviewed" section (REC-53).
- **Fixes**: the gate's red steps (a failure in the impact tests first), every **high** finding, and a lower finding
  that takes under five minutes. A longer lower one goes under **Follow-ups** in contract.md; a high finding that is
  wrong (the code does not do what it says) gets a line under **Review notes** saying why. The reviewer that raised a
  high finding looks again at the fixed hunks (and any new untracked test file). Done when the last gate run came
  after the last fix, is green, and no high finding is open.
- **Ledger evidence** (graph tools available, the stamp written): Read the stamp and post `evidence_add` kind
  `verify_stamp` with `frozen_ok`, `acceptance` (`passed`, `failed`), `db_backend`, `tool_versions` and `ratchet` as
  `{"new": 0, "known": <the known count in the ratchet line, 0 if none>}`, then `contract_transition` to `verified`
  with that evidence id (a `missing` answer: show it and carry on). Then `evidence_add` kind `assumptions`,
  `{"path": ".claude/work/<slug>/assumptions.md", "count": <rows>, "items": [{"id": "A1", "stage": ..., "where": ..., "ambiguity": ..., "default": ..., "choice": ..., "decision": ...}]}`,
  one key per table column, `decision` never left out (the conformance gate reads it); no file: count 0, items `[]`.
- **The Evidence section**, appended to contract.md from the last green gate and both reviews: the test command and
  its summary line; the e2e line (`e2e: N passed, M failed, K skipped; server started|reused at <url>`, and the reason
  per failing spec) when the repo has an `e2e` block; the ratchet line; the acceptance table; both reviewers' verdicts
  and "Not reviewed" lists; an **Assumptions** list (each row, one line, or "None"); and for an HTTP or UI surface the
  output of a real request or a screenshot. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh" <base url>` runs
  only against a server already listening at a URL the contract names (this stage never starts one; a refused
  connection is reported); otherwise name under Not reviewed the request that would show it. **Frontend** (a Foldkit
  app, `"frontend"` in `.claude/sdlc.json`): the `frontend` step's four lines (stories, scenes, api, oxlint), red only
  for new `fe-story` / `fe-scene` keys, fixed by a story or a role or label, never by re-baselining; the contract's
  `- mcp: foldkit_dispatch_messages [...]` lines with their `=> model.<path> == <value>` results stand in for the
  screenshot (`docs/frontend-contracts.md` explains each key).
- **The summary**: "Verified at <fingerprint>. Assumptions: <N> — <A1: one line>; <A2: one line>; … (the reviewer
  confirms or reverses each). Decisions recorded as recommended, unconfirmed: <their numbers, or none>. Run
  /factory:ship." A fix that removed the stamp means the gate runs again first.

## Guards

- **Never ask.** Ambiguity (which evidence a vague plan means, whether a low finding is worth fixing) is settled by
  the contract's `## Defaults when ambiguous` and recorded as an assumption (Stage `verify`) in the format
  `/factory:implement` gives. The only stop is an escalation the contract did not anticipate (implement's list; also a
  fix that needs a path outside `allowed_paths`, a frozen test or a capability), written to
  `.claude/work/<slug>/questions.md`, then `ESCALATION: <one line>`.
- **Nothing is made green by moving the bar**: never edit `.claude/baseline.json`, the frozen tests or the plugin's
  scripts. Fixes stay inside `allowed_paths`; a new file only for the test that pins a finding, under the test paths.
  A frozen test that is wrong goes to a human (`/factory:implement` says how).
- **The gate fixes nothing**: a red step is reported as it printed, then fixed as its own change. Never a fix beside
  a gate run, and never two fixes at once.
- **Work files.** assumptions.md, questions.md and contract.md edits are written whole with the Write tool to
  `.sdlc-stage/<slug>/<file>`, then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/<file>`
  (Claude Code refuses Edit and Write under `.claude/` unattended); if the edit guard denies the staged Write, say so,
  and only interactively Edit contract.md directly. Every path written is built from the repository root. Never a
  heredoc, `echo`, `printf` or `cat` to write a file, and never `echo "exit=$?"` or a redirection after a script.
- **Subagents.** An implementer spawned for this stage carries `[sdlc stage=verify node=<slug>]` first in its
  `description`, so the spawn guard and the ledger record it under this stage. A subagent cannot ask: its assumption
  rows (numbered on from the file's last `A<n>` here) and its `ESCALATION:` line come back in its report. A subagent
  refused by a hook reports the refusal; it does not work around it. Parallel subagents never edit the same file.

## Hints (non-binding)

- What worked before: the gate (a `factory:implementer` that edits nothing) and the two reviewers at the same time,
  since none of them edits; then one fixer at a time; then the gate and the second looks together again.
- Carry both "Not reviewed" sections into the Evidence so the human reviewer knows what nobody looked at.
