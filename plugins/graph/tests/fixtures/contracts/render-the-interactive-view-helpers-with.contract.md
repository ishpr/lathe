# Contract: render-the-interactive-view-helpers-with

## Outcome

An engineer building a Foldkit page program can drop in a toggle row, decisions table, ask-once batch, gate table, data table or form controls, and every control is reachable by role and label and works from the keyboard, dispatching the Message it was given.

## Acceptance criteria

1. `toggle row switch named by the row label dispatches the toggle Message with the new value` — a scene test finds the switch by role and label, Tabs to it, presses Enter or Space, and the toggle Message arrives once with the new value.
2. `decisions table radio named by the option in group "Decision 2" dispatches the choose Message with decision 2 and the option` — scene test finds the radio inside the radio group named "Decision 2" and activates it by keyboard; the choose Message carries decision 2 and the option, once.
3. `ask-once batch button "Submit answers" dispatches the submit Message` — with every decision answered, Tab to the button then Enter or Space dispatches the submit Message once.
4. `gate table switch named by the gate dispatches the toggle Message with the gate` — the switch "Require tests frozen" is found by role and label and activated by keyboard; the toggle Message carries that gate, once.
5. `data table button named by the column dispatches the sort Message with the column` — the button "Sort by cost" is found by role and label and activated by keyboard; the sort Message carries the cost column, once.
6. `form controls textbox, combobox and checkbox named by their label dispatch the change Message with the value` — one scene test per control kind; each is found by role and label, changed or activated by keyboard, and the change Message carries the value, once.
7. `disabled toggle shows its reason and dispatches nothing` — a toggle row with `disabled` and the reason "Set by managed settings" shows that text next to the switch and no Message is dispatched when it is activated. *(negative)*
8. `ask-once batch blocks submit until every decision has a value` — with 3 decisions of which 1 has no value, "Submit answers" is disabled, "Answer 1 more decision to submit" is shown, and activating the button dispatches nothing. *(negative)*

## Acceptance

<!-- The ticket's runnable check is `pnpm test` (vitest, project "programs": scene tests under
     src/programs/**/*.test.ts). acceptance.py does not allow pnpm and the repo has no Makefile, so
     the gate (`npm test --silent` per CLAUDE.md, `pnpm test` per sdlc.json) runs the tests; the
     line below only proves the helpers' home exists. -->

- $ test -f src/programs/_ui/index.ts
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 11 | Where do control behaviours come from? | `@foldkit/ui` where it has the control, Html with shell classes otherwise · Html with shell classes only | **`@foldkit/ui` where it has the control, Html with shell classes otherwise**: keyboard and ARIA behaviour comes tested instead of hand-rolled | `@foldkit/ui` where it has the control, Html with shell classes otherwise | recommended, unconfirmed |

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Give every control a 44px minimum target (`CONTROL` in `src/lib/design/tones.ts`) and the shell's 2px focus ring classes; add new shared class strings to `src/lib/design`, not inline in `src/programs/_ui`.
- Each helper takes the Message (or a function from the value to a Message) as an argument and never imports a program's Message types.
- Scene tests live beside the helpers as `src/programs/_ui/*.test.ts` (the vitest "programs" project, node, no browser).
- New helpers are exported from `src/programs/_ui/index.ts`.
- A disabled control renders its reason as visible text next to the control and dispatches nothing.

## Capabilities

- Paths: write `src/programs/_ui/**` and `src/lib/design/**`; read the repo
- Commands: `pnpm lint`, `pnpm typecheck`, `pnpm test`
- Egress: none
- Data: none

## Out of scope

- The read-only view helpers (the dependency ticket) and any change to their output.
- `src/components/**` (the React shell, including `src/components/shared/data-table.tsx`) and `src/app/**`.
- `src/lib/foldkit/**`, `src/programs/_template/**` and every page program that will use the helpers.
- `package.json` and the lockfile: `@foldkit/ui` 0.170.0 is already a dependency.

## Allowed paths

- `src/programs/_ui/**`
- `src/lib/design/**`

## Risk tier

low — view helpers and class strings only; no path matches `tier_high_paths`, and no auth, money, data or migration is touched.

## Evidence plan

- The eight scene tests above, each failing before the helpers exist and passing after, shown by `pnpm test`.
- `pnpm lint` and `pnpm typecheck` clean, and `ratchet.py` reports no new violations against the baseline.
