---
type: llm
focus: trace
weight: 1
---

The story file has a "Decisions a human makes first" table with Value and Decided by columns, and every decision row (for example the page or stream size, the column set, the timeout behaviour) has a non-empty Value and a named Decided by. Values are concrete choices, not "tbd", "open" or blank. If the run asked questions, it asked them once in one batch with a recommendation each; if nobody could answer, the recommendations are the values and are listed under Assumptions.
