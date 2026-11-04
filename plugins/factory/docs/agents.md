# Agents

The `agents/` directory holds the factory's subagent definitions: `test-author`, `reviewer` and `security-reviewer`
(this guide lives under `docs/` because Claude Code loads every Markdown file in `agents/` as an agent). Each file
is Markdown, YAML frontmatter then a system prompt, and Claude Code loads it as `factory:<name>`. A subagent has its
own context and tool list, cannot ask the human a question, and does not see the conversation: the prompt a skill
hands it is its whole task. The plugin's hooks bind its tool calls like anyone's.

## Why the work is split this way

- The test author never sees the implementation, so the tests pin the contract and not the code. `/factory:tests`
  runs before `/factory:implement`, so the behaviour under test does not exist yet, and the brief holds public names
  and "nothing about how the code works inside". The tests are then frozen, so the implementation cannot change them.
- The reviewers never see each other. They are launched together with the same inputs. Neither gets the other's
  findings or the author's account of the change: "a reviewer handed the author's summary reviews the summary".
- The agent that wrote the code cannot approve it. The main session writes the code in `/factory:implement`. The
  reviewers are other agents with no tool that writes, and a person approves the pull request.

## test-author

Launched by [`/factory:tests`](../skills/tests/SKILL.md), step 1. Tools: `Read, Grep, Glob, Write, Edit, Bash`.

- Purpose: write the failing tests for the contract, one per acceptance criterion, named as the contract names it.
  Its description says it "Touches test paths only", meaning `paths.tests` in `.claude/sdlc.json`.
- Given: the acceptance criteria, the rules in `.claude/rules/sdlc-*.md` and the public names the skeleton will hold.
  It is told not to ask. Decisions settle an ambiguous criterion, then `## Defaults when ambiguous`.
- Returns: `test name → expected failure reason` for each test, and the files it wrote. If a criterion cannot be
  tested as written, it says so "instead of writing a weaker test". The skill records what it chose as assumptions.
- Checked, not trusted: `tests_failing_first.py` records a reason per failing test. An import, attribute, name,
  syntax or collection error is a wrong reason, and `freeze_tests.py --freeze` refuses a file with one.

## reviewer

Launched by [`/factory:verify`](../skills/verify/SKILL.md), step 3. Tools: `Read, Grep, Glob`. It has no shell:
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

- Review mode, used by `/factory:verify`: the same inputs as `reviewer`, read through a security lens only. It looks
  for routes without the repository's auth, identity taken from the body or the query string, secrets compared with
  `==`, SQL or shell built from strings, error bodies that leak internals, and tests that override auth with no
  401/403 test. It returns at most 10 findings, **Not reviewed** and a verdict.
- Assessment mode, used by `/factory:security` when the prompt starts with `Mode: assessment`: it gets the JSON that
  `security_assess.py` wrote and the hot paths, and returns one JSON block of at most 15 findings with `confirmed`
  and `not_confirmed` verdicts. `security_assess.py --merge` refuses a finding whose `file` is missing, whose `line`
  is not real, or whose `evidence` is not on that line or within three lines of it.

## Frontmatter

All three files use the same five fields. Claude Code ignores `permissionMode`, `hooks` and `mcpServers` in a plugin
agent's frontmatter, so the plugin's hooks live in `hooks/hooks.json`.

| Field | Meaning |
|---|---|
| `name` | The agent's name. The plugin prefix makes it `factory:<name>`. |
| `description` | When to launch it. The reviewers' descriptions also state the findings cap and `Read-only`. |
| `tools` | The tools it may use. Left out, it inherits every tool a subagent can have. |
| `model` | `sonnet` in all three. A default, not a pin. |
| `maxTurns` | The most turns it takes before it stops: 40 for `test-author`, 25 for the reviewers. |

## How a skill launches an agent

- The skill calls the Agent tool with `subagent_type` set to `factory:test-author`, `factory:reviewer` or
  `factory:security-reviewer`. `/factory:verify` makes both reviewer calls in one message, so they run in parallel.
- The skill passes no `model`: "Do not pass a `model` on the Agent call; the agent's own `model` applies only when
  the call leaves it unset." `hooks/guard_agent_spawn.py` still runs before every Agent call and pins `model` to the
  alias that `sdlc_graph/ledger/routing_policy.json` gives the stage (`sonnet` for `tests` and `review.lens`, `opus`
  for `contract.design`). It fails closed.
- If the Agent tool answers that an agent runs in the background, the skill ends its turn with one line saying it is
  waiting, and carries on when the report arrives. It never sleeps or polls.

## What an agent cannot do

- Ask. Claude Code removes `AskUserQuestion` from every subagent, and `/factory:tests` and `/factory:verify` say
  "Never ask". A question the contract cannot settle is an escalation: the skill writes `questions.md` and stops.
- Write outside the allowed paths. The reviewers have no `Write`, `Edit` or `Bash`. `test-author` has all three, and
  `guard_edit.py` binds it like the main session. It denies a write outside `allowed_paths` (rule `scope`), to a
  protected file (`protected-file`), to a frozen test (`frozen-test`) or with content that looks like a secret (`secret`).
- Approve or merge. `guard_bash.py` denies `gh pr merge`, `gh pr review --approve` and the same through `gh api`
  (rules `pr-merge`, `pr-approve`), and `freeze_tests.py --unfreeze`. The ledger's guards for `approved` are
  `evidence:review_approval.non_author`, `evidence:ci_run.green.binding` and `actor:human|ci`.

## Adding an agent

1. Create `agents/<name>.md` with `name`, `description`, `tools`, `model` and `maxTurns`. `plugin.json` lists no
   components, so Claude Code finds the file by convention. Give it the fewest `tools` the job needs, and write the
   prompt for a reader with no context: what the caller pastes in, what comes back, the cap.
2. Launch it from a skill with the Agent tool and no `model`. Add it to `SUBAGENT_STAGES` in
   `hooks/guard_agent_spawn.py` (see Limits) and a case to `tests/graph/test_hooks.py`.
3. Permission checker: `python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory`. It reads
   skills, not agent files, and exits 1 on a command a skill names that matches no rule in its `allowed-tools` or
   `templates/settings.project.json`: under `dontAsk` that command is denied. A command the agent runs needs a rule too.
4. Plugin validator: `claude plugin validate plugins/factory --strict`. CI runs both in the `validate` job. It fails on
   frontmatter that does not parse (Claude Code would still load that agent with every field dropped, `tools`
   included). With `--strict` it also fails on a missing frontmatter block or `description`, and on a `version` that
   differs between `plugin.json` and the marketplace entry.

## Limits

- No test runs an agent against a model. CI parses the frontmatter, tests the model routing and asserts a few strings
  in `security-reviewer.md`. On Claude Code 2.1.317 the validator accepted a misspelled field, an unknown `model` and
  a non-numeric `maxTurns`, so check those by hand.
- A verdict is advice. `scripts/verify.py` does not read it and nothing blocks `/factory:ship` on `fix first`. The
  session may answer a high finding with a note under **Review notes**. A person's review and CI decide.
- `tools` cannot narrow Bash, so `test-author` holds all of it, bounded by the allow rules and `guard_bash.py`.
  "Touches test paths only" is its prompt, backed by `allowed_paths` and the commit guard (anything but tests and new
  skeletons needs a verify stamp). No hook confines its writes to test paths.
- The guard's `SUBAGENT_STAGES` names `test-author` and `reviewer`, not `security-reviewer`. An unnamed agent's stage
  comes from a `[sdlc stage=<stage> node=<slug>]` tag in the call's `description`, then `SDLC_STAGE`, then
  `.claude/work/current.json`. While a contract is active that file says `contract.design` (`/factory:ticket` writes
  it), so the guard rewrites the model to `opus`. Under the orchestrator `SDLC_STAGE` applies and it runs on `sonnet`.
- Only the assessment mode of `security-reviewer` says that repository text is data, not instructions. The diff the
  other prompts read is untrusted text too.
- The in-session reviewers follow their own files, not `REVIEW.md`, which `/factory:init` installs for CI's advisory
  review and for human reviewers. The guards are speed bumps: `guard_bash.py` says the sandbox, database roles and CI
  are the boundary.
