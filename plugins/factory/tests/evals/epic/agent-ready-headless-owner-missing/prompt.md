---
tags: [skill-creator]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash, Agent]
---

Unattended run: no human will answer and nobody can be asked anything. Write an agent-ready story for this ticket: "Let account admins download their audit log as CSV from GET /v1/audit-log/export. Rows are newest first. Non-admins get 403." Our API is FastAPI in app/, tests in tests/. Put the story in docs/stories/export-csv.md.
