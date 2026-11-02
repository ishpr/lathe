---
type: llm
focus: trace
weight: 1
---

The acceptance criteria, in Given/When/Then form, cover all four of: the request over the limit (e.g. the 101st in a minute) returning 429 with the X-RateLimit-* headers and Remaining 0; the window resetting on the next minute; one key's exhaustion not affecting another key; and a request with a missing or invalid key being refused (401) rather than rate-limited.
