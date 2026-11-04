# Review guide (REVIEW.md)

Read by the advisory Claude review in CI and by human reviewers. The reviewer's job is to find
what would hurt in production or what the author could not see, not to restyle code.

## Always flag

<!-- sdlc:if routes -->
1. A route with no authentication dependency that is not listed in the public routes file with a
   reason, and any change to the dependency itself.
2. Module-level mutable state on a request path (a dict, list or set at import time, a
   class-level default that is shared).
<!-- sdlc:end -->
3. A weaker test: an assertion removed or loosened, a new `skip`, `xfail` or `quarantine`
   marker, an expected value copied from the implementation, or a test that asserts only that a
   function exists.<!-- sdlc:if routes --> An auth dependency override in a test with no 401 test beside it.<!-- sdlc:end -->
4. A changed or deleted frozen test (listed in `frozen_tests` in `current.json`).
5. A file changed outside the contract's `allowed_paths`, or a contract whose tier is lower than
   its paths require.
<!-- sdlc:if migrate -->
6. A schema change with no forward migration, a migration that drops or rewrites data, or a
   downgrade or stamp of migration history anywhere.
<!-- sdlc:end -->
7. A new dependency: does it exist, is it old enough, what is its licence, is the lock updated.
8. A secret, token, URL with credentials, or an `os.environ` read outside the config module.
9. Any change under `.claude/`, `.github/`, `.mcp.json`, compose files or the devcontainer.
10. A growth in `.claude/baseline.json`, or a check turned off or loosened in the same PR.
11. A TODO with no ticket id, a stub body (`pass`, `...`, `NotImplementedError`) on a path that
    ships, or a credential-named route parameter.
12. A destructive data command (drop, truncate, delete without WHERE, flush, volume removal).
13. A commit message or PR body that does not describe the diff, or a `Signed-off-by` trailer on
    an agent commit.

## Tier-high paths

A change under any of these paths is risk tier high: read it line by line, check the contract recorded the
tier, and hold it to the second-owner rule. They are the `tier_high_paths` in the sdlc config:

__TIER_HIGH_PATHS__

## Do not flag

Formatting, import order and naming that the linter and formatter already settle. Style
preferences. Anything outside the diff, unless the diff makes it newly wrong: grep the callers
of changed code before saying a change is safe.

## Output

Report at most ten findings, worst first, as a table: criterion, evidence (file and line),
and the test that would catch it. Say "no findings" if there are none; a review that always finds
something is noise. End with a "Not reviewed" section naming what you did not read or run. Post
one comment. Never approve, and never suggest merging.

<!-- sdlc: REC-39 -->
