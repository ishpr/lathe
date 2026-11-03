# Contract: render-the-read-only-view-helpers-with-t

## Outcome

An engineer building a page program imports display helpers from `src/programs/_ui` that carry the shell's tokens, so a program's view and the React shell look identical.

## Acceptance criteria

Scene tests sit beside the helpers in `src/programs/_ui` (`*.test.ts`), render each helper with fixture props and query by role and name.

1. `card renders a region named by its title` — the region is found by its title and shows the title.
2. `kpi with sparkline renders a group named by its label` — the group shows the value and an image named "Trend, last 30 days".
3. `segmented bar renders one segment per value` — the image named by its label has one segment per value.
4. `stage strip and stage bar render one item per stage with its count` — the list named "Stages" has one item per stage, each with its count.
5. `feed timeline renders one item per entry, newest first` — the list named "Activity" has one item per entry, ordered newest first.
6. `model chip, outcome chip and chip variants show their text` — the model, outcome or label text is found.
7. `evidence row renders a listitem with the evidence kind label` — the listitem shows the label, e.g. "Tests frozen".
8. `progress ring renders a meter named Progress` — the meter named "Progress" shows the percentage.
9. `caps list, tier table and terminal log render each row or line` — a list, a table and a log each show every row or line given.
10. `kpi without a series says there is no trend` — with an empty series the value and the text "No trend data yet" show, and no trend image is found. *(negative)*
11. `terminal log keeps the last line` — with 500 lines the log region is labelled "Session log" and the last line is the last line given. *(negative: bounds case)*
12. `helpers use no hex colour and no inline style` — no class list in `src/programs/_ui` holds a hex colour and no helper sets an inline style; `pnpm lint` reports 0 errors. *(negative)*

## Acceptance

The runnable checks are `pnpm test`, `pnpm lint` and `pnpm typecheck`. acceptance.py allows only pytest, bash smoke scripts, curl and `make test|check|lint`; the repo has no Makefile, so no line below can run through it. `/sdlc:verify` runs the gate (`npm test --silent`, the ratchet, the secret scan) instead.

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 9 | How is the sparkline drawn inside Html? | inline SVG from the helper · a Recharts island mounted by the shell | **inline SVG from the helper**: no island or client lifecycle inside a pure view | inline SVG from the helper | recommended, unconfirmed |
| 10 | Is there a gallery route for helpers? | none, the feature 2 pages check axe · a development-only gallery route | **none, the feature 2 pages check axe**: a route would be outside the allowed paths | none, the feature 2 pages check axe | recommended, unconfirmed |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Import class strings from `src/lib/design` instead of retyping class lists.
- Respect `prefers-reduced-motion` by rendering helpers without transitions.
- Scene tests are co-located with the helpers in `src/programs/_ui` as `*.test.ts`.
- Replace the placeholder `export {}` in `src/programs/_ui/index.ts` with the helpers' exports, and update `src/programs/_ui/README.md` only if it becomes untrue.
- A class string the helpers need that `src/lib/design` lacks is added there, not written inline.

## Capabilities

- Paths: write `src/programs/_ui/**`, `src/lib/design/**`; read the repo
- Commands: `pnpm test`, `pnpm lint`, `pnpm typecheck`
- Egress: none
- Data: none

## Out of scope

- The React shell and `components/**`, `src/app/**`: the helpers copy their look, they do not change it.
- `src/programs/_template` and any generated page program.
- A gallery route or page for the helpers (Decision 10).
- A Recharts island or any client-side chart code (Decision 9).
- `tests/e2e/**` and its known ratchet debt.

## Allowed paths

- `src/programs/_ui/**`
- `src/lib/design/**`

## Risk tier

low — view helpers and class strings only; no auth, models, migrations, money or data, and no tier-high path matches.

## Evidence plan

- One scene test per table row plus `kpi without a series says there is no trend` and `terminal log keeps the last line`, each shown failing before implementation and passing after.
- `pnpm lint` output for `src/programs/_ui` showing 0 errors.
- `pnpm typecheck` and `pnpm test` green; the ratchet shows no new violations.
- Graph scoping found nothing: the snapshot at 8445b56 has none of the target files, so there are no affected routes, tests or owners to check.
