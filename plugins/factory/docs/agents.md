# Agents

The `agents/` directory holds the factory's eight subagent definitions: `test-author`, `reviewer`,
`security-reviewer`, `auditor`, `maturity-assessor`, `security-assessor`, `drafter` and `implementer`
(this guide lives under `docs/` because Claude Code loads every Markdown file in `agents/` as an agent). Each file
is Markdown, YAML frontmatter then a system prompt, and Claude Code loads it as `factory:<name>`. A subagent has its
own context and tool list, cannot ask the human a question, and does not see the conversation: the prompt a skill
hands it is its whole task. The plugin's hooks bind its tool calls like anyone's.

## Why the work is split this way

- The conversation that runs a skill orchestrates and never executes. It loads the skill, plans the hand-offs, briefs
  each subagent through the Agent tool, collects what comes back, asks the human and summarises. Every step that does
  work (a script, an audit, a draft, tests, code, the gate, a review, the commit) runs in one of these agents, so the
  conversation holds briefs and conclusions, not tool output. Eight skills work this way: `init`, `ticket`, `tests`,
  `implement`, `verify`, `ship`, `assess` and `epic` ([`skills/README.md`](../skills/README.md) says what each hands
  off).
- Steps that need nothing from one another run as parallel subagents, launched together in one message. Parallel
  subagents never edit the same file: `/factory:tests` gives each test author its own test file, and
  `/factory:implement` splits the allowed paths by concern, so an implementer that needs a change in another's file
  reports `SEAM: <file>: <what you need there>` instead of making it.
- The test author never sees the implementation, so the tests pin the contract and not the code. `/factory:tests`
  runs before `/factory:implement`, so the behaviour under test does not exist yet, and the brief holds public names
  and "nothing about how the code works inside". The tests are then frozen, so the implementation cannot change them.
- The reviewers never see each other. They are launched together with the same inputs. Neither gets the other's
  findings or the author's account of the change: "a reviewer handed the author's summary reviews the summary".
- The agent that wrote the code cannot approve it. The implementers write the code in `/factory:implement`, and the
  conversation that briefed them writes none. The reviewers are other agents with no tool that writes, and a person
  approves the pull request.

## test-author

Launched by [`/factory:tests`](../skills/tests/SKILL.md), step 1: one per group of criteria (a module or route), each
writing its own test file, all in one message. Tools: `Read, Grep, Glob, Write, Edit, Bash`. Stage `tests`: executor
tier, `sonnet`.

- Purpose: write the failing tests for its group of the contract's criteria, one per acceptance criterion, named as
  the contract names it. Its description says it "Touches test paths only", meaning `paths.tests` in
  `.claude/sdlc.json`.
- Given: its group's acceptance criteria, the rules in `.claude/rules/sdlc-*.md`, the public names the skeleton will
  hold and the one test file it writes. It is told not to ask. Decisions settle an ambiguous criterion, then
  `## Defaults when ambiguous`.
- Returns: `test name → expected failure reason` for each test, and the files it wrote. If a criterion cannot be
  tested as written, it says so "instead of writing a weaker test". The skill records what it chose as assumptions.
- Checked, not trusted: `tests_failing_first.py`, which the tail `implementer` runs, records a reason per failing
  test. An import, attribute, name, syntax or collection error is a wrong reason, and `freeze_tests.py --freeze`
  refuses a file with one.

## reviewer

Launched by [`/factory:verify`](../skills/verify/SKILL.md), step 3, in the same message as the gate. Tools:
`Read, Grep, Glob`. Stage `review.lens`: executor tier, `sonnet`. It has no shell:
"Bash cannot be narrowed to `git diff` in a subagent's tools, so it is left out".

- Purpose: review the diff against the contract. The checks are **Criteria**, **Scope**, **Test integrity**,
  **Callers**, **Invariants**, **Deletions** and **Message vs diff**. The last runs only when it is given a commit
  message, and `/factory:verify` gives none. Style belongs to "the formatter and the ratchet".
- Given: the contract; the output of `git diff HEAD`, pasted verbatim; the untracked files listed by
  `git ls-files --others --exclude-standard`, which it reads; and `assumptions.md` if it exists. The skill adds that
  "an assumption which contradicts a Decision's Value is a **high** finding".
- Returns, in order: a criterion table (`| criterion | evidence (file:line) | test | ok / missing / weak |`); at most
  10 findings as `severity | file:line | what | fix`, highs first, no lows; a mandatory **Not reviewed** section; and a
  verdict, `ship` or `fix first`, with the list of highs. The skill copies both **Not reviewed** lists into the Evidence.

## security-reviewer

Launched in step 3 of `/factory:verify` and of [`/factory:security`](../skills/security/SKILL.md). Tools: `Read, Grep, Glob`.
Stage `review.lens`: executor tier, `sonnet`.

- Review mode, used by `/factory:verify`: the same inputs as `reviewer`, read through a security lens only. It looks
  for routes without the repository's auth, identity taken from the body or the query string, secrets compared with
  `==`, SQL or shell built from strings, error bodies that leak internals, and tests that override auth with no
  401/403 test. It returns at most 10 findings, **Not reviewed** and a verdict.
- Assessment mode, used by `/factory:security` when the prompt starts with `Mode: assessment`: it gets the JSON that
  `security_assess.py` wrote and the hot paths, and returns one JSON block of at most 15 findings with `confirmed`
  and `not_confirmed` verdicts. `security_assess.py --merge` refuses a finding whose `file` is missing, whose `line`
  is not real, or whose `evidence` is not on that line or within three lines of it. `/factory:init` and
  `/factory:assess` do not launch it: their `security-assessor` runs this mode itself.

## auditor

Launched by [`/factory:init`](../skills/init/SKILL.md), step 7 (five at once, one area each), and by
[`/factory:ticket`](../skills/ticket/SKILL.md), step 1 (two read-only reports). Tools: `Read, Grep, Glob, Bash`. Stage
`audit`: executor tier, `sonnet`.

- Purpose: audit one area of the repository per brief and fix nothing, "not even a typo". The five areas are the ones
  [`/factory:audit`](../skills/audit/SKILL.md) covers, which `init` no longer calls: routes and auth, services and
  workers (the ratchet's `--all` list and the modules nothing imports), tests, docs against the code, and TODO ages
  from `git log -S`. A brief from `ticket` names an area of its own: the code the ticket touches, and what already
  exists for it. It has no `Write` or `Edit`; `Bash` is for the ratchet, `git log` and a `grep` fallback.
- Given: the area, `paths.app` and `paths.tests`, and each command with the plugin's absolute path in place of
  `${CLAUDE_PLUGIN_ROOT}`.
- Returns: one table for the area, `| # | severity | file:line | problem | fix |`, ranked security first, then
  correctness, then debt, with a `none` row for each check that found nothing; the three to fix first, by number; and
  what it could not check. **high** is a security hole a caller can reach today, **medium** a correctness fault or a
  hole in code nothing reaches, **low** debt. It quotes paths and lines as it read them and invents no finding, line
  or date: a TODO it could not date is "unknown".

## maturity-assessor

Launched by `/factory:init`, step 7, and [`/factory:assess`](../skills/assess/SKILL.md), step 2, beside the
`security-assessor`. Tools: `Read, Glob, Bash`. Stage `assess`: executor tier, `sonnet`.

- Purpose: run `maturity.py` once and return what it printed. The scoring is the script's, so the same repository
  state gives the same report; the agent never adjusts a level, estimates one by hand or writes an epic. It does what
  [`/factory:maturity`](../skills/maturity/SKILL.md) does with `--report-only`.
- Given: the plugin's absolute path, and `--stack`, `--run-tools`, `--rev` or `--rubric` only when the arguments hold
  them. Never `--report-only`, which is a skill's flag and not the script's, and never `--epic`.
- Returns: the overall level line, the table as printed, the three moves with their effort and impact, one line from
  "What we did not measure" and the report's path. The script writes `docs/maturity-assessment.md`; the agent does not
  commit it. On exit 2 it returns the script's one error line and stops.

## security-assessor

Launched by `/factory:init`, step 7, and `/factory:assess`, step 3. Tools: `Read, Grep, Glob, Write, Bash`. Stage
`assess`: executor tier, `sonnet`.

- Purpose: the whole of [`/factory:security`](../skills/security/SKILL.md) without its offer, in one subagent: the
  deterministic pass (`security_assess.py`), the judgment pass and the merge. A subagent cannot launch another, so it
  runs `security-reviewer`'s assessment mode itself: it writes the JSON with `Write` to
  `.sdlc-stage/security-reviewer.json` and `workfile.py` moves it into place. It never edits application code, the
  report or the baseline, never commits and never writes an epic.
- Given: the plugin's absolute path, and `--stack` and `--offline` only when the arguments hold them.
- Returns: the summary table, the characteristics, the top five findings (id, severity, file:line, title), the "Not
  confirmed by the reviewer" and "Not reviewed" lists, the report's "What to do next" options A, B and C word for word
  with its `Recommended:` line, and the paths `docs/security-assessment.md` and
  `.claude/work/security-assessment.json`.
- Checked, not trusted: `security_assess.py --merge` refuses a finding the code does not support, as it does for the
  reviewer. On exit 2 the assessor drops exactly the entries the script names, invents no value for them, and reruns
  the merge once.

## drafter

Launched by [`/factory:init`](../skills/init/SKILL.md) (the init script, then the onboarding report), `/factory:assess`
(the epic or feature you choose), `/factory:ticket` (the ticket file, the contract and its fill) and
[`/factory:epic`](../skills/epic/SKILL.md) (one per feature, then the merge). Tools:
`Read, Grep, Glob, Write, Edit, Bash`. Stage `draft`: orchestrator tier, `opus`, the one agent not on `sonnet`.

- Purpose: write one document of the kind its brief names, quoting what it is given and adding no finding, number or
  decision of its own ("unknown" for a figure it was not given): the onboarding report, the security epic, one feature
  section of that epic (returned, no file written), or a ticket's contract draft, written through the stage and
  `workfile.py`. A brief that hands it a skill's steps instead of a document (`init`'s script runs) is run as written.
- Given: the reports and files its brief hands it, and the templates under the plugin root (`docs/epic/`,
  `templates/contract.template.md`) at the absolute path the brief gives.
- Returns: for the report, its path, the number of entries under Audit findings and the first of "What to do first";
  for the epic, a ten-line summary (path, outcome, metric, features, first stories, open decisions and owners); for a
  feature, its section as markdown; for a contract, each `workfile.py` line, the Outcome, the Allowed paths and the
  Risk tier. Then one `FILL: <section>: <what is missing>` line per gap.
- Never asks: a gap keeps the template's placeholder and becomes a `FILL:` line, which the conversation puts to the
  human and hands back; it invents no value, owner or Decided by. On a protected branch it writes no epic and reports
  `ESCALATION:`; it never overwrites an epic that is there, and writes `<slug>-2.md` instead.

## implementer

Launched by [`/factory:implement`](../skills/implement/SKILL.md) (one per concern, then the runner and any fixer) and,
with a stage tag in the description, by `/factory:tests` (the tail), `/factory:verify` (the gate and the fixer) and
`/factory:ship` (the shipper). Tools: `Read, Grep, Glob, Edit, Write, Bash`. Stage `implement`: executor tier,
`sonnet`.

- Purpose: make one concern of the contract work: the store, the dependency wiring, the config, whatever its brief
  names. It edits only the files its brief lists, a subset of the allowed paths, because other implementers own the
  rest at the same time, and never a frozen test. A change it needs in a file not on its list is a seam, reported as
  `SEAM: <file>: <what you need there>` and left alone. It runs the brief's tests, makes the smallest change and runs
  them again until they pass.
- Other briefs: given a skill's sequential steps instead of a concern, it runs them as written, one command per Bash
  call, and reports each result: the tail of `tests` (skeletons, `tests_failing_first.py`, the ratchet, the tests-only
  commit, the freeze), the suite runner, `verify`'s gate, the fix of one review finding, `ship`. A brief that says it
  edits no file (the runner, the gate) is obeyed whatever fails: it reports. It commits, pushes or opens a pull
  request only when the brief's steps say so, in their exact words.
- Never asks: a Decision's Value answers an ambiguity, else the contract's `## Defaults when ambiguous`, and the choice
  goes back as an assumption row. What no default settles (a path outside the allowed paths, a wrong frozen test, a
  capability the contract does not list, money, security, data loss) is an `ESCALATION:` line with the options and its
  recommendation, and it stops.
- Returns: the files changed, its assumption rows, the tests it ran by name with the runner's last line, each `SEAM:`
  line, a hook refusal or denied command verbatim with the allow rule that would have covered it, and the
  `ESCALATION:` line if it stopped on one. It writes neither `assumptions.md` nor `questions.md`: the conversation
  merges every subagent's rows.

## Frontmatter

All eight files use the same five fields. Claude Code ignores `permissionMode`, `hooks` and `mcpServers` in a plugin
agent's frontmatter, so the plugin's hooks live in `hooks/hooks.json`.

| Field | Meaning |
|---|---|
| `name` | The agent's name. The plugin prefix makes it `factory:<name>`. |
| `description` | When to launch it. The reviewers' descriptions also state the findings cap and `Read-only`. |
| `tools` | The tools it may use. Left out, it inherits every tool a subagent can have. |
| `model` | `sonnet`, except `drafter`, which is `opus`. A default, not a pin: the spawn guard sets the model. |
| `maxTurns` | The most turns it takes before it stops: 20 for `maturity-assessor`, 25 for the reviewers, 30 for `auditor`, 40 for `test-author`, `security-assessor` and `drafter`, 60 for `implementer`. |

## How a skill launches an agent

- The skill calls the Agent tool with `subagent_type` set to `factory:<name>`, a `description` of a few words that
  names the hand-off, and the brief as the `prompt`. A brief holds, in this order, the task in one sentence, its
  commands exactly as the skill writes them (one plain command per Bash call), the files it may touch and the ones it
  must not, what to report, and the line "You cannot ask a question: when something cannot be done, report it and
  stop." Hand-offs that need nothing from one another are several Agent calls in one message, so they run in parallel:
  the five auditors and two assessors of `/factory:init`, the intake and two auditors of `/factory:ticket`, the test
  authors of `/factory:tests`, the implementers of `/factory:implement`, the gate and both reviewers of
  `/factory:verify`, the two assessors of `/factory:assess` and the feature drafters of `/factory:epic`. A step that
  needs another's report waits for it.
- The skill passes no `model`: "Do not pass a `model` on the Agent call: the spawn guard pins it from the routing
  policy by agent type." `hooks/guard_agent_spawn.py` runs before every Agent call and pins `model` to the alias that
  `sdlc_graph/ledger/routing_policy.json` gives the stage. It fails closed. Each agent names its own stage there:
  `test-author` is `tests`, `reviewer` and `security-reviewer` are `review.lens`, `auditor` is `audit`, the two
  assessors are `assess`, `drafter` is `draft` and `implementer` is `implement`. `draft` is on the orchestrator tier
  (`opus`); the rest are on the executor tier (`sonnet`).
- An `implementer` also runs steps that are not implementation: the tail of `/factory:tests`, the gate and the fixer of
  `/factory:verify`, the shipper of `/factory:ship`. The skill starts the `description` of those hand-offs with the tag
  `[sdlc stage=<stage> node=<slug>]` (`tests`, `verify` or `ship`), which the guard reads before the agent type, so the
  ledger records the hand-off under that stage and not under `implement`. (The implementers of `/factory:implement`
  need no tag.) The guard reads the tag from the description only, never from the prompt, which can carry ticket text.
- After every Agent call the run recorder (`hooks/record_agent_run.py`) appends one row to
  `.claude/work/<slug>/handoffs.md`, or to `.claude/work/handoffs.md` when no contract is active: the time, the agent,
  the hand-off's `description`, the outcome, the duration and the tool uses. It does so whether or not the graph is
  configured, and `/factory:ship` puts the log in the pull request body under Hand-offs, so the evidence names who did
  what.
- If the Agent tool answers that an agent runs in the background, the skill ends its turn with one line saying it is
  waiting, and carries on when the report arrives. It never sleeps or polls. Unattended runs fan out too:
  `scripts/run_headless.sh` puts `Agent` on its base allow lists.

## What an agent cannot do

- Ask. Claude Code removes `AskUserQuestion` from every subagent, every brief ends with "You cannot ask a question",
  and `/factory:tests`, `/factory:implement` and `/factory:verify` say "Never ask". A question for the human is asked
  in the conversation. A question the contract cannot settle is an escalation: the subagent reports
  `ESCALATION: <one line>` and stops, and the skill writes `questions.md` and stops. A gap in a document is a
  `FILL: <section>: <what is missing>` line in the drafter's report, which the conversation puts to the human.
- Write outside the allowed paths. The reviewers have no `Write`, `Edit` or `Bash`. `test-author`, `drafter` and
  `implementer` have all three, the `security-assessor` has `Write` and `Bash`, the `auditor` and `maturity-assessor`
  have `Bash` and no `Write` or `Edit`, and `guard_edit.py` binds each like the main session. It denies a write outside
  `allowed_paths` (rule `scope`), to a protected file (`protected-file`), to a frozen test (`frozen-test`) or with
  content that looks like a secret (`secret`).
- Run a command no rule covers. A subagent's tool calls are approved by the session's permission rules alone, never by
  the `allowed-tools` of the skill that launched it: under `dontAsk` a command the settings do not allow is denied.
- Approve or merge. `guard_bash.py` denies `gh pr merge`, `gh pr review --approve` and the same through `gh api`
  (rules `pr-merge`, `pr-approve`), and `freeze_tests.py --unfreeze`. The ledger's guards for `approved` are
  `evidence:review_approval.non_author`, `evidence:ci_run.green.binding` and `actor:human|ci`.

## Adding an agent

1. Create `agents/<name>.md` with `name`, `description`, `tools`, `model` and `maxTurns`. `plugin.json` lists no
   components, so Claude Code finds the file by convention. Give it the fewest `tools` the job needs, and write the
   prompt for a reader with no context: what the caller pastes in, what comes back, the cap.
2. Launch it from a skill with the Agent tool and no `model`. Add it to `SUBAGENT_STAGES` in
   `hooks/guard_agent_spawn.py` (see Limits), with a stage that `sdlc_graph/ledger/routing_policy.json` gives a tier (an
   unknown stage falls back to `implement`), and a case to `tests/graph/test_hooks.py`.
3. Permission checker: `python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory`. It reads
   every skill and every agent body, and exits 1 on a command that matches no rule: under `dontAsk` that command is
   denied. A skill's command may be covered by its `allowed-tools` or by `templates/settings.project.json`. An agent's
   only by the settings, because a subagent's tool calls are approved by the session's rules alone and its `tools`
   line names tools, not rules.
4. Plugin validator: `claude plugin validate plugins/factory --strict`. CI runs both in the `validate` job. It fails on
   frontmatter that does not parse (Claude Code would still load that agent with every field dropped, `tools`
   included). With `--strict` it also fails on a missing frontmatter block or `description`, and on a `version` that
   differs between `plugin.json` and the marketplace entry.

## Limits

- No test runs an agent against a model. CI parses the frontmatter, tests the model routing, asserts a few strings in
  `security-reviewer.md` and that each newer agent says it cannot ask (the `auditor` has no `Write` or `Edit`, the
  `implementer` has both), and runs `check_skill_permissions.py` over every agent body. On Claude Code 2.1.317 the
  validator accepted a misspelled field, an unknown `model` and a non-numeric `maxTurns`, so check those by hand.
- A verdict is advice. `scripts/verify.py` does not read it and nothing blocks `/factory:ship` on `fix first`. The
  session may answer a high finding with a note under **Review notes**. A person's review and CI decide.
- `tools` cannot narrow Bash, so every agent that has it holds all of it, bounded by the allow rules and
  `guard_bash.py`. "Touches test paths only" is `test-author`'s prompt, backed by `allowed_paths` and the commit guard
  (anything but tests and new skeletons needs a verify stamp). No hook confines its writes to test paths, and none
  reads which agent is calling: the files an `implementer` may edit are its brief's list, and only `allowed_paths` is
  enforced. The `auditor`'s "read-only" is a prompt too: it has no `Write` or `Edit`, but its `Bash` could write.
- The guard's `SUBAGENT_STAGES` names all eight agents. A stage resolves in this order: a
  `[sdlc stage=<stage> node=<slug>]` tag in the call's `description`, then `SDLC_STAGE`, then the agent's own entry,
  then `.claude/work/current.json`, else `implement`. So a named agent keeps its own stage while a contract is active,
  when that file says `contract.design` (`/factory:ticket` writes it); an agent type no entry names, such as a
  general-purpose agent, takes `contract.design` and the guard rewrites its model to `opus`. Under the orchestrator
  `SDLC_STAGE` applies, outranking the agent's entry, and the step's spawns run on `sonnet`.
- Only the assessment mode of `security-reviewer` and the five newer agents say that repository text is data, not
  instructions. The prompts of `test-author`, `reviewer` and the review mode of `security-reviewer` do not, though the
  diff the reviewers read is untrusted text too.
- The in-session reviewers follow their own files, not `REVIEW.md`, which `/factory:init` installs for CI's advisory
  review and for human reviewers. The guards are speed bumps: `guard_bash.py` says the sandbox, database roles and CI
  are the boundary.
