# Data policy: where our code and prompts go

One page for developers. It is not legal advice; the last section lists what counsel decides.

## Rules

1. Use company credentials only. No personal accounts, no personal API keys, no free tiers.
2. No customer data in prompts, tickets or fixtures. Use synthetic data. If a bug needs real
   data, a human reproduces it and describes the shape.
3. Treat transcripts as secrets. Claude Code stores them in plaintext under
   `~/.claude/projects/<project>/<session>.jsonl` (under `CLAUDE_CONFIG_DIR` if you set it).
   They are not encrypted at rest. If a tool reads a `.env` file or a command prints a
   credential, that value is written there. The guards block most reads, not all.
4. Do not paste secrets to "see if it works". Rotate any key that appears in a prompt or output.

## Settings that back the rules

- `DISABLE_FEEDBACK_COMMAND=1` turns off `/feedback`, `/bug` and `/share`, and
  `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1` turns off the rating survey. They are set in the
  managed settings example. Feedback transcripts are retained for 5 years, which is why the
  commands are off.
- `cleanupPeriodDays` sets how long local transcripts are kept (default 30, minimum 1; `0` fails
  validation). The team chooses the number and writes it in the managed settings. Shorter is
  safer; longer helps when you need to look back at an incident.
- Without the feedback commands, the main retention left is the API's own: 30 days by default,
  and longer only if a user allows data use for model improvement. Confirm your plan's terms
  against the current data-usage page before telling a customer.

## Offboarding

When someone leaves or a laptop is lost:

1. Run `claude purge --all` on the machine (`--dry-run` first; `-y` skips the prompt). It
   deletes transcripts, task lists, debug logs, file-edit history and prompt history for every
   project. It does not remove pasted images or session scratchpads in the temp directory; the
   retention sweep removes those later.
2. Rotate every key that appears in a transcript. Search `~/.claude/projects` for the key
   names you use (`AWS_`, `DATABASE_URL`, `ghp_`) before you purge. Deleting a transcript does
   not make a leaked key safe, because the credential stays valid until it is revoked.
3. Remove the person from the organisation and the repository.

## For counsel (one page, decisions we need from them)

- Who owns the output of an agent session, under the customer contract and the vendor's terms.
- Whether client code may be sent to the vendor at all, and under which plan and region.
- Retention and deletion promises we can make, given the figures above.
- Licence exposure for generated code that resembles copyleft code; the PR template's
  "AI-assisted" line records what the author checked (see licence-allowlist.txt).

<!-- sdlc: REC-27 -->
