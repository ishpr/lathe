# Contract: decode-every-ledger-resource-through-typ

## Outcome

A program's Commands call one typed endpoint function per ledger resource and get either data that decoded against its Schema or a named failure (decode error naming the bad field, or not-found), never an unchecked shape.

## Acceptance criteria

1. `getFeed resolves to a value that decodes against its schema` — calls `getFeed` against the MSW fixture for `/api/v1/feed` and asserts the resolved value matches the Feed schema.
2. `getLifecycle resolves to a value that decodes against its schema` — same for `/api/v1/lifecycle`.
3. `getStory resolves to a value that decodes against its schema` — `getStory(slug)` hits `/api/v1/stories/<slug>` and the value matches the Story schema.
4. `getRuns resolves to a value that decodes against its schema` — same for `/api/v1/runs`; each run row carries requested and resolved model, executor and credential.
5. `getRunners resolves to a value that decodes against its schema` — same for `/api/v1/runners`.
6. `getGates resolves to a value that decodes against its schema` — same for `/api/v1/gates`.
7. `getRoutingPolicy resolves to a value that decodes against its schema` — same for `/api/v1/routing-policy`.
8. `getOrganization resolves to a value that decodes against its schema` — same for `/api/v1/organization`.
9. `getMetrics resolves to a value that decodes against its schema` — same for `/api/v1/metrics`.
10. `rejects an unknown evidence kind` — a story whose first evidence item has kind `screenshot` makes `getStory` fail with a decode error whose message contains `evidence[0].kind`. *(negative)*
11. `rejects a run without resolved model` — a run row with no `resolved_model` makes `getRuns` fail with a decode error whose message contains `resolved_model`. *(negative)*
12. `reports a missing story as not found` — `getStory("no-such-story")` with a 404 fails with a not-found error, and that error is not a decode error. *(negative)*

## Acceptance

<!-- The ticket's checks are `pnpm test` runs. acceptance.py allows no pnpm form and the repo has no Makefile or smoke script, so there is no runnable line to list here. /sdlc:verify runs `pnpm test` as the gate; the three named negative tests must appear in its output. -->

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 5 | What does a page do when a response fails to decode? | fail closed: show the error state, render none of the response · drop the bad rows and show the rest | **fail closed**: a partly rendered ledger hides the very rows that are wrong | fail closed | recommended, unconfirmed |
| 3 | The ticket depends on "Show the three fixture epics on the Lifecycle page through a Foldkit program"; does this work wait for it to merge? | proceed now, importing nothing from `src/programs` · wait for the Lifecycle program to merge | **proceed now**: every criterion is met with MSW fixtures and the endpoint functions, and nothing here needs the program | proceed now | recommended, unconfirmed |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Name fields as the ledger names them (`resolved_model`, `decided_by`); convert case only in views.
- Add `credential` to run types as a credential name or id only; never a field that holds a secret value.
- Each type carries a comment naming its backing ledger table (node, edge, transition, evidence, decision, run) or file (routing policy, state machine).
- Decisions carry options, recommendation, value and decided by. Run rows carry requested and resolved model, executor and credential.
- Schemas use the `effect` Schema already in `package.json`; MSW handlers and fixtures go under `src/lib/api/mocks/`, tests next to the code as `src/lib/api/*.test.ts`.
- A failed decode or a 404 comes back as a named error value (decode error with the field path in its message; not-found error), the same shape for every endpoint function. Pages see no partial data (Decision 5).

## Capabilities

- Paths: `src/lib/api/**`, `src/lib/hooks/**`; read the repo.
- Commands: `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm exec eslint --fix --quiet {file}`, `npx --no-install prettier --write --log-level silent {file}`.
- Egress: none.
- Data: none (MSW fixtures only).

## Out of scope

- `src/programs/**` and `src/app/**`: no page or program is wired to the endpoint functions here.
- `src/lib/hooks/use-workspace.ts` keeps its static demo values; replacing them with a query is a later slice.
- `src/lib/portal/**`, `src/components/**`, and `tests/e2e/**` (known ratchet debt in `tests/e2e/shell.spec.ts` stays).
- Any real network call: the endpoint functions are exercised against MSW only.

## Allowed paths

- `src/lib/api/**`
- `src/lib/hooks/**`

## Risk tier

low — new read-only decoding code under `src/lib/api/**`, no auth, models, migrations or tier-high path, no network egress.

## Evidence plan

- `pnpm test` output lists the nine per-function tests plus `rejects an unknown evidence kind`, `rejects a run without resolved model` and `reports a missing story as not found`, each failing before the implementation and passing after.
- `pnpm typecheck` and `pnpm lint` are clean; the ratchet shows no new violations.
