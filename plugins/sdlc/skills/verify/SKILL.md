---
name: verify
description: The gate. Tests, ratchet (checkers, lint, types vs baseline), secret scan, agent-config scan, frozen tests, the contract's acceptance lines, then two independent reviewers over the diff and the recorded assumptions. Never asks. Stamps the tree so ship can commit.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/worktree_fingerprint.py")
  - Bash(git diff HEAD)
  - Bash(git ls-files --others --exclude-standard)
  - mcp__plugin_graph_graph__graph_impact
  - mcp__graph__graph_impact
  - mcp__plugin_graph_graph__evidence_add
  - mcp__graph__evidence_add
  - mcp__plugin_graph_graph__contract_transition
  - mcp__graph__contract_transition
  - mcp__plugin_graph_graph__contract_update
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
Editing contract.md (Decisions, Follow-ups, Evidence and the like): Claude Code refuses Edit and Write under `.claude/`
in an unattended (`--permission-mode dontAsk`) run, whatever the allow rules say. Read the contract, Write the whole
updated text to `.sdlc-stage/<slug>/contract.md`, then run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/<slug>/contract.md`
(it moves the file into place). If the edit guard denies the staged Write as out of scope, say so in one line
(its `.sdlc-stage/**` exemption is missing) and, in an interactive session only, Edit contract.md directly (Claude Code
asks the human to approve). Never use a heredoc, `echo`, `printf` or `cat` to write a file. Run each script as one plain command: no `echo "exit=$?"`, `$?` or redirection after it (under `dontAsk` that
fails the allow rule); the tool result already reports a non-zero exit.

**Never ask.** This stage runs on the contract. Ambiguity (which evidence a vague evidence plan means,
whether a low finding is worth fixing) is settled by the contract's `## Defaults when ambiguous` and
recorded as an assumption, exactly as `/sdlc:implement` says (Stage `verify`, appended to
`.claude/work/<slug>/assumptions.md` through `.sdlc-stage/` and workfile.py). The only stop is an
escalation the contract did not anticipate (implement's list), written to
`.claude/work/<slug>/questions.md` the same way, then `ESCALATION: <one line>`.

0. **If the graph MCP is available** (your tool list has `mcp__plugin_graph_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), run the affected tests first. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   Call `graph_impact` with `paths` = the changed files (`git diff --name-only HEAD` plus
   `git ls-files --others --exclude-standard`). Run the tests it lists with the stack's test command
   narrowed to them, before step 1, so a break in the blast radius shows up in seconds. A failure there:
   fix it first. The full `verify` run in step 1 still happens; the impact set only orders the work and
   never replaces it. Say which tests you ran first and what `unresolved` held.
0a. **If the graph MCP is available**, keep the ledger's copy of the contract current: Read
   `.claude/work/<slug>/contract.md` and call `contract_update` with `slug` and its WHOLE text as `contract_md` (a
   human may have confirmed a decision or added a path since `/sdlc:ticket`). `unchanged` or `updated`: say which,
   with the `changed` fields. A non-empty `blocks.missing`: print it and say the contract needs those blocks. A
   refusal because the node is past `ready` is expected once `/sdlc:tests` froze the tests (from `tests_frozen` the
   paths and tests are frozen): say so in one line and carry on. Optional like every graph step: without the tools
   skip it; an error is one line, never a block.
1. Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py"`. It runs, in order and all of them even
   after a failure: the stack's test command, the ratchet against `.claude/baseline.json`, a secret
   scan of the diff, the agent-config scan, `freeze_tests.py --check` (a frozen test changed →
   red), and the contract's `## Acceptance` lines through `acceptance.py`. Its last line names what
   failed. It writes `.claude/work/last_verify.json` only when everything is green. Red → fix and
   rerun. Never edit `.claude/baseline.json`, the frozen tests or the plugin's scripts to make it
   green; a frozen test that is wrong goes to a human (`/sdlc:implement` says how).
1a. **If the graph MCP is available** (your tool list has `mcp__plugin_graph_graph__*` or `mcp__graph__*` tools; if tools are deferred, search for `graph_impact`), and step 1 wrote `.claude/work/last_verify.json` (it only does when all green), post
   the stamp: `evidence_add` kind `verify_stamp`, payload from that file: `frozen_ok`, `acceptance`
   (`passed`, `failed`), `db_backend`, `tool_versions`, and `ratchet` as `{"new": 0, "known": <the known
   count in the ratchet line verify printed, 0 if none>}` (new is 0 because the stamp is only written
   when the ratchet is clean). Then `contract_transition` to `verified` with that evidence id. If the
   guard answers `missing`, show the list and carry on. Every graph step is optional: without those tools skip it silently and carry on as this skill did before the step existed; if a call errors or the database is unreachable, say so in one line and carry on. Never block on it.
   Also post the assumptions as evidence (the reviewer and the conformance gate read them): `evidence_add` kind `assumptions`, payload
   `{"path": ".claude/work/<slug>/assumptions.md", "count": <rows>, "items": [{"id": "A1", "stage": ...,
   "where": ..., "ambiguity": ..., "default": ..., "choice": ...}]}` read from that file (no file: count 0,
   items `[]`). A server that does not know the kind answers with an error: say so in one line and carry on.
2. Report the acceptance table verify printed (command, expected, got, PASS/FAIL) and its
   `ACCEPTANCE passed=N failed=M` line. To rerun only those lines while fixing:
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance.py" --root .` (REC-09). A contract with no
   acceptance lines: say so; that is a finding against the contract, not a pass.
3. In parallel, run the `sdlc:reviewer` and `sdlc:security-reviewer` agents. They
   have no shell, so give each in its prompt: the contract, the output of `git diff HEAD`, the
   list from `git ls-files --others --exclude-standard` (they Read those files) and
   `.claude/work/<slug>/assumptions.md` if it exists, with the instruction that an assumption which
   contradicts a Decision's Value is a **high** finding (it fails conformance). Do not pass a
   `model` on the Agent call. Each returns a criterion table, at most 10 findings as
   `severity | file:line | what | fix`, and a "Not reviewed" section (REC-53).
4. A **high** finding: fix it (that changes the tree, so rerun step 1) or write under **Review
   notes** in contract.md why it is not an issue. Lower: fix if it is under five minutes, otherwise
   list it under **Follow-ups** in contract.md. Carry both "Not reviewed" sections into the
   Evidence so the human reviewer knows what nobody looked at.
5. Append an **Evidence** section to contract.md: the test command and its summary line, the
   ratchet line, the acceptance table, both reviewers' verdicts and "Not reviewed" lists, an
   **Assumptions** list (each row of assumptions.md, one line, or "None"), and — for
   anything with an HTTP or UI surface — the output of a real request or a screenshot
   (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/smoke_rate_limit.sh"` is there for rate limits; adapt or
   write the one the contract's evidence plan asks for).
   **Frontend (a Foldkit app, `"frontend"` in `.claude/sdlc.json`):** verify printed a `frontend` step
   and the stamp holds a `frontend` block. Put its four lines in the Evidence: stories (Message tags
   sent by a story test, covered/tags), scenes (scene files that assert `toExist`, with_exist/files),
   api (Command calls that go through the API call table, matched/calls) and oxlint (findings, or why
   it did not run). The step is red only for `fe-story` / `fe-scene` keys that are new versus the
   baseline: add the story that sends the tag, or give the element a role or label; never re-baseline.
   For the live evidence, the contract's `- mcp: foldkit_dispatch_messages [...]` lines with their
   `=> model.<path> == <value>` results stand in for the screenshot (they need the dev-only relay,
   `FOLDKIT_DEVTOOLS_URL` on localhost). `docs/frontend-contracts.md` explains each key.
6. Summarise: "Verified at <fingerprint>. Assumptions: <N> — <A1: one line>; <A2: one line>; … (the
   reviewer confirms or reverses each). Decisions recorded as recommended, unconfirmed: <their numbers, or
   none>. Run /sdlc:ship." If a fix removed the stamp, say so and loop.
