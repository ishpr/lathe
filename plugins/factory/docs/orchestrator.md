# The orchestrator

`sdlc-graph orchestrate <feature>` walks every contract of a feature through the contract state
machine, one fresh Claude Code session per step, and moves the ledger only after its own gates
pass. It is Gas Town's set of mechanisms (docs/DESIGN.md section 12) rebuilt on the ledger:
the loop is deterministic Python, its only state is the ledger plus one lock row per feature, and the
only model calls it makes itself are the bounded judge (off unless `--judge`) and the conformance
reviewer.

It needs the ledger: before anything starts it asks `graph_status` (the same answer as `sdlc-graph status`), and
when the database is unreachable or not initialised it exits 5 with `ledger_unavailable`, the redacted URL and the
hint ("set DATABASE_URL ... then run `sdlc-graph db init`"), and starts no session. `docs/DESIGN.md` is in the
repository root, two levels above the plugin.

## 1. The mapping (design section 12.1)

| Gas Town | Here |
|---|---|
| Beads: the ledger of work | The ledger: nodes, transitions, evidence, decisions, runs. Nothing else is state. |
| Hook: work that persists in the worktree | `.claude/work/current.json` in the contract's worktree, written from the ledger before every step, plus the ledger row itself. A crashed session loses nothing. |
| Propulsion, `gt prime --hook` at SessionStart | The SessionStart hook runs `sdlc-graph prime` when `.claude/work/current.json` exists: contract, state, the one next step, the previous run's summary. |
| Molecule: a template instantiated as tracked steps, one session per step | `state_machines.json` is the formula; `orchestrator/steps.py` maps each state to one slash command; each step is a fresh `claude -p` session with the pack loaded. |
| Polecat: persistent identity, ephemeral sessions, own worktree | One worktree per contract, `<repo>-<slug>` on branch `sdlc/<slug>`, one run row per step. |
| Witness patrol: zombies, stalls, 3 crashes on a step, never nuke a dirty sandbox | `watchdog.py` every tick: dead process, no progress for `stall_minutes`, third failure on a step = P1 decision, a dirty worktree is escalated with its path and never deleted. |
| Deacon heartbeat and the Scheduler | The daemon loop, tick 60 s, `--max-runs` concurrent sessions, `--budget` per session and `--max-cost` per feature; restartable. |
| Convoy: a batch tracked to completion | The feature node; its `decomposes_into` contracts are the convoy. Done when every contract is `in_review` (default) or `merged` (`--refinery`), or a human skipped it. `sdlc-graph feed` is `gt feed`. |
| Refinery: merge queue, never push to main | `/factory:ship` opens a draft PR per contract. Merging stays human; `--refinery` merges a PR only when GitHub already reports it approved, and still never sets `approved`/`merged` in the ledger (human or CI actors only). |
| Mail and escalation severities | Decision rows with `severity` P0/P1/P2 and a dedupe key; the notify adapter prints to stderr (`ORCHESTRATOR_NOTIFY=none` silences it). `sdlc-graph escalations` lists them. |
| Seance: query predecessor sessions | The previous run's `summary` and `last_assistant_message` are stored on the run row and put into the next step's prompt. |
| Settings injected with `--settings` | Every session gets `--settings <generated>` (compiled capabilities, tool denies, hooks) and `--plugin-dir` for the plugin root (one root since the merge); the customer repo is not edited. |

## 2. The loop

```
orchestrate(feature):
  acquire lock(feature)                 # orchestrator_lock row; a second daemon exits (lock_held)
  every tick:
    heartbeat                           # the row gone (--stop) or taken over -> the daemon exits
    feature ready -> building           # the only feature transition the loop makes
    for contract in topo_order:         # depends_on edges, ties by slug; a cycle is an error
      patrol its open run               # watchdog: finished -> gates -> transition
      done (in_review|approved|merged, or skipped)?      next
      a dependency not merged (or, with --stack, not stackable)?  "waiting on <slug> (<state>)", next
      an open decision on the contract?                  report, next (the loop never answers for a human)
      drafted (or in_review/approved in refinery mode)?  report what the human must do, next
      third failure on this step, or conformance low a third time?  P1 decision, next
      capacity (--max-runs) and budget (--max-cost) allow?           dispatch the step
    sleep(tick)
  release the lock
```

`--once` runs one tick and exits (tests, cron). Which contracts may be dispatched is the next section.

### Dependencies (`depends_on`)

The edge points from the dependent to the dependency. A contract is eligible for dispatch only when every
contract it depends on is

- `merged` in the ledger, or skipped by a human; or
- with the daemon's `--stack` (or `ORCHESTRATOR_STACK=1`), the only dependency still unmerged, and `in_review`
  or `approved` (its code is pushed and has a pull request).

Anything else blocks the dependent. The tick reports `action: waiting_on_dependency` for it, with one line per
blocking contract in `detail` (`waiting on <slug> (<state>)`) and the same pairs in `waiting_on`; no decision is
raised, no worktree is made, no run row is written. Without `--stack` a dependency in `in_review` still blocks:
the dependent waits for the merge, so its worktree starts from a base branch that contains the dependency's code.

With `--stack` the dependent's worktree branches from the dependency's branch (`sdlc/<dependency>`) and its pull
request is opened against that branch (stacked pull requests; GitHub retargets it when the dependency merges).
The run rows record it in `meta`: `base`, `pr_base` and `stacked_on` (the dependency's slug, or null). The step's
`current.json` carries `stacked_on` and `pr_base`, and the `ship` prompt tells the session to
`gh pr create --base <pr_base>`. A branch has one parent, so with two or more unmerged dependencies the
dependent waits (every one of them is listed) until at most one is left. A dependency that has no pull request
yet (`ready` through `verified`) always blocks.

A `depends_on` cycle is refused: the first tick raises one P1 decision on the feature (deduped by the members,
options `retry`), marks every contract `dependency_cycle`, dispatches nothing and the daemon exits with
`stopped: dependency_cycle`. Remove an edge in the ledger and start the orchestrator again.

`--stack` is read from `Config.stack`, whose default is `ORCHESTRATOR_STACK` (`1`, `true` or `yes`).

### Steps

| State | Command | Expected evidence (since the run started) | Target | Extra gates |
|---|---|---|---|---|
| `ready` | `/factory:tests` | `tests_failing_first`, `tests_frozen` | `tests_frozen` | a change in allowed paths |
| `tests_frozen` | `/factory:implement` | (none in the ledger) | `implementing` | a change; frozen tests; conformance |
| `implementing` | `/factory:verify` | `verify_stamp` | `verified` | frozen tests; ratchet |
| `verified` | `/factory:ship` | `pr_url` (or the branch's PR read with `gh pr view`) | `in_review` | frozen tests; github.com egress for this step |

`drafted` needs a human (decisions, capabilities approval); `in_review` and later need a human or CI.

### Dispatch

For one step, `dispatch.py`:

1. creates or reuses the worktree `<repo>-<slug>` (branch `sdlc/<slug>`), sets it up when the repository
   asks for that (below, "Worktree setup"), and notes its HEAD as the run's `start_commit`;
2. writes `.claude/work/current.json` from the ledger (slug, allowed paths, tier, reviewer, acceptance,
   capabilities, `frozen_tests`/`frozen_at` from the newest `tests_frozen` evidence that still describes
   the branch: not named by a `superseded` row and on a commit reachable from the worktree's HEAD) and, if the
   worktree has none, `.claude/work/<slug>/contract.md` (copied from the repo, else rendered from the
   ledger row);
3. writes the settings file: `capabilities.compile_full(...)` (sandbox paths and egress, edit
   pre-approvals, credential read denies), plus denies for `contract_transition` and
   `decision_resolve` under both MCP names (the session records evidence; the orchestrator moves
   state), plus the Agent-spawn hooks with absolute paths when the plugin is not among
   the `--plugin-dir`s;
4. writes the prompt: the slash command, the contract, the allowed paths and acceptance, the
   previous run's summary and last message, and why the last attempt failed;
5. records `run_start` with `requested_model` from the routing policy (sonnet for all four steps)
   and starts the executor in its own process group; stores pid, session id, start commit, the
   log path, `executor` and `auth_source` (below) on the run row.

### Worktree setup (`commands.setup`)

A worktree is a fresh checkout, so a node or python project has no `node_modules` or `.venv` there and every test
command would fail until the dependencies are installed. The repository says how, in `.claude/sdlc.json`
(the sdlc pack writes it per stack):

```json
{"commands": {"setup": "pnpm install --frozen-lockfile"}}
```

| Stack | `commands.setup` |
|---|---|
| python with uv | `uv sync --frozen` |
| node with pnpm / yarn / bun / npm | `pnpm install --frozen-lockfile` / `yarn install --immutable` / `bun install --frozen-lockfile` / `npm ci` |
| nothing to install | empty string or no key |

`dispatch` calls `setup.ensure` right after the worktree exists and before anything else about the step, so it
covers a new worktree, a worktree the loop reset (a reset keeps ignored directories such as `node_modules` and
`.venv`, but may return to a commit with another lockfile) and a worktree made again after a human answered
`retry`. What it does:

* **Key missing, blank or not a string: nothing runs** and nothing is recorded. The key is read from the source
  checkout's `.claude/sdlc.json` (a session cannot edit that one); the worktree's copy is used only when the
  checkout has none.
* **Marker.** After a successful run it writes `.claude/work/orchestrator/setup.ok` in the worktree (JSON: the
  command, the lockfile name and its sha256; the first of `pnpm-lock.yaml`, `yarn.lock`, `bun.lock`, `bun.lockb`,
  `package-lock.json`, `npm-shrinkwrap.json`, `uv.lock`, `poetry.lock`, `Pipfile.lock`, `Cargo.lock`, `go.sum`,
  `Gemfile.lock`, `composer.lock` that exists). A worktree whose marker matches the command and the lockfile now on
  disk is not set up again; a changed lockfile or command runs setup again. The marker is local work state, not
  drift.
* **Run.** A plain subprocess, not a Claude session and not through a shell: the command line is split like a shell
  would (`sh scripts/setup.sh` for a chain), run in the worktree with the allowlisted environment and `PATH` that
  `config.session_path` cleans (the daemon's virtualenv out, the worktree's `.venv/bin` first), stdin closed, in its
  own process group, with a 20 minute limit (`Config.setup_timeout`; the group is killed on timeout, exit code 124).
  stdout and stderr are appended to `<repo>/.claude/work/orchestrator/<contract>/setup.log`.
* **Run row.** Step `setup`, executor `local`, requested model `none` (no model), `meta.exit_code`,
  `meta.duration_seconds` and `meta.log`; outcome `ok` or `setup_failed`. Setup rows are not counted as failures of
  the contract's steps.
* **Failure** (non-zero exit, timeout, command not found): the run ends `setup_failed`, a P1 decision is opened on the
  contract quoting the last 20 lines of the log, `dispatch` returns without starting a session (the tick reports
  `dispatch_failed`, then `blocked_on_decision`), and nothing is dispatched until a human answers: fix the cause
  (registry access, the lockfile, egress), then `retry` (setup runs again, the failed row stays as history) or `skip`.

Tests: `tests/graph/test_orchestrator_setup.py`.

Run files go to `<repo>/.claude/work/orchestrator/<contract>/<step>-<attempt>-run<id>.*`
(`.jsonl` stream, `.result.json`, `.prompt.md`, `.settings.json`, `.stderr`).

### The step-executor protocol

```
<executor> --worktree WT --command /factory:<step> --prompt-file P --settings S
           --result-file R --log-file L --session-id UUID --model M --max-turns N
           --budget USD --reviewer @h --allowed-tools-file T
           --permission-mode dontAsk --permission-prompts none [--plugin-dir D]...
```

The executor runs one session in WT and writes R last, in `run_headless.sh`'s result-file shape
(`exit_code`, `duration_seconds`, `last_assistant_message`, `subtype`, `is_error`, `num_turns`,
`total_cost_usd`) plus `session_id` and `permission_denials`. T is the step's compiled `--allowedTools`
list, one rule per line (section 3a); the executor has no list of its own and refuses to run without
one, or with any other permission mode or prompt policy. Its environment carries `SDLC_DATABASE_URL`, `SDLC_SCHEMA`,
`SDLC_NODE`, `SDLC_STAGE`, `ORCH_RUN_ID`, `ORCH_STEP` and `ORCH_ATTEMPT`, and `DATABASE_URL` only
when the daemon was given one: the session's `graph` MCP server reads the ledger URL from
`DATABASE_URL`, and so may the application under test (the sample app's `app/config.py` does), so a ledger URL
in `DATABASE_URL` breaks the app's own test collection inside the session. With the default local
ledger, leave `DATABASE_URL` unset when starting the daemon.

- **Live** (default; needs `--live`): `orchestrator/step_executor.sh` runs `claude -p
  --permission-mode dontAsk --permission-prompts none --allowedTools <T> --max-turns N --max-budget-usd USD --model M
  --settings S --session-id UUID --plugin-dir D... --verbose --output-format stream-json`, the same
  flag set as the pack's `run_headless.sh` minus its worktree creation, scope check and push (the
  orchestrator does those). `run_headless.sh` itself cannot run a step in place: it creates the
  worktree `<repo>-<slug>` and refuses when it or the branch already exists, which every step after
  the first hits. With an in-place mode (and `--settings`/`--session-id`/`--result-file`) it could
  replace `step_executor.sh` through `ORCHESTRATOR_EXECUTOR`.
- **Fake** (tests): `ORCHESTRATOR_EXECUTOR="python -I -m sdlc_graph.orchestrator.fake_executor"`
  does the step with plain file writes and commits and records the evidence, or not, per
  `ORCH_FAKE_PLAN` (`ok`, `nothing`, `drift`, `frozen_edit`, `ratchet`, `hang`, `crash`,
  `crash_dirty`, `assume`, `denied`, the `denied_form` flag, ...), keyed `"<slug>:<step>:<attempt>"`, `"<slug>:<step>"`, `"<step>"` or `"*"`.
  `ORCHESTRATOR_CLAUDE="python -I -m sdlc_graph.orchestrator.fake_claude"` answers the judge and
  the conformance reviewer (`FAKE_CLAUDE_SCORES`, `FAKE_JUDGE_CHOICE`, `FAKE_JUDGE_ESCALATE`).

## 3a. Autonomy: ask before, never during (design section 14)

Every question is asked when the epic, feature and contract are written; a step never waits on one.

**The contract carries the answers.** `drafted -> ready` also needs `defaults_present` (the
`## Defaults when ambiguous` block, `node.attrs.defaults`) and `capabilities_present` (the
`## Capabilities` block: `paths`, `commands`, `egress` and `data` each declared, an empty list being an
explicit "none"). Shell `commands` count as elevated for `capabilities_approved`, so a human marks such
a contract ready. `python -m sdlc_graph.orchestrator.contract_blocks --slug S --file contract.md --actor
@you` parses both blocks onto the node (anyone while `drafted`, a human after; every attach is logged in
`attrs.contract_blocks_log`); `--check` parses only. A contract past `drafted` without them (made ready
before the guard existed) gets a P1 **contract incomplete** decision naming the missing block, and is
never dispatched.

**The allow-list is compiled.** `autonomy.allowed_tools` builds each step's `--allowedTools` from: the
pack's base list, parsed out of `sdlc/scripts/run_headless.sh` (`DEFAULT_TOOLS='...'` and the
per-plugin-root template; `ORCHESTRATOR_PACK_RUN_HEADLESS` overrides where it is read from), minus
branch creation always and push/PR creation outside `ship`; the orchestrator's own tools (`Agent`,
`TodoWrite`, the graph MCP read tools and `evidence_add`); the stack's test, lint, format and typecheck
commands from `.claude/sdlc.json` over the pack's `stacks/<stack>.json` (prefix up to a `{file}`
placeholder, as `Bash(<prefix> *)`); and the contract's capabilities (`commands` -> `Bash(<cmd> *)`,
`paths` -> `Edit(./<path>)` in the worktree; no `Write(...)` rules, which never match). A command that
could run arbitrary text (operators, `$`, wildcards, `sudo`/`env`/`eval`, a bare interpreter, `-c`/`-e`)
is refused by `capabilities.validate`. The list is saved next to the run as `.allowed_tools.txt`.

**Denials escalate, they are not retried.** The gates read the session's stream-json for `system`
events with subtype `permission_denied`, the result event's `permission_denials`, and tool results that
say "Permission to use X has been denied"; any denial makes the run `permission_denied` (drift and
frozen resets still run first) and the loop opens a P1 decision such as: "The tests session was denied
1 tool call(s) under --permission-mode dontAsk --permission-prompts none: Bash `uv run pytest -q` (rule
that would have allowed it: `Bash(uv run pytest -q *)`; add it to the contract's ## Capabilities
commands, ...)". Add the rule, answer `retry`.

**Denials of form are recorded, not escalated.** Two kinds of denial name no missing rule: a Bash command
refused only for its shell shape (an `&&`/`;`/`|` chain, a loop, a heredoc or a redirection inside the
worktree, with no part that is forbidden on its own: no push, reset, deletion, wrapper, `-c` interpreter,
command substitution or write out of the worktree), and the pack's commit guard refusing a message
(`commit-message`, such as a subject of 72 characters or more). No contract could allow them, so they do
not make the run `permission_denied`: the session is told to redo such a call in an allowed form (one
plain command per call, the Write tool for files, a short subject), the gates judge the step on its
evidence as usual, and the run's `meta.denials` keeps every denial with `form: true` plus one line in
`meta.reasons` (which a failed step's retry prompt shows). A call Claude Code lists in
`permission_denials` because a PreToolUse hook blocked it counts once, as the guard's denial. Any other
denial in the same run still escalates.

**Assumptions are evidence.** The prompt carries the defaults and the decisions already made, and tells
the session to record each default it applies in `.claude/work/<slug>/assumptions.md` (staged and moved
with the pack's `workfile.py`), one item each: `- default_applied: ...` with `where:`, `why:` and
`decision:` lines (`D1`, `#12` or `none`; an item without one fails conformance).
After every step the orchestrator reads that file itself and records `assumptions` evidence
(`{"assumptions": [{default_applied, where, why}], step, run_id}`) when the set changed; the feed shows
it ("assumptions #12 from local: 1 default(s) applied (...)") and the view `assumption` (sql/007) lists
every item. The conformance gate fails (`conformance_low`, `conformance` evidence with
`source: assumptions` and score 0) when an item is malformed, when assumptions exist but the contract
has no defaults block, or when one contradicts a resolved decision (it names `#id` without applying the
choice, or names another option of that decision and not the chosen one). The conformance reviewer is
given the decisions and the assumptions too.

Not covered here: the session's own MCP `evidence_add` does not know the `assumptions` kind (the MCP
server does not import the orchestrator), which is why the orchestrator reads the file instead.

## 3. The gates

After a session ends the orchestrator does not trust the session's word. In order, the first that
fails decides the run's outcome:

| # | Gate | Fails as | What happens |
|---|---|---|---|
| 0 | the executor wrote its result file | `crashed` | retry (a dirty worktree escalates instead) |
| 0b | the session did not ask a human something (`.claude/work/<slug>/questions.md`, which dispatch moves aside before every run) | `needs_human` (not a failure) | a P1 decision with the question text and a free-text answer; no session is dispatched until it is answered; the next session's prompt carries the answer; answering `skip` skips the contract |
| 0c | no transition on the node during the run by anything but the orchestrator or a human | `unauthorized_transition` | P1 decision; "already transitioned" is accepted only for a transition the orchestrator itself made for this run (reason `orchestrator run #<id> ...`), so a session cannot move the state and skip the gates |
| 1 | drift: `git diff --name-only <start>` plus untracked files, minus uncommitted changes that were already there at dispatch and are untouched, is a subset of `allowed_paths`; so are gitignored files that are new since dispatch (tool caches such as `__pycache__`, `.pytest_cache`, `node_modules`, `.venv` excepted), and no changed file is a symlink that resolves outside the worktree; `.claude/work/` is exempt only for `current.json`, `last_verify.json`, `blocks.jsonl`, `.stop_nudged`, `spawns.json` (the Agent-spawn hooks' record), `<slug>/`, `headless/` and `orchestrator/` (the setup marker), and the pack's gitignored staging directory only for `.sdlc-stage/<slug>/` (`run_headless.sh` glob dialect) | `drift` | the tree as it is (including untracked and the new ignored files) is saved as a commit under `refs/sdlc/discarded/run-<id>` (named in the run summary; `git show <ref>:<path>` restores a file), the new ignored files are removed, then the worktree is restored to how the step found it (HEAD at the start commit plus the uncommitted work of earlier steps, from a snapshot taken at dispatch; `.claude/work` kept), retry with the files named in the prompt |
| 2 | frozen tests: every file in the newest `tests_frozen` evidence that still describes the branch (as in `current.json`) hashes to its sha256 (all steps after `tests`) | `frozen_changed` | saved and reset like drift, retry |
| 3 | expected evidence recorded since the run started | `no_evidence` | retry |
| 3b | the evidence is true. `tests`: each acceptance test file exists in the worktree, contains the test, is listed in `tests_frozen` with its real sha256 and, with `--test-command`, fails when the orchestrator runs it. `verify`: with `--test-command` the orchestrator runs the acceptance tests itself and they must pass; the stamp may not report failures. `ship`: `pr_url` is the PR `gh pr view sdlc/<slug>` reports, on the `origin` repository with head `sdlc/<slug>`, never the URL the session wrote | `unverified` / `no_evidence` | retry |
| 4 | `tests` and `implement` left a change inside the allowed paths that this run made (HEAD moved, or an uncommitted file differs from the dispatch snapshot; work kept from an earlier attempt does not count) | `no_change` | retry |
| 5 | ratchet: no `ratchet` or `verify_stamp.ratchet` evidence since the start with `new > 0` | `ratchet` | retry |
| 6 | conformance (after `implement`): a Sonnet reviewer scores the diff against the acceptance criteria, `{score 0-1, findings[]}`, stored as `conformance` evidence | `conformance_low` | re-enter implement with the findings, keeping the work; at most two re-entries, then P1 |
| 7 | the lock is still ours, then `transition(slug, target, "agent:orchestrator")`: the ledger's own guards | `guard_refused` | retry |

Before a retry after a non-drift failure nothing uncommitted is ever reset or deleted. The pack's
commit guard lets only tests be committed before verify, so implement's work normally stays
uncommitted until `/ship`; the dispatcher therefore records the uncommitted files (with hashes) at the
start of every run, and the retry rule is:

- a session that died (`crashed`, `stalled`) leaving new uncommitted changes, or one that moved HEAD
  over a dirty tree, becomes a P1 decision with the worktree path, and the loop waits;
- a clean tree whose HEAD moved is reset to the run's start commit (the discarded commits stay in
  the reflog), so the retry starts from the same ledger state and the same commit; the
  `tests_failing_first` and `tests_frozen` rows that attempt recorded are named in a `superseded`
  evidence row (a drift reset does the same), so the retry's tests are not frozen by the attempt it
  replaces and the guards read only the retry's rows;
- otherwise the retry runs in place, and the prompt names any uncommitted changes the last attempt
  left.

A transition the orchestrator made for this run before a restart cut the run's finish short is
recognised and recorded as `ok`; any other transition of the node during a run is not.

**One contract's problem never stops the daemon.** Each contract is patrolled and dispatched inside its
own error boundary: a git error (a stale `index.lock`, a leftover directory where the worktree goes, a
branch name git refuses), a missing worktree, a database error or an OS error becomes a P1 decision
keyed on the contract and the exception, and the tick goes on with the other contracts. A contract
whose worktree directory was deleted by hand escalates (the branch may still hold its commits); answer
`retry` and a worktree is made again from the branch. Contract slugs are validated at intake (letters,
digits, `.`, `_`, `-`; they name a git branch and a directory).

**The lock.** A heartbeat thread refreshes the lock on its own connection every third of a tick, so a
tick that includes a slow gate (a reviewer call, `gh`) never lets the lock go stale and a second daemon
is refused with `LockHeld` the whole time. Before every dispatch and every transition the daemon checks
it still owns the lock (`UPDATE ... WHERE owner = me`), and a daemon that lost it stops
(`stopped: lock_lost`) without transitioning. A run is finished once: a second writer cannot overwrite
the first's outcome. After `kill -9` the lock still goes stale after three ticks and is taken over.

**The session's environment is an allowlist**, not the daemon's: `PATH`, `HOME`, locale, proxy and CA
variables, `ANTHROPIC_*`, `CLAUDE_*`, anything named in `ORCHESTRATOR_ENV_PASSTHROUGH`, the GitHub token only
for `ship`; never a database URL or other credential. `ship` pushes and opens the PR with that token
(`GH_TOKEN` or `GITHUB_TOKEN` in the daemon's environment, e.g. `GH_TOKEN=$(gh auth token)`): the pack's
sandbox denies reading `~/.config/gh`, so the session's `gh` gets an empty config directory of the run's own
(`GH_CONFIG_DIR`, next to its log) and git a credential helper that answers from the token in place of the
engineer's helpers. Without a token in the daemon's environment `ship` cannot push. The session still needs the ledger URL to record
evidence. By default it is the daemon's own, and a session with the owner's URL can bypass the ledger's
actor rules with raw SQL, so the actor distinction is only advisory then. For real enforcement give sessions
a restricted role: `sql/orchestrator_session_role.sql.example` creates one that can read the graph, add
evidence and record runs but not update `node`, `decision` or `transition`; pass its URL with
`--session-database-url` or `ORCHESTRATOR_SESSION_DATABASE_URL`. The sessions' Bash deny list also covers
the `sdlc-graph` CLI. The orchestrator's own test runs (`--test-command`) use the same restricted environment.

**The session's Python environment.** Each step runs in a fresh worktree, so the session's interpreter comes from
its `PATH`. What is passed: `PATH` as the engineer's shell had it, plus the allowlisted variables above. What is
stripped: every `PATH` entry under `VIRTUAL_ENV` or under the plugin's own `.venv`, and the variables
`VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT` and every `UV_*`. Reason: a daemon started the documented way
(`uv run --project <plugin root> sdlc-graph orchestrate ...`) has the plugin's interpreter first on `PATH`,
and a session that picked it up would run the project's tests with the wrong dependencies
(`ModuleNotFoundError`). If the worktree has its own `.venv/bin`, it leads `PATH`. The same environment is used
for the orchestrator's own test runs, and the run row's `meta.path_head` records the first `PATH` entry the
session got. To pass more variables (a private index URL, a tool home), name them in
`ORCHESTRATOR_ENV_PASSTHROUGH` (comma separated); `UV_*` names are dropped even there. Because worktrees start
without an environment, give the project a self-sufficient test command (`uv run pytest -q`,
`poetry run pytest`) in `commands.test` of `.claude/sdlc.json` or `--test-command`, and allow the package index
in the contract's egress (`pypi.org`, `files.pythonhosted.org` for uv).

**Process identity.** A run records the executor's start time at dispatch; its `--session-id` is on the
command line. After a restart or reboot a pid counts as the executor only if `ps` shows that start time
and that session id; a recycled pid is a dead run (gated), and the watchdog never signals a process that
fails the check.

**Budget.** The default feature cap is $50 (`--max-cost`, `--no-cost-limit` to opt out). The check is
`spent + budget_usd x open sessions >= limit`, so free slots are not all dispatched while spend reads $0.
The P2 decision's answers act: `raise` doubles the limit (restart-safe: the decision records the new
limit), `stop` ends the loop (`stopped: budget_stop`; a larger `--max-cost` or `raise` lifts it). A
crashed session leaves no result file, so its cost reads $0; the reservation is what covers it.

### The watchdog

Each tick, per open run: the process is gone → gates. Alive but no progress (new evidence or
transition on the node, or new output in the session's stream log) for `stall_minutes` → the run is
marked warned; still no progress one more window later → SIGTERM, then SIGKILL, to the session's
process group, outcome `stalled`. The watchdog observes; completion is gated only by the gates.

### The judge (`--judge`)

When a step has failed twice, before the third attempt, one Opus call (`claude -p --output-format
json --max-turns 6 --json-schema {choice, reason, escalate}`, tools `Read` only) sees the contract's
node view and the failed runs and picks `retry` or `escalate`. An off-list choice escalates. Its run
is recorded (`step = judge`, stage `review.synthesis`). The contract machine is linear, so "the graph
allows several steps" never arises for contracts today; the hook point is the same.

## 4. Thresholds

| Setting | Default | Flag |
|---|---|---|
| tick | 60 s | `--tick` |
| concurrent sessions | 2 | `--max-runs` |
| stall window | 30 min (kill after two windows without progress) | `--stall-minutes` |
| failures per step before a P1 decision | 3 | (`Config.max_failures`) |
| conformance threshold / re-entries | 0.8 / 2 | `--no-conformance` skips it |
| per-session turns / USD | 40 / 5 | `--max-turns`, `--budget` |
| worktree setup timeout | 20 min | (`Config.setup_timeout`) |
| per-feature USD | unlimited (then a P2 decision on the feature) | `--max-cost` |
| lock staleness | 3 ticks of the holder | |

## 5. Escalations and answering them

`sdlc-graph escalations [--feature F]` lists open decisions with a severity, P0 first, each with the
command that answers it. Answers the loop understands: `retry` (the failure count for that step
restarts from the answer), `skip` (the contract counts as done for the feature), `raise` and `stop`
for the budget question, and for a session's question (`needs_human`) any plain-text answer, which the
next session is told. Any open decision on a contract stops the loop dispatching it. A `permission_denied` or
`contract incomplete` decision is answered `retry` after the rule or block it names has been added
(otherwise the next run is denied, or the contract refused, again), or `skip`.

```
sdlc-graph escalations --feature per-key-rate-limit
sdlc-graph decision resolve 12 --choice retry --actor @ish
sdlc-graph feed --feature per-key-rate-limit --limit 30
```

## 6. Running it and stopping it safely

```
sdlc-graph orchestrate per-key-rate-limit --repo ~/src/my-app --live --reviewer @ish    # foreground daemon
sdlc-graph orchestrate per-key-rate-limit --repo ~/src/my-app --live --once             # one tick
sdlc-graph orchestrate per-key-rate-limit --stop                                         # release the lock
```

`/factory:orchestrate <feature>` (human-invoked only) starts the daemon as a background task and prints
how to watch it. `--stop` deletes the lock row; the daemon notices at its next heartbeat and exits.
Worktrees are left in place, and so is any session still running: the next `orchestrate` patrols its
run row (pid, result file) and gates what it left. Ctrl-C or SIGTERM on the daemon releases the lock
the same way. Never delete a worktree with uncommitted changes to unblock the loop; answer its
escalation instead.

## 6a. Where the executor's Claude comes from

The executor is always Claude Code (`claude -p`, never `--bare`, with `--plugin-dir`, `--settings`,
`--permission-mode dontAsk`, `--permission-prompts none`, `--max-turns`, `--max-budget-usd` and
`--output-format stream-json` passed explicitly). What differs is the credential behind it.
`--executor local|runner` (default `local`) picks the mode, and every run row records `executor` and
`auth_source`; `sdlc-graph feed` shows `[executor/auth_source]` on each run line and `metrics snapshot`
counts runs by both (`runs_by_executor`, `runs_by_auth_source`). The old command-line option that
named a custom executor program is now `--executor-cmd` (or `ORCHESTRATOR_EXECUTOR`).

| Mode | Credential behind `claude` | Suits |
|---|---|---|
| `local` (default; the showcase) | the engineer's own Claude Code login on their machine (Pro, Max, Ultra, Team or Enterprise seat) | a team that already holds licences; the ledger coordinates and each machine executes its own steps. Subscription usage windows apply, so keep `--max-runs` and `--max-turns` small |
| `runner` with a subscription token | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` | one person's own always-on box. The token is tied to the subscription of whoever created it, so it is allowed but logged as "subscription token: tied to one person" |
| `runner` with an API key or federation | `ANTHROPIC_API_KEY`; GitHub OIDC exchanged for a Claude Console service account; or Claude Platform on AWS (`CLAUDE_CODE_USE_ANTHROPIC_AWS=1`, `ANTHROPIC_AWS_WORKSPACE_ID`, and `ANTHROPIC_AWS_API_KEY` or ambient AWS credentials); or `CLAUDE_CODE_USE_BEDROCK=1` | a shared team runner or CI. Federation and the AWS path need no static secret |
| Managed Agents | API key only | not an executor yet: it is beta, has no subscription login and bills as API usage |

`runner` is the same command as `local`; it only refuses to start (exit 2) unless the environment holds
one of: `ANTHROPIC_API_KEY`; `CLAUDE_CODE_OAUTH_TOKEN`; `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` with
`ANTHROPIC_AWS_WORKSPACE_ID`; `CLAUDE_CODE_USE_BEDROCK=1`. Ambient AWS credentials are not checked
(the orchestrator cannot tell), and sessions only inherit the `ANTHROPIC_*`/`CLAUDE_*` variables plus
what `ORCHESTRATOR_ENV_PASSTHROUGH` names, so pass `AWS_*` that way. `auth_source` is detected from the
session's environment at dispatch, first match wins: `api_key`, `oauth_token`, `claude_platform_aws`,
`bedrock`, else `subscription`. In `local` mode with none of those set, the run is `subscription`.

**The GitHub token for `ship` in `local` mode.** The ship step pushes and opens the PR, and the sandbox denies reading
`~/.config/gh`, so `gh` and git need a token in the session's environment (`dispatch.github_env` turns `GH_TOKEN` /
`GITHUB_TOKEN` into an empty `GH_CONFIG_DIR` and a git credential helper that answers from it). When the daemon's
environment has neither and the executor is `local`, `dispatch.ship_token` runs `gh auth token` once (a subprocess,
no shell, 30 s limit), keeps the result on the `Config` for the rest of the process, and hands it to the ship step
only, as `GH_TOKEN`. The log line is `ship credential: gh auth token` (stderr, and `meta.ship_credential` on the run
row); the value is never logged or stored. If `gh` is missing or not logged in the line says `ship credential: none`
and ship fails on its own, visibly. An explicit `GH_TOKEN` or `GITHUB_TOKEN` always wins, and `runner` mode keeps
its behaviour: an explicit token in the environment, or GitHub identity federation, never the engineer's login.

Two unattended-run details: `--permission-prompts none` (Claude Code 2.1.259 and later) makes a run
deny, rather than wait on, a prompt nobody can answer; and a `-p` run executes a folder's hooks and
`.mcp.json` servers without a trust dialog, so executors only run in worktrees the orchestrator
created. The docs announce that `--bare` will become the default for `-p` in a future release, and a
bare run skips hooks and the keychain login. So pin the Claude Code version in your runbook and keep
every flag explicit, as the step executor does.

## 7. What the live smoke showed (2026-11-02)

One feature, the demo's `rate-limit` contract, on a copy of a brownfield FastAPI repository with the pack
initialised and its origin removed, Claude Code 2.1.316, ledger schema `orch_live`:

| Step | Session | Gates | Cost |
|---|---|---|---|
| tests | 157 s, 9 turns; the pack's test-author wrote nine failing tests, froze them and posted `tests_failing_first` and `tests_frozen` through the plugin's MCP server (it saw `SDLC_SCHEMA`) | passed; `ready -> tests_frozen` citing evidence 1 and 2 | $0.61 |
| implement | 116 s, 39 turns; code written, not committed (the pack's commit guard) | drift clean; conformance reviewer (live Sonnet, `structured_output`) 0.95; `tests_frozen -> implementing` | $0.66 + $0.29 |
| verify | 134 s; the app's 94 tests passed but `verify.py` was red: the sandbox refuses `stat` on `.env.example` (the compiled capabilities deny `Read(./.env.*)`) | `no_evidence` (no `verify_stamp`); the session also claimed a change to `.env.example` that the drift gate did not find | $0.64 |

Found and fixed on the way: the ledger URL in `DATABASE_URL` broke the app's test collection
(section 2), and resets would have discarded uncommitted implement work (section 3). Not run live:
`ship` (it needs a scratch GitHub repository; the fixture's own origin must never be pushed to).
`tests/graph/test_orchestrator_live.py` is the full run, opt-in with `SDLC_ORCH_LIVE=1`,
`ORCH_LIVE_REMOTE` (and optionally `SAMPLE_REPO`; the default is `examples/sample-app`). Sessions also load the operator's own user-level plugins
and MCP servers; the settings file does not isolate them.

## 7a. Test schemas

The suite never writes to the `public` schema of its database. `tests/graph/conftest.py` gives every test that uses
a database fixture its own schema, `t_<random>`: `pg_schema` creates it from `sql/*.sql` before the test, sets
`SDLC_SCHEMA` to it (so the CLI, the MCP server, hooks and the orchestrator, which read that variable, land in it)
and drops it afterwards; `conn`, `conn_factory` and `clean_conn` are bound to it, and `clean_conn` is simply a
connection on that new schema (it resets nothing and refuses any schema that is not `t_...`). The live test
(`test_orchestrator_live.py`) uses the same fixtures and prints `ledger schema for this run: t_...` first; its
ledger report is written under the test's `tmp_path` because the schema goes away with the test.

A session fixture, `public_sentinel`, puts a row in a throwaway table in `public` before the first test and checks
it after the last one, then deletes it and the table: a test that resets, truncates or drops in `public` fails
the session at teardown (`the suite touched the public schema`). `tests/graph/test_orchestrator_isolation.py` checks
the pieces. `sdlc-graph db reset` on the default schema, and `demo/run_demo.sh` (which runs it), still reset
`public`: point `DATABASE_URL` at a scratch database for them.

## 8. Not in this increment

Per-model budgets, and counting the conformance reviewer's cost before it is called (it counts once
recorded, and the reservation check then holds back dispatches). Not closed by the gates: a session
can still write through a symlink or to an absolute path outside the worktree before the gate sees it
(the gate catches and reports the link; the sandbox is the barrier); `--test-command` is opt-in because the
orchestrator cannot know the project's runner and environment, so without it `verify` is evidence-checked
only. The ship gate needs an `origin` remote on GitHub (the live smoke above removed it).
Auto-merge (refinery only merges what GitHub already approved, found from the branch on `origin`), multi-repo features, Slack
notifications, Managed Agents. The live end-to-end run with a draft PR needs a scratch GitHub
repository the human creates; the fixture's own remote must never be pushed to.
