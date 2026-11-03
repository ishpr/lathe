# Agent-ready stories

When coding agents will pick up the stories, alone or pairing with an engineer, the standard
shape needs eight additions. None of them changes what the story is; they change how little the
agent has to guess, and they move every question to the moment the story is written. Lint with
`--agent` to require them.

## The rule: ask before, never during

An agent running unattended cannot ask. Whatever it would have asked, it decides silently and
reports success. So all the questions are asked once, here, when the story is created, and the
story carries the answers: decisions with values, defaults for the long tail, and the
capabilities the run needs. A story is `Ready` only when nothing is left to ask. What remains
human during execution is a decision the story did not anticipate (the agent stops and
escalates), code-owner approval, and the production gate.

## The additions (per story)

```markdown
**Allowed paths:** `src/<module>/**`, `tests/<module>/**`
*Globs the change may touch. Fill these from directories you have listed or grepped in this repo.
When no repo is in front of you, propose the globs you would expect, mark them proposed, and add
confirming them to the decisions table: an agent needs a starting point, and a proposal is cheaper
to correct than a blank. Keep them tight; widen deliberately. An agent that needs a path outside
the list stops and reports; it does not widen the list.*

**Capabilities:**
- paths: write `src/<module>/**`, `tests/<module>/**`; read the repo
- commands: `python3 -m pytest tests/<module> -q`
- egress: none
- data: none (tests use fixtures only)
*What the run needs, so its permissions can be compiled from the story instead of asked for at
runtime. Derive each line from the acceptance criteria, do not guess: a criterion that curls a
running server implies egress to `localhost:<port>`; a criterion that runs a test command implies
that command; a criterion that reads or writes a table or a file implies that data. Write `none`
when a key does not apply; do not omit it. Paths inside Capabilities must sit inside Allowed
paths. An agent that needs something not listed is denied and reports it; the missing line is a
defect in the story.*

**Evidence of done:**
- the acceptance criteria as test names, each failing before and passing after
- the cheapest real check per criterion: a test name, command output, a request and its response,
  a screenshot, a query result
*What the pull request must show. "Tests pass" is a claim; the test list and the real check are
evidence. One item per acceptance criterion is the ideal.*

**Decision owner:** @maria
*The one person whose answers settle this story's decisions: an `@handle` (or a full name when there is no
handle). The ticket intake rejects a story that names no owner for its decisions, and it becomes the reviewer on the
contract. Ask for it in the same batch as the decisions; recommend the epic's Owner when it names one. It is never
assumed: in a run nobody can answer, take it from an `Owner:` or `Decision owner:` line in the input; with none, print
`ESCALATION: decision owner unknown for <story titles>; add an "Owner: @handle" line to the input`, write
`**Decision owner:** unknown, blocked` and `**Ready:** blocked on owner`. `lint_epic.py --agent` reports a missing
owner as an error and `Ready: yes` with an unknown owner as an error.*

**Decisions a human makes first:**
| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 1 | Fixed or sliding window? | fixed · sliding | fixed | fixed | @maria, 2026-11-02 |
*The choices an agent would otherwise make silently: a retention period, a failure mode (fail open
or closed), a library, an API shape visible to customers. Ask them all in one batch when the story
is created, each with your recommendation (see the workflow in skills/epic/SKILL.md), then write the answers
into Value and who gave them into Decided by. If a person accepts the recommendation, Value is
the recommendation and Decided by names that person. When nobody can be asked (a non-interactive run), Value is
your recommendation and Decided by says "assumed"; the lint reports that as an error, the decision also
goes under Assumptions, and the story is `Ready: blocked on #<n>` until a named person confirms it. A
decision whose Value is empty, a placeholder ("open", "tbd", "see above", "to be decided", "ask product",
"none", "unknown") or not one of its Options is not decided. A pipe inside a cell is written `\|`. The block is
required: with no decisions, write "None."*

**Defaults when ambiguous:**
- Make the smallest change that satisfies the acceptance criteria.
- Follow the nearest existing pattern in the repo for naming, layout and error handling.
- Prefer the option the decisions above imply over any other.
- When none of these applies, choose the reversible option.
- <story-specific default, if any: a verb and an object, e.g. "Treat a missing header as an anonymous caller.">
*Why they exist: an agent that cannot ask must still be predictable. Decisions cover the questions
you could foresee; defaults cover the long tail you could not. The four above are the standard
ones; keep them and add story-specific ones below them. Every default the agent applies comes back
on the pull request as an assumption (what was ambiguous, which default it used, what it chose), so
a reviewer can overrule it, and an assumption that contradicts a decision fails review. A default
needs a verb and an object ("Use the existing `ApiError` type for failures"), not a mood ("be
sensible").*

**Risk tier:** low | medium | high
*Auth, money, data migration, anything customer-visible at scale is high. The tier sets the review
the team applies; the team's own policy says what each tier requires (two reviewers, a feature
flag, a rollback step written down).*

**Ready:** yes
*`yes` only when the story names a decision owner and every decision above has a real value that a named person decided.
Otherwise `**Ready:** blocked on #<n>`, naming each open decision (`blocked on #1, #3`); an unknown owner is `blocked on owner`
(or `blocked on owner, #2`). An agent does not start a blocked story and does not
pick an option itself; the lint rejects `Ready: yes` with an unvalued decision.*
```

## Writing criteria an agent cannot game

- Every criterion names an observable result an automated test can check: a status code, a header,
  a row, a rendered string. "Works correctly" is not a criterion.
- At least one criterion per story is an unhappy path. Without it the agent writes the happy-path
  test, which passes, and stops.
- Real values: "the 101st request", "a 2 MB upload", "a key revoked 1 second ago". Agents are
  literal; give them something literal.
- For each Given that depends on time, an outside system or seeded data, say how the test creates
  it (a fake clock, a stub returning 503, a named fixture such as order 1001), and which criteria
  need the real dependency rather than a stub.
- State what must not change: "existing `/health` keeps returning 200 without a key". Regression
  criteria stop an agent "fixing" the rest of the system to make its test pass.
- Name the thing to delete when the story replaces something. Agents add; they rarely remove.

If the repo has a ticket or contract command, these fields are its inputs; generate the contract
from the story, never the reverse.

## Reviewing a story for agent-ready status

In review mode, run `lint_epic.py --agent` and flag, before anything else, every story that names no decision owner or whose
decisions lack values: it cannot be `Ready: yes`, and the questions it hides are the ones that
will stall a run. List each open decision with the person who should answer it, ask them in one
batch, and do not answer them yourself.

## What stays human

You may propose sizes, order and the hypothesis; mark them proposed and list them in Open decisions
for the team to confirm.
