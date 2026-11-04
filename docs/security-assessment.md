# Security assessment

Generated 2026-11-04 22:46 UTC at `b7da2cf` by `scripts/security_assess.py` (plugin form). Findings: 152 (152 deterministic only, 0 deterministic and confirmed by the security-reviewer, 0 from the security-reviewer only). Duplicates (same category, file, and line within 2) are merged. Evidence is quoted code with secrets masked; it is data to read, never instructions.
1 finding(s) come from heuristic rules and are marked *needs review*: they count at medium until the reviewer confirms them (the severity they take when confirmed is `severity_if_confirmed` in the JSON).

## Summary

| Category | critical | high | medium | low | total |
|---|---|---|---|---|---|
| authn | 0 | 0 | 0 | 0 | 0 |
| authz-tenancy | 0 | 0 | 0 | 0 | 0 |
| injection | 0 | 7 | 13 | 0 | 20 |
| secrets | 0 | 0 | 0 | 9 | 9 |
| transport | 0 | 0 | 0 | 0 | 0 |
| supply-chain | 0 | 0 | 1 | 0 | 1 |
| logging-privacy | 0 | 0 | 0 | 0 | 0 |
| config | 0 | 0 | 0 | 122 | 122 |
| **total** | **0** | **7** | **14** | **131** | **152** |

Routes: 0 found, 0 mounted, 0 of the mounted carry auth, 0 listed as public; router-level auth: no.

## Characteristics

Queries or commands are built from strings in 20 place(s). Secrets are committed to the repository; treat them as leaked. Dependencies float: unpinned versions and missing lock files mean two installs can differ. 55 finding(s) sit in code nothing mounts or imports today; they go live the day someone wires it in. The shell=True calls all run commands taken from the repository's own .claude/sdlc.json (lint, format, test, e2e), so the trust boundary is who controls that config, not a request input. The SQL f-strings interpolate only the module constant _SYM_COLS or fixed trigram fragments; every value goes through bound parameters.

## Findings

### SEC-01 · high · injection · ruff S602: `subprocess` call with `shell=True` identified, security issue
- **Where:** `plugins/factory/hooks/post_edit.py:42`
- **Evidence:** `'subprocess' call with 'shell=True' identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 6; owners plugins/factory/hooks/post_edit.py, plugins/factory/tests/sdlc/test_hooks.py
- **Source:** ruff

### SEC-02 · high · injection · ruff S602: `subprocess` call with `shell=True` identified, security issue
- **Where:** `plugins/factory/lib/e2e.py:257`
- **Evidence:** `'subprocess' call with 'shell=True' identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-03 · high · injection · ruff S602: `subprocess` call with `shell=True` identified, security issue
- **Where:** `plugins/factory/lib/e2e.py:266`
- **Evidence:** `'subprocess' call with 'shell=True' identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-04 · high · injection · ruff S602: `subprocess` call with `shell=True` identified, security issue
- **Where:** `plugins/factory/lib/e2e.py:310`
- **Evidence:** `'subprocess' call with 'shell=True' identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-05 · high · injection · ruff S604: Function call with `shell=True` parameter identified, security issue
- **Where:** `plugins/factory/scripts/ratchet.py:204`
- **Evidence:** `Function call with 'shell=True' parameter identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-06 · high · injection · ruff S602: `subprocess` call with `shell=True` identified, security issue
- **Where:** `plugins/factory/scripts/tests_failing_first.py:125`
- **Evidence:** `'subprocess' call with 'shell=True' identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-07 · high · injection · ruff S604: Function call with `shell=True` parameter identified, security issue
- **Where:** `plugins/factory/scripts/verify.py:308`
- **Evidence:** `Function call with 'shell=True' parameter identified, security issue`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-14 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/codegraph/queries.py:284`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/codegraph/queries.py
- **Source:** ruff, security_assess

### SEC-15 · medium · injection · ruff S608: Possible SQL injection vector through string-based query construction (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/codegraph/queries.py:402`
- **Evidence:** `f""" | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/codegraph/queries.py
- **Source:** ruff, security_assess

### SEC-16 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/codegraph/queries.py:440`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/codegraph/queries.py
- **Source:** ruff, security_assess

### SEC-17 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/codegraph/queries.py:451`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/codegraph/queries.py
- **Source:** ruff, security_assess

### SEC-18 · medium · injection · SQL built with + (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/impact/affected.py:99`
- **Evidence:** `conn.execute(sql + " ORDER BY committed_at DESC NULLS LAST, ancestors DESC NULLS LAST, indexed_at DESC, id DESC LIMIT 1", params)`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/impact/affected.py
- **Source:** security_assess

### SEC-19 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/impact/record.py:198`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/impact/record.py
- **Source:** ruff, security_assess

### SEC-20 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/index/search.py:109`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/index/search.py
- **Source:** ruff, security_assess

### SEC-21 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/index/search.py:119`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/index/search.py
- **Source:** ruff, security_assess

### SEC-22 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/index/search.py:213`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/index/search.py
- **Source:** ruff, security_assess

### SEC-23 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/ledger/nodes.py:243`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/ledger/contract_md.py, plugins/factory/sdlc_graph/ledger/decisions.py, plugins/factory/sdlc_graph/ledger/decompose.py, plugins/factory/sdlc_graph/ledger/evidence.py, plugins/factory/sdlc_graph/ledger/intake.py, plugins/factory/sdlc_graph/ledger/lifecycle.py, plugins/factory/sdlc_graph/ledger/metrics.py, plugins/factory/sdlc_graph/ledger/nodes.py, plugins/factory/sdlc_graph/ledger/release.py, plugins/factory/sdlc_graph/ledger/runs.py, plugins/factory/sdlc_graph/ledger/transitions.py, plugins/factory/sdlc_graph/ledger/workflow.py
- **Source:** ruff, security_assess

### SEC-24 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/ledger/runs.py:278`
- **Evidence:** `conn.execute(f"SELECT * FROM run WHERE {where} FOR UPDATE", (key,)) | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/ledger/metrics.py, plugins/factory/sdlc_graph/ledger/runs.py, plugins/factory/sdlc_graph/ledger/workflow.py
- **Source:** ruff, security_assess

### SEC-25 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/ledger/runs.py:286`
- **Evidence:** `conn.execute( | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/ledger/metrics.py, plugins/factory/sdlc_graph/ledger/runs.py, plugins/factory/sdlc_graph/ledger/workflow.py
- **Source:** ruff, security_assess

### SEC-26 · medium · injection · SQL built with f-string (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/store.py:68`
- **Evidence:** `conn.execute(f"UPDATE run SET {', '.join(sets)} WHERE id = %s", (*vals, run_id)) | Possible SQL injection vector through string-based query construction`
- **Fix:** Parameterised queries: `text('... WHERE x LIKE :q')` with bound parameters, or the ORM; never interpolate input into SQL.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/deps.py, plugins/factory/sdlc_graph/orchestrator/diagnose.py, plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/feed.py, plugins/factory/sdlc_graph/orchestrator/gates.py, plugins/factory/sdlc_graph/orchestrator/judge.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/prime.py, plugins/factory/sdlc_graph/orchestrator/setup.py, plugins/factory/sdlc_graph/orchestrator/store.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** ruff, security_assess

### SEC-29 · medium · supply-chain · plugins/factory/tests/sdlc/fixtures/foldkit/package.json has 6 dependencies and no lock file
- **Where:** `plugins/factory/tests/sdlc/fixtures/foldkit/package.json:1`
- **Evidence:** `@foldkit/devtools, @foldkit/oxlint-plugin, effect, foldkit, oxlint, vitest`
- **Fix:** Pin with a lock file (uv, pip-tools with hashes, poetry; package-lock for npm) and run the audit tool in CI.
- **Blast radius:** routes none; tests 0; owners plugins/factory/tests/sdlc/fixtures/foldkit/package.json
- **Source:** dependencies

### SEC-30 · low · secrets · ruff S105: Possible hardcoded password assigned to: "GIT_TOKEN_HELPER" (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/dispatch.py:390`
- **Evidence:** `Possible hardcoded password assigned to: "GIT_TOKEN_HELPER"`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** ruff

### SEC-31 · low · secrets · ruff S324: Probable use of insecure hash functions in `hashlib`: `sha1` (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/loop.py:237`
- **Evidence:** `Probable use of insecure hash functions in 'hashlib': 'sha1'`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-33 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/e2e/run_harness.sh:113`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 0; owners none
- **Source:** secret scan

### SEC-34 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:287`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-35 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:300`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-36 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:306`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-37 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:384`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-38 · low · secrets · AWS access key id committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:400`
- **Evidence:** `AWS access key id: AKIA... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-39 · low · secrets · GitHub fine-grained token committed in the repository
- **Where:** `plugins/factory/tests/sdlc/test_hooks.py:2700`
- **Evidence:** `GitHub fine-grained token: gith... (masked)`
- **Fix:** Remove it, rotate it now (it is in the history), and load it from a secrets manager.
- **Blast radius:** routes none; tests 50; owners plugins/factory/tests/sdlc/test_hooks.py
- **Source:** secret scan

### SEC-40 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/guard_agent_spawn.py:109`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 35; owners plugins/factory/hooks/guard_agent_spawn.py, plugins/factory/tests/graph/test_hooks.py, plugins/factory/tests/sdlc/test_autonomy_review.py, plugins/factory/tests/sdlc/test_hooks.py
- **Source:** ruff

### SEC-41 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/guard_bash.py:92`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 13; owners plugins/factory/hooks/guard_bash.py, plugins/factory/sdlc_graph/mcp_server.py, plugins/factory/tests/graph/test_autonomy_review_fixes.py, plugins/factory/tests/graph/test_draft_blocks.py, plugins/factory/tests/graph/test_orchestrator_form_denials.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-42 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/guard_bash.py:1927`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 13; owners plugins/factory/hooks/guard_bash.py, plugins/factory/sdlc_graph/mcp_server.py, plugins/factory/tests/graph/test_autonomy_review_fixes.py, plugins/factory/tests/graph/test_draft_blocks.py, plugins/factory/tests/graph/test_orchestrator_form_denials.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-43 · low · config · ruff S108: Probable insecure usage of temporary file or directory: "/tmp"
- **Where:** `plugins/factory/hooks/guard_edit.py:236`
- **Evidence:** `Probable insecure usage of temporary file or directory: "/tmp"`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 11; owners plugins/factory/hooks/guard_edit.py, plugins/factory/tests/sdlc/test_hooks.py
- **Source:** ruff

### SEC-44 · low · config · ruff S104: Possible binding to all interfaces
- **Where:** `plugins/factory/hooks/observe_capabilities.py:41`
- **Evidence:** `Possible binding to all interfaces`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 3; owners plugins/factory/hooks/observe_capabilities.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_observe_capabilities.py
- **Source:** ruff

### SEC-45 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/observe_capabilities.py:102`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 3; owners plugins/factory/hooks/observe_capabilities.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_observe_capabilities.py
- **Source:** ruff

### SEC-46 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/hooks/observe_capabilities.py:102`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 3; owners plugins/factory/hooks/observe_capabilities.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_observe_capabilities.py
- **Source:** ruff

### SEC-47 · low · config · ruff S108: Probable insecure usage of temporary file or directory: "/tmp/"
- **Where:** `plugins/factory/hooks/observe_capabilities.py:236`
- **Evidence:** `Probable insecure usage of temporary file or directory: "/tmp/"`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 3; owners plugins/factory/hooks/observe_capabilities.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_observe_capabilities.py
- **Source:** ruff

### SEC-48 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception
- **Where:** `plugins/factory/hooks/observe_capabilities.py:323`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 3; owners plugins/factory/hooks/observe_capabilities.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_observe_capabilities.py
- **Source:** ruff

### SEC-49 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/post_edit.py:46`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 6; owners plugins/factory/hooks/post_edit.py, plugins/factory/tests/sdlc/test_hooks.py
- **Source:** ruff

### SEC-50 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/session_start.py:91`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 12; owners plugins/factory/hooks/session_start.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-51 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/hooks/session_start.py:159`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 12; owners plugins/factory/hooks/session_start.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-52 · low · config · ruff S104: Possible binding to all interfaces
- **Where:** `plugins/factory/lib/e2e.py:60`
- **Evidence:** `Possible binding to all interfaces`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-53 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
- **Where:** `plugins/factory/lib/e2e.py:112`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-54 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/lib/e2e.py:122`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-55 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/lib/e2e.py:337`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/e2e.py
- **Source:** ruff

### SEC-56 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/lib/frontend_foldkit.py:919`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 1; owners plugins/factory/lib/frontend_foldkit.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-57 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/lib/sdlc.py:163`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/sdlc.py
- **Source:** ruff

### SEC-58 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/lib/sdlc.py:164`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/sdlc.py
- **Source:** ruff

### SEC-59 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/lib/sdlc.py:225`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/sdlc.py
- **Source:** ruff

### SEC-60 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception
- **Where:** `plugins/factory/lib/sdlc.py:852`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/lib/sdlc.py
- **Source:** ruff

### SEC-61 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/acceptance.py:88`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/acceptance.py, plugins/factory/tests/graph/test_draft_blocks.py, plugins/factory/tests/graph/test_ledger.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-62 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/acceptance.py:495`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/acceptance.py, plugins/factory/tests/graph/test_draft_blocks.py, plugins/factory/tests/graph/test_ledger.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-63 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/acceptance.py:696`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/acceptance.py, plugins/factory/tests/graph/test_draft_blocks.py, plugins/factory/tests/graph/test_ledger.py, plugins/factory/tests/sdlc/test_hooks.py, plugins/factory/tests/sdlc/test_scripts.py
- **Source:** ruff

### SEC-64 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:161`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-65 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:281`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-66 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:289`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-67 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/baseline.py:289`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-68 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:322`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-69 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:330`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-70 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/baseline.py:330`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-71 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/baseline.py:358`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-72 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline.py:421`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 50; owners plugins/factory/scripts/baseline.py, plugins/factory/tests/sdlc/test_scripts.py, plugins/factory/tests/sdlc/test_stage_evals.py
- **Source:** ruff

### SEC-73 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline_diff.py:65`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-74 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/baseline_diff.py:65`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-75 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/baseline_diff.py:75`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-76 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/baseline_diff.py:75`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-77 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/check_commit_range.py:36`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-78 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/check_commit_range.py:36`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-79 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/check_commit_range.py:47`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-80 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/check_new_deps.py:48`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-81 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/check_new_deps.py:48`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-82 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
- **Where:** `plugins/factory/scripts/check_new_deps.py:222`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-83 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
- **Where:** `plugins/factory/scripts/check_new_deps.py:235`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-84 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/check_pr_size.py:37`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-85 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/check_pr_size.py:37`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-86 · low · config · ruff S314: Using `xml` to parse untrusted data is known to be vulnerable to XML attacks; use `defusedxml` equivalents
- **Where:** `plugins/factory/scripts/freeze_tests.py:199`
- **Evidence:** `Using 'xml' to parse untrusted data is known to be vulnerable to XML attacks; use 'defusedxml' equivalents`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-87 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/graph.py:192`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-88 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/graph.py:193`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-89 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/graph.py:281`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-90 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/graph.py:282`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-91 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/graph.py:378`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-92 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/init_project.py:1242`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-93 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/init_project.py:1243`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-94 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/maturity.py:218`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-95 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/maturity.py:238`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-96 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/maturity.py:238`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-97 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/maturity.py:954`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-98 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/metrics_baseline.py:33`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-99 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
- **Where:** `plugins/factory/scripts/route_sweep.py:49`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-100 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
- **Where:** `plugins/factory/scripts/route_sweep.py:134`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-101 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:252`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-102 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:321`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-103 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/security_assess.py:321`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-104 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:968`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-105 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:979`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-106 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:987`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-107 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:994`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-108 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:1001`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-109 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:1038`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-110 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:1283`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-111 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/security_assess.py:1587`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-112 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/security_assess.py:1587`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-113 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/workfile.py:246`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-114 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/scripts/worktree_fingerprint.py:36`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-115 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/scripts/worktree_fingerprint.py:36`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-116 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:55`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-117 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:55`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-118 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:103`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-119 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:103`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-120 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:106`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-121 · low · config · ruff S607: Starting a process with a partial executable path
- **Where:** `plugins/factory/sdlc_graph/codegraph/snapshot.py:106`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-122 · low · config · ruff S104: Possible binding to all interfaces (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/impact/capabilities.py:351`
- **Evidence:** `Possible binding to all interfaces`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-123 · low · config · ruff S314: Using `xml` to parse untrusted data is known to be vulnerable to XML attacks; use `defusedxml` equivalents (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/impact/record.py:103`
- **Evidence:** `Using 'xml' to parse untrusted data is known to be vulnerable to XML attacks; use 'defusedxml' equivalents`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/impact/record.py
- **Source:** ruff

### SEC-124 · low · config · ruff S310: Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected. (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/index/embed.py:85`
- **Evidence:** `Audit URL open for permitted schemes. Allowing use of 'file:' or custom schemes is often unexpected.`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-125 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/index/embed.py:97`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-126 · low · config · per-process state '_jwks_cache' that a security control depends on (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/ledger/oidc.py:113`
- **Evidence:** `module-level mutable dict '_jwks_cache' is per-process state; put it behind a store (Redis/Postgres, with a fake for tests) or end the line with '# state: ok <reason>'`
- **Fix:** Move state a control depends on (rate-limit buckets, idempotency keys, sessions) to a shared store; a second worker otherwise sees a different set.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/ledger/contract_md.py, plugins/factory/sdlc_graph/ledger/decisions.py, plugins/factory/sdlc_graph/ledger/decompose.py, plugins/factory/sdlc_graph/ledger/evidence.py, plugins/factory/sdlc_graph/ledger/intake.py, plugins/factory/sdlc_graph/ledger/lifecycle.py, plugins/factory/sdlc_graph/ledger/metrics.py, plugins/factory/sdlc_graph/ledger/nodes.py, plugins/factory/sdlc_graph/ledger/oidc.py, plugins/factory/sdlc_graph/ledger/release.py, plugins/factory/sdlc_graph/ledger/runs.py, plugins/factory/sdlc_graph/ledger/transitions.py, plugins/factory/sdlc_graph/ledger/workflow.py
- **Source:** module_state

### SEC-127 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/mcp_server.py:221`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-128 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/diagnose.py:91`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-129 · low · config · per-process state 'CHILDREN' (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/dispatch.py:54`
- **Evidence:** `module-level mutable dict 'CHILDREN' is per-process state; put it behind a store (Redis/Postgres, with a fake for tests) or end the line with '# state: ok <reason>'`
- **Fix:** Move state a control depends on (rate-limit buckets, idempotency keys, sessions) to a shared store; a second worker otherwise sees a different set.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** module_state

### SEC-130 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/dispatch.py:426`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** ruff

### SEC-131 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/dispatch.py:522`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** ruff

### SEC-132 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:78`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-133 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:78`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-134 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:93`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-135 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:93`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-136 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:107`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-137 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:107`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-138 · low · config · ruff S108: Probable insecure usage of temporary file or directory: "/tmp" (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_executor.py:308`
- **Evidence:** `Probable insecure usage of temporary file or directory: "/tmp"`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-139 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_gh.py:22`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-140 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/fake_gh.py:22`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-141 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gates.py:141`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-142 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gates.py:221`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-143 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:56`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-144 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:56`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-145 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:70`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-146 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:70`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-147 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:97`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-148 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:97`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-149 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:116`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-150 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:116`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-151 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:251`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-152 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/gitops.py:251`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-153 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/judge.py:86`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-154 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/loop.py:132`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-155 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/orchestrator/setup.py:128`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Blast radius:** routes none; tests 0; owners plugins/factory/sdlc_graph/orchestrator/dispatch.py, plugins/factory/sdlc_graph/orchestrator/loop.py, plugins/factory/sdlc_graph/orchestrator/setup.py, plugins/factory/sdlc_graph/orchestrator/watchdog.py
- **Source:** ruff

### SEC-156 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/spanner/emulator_smoke.py:73`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-157 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/spanner/emulator_smoke.py:73`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-158 · low · config · ruff S603: `subprocess` call: check for execution of untrusted input (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/spanner/emulator_smoke.py:88`
- **Evidence:** `'subprocess' call: check for execution of untrusted input`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-159 · low · config · ruff S607: Starting a process with a partial executable path (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/spanner/emulator_smoke.py:88`
- **Evidence:** `Starting a process with a partial executable path`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-160 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/spanner/emulator_smoke.py:193`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

### SEC-161 · low · config · ruff S110: `try`-`except`-`pass` detected, consider logging the exception (latent: not mounted or imported today)
- **Where:** `plugins/factory/sdlc_graph/status.py:218`
- **Evidence:** `'try'-'except'-'pass' detected, consider logging the exception`
- **Fix:** Follow the ruff S-rule guidance for this code; see `ruff rule <code>`.
- **Source:** ruff

## Not confirmed by the reviewer

Listed for the record; not counted, not planned, not in the epic.

- SEC-08 `plugins/factory/sdlc_graph/codegraph/queries.py:34` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; the id list is bound
- SEC-09 `plugins/factory/sdlc_graph/codegraph/queries.py:50` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; values are bound
- SEC-10 `plugins/factory/sdlc_graph/codegraph/queries.py:55` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; values are bound
- SEC-11 `plugins/factory/sdlc_graph/codegraph/queries.py:58` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; values are bound
- SEC-12 `plugins/factory/sdlc_graph/codegraph/queries.py:133` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; values are bound
- SEC-13 `plugins/factory/sdlc_graph/codegraph/queries.py:136` SQL built with f-string (latent: not mounted or imported today): interpolates only the constant _SYM_COLS; values are bound
- SEC-27 `plugins/factory/sdlc_graph/spanner/export.py:285` ruff S608: Possible SQL injection vector through string-based query construction (latent: not mounted or imported today): f-string interpolates only the constant _SYM_COLS and fixed trigram fragments; user text is bound through %(t)s parameters
- SEC-28 `plugins/factory/scripts/check_skill_permissions.py:99` ruff S105: Possible hardcoded password assigned to: "ROOT_TOKEN": ROOT_TOKEN is the literal placeholder ${CLAUDE_PLUGIN_ROOT}, not a credential
- SEC-32 `plugins/factory/sdlc_graph/orchestrator/setup.py:84` 'digest' compared with == (timing leak) (needs review) (latent: not mounted or imported today): compares a lockfile sha256 against a local marker file, a cache check, not a secret comparison

## Not reviewed

The reviewer says it did not look at:

- guard_*.py hooks, lib/sdlc.py, the MCP server, graph.py and init_project.py were not read in depth; only the files behind the JSON findings were opened.
- SEC-14 to SEC-26 SQL findings other than those listed were not individually opened.

## Blast radius

From sdlc-graph query impact. 25 file(s) resolved

## Severity rules

- **authn**: high = a reachable route or webhook with no authentication, a credential nobody verifies, or no throttling in front of a credential endpoint; medium = the same in code that is not mounted yet; low = a route that is public by design but not listed as public.
- **authz-tenancy**: high = identity or ownership taken from the request, an authenticated caller never compared with the owner, or keys shared across callers; medium = the same in unmounted code; low = hygiene.
- **injection**: critical = request input reaches SQL or shell built from strings on a reachable route; high = SQL or shell built from strings on a reachable path; medium = the same in unmounted code, or unsafe deserialisation; low = string building with constants only.
- **secrets**: critical = a live-format secret committed to the repository; high = credentials stored or compared in clear, or a server secret used for or returned to an unauthenticated caller; medium = a secret read inside a handler, a non-constant-time compare, or the same in unmounted code; low = a hard-coded default.
- **transport**: high = credentials travel in the URL; medium = CORS `*` (high with credentials) or TLS verification turned off; low = plain http to an external host.
- **supply-chain**: critical/high = a known vulnerability the audit tool rates so; medium = no lock file or unpinned runtime dependencies; low = unpinned dev-only dependencies.
- **logging-privacy**: medium = exception text, secrets or tokens returned in a response or logged; low = the same in unmounted code.
- **config**: high = debug on in shipped code; medium = a privileged container, agent-config injection risk, or per-process state that a security control depends on; low = other per-process state, frontend calls to endpoints that do not exist.
- Latent findings (code the dead_code checker lists as unmounted; unverified, the checker can over-count) are one level lower and say so in the title.
- Heuristic rules ship as *needs review* at medium until the reviewer confirms them.

## Tools

| Tool | Ran | Note |
|---|---|---|
| route_auth | yes |  |
| stubs | yes |  |
| api_contract | yes |  |
| module_state | yes |  |
| dead_code | yes |  |
| agent_config | yes |  |
| ruff (S rules) | yes | 157 finding(s) |

Notes:
- 54 file(s) are marked latent (one severity lower) because the dead_code checker lists them as unmounted; that checker can over-count, so "unmounted" is unverified until someone confirms nothing registers them
- no routes recognised (FastAPI, Starlette, Flask decorators or add_url_rule); route auth was not assessed

## Decisions a human makes first

- **secrets: Where secrets live.** (1) a secrets manager, injected at start (2) environment variables read once in config. Recommendation: 1 for shared and production environments; 2 is acceptable for local development only.
- **injection: How queries are built.** (1) ORM or bound parameters everywhere (2) allow-list the input and keep the string. Recommendation: 1: an allow-list is one missed case away from injection.
- **supply-chain: How dependencies are pinned.** (1) a lock file with hashes (uv or pip-tools; package-lock) (2) == pins in the manifest only. Recommendation: 1: pins without hashes still trust whatever the index serves.

## What to do next

- **A)** One epic 'Security hardening' with one feature per category (injection, supply-chain, config, secrets), written agent-ready: allowed paths, evidence of done, decisions a human makes first, risk tier.
- **B)** One feature for the top category only: injection (20 finding(s), worst high). **(recommended)**
- **C)** The report only: nothing else is created.

Recommended: B, because the high or critical findings sit in 1 category; one feature fixes the worst of it first.
