# factory

A thin umbrella plugin. It has no scripts, hooks or MCP servers of its own: it sequences sibling
plugins from the same marketplace so a brownfield repository goes from nothing to a contract in a
few commands, and assesses maturity and security on request.

Requires `sdlc` (working agreement, ratchet, audit, ticket and feature flow) and `graph` (code
graph, impact analysis, evidence ledger). `epic-writer` is optional and is used to draft the epic
that `/factory:assess` offers.

```
/plugin marketplace add ishpr/lathe
/plugin install factory@lathe
/plugin install sdlc@lathe
/plugin install graph@lathe
/plugin install epic-writer@lathe
```

| Command | What it does |
| --- | --- |
| `/factory:init [--stack ...] [--marketplace-repo owner/repo]` | `sdlc:init`, then `graph:index`, then `sdlc:audit`; writes `docs/onboarding-report.md` |
| `/factory:ticket <ticket text>` | graph impact for the paths the ticket names, then `sdlc:ticket`; stops at the Decisions table |
| `/factory:feature <ticket text>` | `sdlc:feature` with graph impact at the start and PR evidence at the end (human-invoked only) |
| `/factory:assess [--stack ...] [--offline] [--run-tools]` | `sdlc:maturity`, then `sdlc:security`, both in report-only mode (each gets only the flags it knows); one combined summary (level, three moves, security characteristics, top three findings with blast radius), then the same A/B/C offer as `/sdlc:security` and an offer to execute; runs nothing you did not choose |
| `/factory:demo` | eight-line explanation of the plugins; offers `/factory:init` |

Every step degrades: if a sibling command fails, the skill continues and records the failure.
Humans resolve Decisions and run `/sdlc:ship`; nothing here approves or merges.

The plugin passes `claude plugin validate --strict`; the skills have not yet had a long live
session, so expect rough edges.
