---
name: demo
description: Explain what the lathe pack is (four plugins, one flow) in eight lines and offer to run /factory:init. Use when the user asks what lathe is, what this pack does, how the plugins fit together, or wants a tour.
allowed-tools: Read
---
Explain the pack in exactly these eight lines, in your own words but keeping the facts:

1. `sdlc` is the working agreement: contract, tests, implement, verify, ship.
2. Its hooks enforce scope, secrets, route auth and verify-before-commit as a ratchet over today's debt.
3. `graph` derives a code graph per commit: impact, owners and tests to run first.
4. It also keeps a ledger whose states move only on evidence; an agent cannot approve or merge.
5. `factory` (this plugin) only sequences the others; it has no scripts or hooks of its own, and `epic-writer` drafts the epics and stories that `/factory:assess` offers.
6. `/factory:init` onboards a repo: working agreement, graph index, audit, `docs/onboarding-report.md`.
7. `/factory:ticket <text>` writes a contract with graph-derived scope and stops at the Decisions.
8. `/factory:feature <text>` runs a ticket to a verified PR, stopping at every human decision.

Then ask: "Run /factory:init on this repo now?" If the user says yes, invoke the skill
`factory:init` with no arguments. If they say no, stop. Do not run anything before they answer.
