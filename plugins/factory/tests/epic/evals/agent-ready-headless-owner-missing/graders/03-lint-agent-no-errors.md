---
type: llm
focus: trace
weight: 1
---

Running the skill's scripts/lint_epic.py with --agent on docs/stories/export-csv.md reports no error about the decision owner (a warning that the owner is unknown is expected) and no error about a missing field. The only errors it may report are for decisions marked "assumed, confirm", which the run listed under Assumptions and did not mark as confirmed.
