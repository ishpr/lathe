# Contract: show-the-three-fixture-epics-on-the-life

## Outcome

An engineer opening `/lifecycle` with the data flag at its default `mock` sees the three Northwind Labs fixture epics, loaded through FoldkitHost, a Foldkit program and one typed endpoint, with a retryable error when the endpoint fails.

## Acceptance criteria

1. `lifecycle lists the three fixture epics` — at 1440x1000 with `NEXT_PUBLIC_LATHE_DATA` unset (default `mock`), `/lifecycle` shows the list labelled "Epics" with exactly 3 items whose titles are the three fixture epic titles in Decision 5.
2. `lifecycle shows a retryable error when the endpoint fails` — with the lifecycle mock answering HTTP 500, `/lifecycle` renders no epic items and shows "We couldn't load the lifecycle. Try again." with a Try again button; after the mock answers 200, pressing Try again renders 3 epic items. *(negative)*
3. `lifecycle is axe clean in dark and light` — once `/lifecycle` has loaded, axe reports 0 violations in the dark theme and again in the light theme.
4. `lifecycle shows no epic items on a failed load` — on the 500 response no element of the "Epics" list is in the DOM and the error is rendered by the program's view, not a React error boundary. *(negative)*
5. `shell.spec.ts still passes` — `pnpm test:e2e` runs the existing `tests/e2e/shell.spec.ts` green and unchanged.

## Acceptance

<!-- The ticket's real checks are Playwright (`pnpm test:e2e`) and vitest (`pnpm test`). acceptance.py allows no
     pnpm form, so they run through the verify gate and the Evidence plan, not here. This line only proves the
     e2e file the criteria name exists. -->

- $ test -f tests/e2e/lifecycle.spec.ts
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 1 | How do fixtures reach the browser in dev and e2e? | MSW service worker · Next route handlers serving fixtures | **MSW service worker**: the ticket names it and `msw` is already a devDependency | MSW service worker | recommended, unconfirmed |
| 2 | Data flag name and default | `NEXT_PUBLIC_LATHE_DATA` = `mock` \| `live`, default `mock` · a runtime toggle in the user menu | **`NEXT_PUBLIC_LATHE_DATA`, default `mock`**: build-time, nothing for users to flip | `NEXT_PUBLIC_LATHE_DATA` = `mock` \| `live`, default `mock` | @ishpr (confirmed 2026-11-02) |
| 3 | Endpoint path shape the live API will serve | `/api/v1/<resource>` on the same origin · an absolute base URL per environment | **`/api/v1/<resource>` on the same origin**: no CORS, one path for mock and live (lifecycle: `/api/v1/lifecycle`) | `/api/v1/<resource>` on the same origin | recommended, unconfirmed |
| 4 | How `public/mockServiceWorker.js` is created (the ticket's commands do not list `msw init`) | add `pnpm exec msw init public --no-save` to the commands · copy the file from `node_modules/msw` by hand | **add `pnpm exec msw init public --no-save`**: the generated file always matches the installed msw, and `--no-save` leaves package.json alone | add `pnpm exec msw init public --no-save` to the commands | @ishpr (confirmed 2026-11-02) |
| 5 | The three fixture epic titles (no fixture exists yet; docs name none) | "Per-key rate limiting" · "Audit log export" · "Webhook delivery retries" · wait for the fixtures story | **"Per-key rate limiting" · "Audit log export" · "Webhook delivery retries"**: invented now; the data-layer story later replaces the fixture and its titles | "Per-key rate limiting" · "Audit log export" · "Webhook delivery retries" | recommended, unconfirmed |
| 6 | How the e2e test flips the lifecycle mock from 500 to 200 | a test hook on `window`, registered only when the flag is `mock` · a `?mock=500` query parameter read by the handler · Playwright `page.route` | **a test hook on `window`, registered only when the flag is `mock`**: `page.route` does not see requests a service worker answers, and a query parameter ships in the URL | a test hook on `window`, registered only when the flag is `mock` | recommended, unconfirmed |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Start MSW only when the flag is `mock`; never register the worker in a production build.
- Show load failures through the program's view, not a React error boundary.
- The lifecycle Schema holds only the epic fields this story shows (id and title, plus any field the list renders); the data-layer story extends it.
- Copy the nearest existing pattern: `src/programs/_template/README.md`, `src/programs/_ui`, `scripts/new-program.mjs`, and `src/components/pages/_stub/page-stub.tsx` for the page header.
- `src/app/(dashboard)/lifecycle/page.tsx` renders a PageHeader and FoldkitHost only; the stub is removed from this page (the `_stub` component stays for other pages).
- The e2e file `tests/e2e/lifecycle.spec.ts` has a negative case (the 500 path); fixtures live under `src/lib/api/**`.
- Fixture state is held behind the endpoint's handler, not in module-level mutable state; the 500-then-200 switch goes through the test hook from Decision 6 with a reset.

## Capabilities

- Paths: `src/lib/api/**`, `src/lib/foldkit/**`, `src/lib/hooks/**`, `src/programs/lifecycle/**`, `src/components/pages/lifecycle/**`, `src/app/(dashboard)/lifecycle/**`, `src/app/providers.tsx`, `public/mockServiceWorker.js`, `tests/e2e/lifecycle.spec.ts`; read the repo
- Commands: `pnpm install`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm test:e2e`, `pnpm build`, `pnpm exec msw init public --no-save` (Decision 4)
- Egress: `registry.npmjs.org` (install only), `localhost:3100` (Playwright)
- Data: none (MSW fixtures only)

## Out of scope

- `package.json` and `pnpm-lock.yaml`: every dependency this story needs (`msw`, `foldkit`, `effect`, `@foldkit/ui`) is already installed; a new one is a contract change.
- `tests/e2e/shell.spec.ts` and `playwright.config.ts`: the shell test stays as it is.
- `src/lib/portal/**`, `src/components/shared/**`, `src/components/ui/**` and `src/components/layouts/**`.
- The data-layer, fixtures-story and integrity-check work (`getOrganization`, runs, evidence, decisions), other pages' endpoints, the live API behind `NEXT_PUBLIC_LATHE_DATA=live`, and sign-in.
- The ratchet debt in `tests/e2e/shell.spec.ts` (3 tests with no assert, no negative case).

## Allowed paths

- `src/lib/api/**`
- `src/lib/foldkit/**`
- `src/lib/hooks/**`
- `src/programs/lifecycle/**`
- `src/components/pages/lifecycle/**`
- `src/app/(dashboard)/lifecycle/**`
- `src/app/providers.tsx`
- `public/mockServiceWorker.js`
- `tests/e2e/lifecycle.spec.ts`

## Risk tier

low — a read-only page over fixtures in the browser, no auth, data store, money or migration, and no allowed path matches `tier_high_paths`.

## Evidence plan

- E2E tests `lifecycle lists the three fixture epics`, `lifecycle shows a retryable error when the endpoint fails` and `lifecycle is axe clean in dark and light` in `tests/e2e/lifecycle.spec.ts`, each failing before and passing after (`pnpm test:e2e`).
- `pnpm test:e2e` output showing `tests/e2e/shell.spec.ts` passing.
- A Playwright screenshot of `/lifecycle` at 1440x1000, attached to the PR.
- `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build` green, and a check that the MSW worker is not registered in the production build.
