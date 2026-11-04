# lib

Standard-library Python that the factory's hooks and scripts share, so each rule is written once. `sdlc.py` loads the config and the contract and decides what a path is. `skeleton.py` decides whether a new file only declares. `test_reasons.py` turns a test runner's output into one reason per failing test. `e2e.py` starts the app an end-to-end suite drives. `frontend_foldkit.py` checks a Foldkit frontend. No module here imports code from the project under review.

## Import convention

Hooks and scripts run as `python3 -I`. Isolated mode ignores the `PYTHON*` environment variables, so `PYTHONDONTWRITEBYTECODE` does nothing. It also leaves the script's own directory off `sys.path`. Each entry point therefore turns bytecode off in code and adds `lib/` itself before it imports a module, usually like this:

```python
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402
```

Without the first line, an import would leave a `__pycache__` inside the plugin. Scripts under `scripts/checkers/<stack>/` use three `..` instead of one. Modules are imported by plain name: `import sdlc as cl`, `import skeleton`, `import test_reasons as tr`, `import e2e`, `import frontend_foldkit as fk`. `frontend_foldkit.py` does the same for `sdlc`, inserting its own directory.

## `sdlc.py`

Config, contract, paths, hook input and the block log.

- Locations. `plugin_root()` returns `CLAUDE_PLUGIN_ROOT`, else the directory above `lib/`. `project_root(hint)` returns `CLAUDE_PROJECT_DIR`, else the git top level of the hint or the cwd (also in a worktree), else that directory. It never climbs to a parent. `rel_to(root, path)` returns the repo-relative path with symlinks resolved on both sides and the prefix compared without case. It returns a `..` path when `path` is outside.
- Git. `git(root, *args)` returns stdout. `run(cmd, cwd, shell, input, env)` returns a `CompletedProcess`. `safe_git_env()` sets `core.fsmonitor=false`, `core.pager=cat` and `GIT_OPTIONAL_LOCKS=0` and drops `GIT_EXTERNAL_DIFF`, so repo config cannot run code in a guard. `changed_paths(root, *pathspecs)` lists the paths `git status --porcelain -uall` reports, untracked files one by one.
- Config. `load_config(root)` merges `DEFAULTS`, then `stacks/<stack>.json` (for `STACKS`: python, node, go, rust), then `.claude/sdlc.json` (`CONFIG_REL`). Dicts merge and lists are replaced. It adds `_configured`, `_config_error`, `_root` and `_path`.
- Config problems. `sanitize_config(data)` returns `(usable config, problems)`. A key of the wrong type falls back to its default and is reported, so one bad key cannot switch off the rest. While `_config_error` is set, `guard_edit.py` and `guard_bash.py` deny every edit and command with the rule `config-invalid` until a human repairs the file.
- Checked keys. `paths` (`app`, `tests`, `protected`, `protected_branches` are string lists), `tier_high_paths`, `extensions`, `checkers`, `secret_patterns_extra` (each must compile), `commands` and `auth` (objects), `stack` (string), `require_tests` (boolean), `graph` (through `graph_block(value)`; providers in `GRAPH_PROVIDERS`) and `e2e` (through `problems` in `e2e.py`). Other keys, `frontend` among them, pass through unchecked.
- Contract. `load_contract(root)` returns the parsed `.claude/work/current.json` (`CONTRACT_REL`), or `None` when it is missing or not JSON. It does not check that the result is an object. `load_stamp(root)` does the same for `.claude/work/last_verify.json` (`STAMP_REL`). `load_frozen_tests(root, contract=None)` returns `{repo-relative path: sha256}` from `frozen_tests`. `tier_high_paths(cfg)` returns the config's `tier_high_paths`, else `TIER_HIGH_DEFAULT`.
- Matching. `match_one(rel, pattern, fold=False)` and `matches(rel, patterns, fold=False)` use `fnmatch`. In addition `dir/**` matches `dir` itself, a pattern with no `*` matches as a directory prefix, and `**/x` matches a top-level `x`.
- Test and app paths. `is_test_path(cfg, rel)` is true for the config's `paths.tests`, for any `test`, `tests`, `__tests__` or `spec` directory, for a name starting `test_` or `conftest`, ending `_test.py`, `_test.go` or `_test.rs`, or containing `.test.` or `.spec.`. `is_app_path(cfg, rel)` is true when the path is not a test path and matches `paths.app`.
- File lists. `has_stack_extension(cfg, rel)` checks `extensions`. `walk_files(root, patterns, exts)` returns sorted repo-relative paths and skips `.git`, `node_modules`, `.venv`, `target`, `dist`, `build`, `.claude` and the tool caches.
- Protected files. `is_protected(rel, extra)` is true for `ALWAYS_PROTECTED` plus `paths.protected`, compared case-folded. Among them: the config, baseline, stamp and block log, the settings files, the hook, rule, agent and skill directories, `.git/config`, `CLAUDE.md`, `.mcp.json`, `.env*` and the freeze and acceptance scripts. `.env` templates (`.example`, `.sample`, `.template` and similar) are ordinary files.
- Hook input. `read_hook_input()` returns `{}` for empty stdin and `None` for unreadable or non-object JSON. The PreToolUse guards block on `None`. `deny(message)` writes `Blocked: ...` to stderr and exits 2. `block(root, hook, tool, rule, detail, message)` calls `log_block`, then `deny`.
- Block log. `log_block` appends one JSON line to `.claude/work/blocks.jsonl` (`BLOCKS_REL`). It writes only when `.claude` exists, scrubs the detail with `scrub(text)` before it cuts it to 200 characters, creates the file 0600 and never raises.
- Secrets. `find_secrets(text, cfg, rel)` returns `(label, first 12 characters)` or `None`. It tries the 14 `SECRET_PATTERNS` and the config's `secret_patterns_extra`. The labels `hard-coded credential` and `URL with a password` are skipped in test paths, `.md` files and template files (`.example`, `.sample` and similar). A match that says what it is (`example`, `changeme`, `xxxx`, `${VAR}`) is not a secret.

Used by the hooks `guard_bash.py`, `guard_edit.py`, `post_edit.py`, `session_start.py` and `stop_check.py`. `guard_agent_spawn.py` and `record_agent_run.py` do not import it. Also used by the scripts `verify.py`, `ratchet.py`, `freeze_tests.py`, `tests_failing_first.py`, `e2e.py`, `init_project.py`, `graph.py`, `maturity.py` and `security_assess.py`, by every checker under `scripts/checkers/<stack>/`, and by `frontend_foldkit.py`.

## `skeleton.py`

`is_skeleton(rel, text)` returns `(True, "")` when a file only declares, else `(False, why)` naming the first offending line. `/factory:tests` may write a skeleton of a module the new tests import, so the import resolves and each test fails because the behaviour is missing. A file with no logic cannot carry unverified behaviour, so the commit guard lets a new skeleton ride in a tests-only commit without a verify stamp.

- Python is judged on the `ast` tree. Allowed: docstrings, imports, `pass` at module or class level, `if TYPE_CHECKING:` blocks that only import, classes holding methods, attributes and nested classes, and constants (literals, tuples, lists, sets and dicts of constants, a signed number, a bare name or attribute).
- In Python every function body must be `raise NotImplementedError`, with constant arguments if any, after an optional docstring. A default value must be a constant. In a function, `pass`, `...`, a docstring alone, `return` and another exception are refused. As a value, a call, `1 + 2` and an f-string are refused.
- TypeScript and JavaScript (`.ts .tsx .js .jsx .mjs .cjs .mts .cts`) are judged on text with comments and string contents blanked. None of `return await yield if else for while do switch case try catch finally delete` or `void 0` may appear. Each function body is empty or one `throw new Error(...)`, whose message, if it has one, says "not implemented" (any case; `NotImplemented` passes).
- The only call allowed is `new Error(...)`. An arrow outside a type declaration or a parameter annotation must open a `{` body. `type` and `interface` declarations may use arrows and call-shaped signatures.
- Any other suffix is refused: "only Python, TypeScript and JavaScript files can be skeletons". The check prefers refusing an odd skeleton to accepting logic, and the message says what to take out.

Used by `hooks/guard_bash.py` only. Its `skeleton_problem` accepts a path only if it is new (`exists at HEAD; a skeleton is a new file` otherwise). It judges the content git will commit: the index for a plain `git commit`, the worktree for `-a`, a pathspec or a preceding `git add`.

## `test_reasons.py`

Reads a runner's text output and gives each failing test one reason. The rule behind `/factory:tests`: a new test is frozen only if it fails because the behaviour is missing. A test that is itself broken would never pass, and freezing it hands `/factory:implement` a test it cannot satisfy.

- Reasons. `REASONS` holds nine. `INTENDED` is `not_implemented`, `assertion` and `status_mismatch`. `WRONG` is `import_error`, `attribute_error`, `name_error`, `syntax_error` and `collection_error`: the test is broken and must be fixed before it is frozen. `other` is in neither list. `is_wrong("other")` is false, so `tests_failing_first.py` accepts it and prints it with the intended reasons.
- `parse(output, runner, report=None, root=None)` returns `{"results": [{test, file, reason, detail, module?}], "passed": {file: n}}`. Playwright adds `"skipped"` and `"errors"`. An unknown runner raises `ValueError`. `module` names what an `import_error` could not find.
- pytest (`parse_pytest`, `-rA` output). The last exception line of a failure section decides.
  - `NotImplementedError` is `not_implemented`. `AssertionError` and pytest's `Failed` are `assertion`, or `status_mismatch` when the section compares HTTP statuses (`assert 200 == 429`, `.status_code`).
  - `ImportError` and `ModuleNotFoundError` are `import_error`. `AttributeError` is `attribute_error`. `NameError`, `UnboundLocalError` and a missing fixture are `name_error`. `SyntaxError`, `IndentationError` and `TabError` are `syntax_error`. Any other type is `other`.
  - A file that fails while pytest collects it keeps a wrong reason, else it is `collection_error`. So a `NotImplementedError` raised at import time is a `collection_error`, and so is a path pytest could not find.
- vitest (`parse_vitest`, default reporter). It reads the `FAIL file > suite > test` blocks and the per-file `(N tests | M failed)` lines, and accepts the `|project|` prefix of a named project. `classify_node` takes the first rule that fits.
  - `not implemented` in the message is `not_implemented`. A missing module, package or export is `import_error`.
  - `AssertionError` is `assertion`, or `status_mismatch` for `expected 200 to be 429`. `ReferenceError` is `name_error`. `SyntaxError`, `Transform failed` and `PARSE_ERROR` are `syntax_error`.
  - A `TypeError` with `Cannot read properties of undefined` or `is not a function` is `attribute_error`. `No test suite found` is `collection_error`. Anything else is `other`.
  - A file-level failure (`FAIL file [ file ]`) that is not already wrong becomes `collection_error`.
- Playwright (`parse_playwright`). It reads the `json` reporter's report (passed in, or printed in the output), else the `list` reporter's text. `classify_playwright(message, block, status)` decides.
  - Intended: a failed `expect(...)` or a timeout waiting for a locator or page state (`assertion`, or `status_mismatch` for a status comparison), and a `not implemented` throw.
  - Wrong: a web server that did not start (`config.webServer`), `No tests found`, a refused or timed-out `page.goto`, a browser that did not launch (`browserType.launch`), a spec that does not parse or import, and a `ReferenceError` or attribute error in the test.
  - `other`: an error raised inside `page.evaluate`, which means the page lacks something. Run-level errors that name no spec go to `"errors"`.
- Helpers. `detect_runner(command)` returns `pytest`, `playwright`, `vitest` or `None`, checked in that order. `runner_of_file(path)` returns `playwright` when the file imports `@playwright/test`. `module_candidates(module, test_file, runner)` lists the repo files an import names (`app.x` gives `app/x.py` and `app/x/__init__.py`). `advice(row, under_test)` returns one line saying what to fix for a wrong row, else `""`.

Used by `scripts/tests_failing_first.py`, which records a reason per test in `.claude/work/<slug>/failing_first.json`, by the e2e step of `scripts/verify.py` and by `scripts/e2e.py`. `freeze_tests.py` refuses a file whose latest verdict is wrong. The ledger's `failing_first_reasons` guard (`sdlc_graph/ledger/`) refuses the same five.

## `frontend_foldkit.py`

Contract checks for a Foldkit app, by text: regex and bracket matching over TypeScript, with comments blanked and string contents masked first, so formatting does not change the result. A Foldkit app is a set of programs under `src/programs/<page>/` (a Model, a Message union from `defineMessageUnion`, a pure `update`, Commands from `Command.define`, views). Folders whose name starts with `_` are shared code and are skipped.

Config is the `frontend` block of `.claude/sdlc.json` (`framework`, `programs`, `api_table`, `oxlint`) plus the checker names in `checkers`. `frontend_of(cfg)` returns `None` unless `framework` is `foldkit`. The five checkers are:

- `fe_story`, key `fe-story|<message file>|<Tag>`: a Message tag that no story test of its program sends.
- `fe_scene`, keys `fe-scene|<view file>|<locator>` and `fe-scene|<scene file>|no-toExist`: an interactive element a scene reaches only by `text()`, and a scene file with no `expect(...).toExist()`. Reachable means a `Role`, `AriaLabel` or `AriaLabelledBy` attribute, a field whose `Id` a label's `For` names or that sits inside a label, or a button or `a` with `Href` that has children. A spread of attributes gets the benefit of the doubt.
- `fe_api`, key `fe-api|<METHOD PATH>` (`fe-api|~<METHOD PATH>` for a templated URL, low confidence): a URL passed to `fetch`, `get`, `post` and similar calls (or a `url:` property) inside a `Command.define` `execute` that the api table (`api_table`) lacks. A call to a function the table exports is matched.
- `fe_secret`, key `fe-secret|<model file>|<field>`: a Model field named like a credential, matched on whole camelCase segments (`apiKey`, `accessToken`, not `tokenCount` or `isPasswordVisible`). It is skipped when the schema is `Redacted` or the line has `// secret: ok <reason>`.
- `fe_devtools`, key `fe-devtools|<file>`: `show: 'Always'` in a `devTools` config, or a fixed `devToolsMcpPort`, with no development check (`import.meta.env.DEV`, `NODE_ENV`, `__DEV__`, `isDev`, vite's `command === 'serve'`) in the property or an enclosing condition.
- oxlint. `run_oxlint(root, fe)` returns `(findings, note, skip prefix)` for keys `lint|<file>|<plugin>/<rule>`. If oxlint is missing, or there is neither an oxlint config nor `@foldkit/oxlint-plugin`, it returns a note and no findings, and the ratchet leaves those keys alone.
- `detect(root)` returns the `frontend` block when the root `package.json` or a workspace package depends on `foldkit`. `summary(root, cfg, oxlint=True)` returns the `frontend` block of the verify stamp (`framework`, `programs`, `stories`, `scenes`, `api`, `oxlint`) plus the story, scene and api findings under `_findings`.
- CLI. `--check <name> --root DIR` prints `key<TAB>file:line: message` per finding and exits 0 (none), 1 (findings) or 2 (misuse). `--summary` and `--detect` print JSON.

Used by `scripts/ratchet.py` (runs `--check` for each frontend checker named in `checkers`, calls `run_oxlint`), `scripts/verify.py` (`summary`; its frontend step fails on `fe-story` and `fe-scene` keys that are new or grown against the baseline, when those checkers are in `checkers`) and `scripts/init_project.py` (`detect`).

## `e2e.py`

Starts the app an end-to-end suite drives, from the `e2e` block of `.claude/sdlc.json`: `command`, `build`, `serve`, `url` and three timeouts in seconds (`ready_timeout`, `build_timeout` and `timeout`, defaulting to 120, 600 and 900). Playwright's own `webServer` runs the project's dev server, and a dev server with a file watcher dies inside Claude Code's sandbox. This module serves the built app instead.

- `problems(block)` lists why a block is unusable (`sdlc.py` reports it as a config problem). `settings(cfg)` returns the normalised block, or `None` when no command is named (`commands.e2e` alone names one).
- `start(root, e2e, say)` returns a `Lease` (`url`, `server` as `none`, `reused` or `started`, `stop()`) or raises `E2EError` saying what to do. With no `url` it manages nothing. If something answers at `url`, it is reused only when its process runs from this project (checked with `lsof`). Another checkout's server, or one whose directory cannot be read, is refused.
- If nothing answers and `serve` is unset, `start` raises. Otherwise it runs `build` once (when set), starts `serve` in its own process group and waits up to `ready_timeout` for any HTTP answer. `stop()` sends SIGTERM to the group, then SIGKILL. `run_suite(root, e2e, files, timeout=None)` runs `command` with `--reporter=list,json` and the report path in `PLAYWRIGHT_JSON_OUTPUT_FILE`, and returns `(process, report or None, command, report path)`.
- Recorded runs. The module docstring says the sandbox denies the Mach bootstrap check-in Chromium needs on macOS, so no browser launches in the gate. The suite therefore runs through `scripts/e2e.py run`, which the human excludes from the sandbox. `excluded_patterns(plugin_root)` builds the `sandbox.excludedCommands` entries.
- That script writes `report.json`, `output.txt` and `run.json` under `.claude/work/e2e/` (`RUN_DIR`) with the tree's `fingerprint(root)`. `run_paths(root)` names the files and `load_run(root)` reads them. `staleness(root, run, files)` returns a reason when no run is recorded, the tree changed since, or the run was narrowed to other files, else `None`. `counts(parsed)` returns `{"passed", "failed", "skipped"}`.

Used by `scripts/e2e.py`, `scripts/tests_failing_first.py`, `scripts/verify.py`, `scripts/init_project.py` (`excluded_patterns`) and `sdlc.py` (`problems`, imported on use). `sdlc_graph/orchestrator/dispatch.py` keeps its own copy of the two exclusion spellings in `E2E_EXCLUDED`.

## How it is tested

The suite is `tests/sdlc/`, standard library plus pytest. CI runs `python3 -m pytest plugins/factory/tests/sdlc -q` on Python 3.11. The tests build a throwaway git repo, run the real hooks and scripts against it and check exit codes and messages, so most of `lib/` is tested through its callers.

- `sdlc.py`: `test_hooks.py` (protected files, secrets, `test_a_broken_config_blocks_every_guard_with_the_reason`, `test_a_config_key_of_the_wrong_type_falls_back_per_key`, the tier-high tests, the block log) and `test_graph_option.py`, which imports it and checks `GRAPH_DEFAULTS` against `sdlc_graph/graphcfg.py`.
- `skeleton.py`: six commit-guard tests in `test_hooks.py`, from `test_a_tests_commit_may_carry_the_new_skeleton_modules_the_tests_import` to `test_a_skeleton_once_committed_is_an_existing_module_for_the_next_commit`.
- `test_reasons.py`: `test_scripts.py` loads it by path and parses runner output (`test_reasons_classify_every_pytest_failure_shape`, `test_reasons_read_vitest_output_from_a_config_with_named_projects`, `test_reasons_classify_every_playwright_failure_shape`), then drives `tests_failing_first.py` and the freeze with it.
- `e2e.py`: `test_e2e_run_records_the_suite_and_the_recorder_reads_it_and_the_freeze_takes_the_spec`, `test_e2e_run_refuses_a_port_held_by_another_checkout` and `test_e2e_run_reuses_the_checkouts_own_running_server`.
- `frontend_foldkit.py`: `test_frontend_foldkit.py` runs it against `tests/sdlc/fixtures/foldkit`, a small app with one planted finding per checker.

## Limits

- No test calls `is_skeleton` or anything in `lib/e2e.py` on its own. They run through the commit guard and the scripts.
- The e2e tests use a stand-in suite script and `http.server`. No test starts a real browser, Playwright or `next build`.
- The runner parsers read text. The tests parse excerpts that their comments label as real pytest 9, vitest 5 and Playwright 1.64 output, trimmed. Another version or reporter may print something else.
- `other` is not a wrong reason, so a test that fails with an exception the parser does not know, such as a `KeyError` in the test body, passes the failing-first check. Without a traceback (`--tb=no`) a status assert reads as `assertion`, not `status_mismatch`.
- `detect_runner` reads `e2e` anywhere in a command as Playwright, so `pnpm vitest run tests/e2e-helpers` is taken for Playwright. `go test` and `cargo test` have no parser, and `tests_failing_first.py` then exits 2 and names the supported runners.
- The ledger keeps its own copy of the reason lists (`WRONG_FAILURE_REASONS` in `sdlc_graph/ledger/evidence.py`). No test compares it with `WRONG`.
- Skeleton, Python: decorators, base-class expressions and annotations are not inspected, so `@app.get("/x")` passes.
- Skeleton, TypeScript: the rules are text, not a parser. A call inside parentheses (a default parameter) or inside a template literal's `${...}` is not seen, and `1 + 2` passes. A type member named like a keyword (`delete`, `case`) is refused, and so is any decorator.
- `match_one` is `fnmatch`, so `*` also crosses `/`: `tests/*` matches `tests/a/b.py`.
- `changed_paths` strips the outer quotes git puts around an unusual name and does not decode the escapes inside.
- `load_contract` and `load_stamp` return `None` for a missing file and for an unparsable one alike.
- `e2e.py` needs `lsof` to find who holds a port and uses process groups, so it targets macOS and Linux. Any HTTP answer, a 500 included, counts as the app being up.
- `frontend_foldkit.py` knows only the Foldkit shapes named above. Its docstring lists `--config PATH`, which `main` does not read: the config comes from `.claude/sdlc.json` under `--root`.
