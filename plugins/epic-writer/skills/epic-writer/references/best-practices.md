# The practices behind the rules

Read this when you need to justify a rule, adapt it to a team that uses different words, or
decide between two reasonable shapes. Each section says where the practice comes from, what it is
for, and the failure it prevents. Dates and attributions are given so you can look them up; treat
them as pointers, not scripture.

## Contents

1. Outcomes over outputs
2. The epic hypothesis
3. User stories and the three Cs
4. INVEST
5. Acceptance criteria: Given/When/Then and specification by example
6. Splitting stories
7. Story mapping and the walking skeleton
8. Sizing and the shape of a healthy backlog
9. Definition of Ready, Definition of Done
10. Prioritisation: WSJF, RICE, MoSCoW
11. Non-functional requirements
12. Metrics: leading, lagging, guardrails
13. Non-goals, dependencies, risks, decisions
14. Working agreements: refinement, three amigos, example mapping
15. Failure modes to recognise on sight

## 1. Outcomes over outputs

The idea that a backlog item should name the change in the world rather than the thing built runs
through modern product practice: Impact Mapping (Gojko Adzic, 2012) draws goal → actor → impact →
deliverable, and OKRs (Andy Grove at Intel, popularised by John Doerr's *Measure What Matters*,
2018) separate the objective from the key results that prove it. The practical rule: an epic's title
and hypothesis talk about users and measures; features and stories may talk about deliverables.
Failure prevented: a team ships the thing in the title, nothing changes, and nobody can say whether
the epic failed or the idea did.

## 2. The epic hypothesis

The sentence this skill uses is adapted from Gothelf and Seiden's Lean UX hypothesis statement
(2013; Gothelf's talks use "We believe that [doing this] for [these people] will achieve [this
outcome]", and the book adds the signal that tells you whether you were right —
https://www.oreilly.com/library/view/lean-ux-3rd/9781098116293/ch10.html). SAFe's "epic hypothesis
statement" is a longer form with the same intent (for / who / the / is a / that / unlike / our
solution, plus leading indicators and NFRs).
Either works; the point is that an epic is a bet, written so it can lose. Failure prevented:
epics that are really commitments dressed as work, which cannot be stopped when the evidence
turns.

## 3. User stories and the three Cs

The "As a <role>, I want <capability>, so that <benefit>" template comes from the Connextra team
(Rachel Davies and colleagues, early 2000s —
https://www.mountaingoatsoftware.com/blog/advantages-of-the-as-a-user-i-want-user-story-template)
and spread through Mike Cohn's *User Stories Applied* (2004). Ron Jeffries' three Cs
(2001) say what a story is: a **Card** (a placeholder small enough to fit on one), a
**Conversation** (the details are agreed by talking, not by writing a specification), and a
**Confirmation** (the acceptance criteria that say when it is done). The template is a prompt for
the conversation, not a form to fill in: a story whose role is "user" and whose benefit is "so that
I can do X" has skipped the thinking. Failure prevented: stories that are tasks ("add a column"),
which cannot be valued, and stories whose benefit nobody can state, which should not be built.

## 4. INVEST

Bill Wake's checklist (2003): **I**ndependent (can be scheduled in any order), **N**egotiable
(the details are open until the conversation), **V**aluable (to a user or a stakeholder, not just a
layer), **E**stimable (the team can size it), **S**mall (fits in a sprint with room to spare),
**T**estable (there is a way to confirm it). Use it as a diagnostic, not a gate: a story failing
"Independent" and "Small" usually wants splitting (§6); one failing "Valuable" is a task that
belongs inside another story; one failing "Testable" has no acceptance criteria yet, or the
criteria describe implementation.

## 5. Acceptance criteria: Given/When/Then and specification by example

Given/When/Then comes from Behaviour-Driven Development (Dan North and Chris Matts; North's
"Introducing BDD", Better Software, March 2006 — https://martinfowler.com/bliki/GivenWhenThen.html).
Cucumber (Aslak Hellesøy, first released 2008) later standardised it as the Gherkin syntax
(https://www.infoq.com/news/2018/04/cucumber-bdd-ten-years/). *Given* a context, *when* an action
happens, *then* an observable outcome follows. Gojko Adzic's
*Specification by Example* (2011) adds the discipline: criteria are concrete examples, not
restatements of the title, and the examples become the tests. Good criteria:

- describe what an observer sees (a status code, a message, a row, a state), not what the code does;
- include the unhappy paths: the invalid input, the missing permission, the limit exceeded, the
  dependency down — the happy path is the one everybody tests anyway;
- use real values when they matter ("the 101st request", "a 2 MB file"), because "a large file" is
  an argument waiting to happen;
- are few. Five criteria that matter beat fifteen that restate the UI.

"Then the data is stored in Redis" is not a criterion, it is a design decision; the criterion is
"then the second request within the minute is refused". Failure prevented: tests that pass while
the feature is wrong, and arguments at demo time about what "done" meant.

## 6. Splitting stories

Richard Lawrence's nine splitting patterns (Humanizing Work; originally a 2009 article, now the
story-splitting flowchart —
https://www.humanizingwork.com/the-humanizing-work-guide-to-splitting-user-stories/) and Mike
Cohn's SPIDR (Spike, Paths, Interfaces, Data, Rules —
https://www.mountaingoatsoftware.com/blog/five-simple-but-powerful-ways-to-split-user-stories)
cover almost every case. The table blends them; "happy / unhappy paths" is a common extension
(close to SPIDR's Paths), not one of Lawrence's nine. Apply the first that fits:

| Pattern | Split along | Example |
|---|---|---|
| Workflow steps | the steps of the flow; ship the core step first | checkout: pay first, then address validation, then promo codes |
| Business rules | one rule per story | shipping cost: domestic first, then international, then free-shipping threshold |
| Happy / unhappy paths | success first, then each failure mode | login works; then wrong password; then locked account |
| Data variations | one input type or source | import CSV; then XLSX; then Google Sheets |
| Interface variations | one channel | API first; then the UI; then the CLI |
| Major effort | the expensive part as its own story once the simple part proves the shape | one payment provider; then the abstraction; then the second provider |
| Simple / complex | the plain case, then the elaborate ones | fixed-rate tax; then per-region tax |
| Defer performance / polish | make it work; then fast; then pretty | search returns results; then under 200 ms; then with highlighting |
| Operations (CRUD) | create first, then read, update, delete as the product needs them | create an API key; list keys; revoke a key |
| Spike | a time-boxed investigation whose output is knowledge | "find out whether the vendor API supports webhooks: 1 day" |

A split is good when each piece is still a vertical slice with its own acceptance criteria.
Splitting into "front end" and "back end" is not a split, it is layering: nothing is demonstrable
until both are done.

## 7. Story mapping and the walking skeleton

Jeff Patton's story mapping (*User Story Mapping*, 2014) lays the user's activities left to right
(the backbone) and the stories for each activity top to bottom by priority; a release is a
horizontal line through the map. Alistair Cockburn's walking skeleton (*Crystal Clear*, 2004) is
"a tiny implementation of the system that performs a small end-to-end function", built first to
prove the architecture (https://alistair.cockburn.us/walking-skeleton/; Adzic's note on its
misuse: https://gojko.net/2014/06/09/forget-the-walking-skeleton-put-it-on-crutches/). Story
mapping borrows it for the first slice of the map: the thinnest end-to-end path that works,
however crude. In this skill: the first story of the first feature is the walking skeleton, and
later features are columns on the map. Failure prevented: a
"minimum viable product" that is actually all the features with less polish.

## 8. Sizing and the shape of a healthy backlog

Relative sizing (story points, t-shirt sizes) is a conversation tool, not a time estimate; its
value is the disagreement it surfaces. Rules of thumb that hold across teams, and the bands this
skill uses: an epic fits in a quarter or less (longer: an initiative or theme); a feature in up to
about two sprints; a story in one to three days (Wake's "Small", made concrete). A backlog is healthy when the next two sprints are sliced and estimated,
the next quarter is features with sizes, and beyond that is epics with hypotheses — detail decays
with distance on purpose. Failure prevented: four hundred stories, most of them wrong by the time
they are reached.

## 9. Definition of Ready, Definition of Done

The Definition of Done is part of the Scrum Guide (Schwaber and Sutherland): the quality
commitment every increment meets (tests pass, reviewed, documented, deployed to an environment —
whatever the team agreed). A Definition of Ready is a widespread practice but not in the Guide, and
some practitioners warn it becomes a stage gate that pushes thinking back into a waterfall; use it
as a checklist for the refinement conversation (criteria written, dependencies known, sized,
questions answered or owned), not as a wall. This skill's checklist is a Definition of Ready for
documents; the team's Definition of Done belongs in each story's expectation of "done".

## 10. Prioritisation: WSJF, RICE, MoSCoW

- **WSJF** (weighted shortest job first; Don Reinertsen, *The Principles of Product Development
  Flow*, 2009: cost of delay ÷ duration). SAFe adopted it as cost of delay ÷ job size, with cost
  of delay = business value + time criticality + risk reduction or opportunity enablement
  (https://framework.scaledagile.com/wsjf). Favours small, urgent, valuable work.
- **RICE** (Intercom, 2016): Reach × Impact × Confidence ÷ Effort. Good when reach is measurable.
- **MoSCoW** (Dai Clegg, 1994, from DSDM): must / should / could / won't. Good for scoping a
  release; weak for ordering within a release because everything becomes a must.

Put the chosen score on features, not stories; order stories by dependency and learning value.

## 11. Non-functional requirements

Quality attributes bind a feature as much as behaviour does: performance (p95 latency at a stated
load), reliability (error budget, failure modes), security (authentication, authorisation, data
handling), accessibility (WCAG level), observability (what is logged and measured), compliance.
ISO/IEC 25010 is the standard taxonomy if the team wants one (2023 edition: functional
suitability, performance efficiency, compatibility, interaction capability, reliability, security,
maintainability, flexibility, safety — https://www.iso.org/standard/78176.html; teams on the 2011
edition still say usability and portability). Write only the NFRs that bind this
feature, as criteria with numbers, and prefer putting them in a story's acceptance criteria over a
separate list nobody tests.

## 12. Metrics: leading, lagging, guardrails

A lagging metric is the outcome (retention, revenue, support tickets); it moves late and is noisy.
A leading metric predicts it and moves fast (activation within a day, time to first success,
self-service completions). A guardrail is what must not get worse while you move the others
(latency, error rate, cost, churn). Google's HEART framework (Rodden, Hutchinson and Fu, 2010) —
Happiness, Engagement, Adoption, Retention, Task success — is a useful menu for user-facing work.
Every metric needs: a baseline (or the plan to obtain one), a target, and how it is measured.
Failure prevented: an epic that "succeeds" by a metric nobody can produce at the retrospective.

## 13. Non-goals, dependencies, risks, decisions

Non-goals are the cheapest scope control that exists: a line like "not doing SSO in this epic"
saves a week of assumed work. Dependencies and risks follow the RAID habit (risks, assumptions,
issues, dependencies) without the ceremony: one line each, with an owner where it matters. Open
decisions are the questions that will otherwise be settled silently by whoever picks up the story;
a table with options, a recommendation, an owner and a date makes them visible and cheap to close.

## 14. Working agreements: refinement, three amigos, example mapping

Backlog refinement is a recurring conversation, not a document review. "Three amigos" (George
Dinwiddie's term, around 2009: a product person, a developer, a tester —
https://cucumber.io/blog/bdd/example-mapping-introduction/) look at a story together before it is
scheduled. Example mapping
(Matt Wynne, 2015) runs that conversation with index cards: the story, its rules, examples of each
rule, and questions nobody can answer — the questions become open decisions, the examples become
acceptance criteria. Twenty-five minutes per story is typical; a story that needs longer is too
big or too unclear to schedule.

## 15. Failure modes to recognise on sight

- **Solution-first epic**: the title is a technology or a component. Ask what changes for whom.
- **Adjective metrics**: "faster", "better", "more reliable". Ask for the number and the method.
- **Tasks as stories**: "add column", "write migration". Fold into the story that needs them.
- **Criteria that restate the title**: "Given I am on the page, when I click Save, then it saves."
  Ask what could go wrong and what the user sees when it does.
- **Layered stories**: "backend for X", "frontend for X". Re-slice vertically.
- **Everything is a must**: no non-goals, MoSCoW with no "won't". Ask what you would cut first.
- **Invented facts**: a baseline nobody measured, a stakeholder nobody named. Mark unknown.
- **Decisions hidden in stories**: "use provider A or B" inside the notes. Lift to the table.
- **The MVP that is the whole product**: a walking skeleton is a path, not a reduced version of
  every feature.
