---
type: regex
target:
  source: file
  path: ".claude/work/last_verify.json"
pattern: '"acceptance":\s*\{[^}]*"failed":\s*0'
---

The stamp's acceptance has no failure.
