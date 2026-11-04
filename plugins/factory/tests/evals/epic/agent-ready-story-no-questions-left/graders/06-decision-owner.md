---
type: llm
focus: trace
weight: 1
---

The story carries a "**Decision owner:** @maria" line, placed before the decisions table, naming the person the prompt gives. The owner is not invented and not left as "unknown" or a placeholder. The story file is at docs/stories/export-csv.md, written without the run being denied a write, and `lint_epic.py --agent` does not report a missing owner.
