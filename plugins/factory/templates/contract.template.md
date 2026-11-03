# Contract: <slug>

<!-- Written by /factory:ticket after the human answered its one batch of questions.
     Execution (/tests, /implement, /verify) never asks: every answer is in this file, and the
     agent applies "Defaults when ambiguous" to anything else, recording each use in
     .claude/work/<slug>/assumptions.md. `acceptance.py --ready` refuses a contract whose
     Decisions have an empty Value or that has no Defaults block. -->

## Outcome

<One sentence, user-visible: what is true for whom when this ships.>

## Acceptance criteria

<!-- /factory:tests writes one test per criterion and freezes it only when it fails for the intended reason: the
     behaviour is missing (NotImplementedError from a skeleton, an assertion, a status code). tests_failing_first.py
     reads the runner's output and records a reason per test; an import, attribute, name, syntax or collection error
     means the test itself is broken, and freeze_tests.py refuses to freeze it until it is fixed. -->

1. `test_<behaviour>_<condition>` — <what it asserts>.
2. `test_<behaviour>_<condition>` — <what it asserts>. *(negative)*
3. `test_<behaviour>_<condition>` — <what it asserts>. *(negative)*

## Acceptance

<!-- Run by verify through acceptance.py, from the repo root, 300 s each, without a shell.
     Allowed forms: python3 -m pytest ..., pytest ..., bash scripts/smoke_<name>.sh ...,
     curl ... (no -o/-O/-T/-K), make test|check|lint, echo/printf/true/false/test; any of these wrapped as
     uv run [--project X] [--no-sync] ..., poetry run ... or pdm run ... (use the form in .claude/sdlc.json commands.test).
     Node: pnpm test [args], pnpm exec vitest run [args], pnpm vitest run [args], pnpm exec playwright test [args],
     pnpm lint, pnpm typecheck, npm test, npm run test, npx [--no-install] vitest run [args], yarn test, yarn vitest run,
     bun test, bunx vitest run (no &&, ;, |, redirection, config or snapshot-update options).
     Foldkit frontend (runs only when FOLDKIT_DEVTOOLS_URL names a local relay; otherwise it FAILS, never skipped):
     a list item `mcp: foldkit_dispatch_messages <json array of messages>` followed by the line
     `=> model.<dotted.path> == <json value>` (or `!=`). -->

- $ python3 -m pytest tests/<area> -q
  => exit 0

## Decisions

<!-- One row per question the human settled. Value is what the agent implements; it must be
     filled before the contract is ready. Decided by is the @handle who answered, or
     "recommended, unconfirmed" when /ticket ran with no human and took the recommendation.
     No questions at all: replace the table with the line "None." -->

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| D1 | <the question> | <option a> · <option b> | **<option>**: <why, one line> | <fill before ready> | <@handle> |

## Defaults when ambiguous

<!-- The agent applies these instead of asking, in order, and records every use as an
     assumption. An assumption may never contradict a Value above; when the only way forward
     would, or the question is outside these defaults (a path outside Allowed paths, a new
     capability, a wrong frozen test, money, security, data loss), the agent escalates:
     it writes .claude/work/<slug>/questions.md and stops. -->

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- <for example: error responses copy the shape of the nearest existing error response>

## Capabilities

<!-- What execution may use. The allow list is compiled from this; needing something not
     listed is a contract defect found at verify, not a prompt at run time. -->

- Paths: <the globs from Allowed paths, written out, comma-separated>
- Commands: <the stack's test, lint, type-check and format commands; anything else by name>
- Egress: none
- Data: none

## Out of scope

- <adjacent code left alone, by name>

## Allowed paths

- `<app/area/**>`
- `<tests/area/**>`

## Risk tier

<trivial | low | medium | high> — <one line on why; a tier_high_paths match forces high>.

## Evidence plan

- <which tests, which real request or screenshot verify must show>
