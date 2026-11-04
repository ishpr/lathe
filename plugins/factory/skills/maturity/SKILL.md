---
name: maturity
description: Assess how mature this repo's agent-assisted SDLC is. Scores plan, design, build, test, deploy, maintain and AI practice from 1 to 5 using git history and files, names the move that raises each, ranks three moves first, then offers to write an epic for the one you pick.
argument-hint: "[--run-tools] [--rev REV] [--rubric PATH] [--stack NAME] [--report-only]"
allowed-tools:
  - Skill
  - Artifact
  - Read
  - Glob
  - AskUserQuestion
  - Skill(factory:epic)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py")
---
You are producing a maturity assessment of this repository, then offering the next step. The
arguments: $ARGUMENTS

The scoring is a script, not your judgment: the same repository state gives the same report. You
run it, show it, and ask. You do not adjust a level, soften a finding, or write the epic before the
user answers.

Run each script as one plain command: no `echo "exit=$?"`, `$?` or redirection after it (under `dontAsk` that fails the allow rule); the tool result already reports a non-zero exit.
1. **Run the assessment.** Pass through only `--run-tools`, `--rev <ref>`, `--rubric <path>` and
   `--stack <name>`, and only if the arguments above contain them. `--report-only` is yours, never the
   script's: it means stop after step 2 (no question, no epic), which is how `/factory:assess` calls you.
   ```
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py"
   ```
   It reads the files and the git history of the current repository (nothing from the project is executed, `--run-tools` aside: that runs ruff, which reads the repository's own ruff config)
   and writes `docs/maturity-assessment.md`. Exit 2: show its one error line and stop; do not
   estimate levels by hand. Exit 0: carry on. A repository with no git history is still scored, from
   its files only, and the report says so.

2. **Show the result.** Print the overall level line and the table exactly as the script printed it
   (the dimension, the level, the evidence lines, the move). Then add at most three sentences of your
   own: which dimension holds the overall level down, which single evidence line you would show a
   sceptical engineer first, and one sentence from "What we did not measure" that changes how far the
   numbers can be trusted (trailers undercount, tests were not run, review quality is not visible).
   Do not restate the table.

2b. Unless `--report-only`: create a review artifact for a customer, PMO and management audience with the Artifact tool, from `docs/maturity-assessment.md`. Use only figures from those reports, publish it privately, give the human its claude.ai link so they can open and check it inline (it is private: others can open it only once the human shares it from the page's Share menu; the saved HTML opens anywhere), and save its HTML (read it back with the Artifact tool) as `docs/reviews/<date>-maturity.html` for the portal's evidence. If the Artifact tool is not available (an unattended run has none), design the same artifact yourself as one self-contained HTML file at `docs/reviews/<date>-maturity.html` and say that no claude.ai link was made.
3. **Ask which move to turn into an epic, then stop.** With `--report-only`, skip this step and the rest. Present numbered options, one recommendation,
   and the reason. Use the three moves from "Three moves first" and their effort and impact:
   ```
   1. Write the epic for <dimension> <from> to <to> (recommended): <why: low effort, high impact, and
      what else it raises>
   2. Write the epic for <dimension> <from> to <to>: <what it buys instead>
   3. Write the epic for <dimension> <from> to <to>: <what it buys instead>
   4. Stop here: the report stays at docs/maturity-assessment.md
   ```
   If the AskUserQuestion tool is available, use it with these options, the recommended one first.
   Otherwise ask in chat. Wait for the answer. Do not infer a yes from silence or from the arguments.

4. **If the user picks 1, 2 or 3**, get the filled prompt for that move (use the same `--rev`, `--rubric`
   and `--run-tools` as in step 1):
   ```
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/maturity.py" --epic <N>
   ```
   It contains the outcome, the evidence lines as the problem statement, what is not yet true, and the
   rubric's success metrics with today's values as the baseline. Invoke the epic skill
   (`factory:epic`) with that prompt, unchanged. If that skill is not in your skills list,
   print the prompt and say: paste it into `/factory:epic` or any epic-writing flow. Never write the epic yourself in its place.
   When the epic comes back, show it and stop. Do not start work on it; the next step is a human
   reading it, then `/factory:ticket` for the first story.

5. **If the user picks 4**, say where the report is and that `--epic N` prints the epic prompt later.

Disagreement with a level: show the "Not yet true" lines for that dimension (each is a condition in the
rubric with today's value). If a threshold does not fit this client, offer to copy the rubric
(`maturity.py --dump-rubric > <path>`), edit the number there, and re-run with `--rubric <path>`; do not
change the default rubric, and do not edit a score by hand.

Rules:
- The report is a file in the working tree; do not commit it unless asked.
- Never print a secret value; the report names a file and a pattern, not the string.
- Do not run the project's tests, install tools, or call the network for this.
