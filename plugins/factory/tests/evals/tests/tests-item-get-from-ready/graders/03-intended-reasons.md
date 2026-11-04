---
type: regex
target:
  source: file
  path: ".claude/work/item-get/failing_first.json"
match: contains
pattern: '"reason": "(not_implemented|assertion|status_mismatch)"'
---

The record holds tests failing for an intended reason.
