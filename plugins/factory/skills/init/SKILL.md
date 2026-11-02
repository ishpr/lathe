---
name: init
description: Onboard this repository in one command: set up the SDLC working agreement, index the code graph, run the audit and write docs/onboarding-report.md. Use whenever the user says onboard, set up, get started, adopt the pack, or runs this on a repo for the first time. Run once per repo, before /factory:ticket.
argument-hint: "[--stack python|node|go|rust] [--marketplace-repo owner/repo]"
allowed-tools: Skill, Read, Write, Glob, Bash(git rev-parse *), Bash(mkdir -p docs), Bash(jq *)
---
Onboard this repository. Arguments: $ARGUMENTS

You orchestrate two sibling plugins and write one report. You have no scripts of your own. Never
edit application code. A failed sibling step never stops the run: note the failure in the report
and go on to the next step.

1. **Check the siblings.** Look in your available skills list for `sdlc:init` and
   `graph:index`. If either is missing, print exactly these two lines for the user and stop:
   ```
   /plugin install sdlc@lathe
   /plugin install graph@lathe
   ```
   Then say: "Install both, restart the session, and run /factory:init again."
2. **Working agreement.** Invoke the skill `sdlc:init` with the arguments above. If the
   arguments do not contain `--marketplace-repo`, append `--marketplace-repo ishpr/lathe`. Pass
   no other flag. If it asks which stack or asks the human to confirm paths, relay the question
   and wait. If it fails, record "init failed: <one line>" for the report and continue.
3. **Code graph.** Run `git rev-parse HEAD`, then invoke the skill `graph:index` with that
   sha. If it fails (for example the database is down), record "graph not indexed: <one line>" and
   continue; later graph steps are then skipped.
4. **Audit.** Invoke the skill `sdlc:audit` (no arguments). Keep its ranked table. If it
   fails, record that and continue.
5. **Write `docs/onboarding-report.md`** (run `mkdir -p docs` first). Overwrite it if present.
   Sections, in order:
   - **Stack**: the stack and the app and test paths from `.claude/sdlc.json`.
   - **Baseline**: total keys in `.claude/baseline.json` and the number of violations by key
     prefix (the text before the first `|`, for example `dead`, `stubs`, `contract`, `routes`).
     Read the file; use `jq` only to count. If the file is missing, say so.
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
   should review `docs/onboarding-report.md` and the files `sdlc:init` created.

Rules: do not fix findings, do not update the baseline by hand, do not invent numbers; if a
number is not in a sibling's output, write "unknown".
