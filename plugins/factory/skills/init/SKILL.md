---
name: init
description: Onboard this repository in one command: set up the SDLC working agreement, index the code graph, run the audit and write docs/onboarding-report.md. Use whenever the user says onboard, set up, get started, adopt the pack, or runs this on a repo for the first time. Run once per repo, before /factory:ticket.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo] [--headless]"
allowed-tools: Skill, Read, Write, Glob, Bash(git rev-parse *), Bash(git branch --show-current), Bash(git switch -c chore/onboarding*), Bash(mkdir -p docs), Bash(jq *)
---
Onboard this repository. Arguments: $ARGUMENTS

You orchestrate two sibling plugins and write one report. You have no scripts of your own. Never
edit application code. A failed sibling step never stops the run: note the failure in the report
and go on to the next step.

The run is **headless** when the arguments hold `--headless` or the prompt says no human will answer (an
orchestrated or `claude -p` run). Headless, no step waits for an answer: the questions were asked at creation
(the autonomy policy's "ask before"), and an unattended run takes the detected values and reports them.
**One command per Bash call, exactly as its skill writes it.** Never chain a command with anything: no semicolon, no
`&&`, no pipe, no redirect, no trailing echo of the exit code and no cat of a file afterwards. Each allow rule matches
one command, so a chained line matches none and is denied headless. Read files with the Read tool.
**A denied command is one command denied, not the tool: run that command once more on its own, exactly as its skill
writes it; if that is denied too, record the denied command text under Failures and go on to the next step.**

0. **Leave a protected branch first, before anything is written.** Run `git branch --show-current`. If it is `main`,
   `master` or a name in `paths.protected_branches` of `.claude/sdlc.json` (read the file if it exists), run
   `git switch -c chore/onboarding`; when that name is taken (`git rev-parse --verify --quiet refs/heads/chore/onboarding`
   prints a sha) use `git switch -c chore/onboarding-2`. Say it in one line: `On <branch>, a protected branch: switched to
   chore/onboarding; the onboarding report and every file init writes land there.` Headless too: nobody is asked, because
   the edit guard refuses to write on a protected branch. An empty branch name (detached HEAD) or any other branch: change
   nothing.
1. **Check the siblings.** Look in your available skills list for `sdlc:init` and
   `graph:index`. If either is missing, print exactly these two lines for the user and stop:
   ```
   /plugin install sdlc@lathe
   /plugin install graph@lathe
   ```
   Then say: "Install both, restart the session, and run /factory:init again."
2. **Working agreement.** Invoke the skill `sdlc:init` with the arguments above. If the
   arguments do not contain `--marketplace-repo`, append `--marketplace-repo ishpr/lathe`. Pass
   no other flag. Interactive: if it asks which stack or asks the human to confirm paths, relay the
   question and wait. Headless: also append `--headless` (if it is not there already) and never wait on its
   questions; if it prints a line starting `ESCALATION:`, print that line, write nothing, and end the run (a
   sibling's ESCALATION is the one thing that ends it); keep its `Assumptions:` block for the report. If it
   fails, record "init failed: <one line>" for the report and continue.
3. **Code graph.** Run `git rev-parse HEAD`, then invoke the skill `graph:index` with that
   sha. If it fails (for example the database is down), record "graph not indexed: <one line>" and
   continue; later graph steps are then skipped.
4. **Audit.** Invoke the skill `sdlc:audit` (no arguments) and let it do every step, its by-hand steps 2–6
   included (they use Read, Grep and Glob; headless too, they are not optional). Keep its ranked table. If it
   fails, record that and continue.
5. **Write `docs/onboarding-report.md`** (run `mkdir -p docs` first). Overwrite it if present.
   Sections, in order:
   - **Stack**: the stack and the app and test paths from `.claude/sdlc.json`.
   - **Assumptions**: headless only (interactive: omit). The app paths, test paths, test command and
     protected paths accepted unconfirmed from `sdlc:init`'s `Assumptions:` block, and any other default a
     sibling reported, ending "confirm in .claude/sdlc.json".
   - **Baseline**: total keys in `.claude/baseline.json` and the number of violations by key
     prefix (the text before the first `|`, for example `dead`, `stubs`, `contract`, `routes`).
     Copy them from the `baseline by check:` line that `sdlc:init` printed (init_project.py counts them from the
     file); run no shell command to count them. If there is no such line, say the baseline was not recorded and
     why. If the file is missing, say so.
   - **Graph**: symbols and edges from the `graph:index` result, with the sha. If not
     indexed, say why.
   - **Audit findings**: take the groups from the audit output, do not rerun checks. Use these
     headings and omit any that is empty: "Routes without auth", "Routers never mounted",
     "Frontend calls with no endpoint", "Env reads outside config", "Stubs and TODOs without a
     ticket". Each entry: severity, file:line, one line. Quote the audit; do not add findings.
   - **What to do first**: three findings chosen by blast radius. If the graph is indexed, invoke
     `graph:impact` once per candidate file and rank by affected routes plus tests; say
     the numbers. Otherwise take the audit's own top three and say the graph was unavailable.
   - **Failures**: every step you recorded as failed, or "none".
6. **Summary.** Print exactly six lines in chat: stack; baseline keys; graph symbols and edges;
   number of audit findings; the first of "what to do first"; and the closing line
   "Next: /factory:ticket <ticket text>". Mention that nothing was committed and that the human
   should review `docs/onboarding-report.md` and the files `sdlc:init` created. When step 0 switched branches, add one
   line: the files are on `chore/onboarding`; open a pull request from it.

Rules: do not fix findings, do not update the baseline by hand, do not invent numbers; if a
number is not in a sibling's output, write "unknown".
