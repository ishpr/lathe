# .claude/work

Where a piece of work keeps its paperwork.

- `<slug>/contract.md` — written by `/sdlc:ticket`, appended by `implement` (Decisions)
  and `verify` (Review notes, Follow-ups, Evidence). A working file: `/sdlc:ship` deletes it
  before the final commit and carries the evidence in the PR body instead.
- `current.json` — the active contract: slug, allowed_paths, tier, require_tests. The edit guard
  and the stop hook read it. A working file, never committed: `/sdlc:ship` deletes it (and the
  slug's directory) before the final commit, and CI fails if it is on a protected branch.
- `last_verify.json` — the verify stamp (tree fingerprint). Written by the gate, read by the commit
  guard. Ignored by git.
