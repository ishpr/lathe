---
name: migrate
description: Render the SQL of this branch's new schema migrations with the project's configured migrate command, for a human to review, and stop. Never applies, downgrades or stamps a migration.
argument-hint: "[from-revision]"
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git diff --name-only --diff-filter=A *)
  - Bash(alembic upgrade * --sql)
  - Bash(alembic check)
---
Render, review, stop (REC-37). The human applies the migration; this skill never does.

1. Find the command. `commands.migrate_sql` in `.claude/sdlc.json` renders SQL offline, with
   `{from}` replaced by the starting revision; `commands.migrate_check` (optional) compares models
   with the schema. Neither set and the repo has `alembic.ini` → use `alembic upgrade {from}:head --sql`
   and `alembic check`. Neither set and no `alembic.ini` → stop: "No migrate command configured. Add
   `commands.migrate_sql` to .claude/sdlc.json; a project without a migration baseline needs one
   first, as its own tier-high ticket."
2. Find what this branch adds: `git diff --name-only --diff-filter=A origin/<base>...HEAD` over the
   migrations directory (`alembic/versions/` for Alembic). None → say "no new migrations" and stop.
   `{from}` is the argument if given, else the parent revision of the oldest new migration (its
   `down_revision` for Alembic). More than one head, or a new file whose parent is not on the base
   branch → stop and report it; that is a merge a human resolves.
3. Render: run `commands.migrate_sql` (or the Alembic default) and show the SQL in full. Do not
   write it into the repository.
4. Review the SQL and say, per statement: additive (new table, nullable column, new index) or not.
   Anything that drops, renames, narrows a type, adds NOT NULL without a default, or rewrites a large
   table is flagged: it needs the expand → migrate → contract sequence (add the new shape, backfill,
   switch reads, drop the old one in a later release) and a rollback note in the contract.
   Long locks: an index without `CONCURRENTLY` on Postgres, a table rewrite. Data loss: say so plainly.
5. If `commands.migrate_check` (or `alembic check`) is available and the dev database is up, run it
   and report whether the models and migrations agree. Database down → say it was not checked.
6. Stop. Say: "Rendered SQL above. A human reviews it and applies it; nothing was run against a
   database." Never run the migration for real, `downgrade` or `stamp` (the bash guard blocks the
   last two), or edit an applied migration.
