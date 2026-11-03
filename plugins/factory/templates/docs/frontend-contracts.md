# Frontend contracts: Foldkit programs

> Scripts named `scripts/NAME` and `lib/NAME` below live in the plugin, not in this repo. Run one from a marketplace
> checkout as `<checkout>/plugins/factory/scripts/NAME`, or ask a session to run it.

A backend story is contracted with requests and status codes; the pack checks routes, auth and state
with an AST. A Foldkit frontend gets the same treatment, because its architecture names every part of
a page: one Model (an effect Schema) holds all state, Messages from `defineMessageUnion` are the only
way it changes, `update` is pure, side effects are Commands (`Command.define`, values the runtime
executes), and views return Html built with role and label attributes. Each of those is something a
contract line or a checker can point at.

## The rule: pages are programs

- Every page's state and interactions live in a program under `src/programs/<page>/`: `model.ts`,
  `message.ts`, `update.ts`, `commands.ts` (or `command.ts`), `view.ts`, plus `<page>.story.test.ts`
  and `<page>.scene.test.ts` (`story.test.ts` and `scene.test.ts` also count).
- The host (React, Next.js, plain HTML) only mounts a program. A page file holds no state, effects or
  handlers of its own.
- Every backend call is a Command whose `execute` calls a function from the API call table
  (`src/lib/api/endpoints.ts`). A program never calls `fetch` with its own URL.
- Every interactive element has a role or a label, so a scene test can reach it the way a user does.
- The Model and Messages carry no credentials. DevTools records both, and the DevTools MCP lets an
  agent read them.
- Folders whose name starts with `_` (`_template`, `_ui`) are shared code, not programs; the checkers
  skip them.

## How a frontend story is contracted

The contract's `## Acceptance` lines run through `acceptance.py`, exactly as backend lines do.

Story tests are executable acceptance for state (`foldkit/story` on Vitest: `given`, `message`,
`Command.resolve`, `model`). Scene tests are executable acceptance for the view (`foldkit/scene`:
`role`, `label`, `placeholder`, `text` locators, `click`, `expect(...).toExist()`), without a browser
and without mocks. A frontend story names one of each per program it touches:

```
- $ pnpm exec vitest run src/programs/<page>/story.test.ts
  => exit 0
- $ pnpm exec vitest run src/programs/<page>/scene.test.ts
  => exit 0
```

Use the file names the program has (`src/programs/runs/runs.story.test.ts`). The edit guard freezes
story and scene tests like any other test once the contract is approved.

Live acceptance drives the running app through its DevTools relay: a list item
`- mcp: foldkit_dispatch_messages [...]` (a JSON array of Message objects, each with its `_tag`)
followed by `=> model.<path> == <value>` (a JSON value; `!=` also works):

```
- mcp: foldkit_dispatch_messages [{"_tag": "ClickedRefresh"}]
  => model.isLoading == true
```

`acceptance.py` dispatches the Messages, reads the Model at that path (`foldkit_get_model`) and
compares. The relay is dev-only: the line runs only when `FOLDKIT_DEVTOOLS_URL` names a relay on
localhost or 127.0.0.1, and otherwise fails ("devtools relay not configured"); it is never skipped.
Dispatch needs the `Message` Schema in the app's `devTools` config, behind a development check.

## What the checkers flag

`/factory:init` adds five checkers when a `package.json` (the root or a workspace package) depends on
`foldkit`. They are regex and bracket matching over the TypeScript sources (no node needed), and their
keys go through the ratchet like every other check, so existing debt is recorded once and only new
findings fail.

| Checker | Ratchet key | Flags | Fix |
|---|---|---|---|
| `fe_story` | `fe-story\|<message file>\|<Tag>` | a Message tag no story test of its program sends (`Tag(` or `Message.Tag(` in a story file; comments do not count) | a story with `message(Message.Tag(...))`, or `Command.resolve(Cmd, Message.Tag(...))` for a result Message |
| `fe_scene` | `fe-scene\|<view file>\|<tag>:text(<text>)` (or `<tag>:on(<Message>)`, `<tag>:#<n>`) | an interactive element (button, a, input, select, textarea, or anything with `OnClick`) with no `Role`, `AriaLabel` or `AriaLabelledBy`, no label (`Id` + a label's `For`, or inside a label), and for button and `a` (with `Href`) no content to name it: a scene can reach it only by `text()` | a button, or `Role(...)` plus `AriaLabel(...)`, or a label for the field |
| `fe_scene` | `fe-scene\|<scene file>\|no-toExist` | a scene file that never asserts `expect(...).toExist()` | assert the elements the scene reaches, by role or label |
| `fe_api` | `fe-api\|<METHOD PATH>`, `fe-api\|~<METHOD PATH>` | a Command's `execute` passes a URL (starting with `/` or `http`) to `fetch` / `get` / `post` / ... that no function in the API call table exposes; `~` marks a templated URL (low confidence) | add an endpoint function to the table and call it from `execute` |
| `fe_secret` | `fe-secret\|<model file>\|<field>` | a Model field named like a credential (password, secret, token, api key, authorization, credential) that is not `Schema.Redacted` | keep it behind the server; if the browser must hold it briefly, `Schema.Redacted`; a false positive ends its line with `// secret: ok <reason>` |
| `fe_devtools` | `fe-devtools\|<file>` | `show: 'Always'` in a `devTools` config, or a fixed `devToolsMcpPort`, with no development check (`import.meta.env.DEV`, `NODE_ENV`/`MODE`, vite's `command === 'serve'`) | `devTools: import.meta.env.DEV ? { ... } : false` |

Calls through the table's functions count as matched; a templated URL's parameters (`${id}`, `:id`,
`{id}`) match any segment of a table path, and a table path's method (a `method:` option or a
`.post(` style call; none means GET) must agree.

The frontend block in `.claude/sdlc.json`:

```json
"frontend": {"framework": "foldkit", "programs": ["src/programs/**"], "api_table": "src/lib/api/endpoints.ts", "oxlint": true}
```

In a workspace the paths carry the package folder (`apps/console/src/programs/**`). `--upgrade` never
adds the block or the checkers (they would report today's findings as new), and a plain rerun keeps an
existing `.claude/sdlc.json` as it is; both print the block to add, after which a human runs
`scripts/ratchet.py --update`.

## oxlint with the Foldkit plugin

With `"oxlint": true` the ratchet runs oxlint with `@foldkit/oxlint-plugin` over the programs folder,
from the frontend package: the package's `.oxlintrc.json` when it has one (extend
`./node_modules/@foldkit/oxlint-plugin/recommended.json` there), otherwise the plugin's recommended
config. Each diagnostic becomes `lint|<file>|foldkit/<rule>` (`no-module-level-mutable-state`,
`no-hardcoded-route-strings`, `no-impure-call-at-decision-time`, `no-disabling-dev-guardrails`, ...),
the same key shape as ESLint. When oxlint or the plugin is not installed the ratchet prints a note and
does not ratchet those keys (they are neither stale nor pruned); under CI or `--require-tools` that
is exit 2, like any other missing tool.

## What verify records

`/factory:verify` adds a `frontend` step after the ratchet and a block to `.claude/work/last_verify.json`:

```json
"frontend": {"framework": "foldkit", "programs": 3,
             "stories": {"tags": 24, "covered": 24}, "scenes": {"files": 3, "with_exist": 3},
             "api": {"calls": 9, "matched": 9}, "oxlint": {"findings": 0}}
```

The step fails when an `fe-story` or `fe-scene` key is new or grown versus `.claude/baseline.json`: a
Message tag without a story test, or a view element a scene cannot reach by role or label. A brownfield
app's recorded debt does not block it; burn it down with `scripts/ratchet.py --all`. `oxlint.findings`
is `null` (with a note) when oxlint did not run.

## Limits

The checkers read text, not types. A Message union assembled from imported pieces, an element built by
a helper outside the program (the `_ui` folder is skipped), a URL computed far from the call, or a
DevTools config held in a variable not named `devTools` can be missed; a false positive is fixed in the
code or recorded in the baseline by a human, never by editing the checker.
