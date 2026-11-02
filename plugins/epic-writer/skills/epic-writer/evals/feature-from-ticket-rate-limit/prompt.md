---
tags: [skill-creator]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Here's the ticket we got: "Per-API-key rate limiting. Clients identified by API key must be limited to 100 requests per minute. Requests over the limit get HTTP 429. The window resets every minute. Limits are per key, not global. Every response carries X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset headers."

Write this up as a feature with stories and acceptance criteria that both our QA engineer and our coding agent can work from. We use Redis already. Put it in docs/features/rate-limiting.md.
