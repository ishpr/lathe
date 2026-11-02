# Templates with guidance

Copy the shape, delete the guidance in *italics*, keep the headings (the lint script and readers
rely on them). Markdown is the source of truth; `exports.md` maps these fields to trackers.

## Epic

```markdown
# Epic: <outcome in plain words>
*Name the change for the user, not the solution: "Customers answer 'where is my order' themselves",
not "Order tracking service".*

**Owner:** <name> · **Status:** draft | ready | in progress | done · **Target:** <quarter or "unscheduled">
*Owner is the person who can say yes or no to scope. Target is a quarter; dates belong to plans.*

## Problem
*Who (and roughly how many), what it costs them and us, the evidence with its source, and why now.
Numbers, quotes, ticket counts. Two to five sentences. If there is no evidence, say so — "we have
not measured this; anecdotes from support" — and add obtaining it to the first feature.*

## Outcome and hypothesis
We believe that <capability> for <who> will <outcome>.
We will know it worked when <measure> moves from <baseline> to <target> by <when>.
Riskiest assumption: <the belief that, if false, sinks the bet> — feature 1 tests it.
*One bet, written so it can lose. If you cannot fill the second sentence, the epic is not ready
(mandated or discovery work: state the obligation and its deadline, or the question, instead).*

## Success metrics
- <leading metric> — baseline <value or "unknown — instrument in feature 1">, target <value>, measured by <method>
- <lagging metric> — baseline, target, method
- Guardrail: <what must not get worse> — stays within <bound>
- If the target is missed by <date>: <continue, change course or stop>, decided by <owner>
*Leading moves early and predicts; lagging is the real outcome; the guardrail stops you winning by
cheating; the miss rule is what makes the bet able to lose. Three to five lines.*

## Scope
### In
- <capability area>
### Non-goals
- <what this epic deliberately does not do, and where it goes instead if anywhere>
*Non-goals are mandatory. Every gap here will be filled by a different assumption on each side.*

## Features
1. **<Feature name>** — <what the user can do> — moves <metric> — size S/M/L
2. ...
*Ordered by learning value and dependency, not by stakeholder volume. The first should prove the
hypothesis cheaply. Three to seven features; more is a theme, fewer is a feature.*

## Dependencies and risks
- Depends on: <team, system, decision, vendor> — <what happens if late>
- Risk: <what could make the hypothesis false or the work fail> — mitigation: <one line>

## Open decisions
| # | Question | Options | Recommendation | Owner | Due |
|---|----------|---------|----------------|-------|-----|
| 1 | | | | | |
*Anything that would otherwise be decided silently by whoever picks up the story.*

## Assumptions
- <what you assumed because nobody could be asked, and who should confirm it>
```

## Feature

*Start with `# Feature:` when the feature is its own file; use `## Feature:` inside an epic file.*

```markdown
## Feature: <capability, as a verb phrase>
**Epic:** <epic name> · **Size:** S/M/L · **Moves:** <the epic metric this feature moves>

### Description
*What the user can do after this ships, in two to four sentences. Not how it is built.*

### Stories
*Ordered. In the epic's first feature, the first story is the walking skeleton: the thinnest
end-to-end path through the real system, deployed. In later features, the first story is the
simplest complete slice. If the skeleton needs pipeline or environment work, make that its own
enabler story. Slice only the features that will be scheduled next; later features and stories get
one line each: `- Later: <title> — to be sliced when scheduled`. Never a `### Story:` heading for
a placeholder; the lint treats every Story heading as a full story.*

### Non-functional requirements
- <attribute>: <number and condition> — checked by <story or test> — *e.g. "p95 under 300 ms at 50 req/s — load test before release"*
*Only the ones that bind this feature, each with a number and a condition. An NFR one story can
fail goes in that story's criteria; list here only constraints that span several stories, each
naming the story or test that checks it. Cross-cutting ones (security baseline, accessibility
level, data retention) go once in the epic or the team's Definition of Done.*

### Out of scope
- <explicitly not in this feature>

### Dependencies and risks
- <one line each>
```

## Story

```markdown
### Story: <verb phrase, what the user can do>
As a <specific user or role — not "user">, I want <capability> so that <benefit that ties to the metric>.

**Acceptance criteria**
- Given <context>, when <action>, then <observable result>.
- Given <context>, when <action>, then <observable result>.
- Given <edge or failure case>, when <action>, then <observable result>.
*Two to five. At least one unhappy path. Observable results only: a status, a message, a row, a
state the user can see. Real values where they matter.*

**Notes:** <only when the implementer needs something — data shape, API contract, UI state, links to designs>
**Size (proposed):** <points or S/M/L> · **Depends on:** <story or nothing>
```
*Sizes are proposals; the team re-estimates. When agents will carry out the story, add the fields
from `agent-ready.md` after the size line and before the next story:*

```markdown
**Allowed paths:** `src/<module>/**`, `tests/<module>/**`
**Capabilities:**
- paths: write `src/<module>/**`, `tests/<module>/**`; read the repo
- commands: `<the test command the criteria imply>`
- egress: none | `localhost:<port>` (when a criterion calls a running server)
- data: none | <tables, files or fixtures the criteria read or write>
**Evidence of done:**
- <each acceptance criterion as a test name, failing before and passing after>
- <the cheapest real check per criterion>
**Decisions a human makes first:**
| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 1 | <choice the agent would otherwise make silently> | <a> · <b> | <a> | <the answer> | <who, date> |
**Defaults when ambiguous:**
- Make the smallest change that satisfies the acceptance criteria.
- Follow the nearest existing pattern in the repo for naming, layout and error handling.
- Prefer the option the decisions above imply over any other.
- When none of these applies, choose the reversible option.
- <story-specific default: a verb and an object>
**Risk tier:** low | medium | high
**Ready:** yes | blocked on #<n>
```
*Ready is `yes` only when every decision has a value; otherwise name the open ones. Ask all the
decisions in one batch when the story is created. Capabilities come from the criteria: a criterion
that curls a server implies egress to localhost, a test command implies that command.*

## Enabler (technical work with a stated benefit)

```markdown
## Feature: <the capability the enabler unlocks, or the risk it removes>
**Epic:** <epic> · **Size:** · **Moves:** <a measurable benefit: incident rate, build time, cost, lead time> — or **Deadline:** <date and consequence: end of life, vulnerability, audit finding, contract>

### Description
*Why this is worth doing in the users' or the business's terms. "Replace the hand-rolled migrations
with Alembic so schema changes ship in one PR instead of three and stop breaking the test suite"
is a benefit; "refactor the data layer" is not. A platform epic's "who" is its internal consumers.
If you can name neither a benefit nor a deadline, ask before writing the item; never invent a
number to justify it.*

### Stories
*Same shape as any story. The "As a" is often a developer or operator; that is fine when the
benefit is real: "As an on-call engineer, I want the migration to run as a release step so that a
failed deploy rolls back cleanly."*
```

## Spike (time-boxed investigation)

```markdown
### Spike: <the question to answer>
**Time box:** <hours or days> · **Owner:** <name>

**Question:** <what we need to know to size or decide>
**Done when:** <a written answer with evidence: a prototype, a benchmark, a vendor reply>
**Decision it feeds:** <open decision # in the epic>
```
*A spike's output is knowledge, never production code. If the spike "accidentally" builds the
feature, the story was not too uncertain to estimate; it was just unscheduled.*
