# Contract: serve-fixtures-that-tell-the-northwind-l

## Outcome

Every fixture served to the console describes one organization, "Northwind Labs", one repository, `northwind/platform`, and one set of stories, so every page shows the same numbers and links as its artboard.

## Acceptance criteria

1. `fixtures describe Northwind Labs` — `getOrganization` returns the organization "Northwind Labs" with repository `northwind/platform`; `getLifecycle` returns 3 epics and the story `per-key-rate-limit` in state `implementing`.
2. `fixtures have no dangling references` — the integrity check over every run, evidence and decision row returns `[]`.
3. `integrity check names a dangling run` — on a test copy where run 4 points at story `missing-story`, the integrity check returns exactly `["runs[3].story: missing-story"]`. *(negative)*
4. `fixture timestamps precede the fixture clock` — every timestamp in the fixtures is at or before the fixture clock 2026-11-02T09:00:00Z.
5. `fixture timestamps after the clock are detected` — on a test copy with one timestamp after the clock, the timestamp check reports it instead of passing. *(negative)*

## Acceptance

- $ make test
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 7 | Fixture format | TypeScript modules typed against the Schemas · JSON files | **TypeScript modules**: the compiler checks them against the Schemas | TypeScript modules | recommended, unconfirmed |
| 8 | Fixture times | fixed clock 2026-11-02T09:00:00Z · relative to the current time | **fixed clock**: output is the same on every run and in every test | fixed clock | recommended, unconfirmed |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Use invented names and the `example.com` domain for people and emails in fixtures.
- Keep a mutable fixture store per MSW start with a `resetFixtures()` for tests; no module-level mutable state.
- Tests live beside the code as `src/lib/api/**/*.test.ts`, which the `react` vitest project includes.
- The integrity check and the clock-bound check are pure functions that take fixtures as an argument, so tests can pass a modified copy.
- Dangling-reference messages have the form `<collection>[<index>].<field>: <missing id>`.

## Capabilities

- Paths: `src/lib/api/**`; read the repo
- Commands: `pnpm test`, `pnpm lint`, `pnpm typecheck`
- Egress: none
- Data: fixtures under `src/lib/api/**` only

## Out of scope

- The typed endpoint functions and Schemas, which the dependency "Decode every ledger resource through typed endpoint functions" delivers; this work consumes them and does not write them.
- Pages under `src/app/**`, shared components and programs under `src/programs/**`.
- The MSW dependency and its version in `package.json`.
- The known test-quality debt in `tests/e2e/shell.spec.ts`.

## Allowed paths

- `src/lib/api/**`

## Risk tier

low — fixture data and pure checks under one directory; no auth, money, migration or protected path.

## Evidence plan

- Tests `fixtures describe Northwind Labs`, `fixtures have no dangling references`, `integrity check names a dangling run` and `fixture timestamps precede the fixture clock` fail before the work and pass after.
- `pnpm test`, `pnpm lint` and `pnpm typecheck` are green.
