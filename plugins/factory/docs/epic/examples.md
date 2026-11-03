# Worked example

A generic e-commerce support problem, taken from a vague ask to a sliced first feature, then a bad
epic rewritten. The domain does not matter; the moves do.

## The ask

> "Support is drowning in 'where is my order' emails. Leadership wants better self-service by
> end of Q1."

## The epic

```markdown
# Epic: Customers answer "where is my order" themselves
**Owner:** [unknown — ask Head of Support] · **Status:** draft · **Target:** Q1

## Problem
Order-status questions are 38% of inbound support email (support dashboard, last 90 days: 4,100 of
10,800 tickets). Each costs about 6 minutes of agent time and the customer waits a median 9 hours
for an answer. The order status exists in the OMS; customers cannot see it after the confirmation
email, and the email has no link.

## Outcome and hypothesis
We believe that showing customers their live order status, and telling them when it changes, will
remove most order-status tickets.
We will know it worked when order-status tickets fall from 38% to under 15% of inbound email by
the end of Q1, without the CSAT score for shipping questions dropping.
Riskiest assumption: customers who can see the status stop writing in, rather than writing anyway —
features 1 and 3 test it.

## Success metrics
- Order-status tickets as a share of inbound email — baseline 38%, target < 15%, measured weekly from the support dashboard's category field
- Self-service status views per shipped order — baseline 0 (no page exists), target > 0.6, measured from page analytics
- Guardrail: shipping-question CSAT — stays ≥ 4.2 / 5 (current 4.3)
- Guardrail: notification complaints / unsubscribes — under 0.5% of notified customers
- If the share is still above 25% four weeks after feature 2 ships: re-plan before feature 4, decided by the Head of Support

## Scope
### In
- A status page per order, reachable from the confirmation email and the account
- Proactive status notifications on the events customers ask about (shipped, delayed, delivered)
- Measuring deflection
### Non-goals
- Returns and refunds self-service (separate epic)
- Carrier map tracking or live courier location
- Changing the OMS or the carriers' integrations
- SMS as a channel this quarter (email only; SMS is an open decision for Q2)

## Features
1. **See my order's status** — a status page with the current state and history, linked from the confirmation email — moves status views per order — size M
2. **Tell me when it changes** — email on shipped / delayed / delivered, with a link to the page — moves ticket share — size M
3. **Measure deflection** (enabler) — log every status-page view with a hashed order id, tag tickets that arrive after a view, weekly report — moves: makes the ticket share readable weekly, which is how the hypothesis is judged — size S
4. **Delays explained** — when the carrier reports a delay, show the new estimate and why — moves ticket share for the hardest cases — size M, to be sliced when scheduled

## Dependencies and risks
- Depends on: OMS status webhook (Platform team) — without it, polling every 15 min is the fallback and "delivered" arrives late
- Depends on: email provider template approval (Marketing) — two-week lead time
- Risk: carriers report "delayed" inconsistently — the delay feature may need per-carrier rules; mitigation: ship features 1-3 first, measure how many delay tickets remain
- Risk: a status page that is wrong creates more tickets than none — mitigation: show "last updated" and the carrier's own reference on every page

## Open decisions
| # | Question | Options | Recommendation | Owner | Due |
|---|----------|---------|----------------|-------|-----|
| 1 | Does the status page require login? | login · signed link in the email · both | signed link, 30-day expiry; login as fallback | Product | before feature 1 starts |
| 2 | Which events notify? | shipped only · shipped + delivered · all four | shipped + delayed + delivered; "out for delivery" later | Support lead | before feature 2 |
| 3 | SMS in Q2? | yes · no | decide after feature 2's data | Head of Support | end of Q1 |

## Assumptions
- The OMS exposes status history, not only the current state (confirm with Platform)
- The 38% figure counts tickets by category; some "where is my order" tickets are categorised as "shipping" (the real share may be higher)
```

## The first feature, sliced

```markdown
# Feature: See my order's status
**Epic:** Customers answer "where is my order" themselves · **Size:** M · **Moves:** status views per order; ticket share

### Description
A customer who has ordered can open a page that shows where their order is now (confirmed, packed,
shipped, delayed, delivered), when it changed, and the carrier's reference, without logging in,
from a link in their confirmation email.

### Stories

### Story: Open my order's status from the confirmation email
As a customer who has just ordered, I want a link in my confirmation email that opens my order's
status so that I can check it without writing to support.

**Acceptance criteria**
- Given order 1001 was marked shipped at 14:02 UTC, when I open the "Track your order" link in its confirmation email, then I see "Shipped" and "Last updated 14:02 UTC".
- Given the link was issued 31 days ago, when I open it, then I see "this link has expired" with a button to request a new one, and no order details.
- Given a link whose signature does not match, when I open it, then I see the same "Order not found" page and HTTP status as for an order number that does not exist.
- Given our OMS mirror last updated order 1001 more than 15 minutes ago, when I open a valid link, then I see the last known status, "Last updated <time>" and the text "This may be out of date".
- Given order 1001 is shipped, when a screen reader reads the page, then the status is announced as "Shipped" (text, not only a colour or an icon).

**Notes:** signed URL with order id + expiry; the page reads from our mirror of OMS status, not live from the OMS. Expiry per open decision 1.
**Size (proposed):** 3 · **Depends on:** Marketing's email template approval (two-week lead time, started now); otherwise nothing — this is the walking skeleton

### Story: See the history, not just the current state
As a customer whose order has been moving, I want to see each status change with its time so that
I can tell whether it is stuck.

**Acceptance criteria**
- Given an order with the events confirmed → packed → shipped, when I open its status page, then I see all three with timestamps, newest first.
- Given an order with only "confirmed", when I open the page, then I see one event and the text "we'll update this when it ships".
- Given an event with no timestamp from the OMS, when the page renders, then the event shows "time unknown" rather than a blank or a default date.

**Size (proposed):** 2 · **Depends on:** the story above

- Later: Reach the status page from my account — to be sliced when scheduled (logged-in customers see "Track" on each order in their order list)

### Non-functional requirements
- Status page p95 under 500 ms at 20 req/s (it will be opened from email bursts) — checked by a load test on the status page before release

### Out of scope
- Editing the order, cancelling, or contacting support from the page (link to the existing form only)

### Dependencies and risks
- Marketing approves the email template change (two-week lead time) — start it now
```

Both documents lint with 0 errors (the feature saved as its own file, which is why it starts with
`# Feature:`). `tests/epic/check_examples.sh` re-runs that check; run it after changing the examples
or the linter.

## An agent-ready feature

The same shapes, with the additions from `agent-ready.md`, for a different ticket: per-key rate
limiting that a coding agent will carry out. Every question was asked in one batch when the stories
were written, so the first story has a value for each decision and says `Ready: yes`; the second
still waits on one decision and says so. The agent never has to ask.

```markdown
# Feature: Limit each API key to 100 requests per minute
**Epic:** Keep one noisy client from degrading the API for everyone · **Size:** S · **Moves:** share of requests served within p95 under load

### Description
A client calling the API with a key can make 100 requests in a minute; the 101st is refused with a
clear status and a hint of when to retry. Every response tells the client how much of its limit is
left. One client's traffic never uses up another client's limit.

### Stories

### Story: Refuse the 101st request in a minute
As an API client developer, I want requests over my key's limit refused with a retry hint so that I can back off instead of guessing.

**Acceptance criteria**
- Given a key with 100 requests already made in the current minute, when it sends the 101st, then the response is 429 with a `Retry-After` header between 1 and 60.
- Given a key with 99 requests made this minute, when it sends the 100th, then the response is 200 and `X-RateLimit-Remaining` is 0.
- Given two keys, when key A has used 100 requests, then key B's next request is still 200.
- Given a request with no key, when it calls `/v1/runs`, then the response is 401, as before.

**Size (proposed):** 3 · **Depends on:** nothing

**Allowed paths:** `app/ratelimit/**`, `tests/ratelimit/**`
**Capabilities:**
- paths: write `app/ratelimit/**`, `tests/ratelimit/**`; read the repo
- commands: `python3 -m pytest tests/ratelimit -q`, `bash scripts/smoke_rate_limit.sh http://localhost:8000 demo-key 101`
- egress: `localhost:8000` (the smoke script calls the running server)
- data: none (tests use a fake clock and an in-memory counter)
**Evidence of done:**
- `test_101st_request_is_429`, `test_100th_request_is_200`, `test_keys_are_isolated`, `test_missing_key_is_401`, each failing before and passing after
- the smoke script output containing "429" for request 101
**Decision owner:** @maria
**Decisions a human makes first:**
| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 1 | Fixed or sliding window? | fixed · sliding | fixed | fixed | @maria, 2026-11-02 |
| 2 | Retry-After rounds to | whole seconds · fractional | whole seconds | whole seconds | @maria, 2026-11-02 |
**Defaults when ambiguous:**
- Make the smallest change that satisfies the acceptance criteria.
- Follow the nearest existing pattern in the repo for naming, layout and error handling.
- Prefer the option the decisions above imply over any other.
- When none of these applies, choose the reversible option.
- Return the existing `ApiError` shape for every refusal.
**Risk tier:** high
**Ready:** yes

### Story: Keep serving when the counter store is down
As an on-call engineer, I want a defined behaviour when the limit counter is unreachable so that an outage there does not become an outage of the API.

**Acceptance criteria**
- Given the counter store is unreachable, when a key sends a request, then the response follows decision 1 and a `ratelimit_store_down` log line is written.
- Given the store comes back, when the key sends the next request, then limits apply again from a fresh window.

**Size (proposed):** 2 · **Depends on:** the story above

**Allowed paths:** `app/ratelimit/**`, `tests/ratelimit/**`
**Capabilities:**
- paths: write `app/ratelimit/**`, `tests/ratelimit/**`; read the repo
- commands: `python3 -m pytest tests/ratelimit -q`
- egress: none
- data: none (the test stubs the store to fail)
**Evidence of done:**
- `test_store_down_follows_decision_1`, `test_limits_resume_after_recovery`, each failing before and passing after
**Decision owner:** @maria
**Decisions a human makes first:**
| # | Question | Options | Recommendation | Value | Decided by |
|---|----------|---------|----------------|-------|------------|
| 1 | When the store is down, fail open or closed? | open · closed | open, with an alert | | |
**Defaults when ambiguous:**
- Make the smallest change that satisfies the acceptance criteria.
- Follow the nearest existing pattern in the repo for naming, layout and error handling.
- Prefer the option the decisions above imply over any other.
- When none of these applies, choose the reversible option.
**Risk tier:** high
**Ready:** blocked on #1

### Out of scope
- Per-endpoint limits and limits that clients can configure
```

Both agent-ready stories lint with 0 errors under `--agent`; the second is correctly blocked.

## A bad epic, and its rewrite

**Before**

```markdown
# Epic: Build order tracking microservice
We need a new microservice that integrates with the OMS and carriers to provide tracking.
Stories:
- Set up the service repo
- Add database tables
- Build the API
- Build the UI
- Write tests
Success: better customer experience, fewer tickets.
```

What is wrong, in order of damage: the title is a solution, so there is no way to know whether a
cheaper approach (a page over the existing OMS mirror) would do; "better experience, fewer tickets"
has no number, so nobody can call it done; the stories are tasks layered by component — nothing is
demonstrable until the last one; there are no non-goals, so carrier maps and SMS will be assumed by
someone; there are no decisions, so login-or-link will be settled by whoever writes the page.

A reviewer would also challenge the bet before rewriting: can "patients are reminded" lose? What
is the no-show rate today, and who can say? What would game "fewer tickets" (closing them faster
rather than preventing them)? Those questions go to the author with a name beside each.

**After**: the epic above. What changed and why:

1. Title names the outcome; the microservice became one option under open decisions, to be made
   after feature 1 proves the shape.
2. "Fewer tickets" became 38% → under 15% with a method and a guardrail.
3. Five layered tasks became three vertical stories, the first of which ships a working page.
4. Non-goals exclude the three things most likely to be assumed.
5. The login question became decision #1 with an owner and a date.
