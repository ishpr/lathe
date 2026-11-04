---
type: regex
target:
  source: file
  path: ".claude/work/rate-limit/failing_first.json"
match: not_contains
pattern: '"reason": "(import_error|attribute_error|name_error|syntax_error|collection_error)"'
---

No test in the failing-first record fails for a wrong reason (a broken test).
