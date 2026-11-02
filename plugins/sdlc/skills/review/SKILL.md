---
name: review
description: Help a human review a branch or pull request written with an agent — message vs diff, evidence present, contract honoured, ratchet clean, what to look at by hand. Read-only.
argument-hint: <PR number or branch>
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(gh pr diff *)
  - Bash(gh pr view *)
  - Bash(git diff *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_range.py" *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py")
---
You are helping a human review, not replacing them. Target: $ARGUMENTS. For a PR number, run
`/sdlc:review-pr <n>` first: it runs the new tests against the base code.

1. Get the diff (`gh pr diff <n>` or `git diff <base>...<branch>`) and the contract. `/ship` clears
   `.claude/work/` before the final commit, so on a shipped PR the contract is in the PR body
   (`gh pr view <n> --json body`); on an unshipped branch it is `.claude/work/<slug>/contract.md`.
   No contract in either place → say so; that is the first finding.
2. Check, and report each as a line `ok | look | fix` with a file:line:
   - every commit message in the range describes its diff
     (`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/check_commit_range.py" <base>..<branch>`);
   - the Evidence section matches the diff (tests named exist; the real request shown is for a
     route in the diff) and the acceptance table shows every line passing;
   - every acceptance criterion in the contract has a test; every test asserts behaviour;
   - the diff stays inside `allowed_paths`; nothing under Out of scope moved;
   - the ratchet is clean on the branch (`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/ratchet.py"`);
   - something was deleted, or the PR says why not.
3. Then the part only a human can do: list the three places to read by hand — the decision the
   contract left open, the failure mode, the line that handles identity — with why each matters.
4. Verdict: approve-ready / needs changes, in one line. Do not approve or merge; the human does.
