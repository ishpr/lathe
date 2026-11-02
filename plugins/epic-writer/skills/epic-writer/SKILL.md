---
name: epic-writer
description: Write, break down and review epics, features and user stories that follow standard agile practice — outcome-first epics, releasable features, INVEST stories with runnable acceptance criteria. Use it whenever someone wants to draft, shape, split, refine, critique or rewrite an epic, feature, story, initiative, PRD section, roadmap item or ticket tree (Jira, Linear, Azure DevOps, GitHub issues) — including "turn this into tickets", "break this down", "split this story", "is this story too big", "write this up for the team", "is this epic any good", "make this agent-ready", or a vague ask pasted from leadership. Also use it when a plan or spec needs acceptance criteria that a tester or a coding agent can run.
argument-hint: <initiative, feature request, ticket text, or path to an existing epic>
---

# Epic writer

You turn an intention — a leadership ask, a customer complaint, a ticket, a half-written epic —
into work a team can pick up: an epic that says why, features that say what, stories that say how
small. The input: $ARGUMENTS

If the input is empty, ask what to shape. If it is a path, read the file and use review mode.

## What you are producing

Three levels, each answering a different question. Keep them distinct; most bad backlogs come from
mixing them.

| Level | Answers | Size | Test of a good one |
|---|---|---|---|
| **Epic** | *Why?* An outcome someone will notice, a hypothesis, the measure that says it worked | a quarter or less (longer: an initiative or theme) | you could stop after two features and still have delivered value |
| **Feature** | *What?* A capability that moves the epic's metric and can be released on its own | up to about two sprints | a user can do something new after it ships, without the features after it (an enabler says what it unlocks instead) |
| **Story** | *How small?* The smallest slice a team finishes and verifies in a few days | one to three days | the acceptance criteria can be run by a tester, a script, or an agent |

These are roles, not mandatory names. Use the team's words (initiative, capability, PBI, ticket);
if they have only two levels, merge Feature into Epic or Story. Sizes are rules of thumb, not tests.

An epic written as a solution ("build a Redis cache") cannot be judged done or valuable. A feature
that cannot be released without the ones after it cannot be reprioritised. A story without
acceptance criteria cannot be tested, and the first thing an agent does with it is invent them.

## Workflow

1. **Read before you write.** Find out who has the problem, what the evidence is (numbers, support
   tickets, quotes), the constraints (deadline, compliance, platform), and what already exists: grep
   `docs/`, `README`, existing epics or ADRs, the tracker's conventions if a repo is in front of you.
   Note the team's definition of done if they have one; reuse their words.
2. **Pick the level.** A vague or strategic input starts at the epic. A concrete capability is a
   feature, with its epic named in one line (invent the epic only if none exists, and say so). An
   existing epic or feature means review mode (below).
3. **Ask once, then write.** Answer what you can from the input and the repo first. Then ask every
   open question in a single batch, before writing anything: for an epic or a human-built story at
   most four questions, each with your recommendation as the default, so a person can answer "yes to
   all". Never ask a second round and never ask in the middle of writing. For an agent-ready story the
   cap does not apply to its decisions: every decision the story has goes in the one batch (group them
   by story; a long batch is still one batch), because a decision left out of it is one the agent
   makes silently. For the epic's own questions, what you did not ask stays an assumption. Choose from: who
   exactly the user is; the outcome and how it is measured today; why now (what happens if it waits
   a quarter); hard constraints; what is explicitly out; what result would make us stop; who will
   carry out the stories (people, coding agents, or both). For agent-ready stories the batch is
   the story's decisions (retention, failure mode, library, customer-visible shape), because an
   agent that cannot ask will otherwise decide them silently. If nobody can answer (a
   non-interactive run, a subagent, or "just draft it"), take your recommendations, write them as
   the decision values with "Decided by: assumed, confirm" and list them under **Assumptions**; an
   assumption on the page is cheap to correct, a silent one is expensive. A story with an assumed
   decision is not ready: its Ready line is `**Ready:** blocked on #<n>` until a named person confirms
   that decision (then Decided by names them), and `lint_epic.py --agent` reports an assumed decision
   as an error.
4. **Write top-down, slice bottom-up.** Epic, then its features, then stories for feature 1 only —
   plus feature 2 only if feature 1 alone cannot test the hypothesis. Aim for three to six stories
   per feature. Later features and stories get one line each, in the form
   `- Later: <title> — to be sliced when scheduled`, never a `### Story:` heading (the lint treats
   every Story heading as a full story). Detailed stories for feature five are wasted when feature
   one's learning changes them.
   If agents will carry out the stories (the user says so, or a repo with agent tooling is present
   and nobody says otherwise), read `references/agent-ready.md` before writing stories, add its
   fields to each one (allowed paths, capabilities, evidence of done, decisions with values,
   defaults when ambiguous, risk tier, a Ready line), and lint with `--agent`. Agents execute on
   contracts and never ask: whatever is open when the story is written is what they will decide
   alone. A story is `**Ready:** yes` only when every decision has a real value (one of its options, never
   "see above" or "ask product") that a named person decided; otherwise
   `**Ready:** blocked on #<n>`. An agent given a story without a negative criterion writes a
   happy-path test and calls it done.
5. **Check.** Run the checklist at the end of this file. When the output is a file, run
   `python3 <this skill's directory>/scripts/lint_epic.py <file>` — take the path from where this
   SKILL.md lives, not from the current directory; the script needs only the Python 3 standard
   library. Fix every error. Fix a warning only when it is right, and never reword a criterion just
   to satisfy a pattern: the lint only checks that a failure word is present; you still confirm each
   one is a real failure. Two passes at most, then list what remains in the chat summary. If Python
   is unavailable, say so and check by hand.
6. **Deliver.** Default output is one markdown file per epic where the project keeps documents
   (`docs/epics/<slug>.md` if nothing else exists; otherwise where the user says), plus a summary
   in chat of at most eight lines: the outcome, the headline metric, the features by name, the
   first stories by name, open decisions (how many, and the first to settle), assumptions (how
   many, and the one most likely to be wrong). Everything else is in the file; do not repeat it.
   For tracker import (Jira, Linear, Azure DevOps, GitHub) read `references/exports.md`. The document
   opens refinement; it does not replace it. Criteria and sizes are proposals for the people who
   will build and test the story to change.

## The shapes

Compact versions; `references/templates.md` has the full ones with guidance per field.

**Epic**

```markdown
# Epic: <the outcome in plain words — no solution in the title>
**Owner:** <name or [unknown — ask <who>]> · **Status:** draft · **Target:** <quarter, or "unscheduled">

## Problem
Who (and roughly how many), what it costs them and us, the evidence with its source, and why now.
Two to five sentences with numbers or quotes.

## Outcome and hypothesis
We believe that <capability> for <who> will <outcome>.
We will know it worked when <measure> moves from <baseline> to <target> by <when>.
Riskiest assumption: <the belief that, if false, sinks the bet> — feature 1 tests it.

## Success metrics
- <leading metric> — baseline, target, how it is measured
- <lagging metric> — baseline, target, how it is measured
- Guardrail: <what must not get worse> — stays within <bound>
- If the target is missed by <date>: <continue, change course or stop>, decided by <owner>

## Scope
### In
### Non-goals
What this epic deliberately does not do, so nobody assumes it.

## Features
1. <Feature> — what the user can do, which metric it moves — size S/M/L
2. ...

## Dependencies and risks
## Open decisions
| # | Question | Options | Recommendation | Owner | Due |
## Assumptions
```

**Feature**

```markdown
## Feature: <capability, as a verb phrase>
**Epic:** <name> · **Size:** S/M/L · **Moves:** <metric>

### Description
What the user can do after this ships, in two to four sentences. Not how it is built.

### Stories
Ordered. In the epic's first feature, the first story is the walking skeleton: the thinnest
end-to-end path through the real system, deployed. In later features, the first story is the
simplest complete slice. If the skeleton needs pipeline or environment work, make that its own
enabler story.

### Non-functional requirements
Only the ones that bind this feature, each with a number and a condition. An NFR one story can
fail goes in that story's criteria; list here only constraints that span several stories, each
naming the story or test that checks it.

### Out of scope
### Dependencies and risks
```

**Story** (an agent-ready story adds the block shown in `references/templates.md`: allowed paths,
capabilities, evidence of done, decisions with a Value and Decided by, defaults when ambiguous,
risk tier, Ready)

```markdown
### Story: <verb phrase>
As a <specific user or role>, I want <capability> so that <benefit that ties to the metric>.

**Acceptance criteria**
- Given <context>, when <action>, then <observable result>.
- Given <edge or failure case>, when <action>, then <observable result>.

**Notes:** only when the implementer needs something (data, API shape, UI state) — not the design.
**Size (proposed):** S/M/L · **Depends on:** <story or nothing>
```

## Rules of thumb, and why they hold

- **Title the outcome, not the solution.** "Customers answer 'where is my order' themselves", not
  "Order tracking microservice". The solution may change; the outcome is the reason the work exists.
- **Numbers, not adjectives.** "Faster", "better", "improved" become "p95 under 300 ms", "first-week
  retention from 40% to 50%". A measure nobody can take is a wish, and you cannot tell when to stop.
- **Say how the baseline will be obtained** when it is unknown. "Baseline: unknown — instrument
  before feature 1" is honest; a made-up baseline is a lie that outlives the epic.
- **Every feature releasable without the ones after it; every story a vertical slice.** A feature
  may build on earlier features (say which), never on later ones, so the epic can stop after any
  feature with value delivered. UI to API to data in one story beats three layered stories, because
  the first delivers something and the layered ones deliver nothing until the last lands.
- **Acceptance criteria are examples a tester can run.** Each has one context, one action and one
  observable result, with concrete values ("a 2 MB file", "the 101st request", "a link issued 31
  days ago") and both sides of any limit. Include at least one unhappy path; to find it, ask:
  invalid input? not allowed? at the limit? done twice? dependency down? nothing there yet? Say what
  the user or caller sees, never how it is built: "then the response is 429 with `Retry-After`" is
  testable by anyone; "then the counter is stored in Redis" is a design choice nobody will test.
  Observable means observable by the story's user: a caller sees a status, headers and a body; an
  operator sees a metric or a log line — say which user the story is for. Storage internals (keys,
  rows, cache entries, what is or is not written) are never the observed thing, even when the
  behaviour is about storage; write what the caller or operator sees instead. Name the value even
  when it is unchanged: "still 401", not "as today". When one rule covers many inputs, write one
  criterion with an examples table (input | expected), not ten near-duplicates.
- **INVEST** — independent, negotiable, valuable, estimable, small, testable. A story failing two of
  these is usually two stories, or a task hiding as a story.
- **Non-goals are mandatory.** Scope grows in the gaps that people fill differently.
- **Open decisions stay visible**, with an owner and a date. A question hidden inside a story stalls
  the sprint it lands in; a question in a table gets answered.
- **Do not invent stakeholders, baselines or metrics.** Mark them `[unknown — ask <who>]`. Readers
  trust the parts you did know because you were honest about the parts you did not.
- **Technical work states a reason a product owner can weigh:** a measurable benefit (incident rate,
  build time, cost, lead time) or a cost of delay with its date (end of life, vulnerability, audit
  finding, contract). Its users may be engineers or operators; say who. If you can name neither, ask
  before writing the item. Never invent a number to justify it.
- **Not every epic is a product bet.** Mandated work (compliance, security, contract, end of life)
  states the obligation and the deadline instead of "We believe", and measures completion or risk
  removed. Discovery work makes the hypothesis the question and starts with a spike.
- **Sizes are proposals; dates live in the target quarter only.** Propose S/M/L or the team's points
  and mark it proposed; the team re-estimates. A date inside a story is a promise the story cannot
  keep.

## Splitting a story that is too big

If nobody can estimate the story, spike first; otherwise pick the first pattern that applies and
split along it. `references/best-practices.md` §6 has the longer list with examples.

- **Workflow steps** — ship the middle of the flow first, add the ends later.
- **Business rules** — one rule per story (the common case, then each exception).
- **Happy / unhappy paths** — the first story carries the success path plus the failure the user is
  most likely to hit; later stories add the other failure modes.
- **Data variations** — one input type or source at a time.
- **Interface variations** — one channel (API, UI, CLI) first.
- **Defer performance and polish** — make it work, then fast, then pretty, but only when no NFR
  binds at release; never defer security, accessibility or data safety.
- **Spike** — when nobody can estimate, a time-boxed story whose output is knowledge, not code.

## Reviewing an existing epic or feature

1. Score it against the checklist below; name the three biggest problems first — usually a
   solution-first title, no measure, or stories that are tasks.
2. Challenge the bet: could the hypothesis lose? Is the target plausible against the baseline and
   the time? What would game the metric, and does a guardrail catch it? List what the author must
   answer, each with who to ask; do not answer it for them.
3. When the stories are for agents, run `lint_epic.py --agent` and name first every story whose
   decisions lack values, with who should answer each; it cannot be `Ready: yes`. Put all of those
   questions to the author in one batch, each with a recommendation.
4. Rewrite it in the shapes above, to a new file next to the original unless asked to replace it.
   Keep every fact the original had; do not add facts it did not.
5. End with "What changed and why", five lines or fewer, so the author learns the pattern rather
   than just receiving a new document.

## Checklist (run before delivering)

- [ ] Title names an outcome; the hypothesis sentence exists; each metric has a baseline, a target
      and a method, or says how the baseline will be obtained; a guardrail with a bound; a rule for
      what happens on a miss
- [ ] Non-goals listed; dependencies and risks listed; open decisions have owners
- [ ] Each feature: one line of user value, the metric it moves, a size; releasable without the
      features after it
- [ ] Feature 1 is sliced; each story has role / need / benefit, two or more Given/When/Then
      criteria covering distinct behaviours, one of them a failure, and a proposed size
- [ ] Nothing says TBD or TODO without an owner; unknowns are marked, not invented
- [ ] Assumptions are written down when nobody could be asked
- [ ] Agent-ready stories: every decision has a Value and a Decided by, the Defaults block is
      present, Capabilities follow from the criteria, and Ready says `yes` only with no open decision
- [ ] The chat summary is eight lines or fewer; the file carries the rest
- [ ] lint reports 0 errors; any remaining warnings are explained in the summary

## References

- `references/templates.md` — read before writing a full file; the lint keys on these headings
- `references/best-practices.md` — read only to justify a rule, map another vocabulary, or choose
  between two shapes; read the numbered section you need
- `references/examples.md` — read when unsure what good looks like, or before a rewrite
- `references/exports.md` — read when the output goes into Jira, Linear, Azure DevOps or GitHub
- `references/agent-ready.md` — read when coding agents will carry out the stories
