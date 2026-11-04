---
name: init
description: Onboard this repository in one command — detect the stack, write .claude/sdlc.json, rules, a CI gate and today's debt baseline, then index the code graph, run the audit and the maturity and security assessments in parallel subagents, and write docs/onboarding-report.md. Use whenever the user says onboard, set up, get started, adopt the pack, or runs this on a repo for the first time. Run once per repo, before any other factory command.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo] [--graph on|off] [--graph-provider local|neon|aws|spanner] [--upgrade] [--headless]"
allowed-tools:
  - Agent
  - Skill
  - Artifact
  - Read
  - Glob
  - Edit(docs/onboarding-report.md)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py")
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/security_assess.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" *)
  - Bash(git branch --show-current)
  - Bash(git rev-parse *)
  - Bash(git switch -c chore/onboarding*)
  - Bash(mkdir -p docs)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status)
  - mcp__plugin_factory_graph__graph_status
  - mcp__graph__graph_status
  - mcp__plugin_factory_graph__graph_snapshot
  - mcp__graph__graph_snapshot
  - mcp__plugin_factory_graph__graph_impact
  - mcp__graph__graph_impact
---
Onboard this repository: the working agreement first, then the code graph, the audit, the maturity and security
assessments, and one report. Arguments:
$ARGUMENTS

**Orchestration.** These rules hold at every step below.
This conversation orchestrates and never executes: every step that does work runs in a subagent through the Agent tool; here you load the skill, plan the hand-offs, brief each subagent, collect what comes back, ask the human and summarise.
Where steps are independent, launch them together, in one message: one Agent call per subagent, all in the same reply; a step that needs another's result waits for it.
Before each hand-off say in one line what is handed off and why; when the subagents return say in one line what came back and what it means.
Say each such line in the same message as your next tool call: a headless run ends at the first message without one.
A question for the human is asked here, in the open: a subagent cannot ask.
Parallel subagents never edit the same file.
A subagent refused by a hook reports the refusal; it does not work around it.
What stays here: the branch commands of step 0 and `git rev-parse HEAD` in step 6 (the only commands you run
yourself), the questions of steps 1 to 3, the graph's MCP tools and the `factory:graph` and `factory:index` skills
(steps 5, 6 and 8), and the summary. Launch a subagent with the Agent tool: `subagent_type` `factory:<agent>`, a
`description` of a few words naming the hand-off (the hand-off log records it), never pass a `model` (the spawn guard pins
it), and a `prompt` that is the brief. A brief is the subagent's whole context, in this order: the task in one
sentence; its commands exactly as the step writes them; the files it may touch and the ones it must not; what to
report; and, last, this line:
"You cannot ask a question: when something cannot be done, report it and stop."
The briefs of steps 2, 7 and 8 carry all five. If the Agent tool answers that a subagent runs in the background, end
the turn with one line saying what is awaited and carry on when its report arrives; never sleep or poll.

Never edit application code, here or in a subagent. A failed step never stops the run: note the failure for the report
and go on to the next step; a subagent that reports it could not do its task is a failed step. The run is **headless**
when the arguments hold `--headless` or the prompt says no human will answer (an orchestrated or `claude -p` run); then
nothing is asked: steps 1, 2 and 3 say what to do instead, and the hand-offs are the same. Interactive, the questions
below are the "ask before" moment: asked once, here, never during later work.

**Each command exactly as written below**: no redirect and no trailing echo of the exit code. Read files with
the Read tool. **A denied command is one command denied, not the tool:** run it once more on its own, exactly as
written; if that is denied too, record the denied command text under Failures and go on to the next step. Both rules
hold in every subagent, so every brief says them.
When `init_project.py` prints `settings: .claude/settings.json is not writable here ... staged at
.sdlc-stage/settings.json` (or the same for `settings.local.json`), that is Claude Code's sandbox protecting a
settings file it loaded when the session started: not a denial, not a failure, and the script went on with its other
steps. Carry the `cp` line it printed into the review text of step 9 ("a human runs: cp .sdlc-stage/settings.json
.claude/settings.json"), never into Failures, and never try the copy yourself.

0. **Leave a protected branch first, before anything is written.** Here, yourself: run `git branch --show-current`. If it is `main`,
   `master` or a name in `paths.protected_branches` of `.claude/sdlc.json` (read the file if it exists), run
   `git switch -c chore/onboarding`; when that name is taken (`git rev-parse --verify --quiet refs/heads/chore/onboarding`
   prints a sha) use `git switch -c chore/onboarding-2`. Say it in one line: `On <branch>, a protected branch: switched to
   chore/onboarding; the onboarding report and every file init writes land there.` This is the same headless: nobody is
   asked, because the edit guard refuses to write on a protected branch. An empty branch name (detached HEAD) or any
   other branch: change nothing.
1. **Graph: two questions, asked once.** Unless the arguments already hold `--graph`, ask both now, in one batch,
   before the init script runs (it writes the answers into `.claude/sdlc.json`):
   1. Code graph and ledger: **on** (recommended: the orchestrator, impact analysis and the console run on it) or
      **off** (hooks, contracts and evidence stay; no ledger).
   2. Where the graph database lives: **local** (Docker, pgvector, port 5435; the laptop default), **neon**
      (serverless Postgres with pgvector, free tier; the shared default for a team), **aws** (Aurora PostgreSQL or RDS
      for PostgreSQL with pgvector, in the customer's account) or **spanner** (Google Cloud Spanner as the graph
      mirror; the ledger stays on PostgreSQL).
   Append the answers as `--graph on|off --graph-provider <provider>` to the arguments of both init_project.py runs.
   **Headless:** do not ask; the script's defaults apply (`graph: on, provider local`), and the line it prints goes
   into the `Assumptions:` block of step 3 as `graph on, provider local (defaults)`.
   If the arguments do not contain `--marketplace-repo`, append `--marketplace-repo ishpr/lathe` to them for both
   init_project.py runs (the CI gate checks out `plugins/factory` of that repository).
2. **Plan and working agreement: one hand-off.** Launch one `factory:drafter`, description "init script" (it may write
   the files the script writes), with this brief:
   - Task: run the init script, its dry run first, and report what it planned and what it wrote.
   - Commands, one per Bash call, exactly as written; a denied command is run once more on its own, then reported.
     The dry run, with the arguments as step 1 completed them:
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --dry-run $ARGUMENTS`. If it reports several stacks
     or none, stop there, with nothing written. Otherwise the real run, with the same arguments:
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" $ARGUMENTS`, then
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status`, then Read `.claude/sdlc.json`.
   - Files: the script writes `.claude/sdlc.json`, `.claude/rules/sdlc-*.md`, `.claude/work/`, a PR template, a CI
     workflow (`.github/workflows/sdlc-gate.yml`), gitignore lines, the working-agreement section in CLAUDE.md, and
     records the current violations as `.claude/baseline.json`. You write and edit nothing yourself and never touch
     application code; nothing is committed. A `settings: ... staged at .sdlc-stage/...` line is not a denial and not
     a failure: quote it with its `cp` line, and never try the copy yourself.
   - Report: the plan (detected stack, app and test paths, the `graph:` line, the files it creates, what it appends to
     CLAUDE.md); the stacks it named, when it stopped; the real run's `baseline by check:` line, copied; any staged
     settings line; the `graph.py status` output; from `.claude/sdlc.json` the app paths, test paths, test command
     and `paths.protected`, each with one line on whether it points at the right place; a failure as
     `init failed: <one line>`.
   - "You cannot ask a question: when something cannot be done, report it and stop."
   When it returns, show the plan. It stopped on the stack: ask which stack, then launch it again with `--stack <one>`
   added to the arguments. **Headless:** print `ESCALATION: stack ambiguous: <list>; rerun with --stack <one>`
   (`<list>` is the stacks it named, or `none`) and stop with nothing written. It reports `init failed: <one line>`:
   record that for the report and continue. Nothing is committed.
3. **Confirm the paths.** From the init script's report of `.claude/sdlc.json`, check: app paths point at the code, test
   paths at the tests, the test command is the one the team runs, and `paths.protected` lists anything humans own
   (generated files, a dead-code sink). Ask the human to confirm or change these; do not guess. The file is protected
   (no agent edits it), so a change is the human's own edit; step 7 uses the paths as confirmed.
   **Headless:** do not ask. Accept the detected app paths, test paths and test command and the empty protected paths,
   and keep one `Assumptions:` block that lists them (app paths, test paths, test command, protected paths), ending
   with the line `confirm in .claude/sdlc.json`.
4. **Baseline.** Copy the `baseline by check:` line step 2 printed, as the init script's report quotes it (keys,
   violations, and the count per key prefix, from `.claude/baseline.json`); do not count them again with a shell pipe.
   No such line (no baseline was recorded): say so and why, from step 2's output. Say which checks this stack gets:
   Python has route auth, module state and test quality; Node, Go and Rust have module state and test quality as text
   heuristics; lint and types come from the stack's own tools when they are installed.
5. **Is the graph on and reachable?** Here: call the MCP tool `graph_status` (server `graph`;
   `mcp__plugin_factory_graph__graph_status`, or `mcp__graph__graph_status`; if tools are deferred, search for
   `graph_status`; with no such tool, take the `graph.py status` output the init script reported, which says the
   same). Three cases:
   - `configured: true`: go on to step 6.
   - `enabled: false` (or an answer `{"error": "graph off", ...}`): print one line, `graph off: the graph steps are
     skipped`, record it for the report, and skip step 6 and the graph part of step 8.
   - on but `configured: false` (unreachable, no schema, or `{"error": "graph not configured", ...}`): never skip it
     silently. Interactive: say the hint in one line (`graph on but not reachable: run /factory:graph provision
     <provider> or set DATABASE_URL`), invoke the skill `factory:graph` with `provision <provider>` (it asks its own
     decisions once; local runs unattended), then call `graph_status` again (with no such tool, the status line that
     skill ends with) and go on as above. **Headless:** do not provision; print that hint as one line, record it under
     Assumptions and in the report's Graph section, and skip step 6 and the graph part of step 8.
   Graph off, or on but not reachable, is a state the report describes, **not a failure**: it never goes under
   Failures (only a step that ran and failed does, such as `factory:index` failing in step 6).
6. **Code graph.** Here: run `git rev-parse HEAD`, then invoke the skill `factory:index` with that sha
   (with the Skill tool, so its own allow rules apply; the graph's MCP tools belong to this conversation). If it fails
   (for example the database is down), record "graph not indexed: <one line>" and continue; the later graph steps are
   then skipped.
7. **Audit and the two assessments: seven subagents.** They need nothing from each other: launch them together, in one
   message. Five `factory:auditor`, one area of `/factory:audit` each (descriptions "audit: routes and auth", "audit:
   services and workers", "audit: tests", "audit: docs vs code", "audit: TODO ages"); one `factory:maturity-assessor`
   ("maturity assessment"); one `factory:security-assessor` ("security assessment"). The auditors write nothing and the
   two assessors write different files.
   - **Each auditor's brief**: audit one area of this repository, read-only, and report one table. Give it the app paths
     and test paths as confirmed in step 3. Use the Read, Grep and Glob tools (a session whose tool list has no Grep or
     Glob uses `grep -rn <pattern> <paths>` instead (`grep -rln` to list files)); missing Grep or Glob tools are not a
     failure: use the `grep` fallback and say nothing more about it. Each command exactly as written; a
     denied command is run once more on its own, then reported. Every check of the area is done, headless too: a check
     that finds nothing gets a "none" row. You write, edit and fix nothing. Rank: security first, then correctness
     (state that does not survive a second process, contracts that lie), then debt. Report one table,
     `| # | severity | file:line | problem | fix |` (the problem and the fix in one line each), the three you would fix
     first in this area and why, and what you could not check. Then the line
     "You cannot ask a question: when something cannot be done, report it and stop."
     and its one area, copied from this list:
     1. **Routes and auth.** Entry points nothing mounts: list the routers / handlers / modules defined under the app
        paths and compare with what the entry point actually registers. Inbound calls that verify nothing (webhooks
        without signature checks), identity taken from a request body, secrets read inside a handler, SQL or shell
        built from strings.
     2. **Services and workers.** `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all` — every current
        violation, known and new, by check; your table covers every check it prints (route auth, state and tests
        included), because no other area runs it. On Python the `dead|` keys are unmounted routers and unreachable
        modules, `stubs|` keys are TODOs without a ticket id, stub bodies, env reads outside config and class-level
        state, and `contract|` keys are endpoints the frontend or README names that do not exist; confirm those by
        hand. Unused modules: for each module under the app paths, search the app and the tests for an import of it;
        list those with none.
     3. **Tests.** Tests that assert existence, test files with no negative case, and shared fixtures that would touch
        a real database.
     4. **Docs against the code.** Endpoints the README or the frontend name that do not exist, documented env vars
        nothing reads, and documented commands that cannot run (a script, target or file that is not there): read the
        README and the docs against the code, and run none of the documented commands.
     5. **TODO ages.** Search the app paths for `TODO|FIXME|XXX` with line numbers (the Grep tool) — what each one
        admits, and how long it has been there: one command per TODO, exactly in this form, with `-S`, a space and the
        quoted text first and every other option after it (the allow rule matches only a command that starts
        `git log -S `): `git log -S "<text of the line>" --format="%h %ad" --date=short -- <file>`;
        never reorder its options. The oldest line printed is when it arrived; no output means "unknown".
   - **The maturity assessor's brief**: assess this repository's engineering maturity, report-only. Run, as one plain
     command (no `echo "exit=$?"`, `$?` or redirection after it): `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py"`,
     adding `--stack <name>` only when the arguments hold it, and nothing else. It writes `docs/maturity-assessment.md`,
     the only file it touches; you edit nothing and never adjust a level. Report the overall level line, the table as
     printed, the three moves and one line from "What we did not measure"; on exit 2, its one error line.
     "You cannot ask a question: when something cannot be done, report it and stop."
   - **The security assessor's brief**: assess this repository's security, report-only; never edit code, write an epic
     or commit. In this order. The deterministic pass:
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/security_assess.py" --root .`, adding `--stack <name>` only when the
     arguments hold it; exit 2: report its stderr and stop. Your judgment pass (your assessment mode): read
     `.claude/work/security-assessment.json` first, then the hot paths (the auth module, the route modules, the
     models, the services); Write that JSON, unchanged and alone, to `.sdlc-stage/security-reviewer.json`, with any
     secret value masked to its first four characters plus `...`, then run
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/workfile.py" .claude/work/security-reviewer.json`. The merge:
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/security_assess.py" --root . --merge .claude/work/security-reviewer.json`
     with the same flags; exit 2 lists every refused entry: drop exactly those and rerun once. The script writes
     `docs/security-assessment.md` and `.claude/work/security-assessment.json`; you write only
     `.sdlc-stage/security-reviewer.json`. Report the summary table (the totals by severity), the characteristics, the
     top five findings (id, severity, file:line, title), and the unconfirmed and not-reviewed lists.
     "You cannot ask a question: when something cannot be done, report it and stop."
   When they return, keep the seven reports for step 8 and print none of them in chat: a table printed here reads as
   the run's last message, and a headless run ends at the first message without a tool call. Say the one line in the
   same message as step 8's first call. An auditor that came back without a table is recorded as "audit <area>
   failed: <one line>", an assessor's exit 2 or refusal as "maturity failed: <one line>" or "security failed: <one
   line>"; the other reports stand.
8. **Write `docs/onboarding-report.md`: one hand-off.** First, here: if the graph is indexed, call `graph_impact` once
   per candidate file (at most the five highest-severity files across the five tables), for the order of "What to do
   first". Then launch one `factory:drafter`, description "onboarding report". Its brief: write
   `docs/onboarding-report.md` (run `mkdir -p docs` first; write it with the Write tool; overwrite it if present) from
   what the brief gives it, with the sections below; it is the only file it writes, and it quotes, never adds a
   finding. Give it the init script's report, the `Assumptions:` block (headless), the baseline line of step 4, the
   graph result of steps 5 and 6, the five tables as the auditors returned them, the `graph_impact` numbers, the two
   assessors' reports and the failures recorded so far. It reports the path, the number of entries under Audit
   findings, the first of "What to do first", and a `FILL` line for each gap it could not fill. Last, the line
   "You cannot ask a question: when something cannot be done, report it and stop."
   Sections, in order:
   - **Stack**: the stack and the app and test paths from `.claude/sdlc.json`.
   - **Assumptions**: headless only (interactive: omit). The app paths, test paths, test command and protected paths
     accepted unconfirmed in step 3, the graph defaults of step 1, the graph line from step 5 when it was skipped, and any other default a step
     reported, ending "confirm in .claude/sdlc.json".
   - **Baseline**: total keys in `.claude/baseline.json` and the number of violations by key prefix (the text before
     the first `|`, for example `dead`, `stubs`, `contract`, `routes`), from step 4. If the file is missing, say so.
   - **Graph**: on or off and the provider (from `graph` in `.claude/sdlc.json`), then symbols and edges from the
     `factory:index` result, with the sha. Off, not reachable or not indexed: say why in one line (the line from step
     5, or the index failure).
   - **Audit findings**: take the groups from the five tables, do not rerun checks; the same file:line in two tables is
     one entry. Use these headings and omit any that is empty: "Routes without auth", "Routers never mounted",
     "Frontend calls with no endpoint", "Env reads outside config", "Stubs and TODOs without a ticket". Each entry:
     severity, file:line, one line. Quote the audit; do not add findings.
   - **What to do first**: three findings: the audit's highest severity first, and among equals one that is reachable today (a mounted route, an imported module) before a latent one. If the graph is indexed, use the `graph_impact` numbers of step 8 (affected routes plus tests) only to order findings of the same severity; say the numbers, and say so when every candidate reaches the same tests. Otherwise take the audit's own top three (from the five areas' first three, highest severity first) and say the graph was unavailable. Report results, not how a tool was reached.
   - **Assessments**: one line, nothing more: the maturity level and the security totals by severity, pointing at
     `docs/maturity-assessment.md` and `docs/security-assessment.md` (a failed assessment: its failure line instead).
   - **Failures**: every step recorded as failed (the brief lists them), or "none". Not failures, and never listed here: the graph off
     or not reachable (step 5; the Graph section and Assumptions say it), missing Grep or Glob tools when the grep
     fallback ran, a default taken headless (Assumptions), and a settings file init staged under `.sdlc-stage/`
     because the sandbox refused the write (its `cp` line goes in the review text of step 9).
   When it returns, the summary takes its count of audit findings and its first of "What to do first".
9. **Summary.** Print exactly eight lines in chat: stack; baseline keys; graph symbols and edges (or "graph off" /
   "graph on but not reachable"); number of audit findings (the entries under Audit findings in the report, not the
   ratchet's violation count); the first of "what to do first"; the maturity level (the overall level line); the
   security totals (critical, high, medium, low); and the closing line
   "Next: /factory:epic from the security assessment, then /factory:ticket <ticket text>". A failed assessment's line
   is its failure line. Then tell the human what to review and commit (the files step 2 created,
   `docs/onboarding-report.md`, `docs/maturity-assessment.md` and `docs/security-assessment.md`; nothing is committed;
   when step 0 switched branches, the files are on `chore/onboarding` and they open a pull request from it), that
   `/factory:onboard` reads the agreement, and the escape hatch: `/plugin disable factory` turns every hook off.
   Create a review artifact for a customer, PMO and management audience with the Artifact tool, from `docs/onboarding-report.md`, `docs/maturity-assessment.md` and `docs/security-assessment.md`. Use only figures from those reports, publish it privately, give the human its claude.ai link so they can open and check it inline (it is private: others can open it only once the human shares it from the page's Share menu; the saved HTML opens anywhere), and save its HTML (read it back with the Artifact tool) as `docs/reviews/<date>-onboarding.html` for the portal's evidence.

Rules: do not fix findings, do not update the baseline by hand, do not invent numbers; if a number is not in a step's
output, write "unknown". They hold in every subagent this skill launches.
