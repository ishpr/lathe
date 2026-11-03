---
name: init
description: Onboard this repository in one command — detect the stack, write .claude/sdlc.json, rules, a CI gate and today's debt baseline, then index the code graph, run the audit and write docs/onboarding-report.md. Use whenever the user says onboard, set up, get started, adopt the pack, or runs this on a repo for the first time. Run once per repo, before any other factory command.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo] [--graph on|off] [--graph-provider local|neon|aws|spanner] [--upgrade] [--headless]"
allowed-tools:
  - Skill
  - Read
  - Glob
  - Edit(docs/onboarding-report.md)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py" --all)
  - Bash(git branch --show-current)
  - Bash(git rev-parse *)
  - Bash(git switch -c chore/onboarding*)
  - Bash(mkdir -p docs)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status)
  - mcp__plugin_factory_graph__graph_status
  - mcp__graph__graph_status
---
Onboard this repository: the working agreement first, then the code graph, the audit and one report. Arguments:
$ARGUMENTS

Never edit application code. A failed step never stops the run: note the failure for the report and go on to the
next step. The run is **headless** when the arguments hold `--headless` or the prompt says no human will answer (an
orchestrated or `claude -p` run); then nothing is asked: steps 1 and 3 say what to do instead. Interactive, the
questions below are the "ask before" moment: asked once, here, never during later work.

**One command per Bash call, exactly as written below.** Never chain a step's command with anything: no semicolon,
no `&&`, no pipe, no redirect, no trailing echo of the exit code and no cat of a file afterwards. Each allow rule
matches one command, so a chained line matches none and is denied when nobody can answer a prompt. Read files with
the Read tool. **A denied command is one command denied, not the tool:** run it once more on its own, exactly as
written; if that is denied too, record the denied command text under Failures and go on to the next step.
When `init_project.py` prints `settings: .claude/settings.json is not writable here ... staged at
.sdlc-stage/settings.json` (or the same for `settings.local.json`), that is Claude Code's sandbox protecting a
settings file it loaded when the session started: not a denial, not a failure, and the script went on with its other
steps. Carry the `cp` line it printed into the review text of step 9 ("a human runs: cp .sdlc-stage/settings.json
.claude/settings.json"), never into Failures, and never try the copy yourself.

0. **Leave a protected branch first, before anything is written.** Run `git branch --show-current`. If it is `main`,
   `master` or a name in `paths.protected_branches` of `.claude/sdlc.json` (read the file if it exists), run
   `git switch -c chore/onboarding`; when that name is taken (`git rev-parse --verify --quiet refs/heads/chore/onboarding`
   prints a sha) use `git switch -c chore/onboarding-2`. Say it in one line: `On <branch>, a protected branch: switched to
   chore/onboarding; the onboarding report and every file init writes land there.` This is the same headless: nobody is
   asked, because the edit guard refuses to write on a protected branch. An empty branch name (detached HEAD) or any
   other branch: change nothing.
1. **Graph: two questions, asked once.** Unless the arguments already hold `--graph`, ask both now, in one batch,
   before the dry run (the script writes the answers into `.claude/sdlc.json`):
   1. Code graph and ledger: **on** (recommended: the orchestrator, impact analysis and the console run on it) or
      **off** (hooks, contracts and evidence stay; no ledger).
   2. Where the graph database lives: **local** (Docker, pgvector, port 5435; the laptop default), **neon**
      (serverless Postgres with pgvector, free tier; the shared default for a team), **aws** (Aurora PostgreSQL or RDS
      for PostgreSQL with pgvector, in the customer's account) or **spanner** (Google Cloud Spanner as the graph
      mirror; the ledger stays on PostgreSQL).
   Append the answers as `--graph on|off --graph-provider <provider>` to the arguments of both init_project.py runs.
   **Headless:** do not ask; the script's defaults apply (`graph: on, provider local`), and the line it prints goes
   into the `Assumptions:` block of step 3 as `graph on, provider local (defaults)`.
   **Plan.** If the arguments do not contain `--marketplace-repo`, append `--marketplace-repo ishpr/lathe` to them for
   both init_project.py runs (the CI gate checks out `plugins/factory` of that repository). Run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --dry-run $ARGUMENTS` and show the plan: detected
   stack, app and test paths, the `graph:` line, the files it will create, and what it will append to CLAUDE.md. If it reports several
   stacks or none, ask which and rerun with `--stack`.
   **Headless:** if it reports several stacks or none, print `ESCALATION: stack ambiguous: <list>; rerun with --stack <one>`
   (`<list>` is the stacks it named, or `none`) and stop with nothing written.
2. **Working agreement.** Run it for real: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" $ARGUMENTS`.
   It writes `.claude/sdlc.json`, `.claude/rules/sdlc-*.md`, `.claude/work/`, a PR template, a CI workflow
   (`.github/workflows/sdlc-gate.yml`), gitignore lines, the working-agreement section in CLAUDE.md, and records the
   current violations as `.claude/baseline.json`. Nothing is committed. If it fails, record "init failed: <one line>"
   for the report and continue.
3. **Confirm the paths.** Open `.claude/sdlc.json` and check: app paths point at the code, test paths at the tests, the
   test command is the one the team runs, and `paths.protected` lists anything humans own (generated files, a
   dead-code sink). Ask the human to confirm or change these; do not guess.
   **Headless:** do not ask. Accept the detected app paths, test paths and test command and the empty protected paths,
   and keep one `Assumptions:` block that lists them (app paths, test paths, test command, protected paths), ending
   with the line `confirm in .claude/sdlc.json`.
4. **Baseline.** Copy the `baseline by check:` line step 2 printed (keys, violations, and the count per key prefix,
   from `.claude/baseline.json`); do not count them again with a shell pipe. No such line (no baseline was recorded):
   say so and why, from step 2's output. Say which checks this stack gets: Python has route auth, module state and
   test quality; Node, Go and Rust have module state and test quality as text heuristics; lint and types come from
   the stack's own tools when they are installed.
5. **Is the graph on and reachable?** Call the MCP tool `graph_status` (server `graph`; `mcp__plugin_factory_graph__graph_status`,
   or `mcp__graph__graph_status`; if tools are deferred, search for `graph_status`; with no such tool run
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status`, which says the same). Three cases:
   - `configured: true`: go on to step 6.
   - `enabled: false` (or an answer `{"error": "graph off", ...}`): print one line, `graph off: the graph steps are
     skipped`, record it for the report, and skip step 6 and the graph part of step 8.
   - on but `configured: false` (unreachable, no schema, or `{"error": "graph not configured", ...}`): never skip it
     silently. Interactive: say the hint in one line (`graph on but not reachable: run /factory:graph provision
     <provider> or set DATABASE_URL`), invoke the skill `factory:graph` with `provision <provider>` (it asks its own
     decisions once; local runs unattended), then call `graph_status` again and go on as above. **Headless:** do not
     provision; print that hint as one line, record it under Assumptions and in the report's Graph section, and skip
     step 6 and the graph part of step 8.
   Graph off, or on but not reachable, is a state the report describes, **not a failure**: it never goes under
   Failures (only a step that ran and failed does, such as `factory:index` failing in step 6).
6. **Code graph.** Run `git rev-parse HEAD`, then invoke the skill `factory:index` with that sha. If it fails (for
   example the database is down), record "graph not indexed: <one line>" and continue; the later graph steps are then
   skipped.
7. **Audit.** Invoke the skill `factory:audit` (no arguments, with the Skill tool, so its own allow rules apply) and
   let it do every step, its by-hand steps 2–6 included (they use Read, Grep and Glob; headless too, they are not
   optional). Every command it runs is in the exact form the audit skill writes (its TODO-age git log command starts
   with -S and the quoted text, every other option after it); never reorder its options. A session whose tool list
   has no Grep or Glob uses the audit's one-command grep fallback: that is not a failure and is not reported as one. Keep its ranked table. If it fails, record that and continue. The audit's table is not the end of this run: go straight on to steps 8 and 9 in the same turn, whatever the audit printed.
8. **Write `docs/onboarding-report.md`** (run `mkdir -p docs` first; write it with the Write tool). Overwrite it if
   present. Sections, in order:
   - **Stack**: the stack and the app and test paths from `.claude/sdlc.json`.
   - **Assumptions**: headless only (interactive: omit). The app paths, test paths, test command and protected paths
     accepted unconfirmed in step 3, the graph defaults of step 1, the graph line from step 5 when it was skipped, and any other default a step
     reported, ending "confirm in .claude/sdlc.json".
   - **Baseline**: total keys in `.claude/baseline.json` and the number of violations by key prefix (the text before
     the first `|`, for example `dead`, `stubs`, `contract`, `routes`), from step 4. If the file is missing, say so.
   - **Graph**: on or off and the provider (from `graph` in `.claude/sdlc.json`), then symbols and edges from the
     `factory:index` result, with the sha. Off, not reachable or not indexed: say why in one line (the line from step
     5, or the index failure).
   - **Audit findings**: take the groups from the audit output, do not rerun checks. Use these headings and omit any
     that is empty: "Routes without auth", "Routers never mounted", "Frontend calls with no endpoint", "Env reads
     outside config", "Stubs and TODOs without a ticket". Each entry: severity, file:line, one line. Quote the audit;
     do not add findings.
   - **What to do first**: three findings chosen by blast radius. If the graph is indexed, invoke `factory:impact` once
     per candidate file and rank by affected routes plus tests; say the numbers. Otherwise take the audit's own top
     three and say the graph was unavailable.
   - **Failures**: every step you recorded as failed, or "none". Not failures, and never listed here: the graph off
     or not reachable (step 5; the Graph section and Assumptions say it), missing Grep or Glob tools when the grep
     fallback ran, a default taken headless (Assumptions), and a settings file init staged under `.sdlc-stage/`
     because the sandbox refused the write (its `cp` line goes in the review text of step 9).
9. **Summary.** Print exactly six lines in chat: stack; baseline keys; graph symbols and edges (or "graph off" /
   "graph on but not reachable"); number of audit findings; the first of "what to do first"; and the closing line
   "Next: /factory:ticket <ticket text>". Then tell the human what to review and commit (the files step 2 created and
   `docs/onboarding-report.md`; nothing is committed; when step 0 switched branches, the files are on
   `chore/onboarding` and they open a pull request from it), that `/factory:onboard` reads the agreement, and the
   escape hatch: `/plugin disable factory` turns every hook off.

Rules: do not fix findings, do not update the baseline by hand, do not invent numbers; if a number is not in a step's
output, write "unknown".
