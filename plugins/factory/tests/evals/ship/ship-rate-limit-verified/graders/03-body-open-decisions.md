---
type: regex
target:
  source: file
  path: ".eval/gh/pr-body.md"
flags: i
pattern: 'Open decisions:\s*(\d+|n/a)'
---

The PR body carries the open-decisions line.
