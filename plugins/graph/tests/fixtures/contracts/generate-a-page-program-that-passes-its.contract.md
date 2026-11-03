# Contract: generate-a-page-program-that-passes-its

## Outcome

An engineer or agent runs `pnpm new-program <page>` and gets a complete Foldkit program (model, message, update, command, view, ports, story and scene tests) whose own tests and lint pass, with the architecture rules explained in `src/programs/CONVENTIONS.md`.

## Acceptance criteria

1. `generated program passes its story and scene tests` — after `pnpm new-program sample-page`, the folder holds `model.ts`, `message.ts`, `update.ts`, `command.ts`, `view.ts`, `ports.ts`, `sample-page.story.test.ts` and `sample-page.scene.test.ts`, and `pnpm vitest run --project programs src/programs/sample-page` exits 0 with at least one story test per Message tag in `message.ts`.
2. `generated program is lint clean` — `oxlint src/programs/sample-page` reports 0 errors on the generated folder.
3. `refuses an existing id` — a second `pnpm new-program sample-page` exits 1, prints "src/programs/sample-page already exists. Pick another id or edit it in place." and no file in the folder changes (content and mtime). *(negative)*
4. `refuses a non-kebab id` — `pnpm new-program Runs` exits 1, prints "Give a page id in kebab case, for example: pnpm new-program runs" and `src/programs/Runs` is not created. *(negative)*

## Acceptance

<!-- acceptance.py cannot run pnpm, so the four tests above are proven by the gate (`pnpm test`).
     These lines prove the files the tests depend on exist. -->

- $ test -f src/programs/CONVENTIONS.md
  => exit 0
- $ test -f src/programs/_template/model.ts.tmpl
  => exit 0

## Decisions

| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 4 | Where does the conventions file live? | `src/programs/CONVENTIONS.md` · `src/lib/foldkit/README.md` | **`src/programs/CONVENTIONS.md`**: next to the programs it governs | `src/programs/CONVENTIONS.md` | recommended, unconfirmed |
| 5 | The ticket depends on the Lifecycle Foldkit program, which does not exist yet (`src/programs` holds only `_template` and `_ui`; `src/lib/api` and `src/programs/_ui` are placeholders). What does the template follow? | Build the template from the installed `foldkit` package APIs (`foldkit/story`, `foldkit/scene`) and keep `command.ts` free of `src/lib/api` imports · Wait for the Lifecycle program | **Build from the package APIs**: the template is the first program, the Lifecycle page can adopt it later | Build from the package APIs; `command.ts` uses a local stub Command | recommended, unconfirmed |
| 6 | Files named `*.test.ts`, `*.ts` under `src/programs/_template` are picked up by vitest (`src/programs/**/*.test.ts`), oxlint (`src/programs`) and tsc. `vitest.config.ts` and `tsconfig.json` are outside the allowed paths. How are template files kept out of them? | Template files carry a `.tmpl` suffix that the script strips · Widen the allowed paths to edit `vitest.config.ts`, `tsconfig.json` | **`.tmpl` suffix**: stays inside the allowed paths, reversible | `.tmpl` suffix; the script strips it on copy | recommended, unconfirmed |

No route or owner turned up in the graph that the ticket does not name: the snapshot (8445b56) holds none of the allowed paths, so `graph_impact` returned empty.

## Defaults when ambiguous

1. Make the smallest change that satisfies the acceptance criteria.
2. Follow the nearest existing pattern in the code being changed.
3. Prefer the option the Decisions above imply.
4. When none applies, choose the reversible option.

Per contract:

- Extend `scripts/new-program.mjs` (it already holds the kebab check, the exists check and the `__PROGRAM__` / `__Program__` replacement); do not rewrite it. Add the `.tmpl` suffix strip and keep both error messages byte-for-byte as the ticket states.
- Put the script's test in `src/lib/foldkit/new-program.test.ts` with `// @vitest-environment node`; it generates `sample-page`, runs vitest and oxlint on it as child processes, and removes `src/programs/sample-page` in `afterAll` (and before the run, in case an earlier run died).
- The placeholder `src/programs/_template/README.md` is replaced by the `.tmpl` files and deleted; `src/lib/foldkit/README.md` is left as it is.
- `CONVENTIONS.md` has one section per rule (Model, Message, update, Command, view, ports), each with one short example, and says where `.tmpl` files live.
- Generated test names use the Message tag as the story test name so "one story test per tag" can be checked by name.
- Naming: file names follow the placeholder README (`__PROGRAM__.story.test.ts.tmpl`, `__PROGRAM__.scene.test.ts.tmpl`).

## Capabilities

- Paths: `src/programs/_template/**`, `src/programs/CONVENTIONS.md`, `scripts/new-program.mjs`, `src/lib/foldkit/**`; read the repo.
- Commands: `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm new-program`, `pnpm vitest run --project programs src/programs/sample-page`, `pnpm exec oxlint src/programs`.
- Egress: none
- Data: none

## Out of scope

- `vitest.config.ts`, `tsconfig.json`, `.oxlintrc.json` and `package.json` (see Decision 6).
- `src/programs/_ui/**`, `src/lib/api/**`, `src/lib/design/**` and the React shell under `src/app/**` and `src/components/**`.
- `src/lib/foldkit/README.md` (the FoldkitHost placeholder) and the Lifecycle page program.
- The four known ratchet violations in `tests/e2e/shell.spec.ts`.

## Allowed paths

- `src/programs/_template/**`
- `src/programs/CONVENTIONS.md`
- `scripts/new-program.mjs`
- `src/lib/foldkit/**`

## Risk tier

low — a developer script and template files; no auth, money, data, migration or `.claude` path is touched.

## Evidence plan

- `pnpm test` output listing `generated program passes its story and scene tests`, `generated program is lint clean`, `refuses an existing id` and `refuses a non-kebab id`, each failing before and passing after.
- `pnpm lint` and `pnpm typecheck` clean on the diff; `src/programs/sample-page` absent after the test run.
