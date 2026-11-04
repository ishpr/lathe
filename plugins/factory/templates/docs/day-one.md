# Day one: hook messages, causes and fixes

When a guard stops you it prints "Blocked: ..." and the reason. Every block is also logged as
one JSON line in `.claude/work/blocks.jsonl` (time, hook, rule, detail), which is git-ignored.
If a block is wrong, file the false-positive issue; do not work around it.

## Before you start

1. Open the repository in Claude Code and read the first lines it prints: branch, stack,
   active contract, whether the verify stamp is fresh, and any `MISSING TOOL: <name> - install: <cmd>`
   line. Run the install command, then restart the session.
2. Run `/factory:onboard`. Read `the working-agreement section of CLAUDE.md`.
3. Start work with `/factory:ticket`. Nothing is edited before the contract exists.

## Messages from the Bash guard (`guard_bash`)

| Rule | The message starts | Cause | Fix |
|---|---|---|---|
| no-verify | git hooks are not optional here | `commit -n` or `--no-verify` | remove it; fix what the hook reported |
| hooks-path | git hook paths are not overridden | `core.hooksPath` set from a session | don't |
| pr-merge, pr-approve | PRs are merged by a reviewer | `gh pr merge`, `gh pr review --approve` | a reviewer does it in GitHub |
| humans-only | only a human updates the ratchet baseline | `ratchet.py --update` or `--prune`, `freeze_tests.py --unfreeze`, writing `baseline.json` | say what changed and why, and stop; a human runs it |
| rm-rf | rm -rf of root, home, .git, * or . | a broad `rm -r` | name the exact path |
| data-store | dropping or truncating a database or table is for a human | `dropdb`, DROP or TRUNCATE through psql, mysql, sqlite3 or `pg_*` | write a migration; reset dev data through test fixtures |
| data-store | DELETE without a WHERE clause | `DELETE FROM t` with no WHERE | add the WHERE |
<!-- sdlc:if migrate -->
| data-store | alembic downgrade/stamp rewrites migration history | `alembic downgrade` or `stamp` | write a new forward migration |
<!-- sdlc:end -->
| data-store | FLUSHALL/FLUSHDB wipes every key | `redis-cli FLUSHALL` or `FLUSHDB` | delete your keys, or give tests their own DB index |
| data-store | this deletes the data volumes | `compose down -v`, `docker volume rm` or `prune`, `system prune --volumes` | `compose down` without `-v` |
| data-store | rm -r of a data directory | `rm -r` on a data dir | stop with `compose down`; a human deletes data |
| credential-read | reading credential files from a session is blocked | `cat`, `cp`, `curl @file`, redirects on `~/.ssh`, `~/.aws`, `~/.config/gh`, `.env*` | ask the human to export the value; `.env.example` is fine |
| nesting | the command nests shells too deeply | `bash -c` inside `eval` inside ... | run the inner command directly |
| force-push | no force pushes | `--force`, `-f`, `+ref` | push a new commit |
| push-protected | no pushes to main | pushing main, or pushing while on main | branch, push the branch, open a PR |
| destructive-git | destructive git | `reset --hard`, `checkout .`, `clean -f`, `branch -D`, `stash drop` | ask the human |
| no-stamp | no verify stamp | commit with no `/factory:verify` run | run `/factory:verify`, fix what it reports, commit |
| stale-stamp | the tree changed since the last verify | edits after verify | run `/factory:verify` again |
| commit-message | commit message problems | message does not describe the diff | rewrite it to match the files changed |

## Messages from the edit guard (`guard_edit`)

| Rule | The message starts | Cause | Fix |
|---|---|---|---|
| secret | this write contains what looks like a ... | a key, token, private key or hard-coded credential | read it from the environment |
| protected-branch | you are on main | editing while on main | `git switch -c <type>/<slug>` |
| protected-file | is maintained by humans | guards, baseline, stamp, `.env`, listed paths | say what you need and why; a human edits |
| contract-unreadable | current.json exists but is not readable JSON | broken `current.json` | fix it or re-run `/factory:ticket` |
| frozen-test | is frozen by /tests | editing a test `/factory:tests` froze, or dropping its `frozen_tests` entry | if the test is wrong, a human runs `freeze_tests.py --unfreeze <path>` |
| scope | is outside the contract's allowed_paths | file not in `allowed_paths` | change the contract first and say why |
| tier | this path forces tier high | a path in `tier_high_paths` (listed in REVIEW.md) with a lower tier | re-run `/factory:ticket` so the contract records tier high |

## Other messages

- Input or crash: "hook input unreadable; refusing" or "the ... guard crashed". The guards fail
  closed. Fix the hook (or `claude plugin disable factory@lathe` is the escape hatch, with the pack owner's agreement), then retry. Tell the pack owner.
- After an edit: "New violations in <file> (vs .claude/baseline.json)". Fix them; do not add
  them to the baseline. Only new or grown violations are reported.
- At stop: app code changed and tests did not. Add the test, or write in the contract's Decisions
  why none is needed.
- `ACCEPTANCE passed=N failed=M` in the verify output: an acceptance line in the contract failed.

<!-- sdlc: REC-82 -->
