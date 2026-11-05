# Install lathe

This guide takes a clean machine to a working `factory` plugin in a repository, with the
marketplace's auto-update on. The plugin is `factory`, from the marketplace `lathe`
(`ishpr/lathe` on GitHub).

## 1. Prerequisites

Check each one before you install.

| Tool | Why | Check |
|---|---|---|
| Claude Code (the CLI), 2.1.295 or later | Runs the plugin. 2.1.295 is the first release that honours `onFailure: "block"` on the guards; on an older one a crashed or missing guard lets the call through. | `claude --version` |
| git | Every stage works on a branch; the guards read the repository. | `git --version` |
| GitHub CLI `gh`, signed in | `/factory:ship` and `/factory:review-pr` use `gh pr ...`; `git push` authenticates through `gh auth git-credential`. | `gh auth status` |
| Python 3 on the PATH | The hooks and the `scripts/` are Python 3 standard library only and run as `python3 -I`. Session start tells you to install Python 3.11+ when `python3` is missing, so use 3.11 or later. | `python3 --version` |
| `uv` | The code graph's MCP server and CLI start as `uv run --project <plugin root> sdlc-graph mcp`. The graph package pins `requires-python = ">=3.11,<3.12"`; `uv` fetches a matching 3.11 itself when your `python3` is another version. | `uv --version` |
| Docker (only for the local graph database) | `/factory:graph provision local` starts Postgres with pgvector on port 5435 through `docker compose`. | `docker --version`, then `docker info` |

What is optional when the graph is off: `uv` and Docker. With the graph off (answer "off" in
`/factory:init`, or run `/factory:graph off`), the hooks, contracts, ratchet and evidence files all
work; only the ledger, impact analysis and the orchestrator need the graph. With the graph on and
its database held elsewhere (`neon`, `aws`, `spanner`), you need `uv` but not Docker.

Your repository's own stack tools (ruff and mypy, pytest, and so on) are used too. Session start
prints a `MISSING TOOL: <name> -- install: <cmd>` line for each one the machine lacks, and the
ratchet skips what it cannot run rather than blocking. Hooks are tested on macOS and Linux; on
Windows they need Git Bash or a `python3` alias.

## 2. Install the plugin

Add the marketplace, then install the plugin. From inside Claude Code:

```
/plugin marketplace add ishpr/lathe
/plugin install factory@lathe
```

or from a shell:

```
claude plugin marketplace add ishpr/lathe
claude plugin install factory@lathe
```

Restart the session (or run `/reload-plugins`) so the skills load. From a local checkout,
`/plugin marketplace add ./lathe` works the same way; for one session only,
`claude --plugin-dir plugins/factory`.

### Choose a scope

Both `claude plugin install` and `claude plugin marketplace add` take `--scope user|project|local`.
`install` defaults to `user`, and so does `marketplace add`.

| Scope | Recorded in | Use it when |
|---|---|---|
| `user` | your user settings | you work on several repositories and want the plugin everywhere. The default. |
| `project` | the repository's `.claude/settings.json`, shared through git | the whole team should get the plugin when they clone. |
| `local` | the repository's `.claude/settings.local.json`, gitignored | you want it in one repository without changing what the team shares. |

```
claude plugin marketplace add ishpr/lathe --scope project
claude plugin install factory@lathe --scope project
```

`/factory:init` also registers the marketplace and the plugin in the repository's
`.claude/settings.json` (`extraKnownMarketplaces`, `enabledPlugins`), so a fresh clone is offered
the plugin without anyone repeating these steps.

## 3. Turn on auto-update

Auto-update is off by default for third-party marketplaces, and `lathe` is one. Turn it on once:

1. In Claude Code, run `/plugin`.
2. Open Marketplaces and select `lathe`.
3. Turn auto-update on.

With it on, Claude Code refreshes the marketplace and updates `factory` itself, outside the sandbox
the factory installs, and you run `/reload-plugins` to load the new version. This matters because a
session running under that sandbox cannot update its own plugin (the sandbox blocks writes to
`~/.claude/plugins`).

To update by hand, from a normal terminal (not from a factory session):

```
claude plugin marketplace update lathe
claude plugin update factory@lathe
```

then `/reload-plugins` in Claude Code (`claude plugin update` says a restart applies the update).
The version comes from the plugin's `plugin.json` and its entry in the marketplace's
`marketplace.json`; a release that does not change those numbers is not seen as an update.

To see what is installed: `claude plugin list` and `claude plugin marketplace list`.

## 4. Onboard a repository

In the repository, start Claude Code and run, once per repository and before any other factory
command:

```
/factory:init
```

**It works on a branch.** On `main`, `master` or another protected branch it first switches to
`chore/onboarding` (or `chore/onboarding-2`), and everything it writes lands there. Nothing is
committed for you.

**What it asks**, once, at the start: whether the code graph is on or off (on is recommended) and
where its database lives (`local` Docker, `neon`, `aws` or `spanner`); which stack, if it cannot
tell or finds several (`python`, `node`, `go`, `rust`); and the paths, each shown as the dry run
detected it for you to confirm or change: app paths, test paths, protected paths and high-risk
paths. Your answers go to the real run, which writes the config and records the baseline on them.

**What it writes**: `.claude/sdlc.json`, `.claude/rules/sdlc-*.md`, `.claude/work/`, a PR
template, the CI gate and evidence workflows under `.github/workflows/`, gitignore lines, the
working agreement in `CLAUDE.md`, and today's debt as `.claude/baseline.json`. It then indexes the
graph (when on), runs the audit and the maturity and security assessments, and writes
`docs/onboarding-report.md`, `docs/maturity-assessment.md` and `docs/security-assessment.md`. It
never edits application code.

**The two review pages.** It publishes two private pages with the Artifact tool, saves each as HTML
under `docs/reviews/`, and gives you the links: the onboarding page ("is this what our system looks
like?", asking you to confirm the paths and protected areas) and the assessment page (maturity and
security, ending in the A/B/C decision). They are private until you share them from the page's
Share menu.

**What you approve.** Claude Code asks, and you answer yes or no, for these; the baseline re-record is the one
step that stays yours:

- A staged file. The sandbox protects settings files a session loaded at start, so init stages them
  under `.sdlc-stage/`; the conversation writes each to its place with the Write tool, one approval
  prompt each. Restart the session afterwards, because sandbox settings are read at start.
- The onboarding commit. The commit guard asks, listing every file and marking the ones the edit
  guard does not protect (for example `.github/workflows/*`). A commit that also holds an
  application or test file, or a stale or failed stamp, is refused.

The baseline stays a human step under the sandbox. When init prints `baseline: not recorded; the
sandbox forbids ...`, the settings template denies `ratchet.py --update` to the agent, so you run the
printed command yourself (turning it into an approval prompt is a follow-up).

Headless runs ask nothing: they name the staged files and the baseline command in the review text,
and the commit guard refuses the commit, so a human commits.

Creating a worktree for parallel work is still a human command: init prints it, with the
`--upgrade` rerun that adds the path to `allowWrite`.

Then review the files on the onboarding branch, commit them and open a pull request. The CI gate
init added runs on that PR.

## 5. Protect the default branch on GitHub

The agent never merges and its hooks refuse a push to a protected branch, but the server must
enforce it as well. In the repository on GitHub: Settings, Rules, Rulesets, New branch ruleset.

- Target **the default branch only** (the "Default" target, `~DEFAULT_BRANCH`, or `refs/heads/main`).
- On it: require a pull request before merging (with an approval, and code owner review if you use
  CODEOWNERS), block deletions, block force pushes. Once `sdlc-gate` has run on the onboarding PR,
  require that status check too.

Do not point the pull-request rule at all branches. A ruleset that does refuses the push of every
PR branch (`GH013: Changes must be made through a pull request`), so no PR can be created or
updated. A second ruleset on all branches may block force pushes only. See
`docs/dogfood-findings.md`, finding 49.

## 6. Check that it works

In Claude Code, in the onboarded repository:

1. `/plugin` lists `factory` as enabled (or `claude plugin list` from a shell).
2. Start a fresh session. The session-start hook prints the branch, the stack, the active contract,
   frozen tests and the stamp state, plus a `MISSING TOOL` line for any stack tool the machine lacks.
3. Run `/factory:audit`. It lists the repository's current issues, ranked, with `file:line`. A
   healthy first run ends in a table (a check that finds nothing says "none"), and the violations
   it lists are the ones already recorded in `.claude/baseline.json`; new ones appear only after
   your own changes.
4. Run `/factory:graph status`. With the graph on, healthy is `configured: true`, the provider, a
   schema version with no `behind by N migrations` (if there is one, run `/factory:graph migrate`),
   and snapshot and node counts. With the graph off it says so, which is also a valid state. If it
   says `graph on but not reachable`, run `/factory:graph provision <provider>` or set
   `DATABASE_URL`.
5. Run `/factory:onboard` to read the working agreement the repository now follows.

## 7. Disable or uninstall

Turn every hook off without removing anything (for a demo, or to debug the pack):

```
/plugin disable factory@lathe
```

or `claude plugin disable factory@lathe` from a shell; `claude plugin enable factory@lathe` turns
them back on. Nothing in the repository changes. This is not an enforcement boundary: a team that
needs one delivers managed settings (`docs/admin-setup.md` in an onboarded repository), and a
managed floor stays in force when the plugin is off.

To remove the plugin, and the marketplace if you want it gone too:

```
claude plugin uninstall factory@lathe
claude plugin marketplace remove lathe
```

`uninstall` takes `--scope` (default `user`; pass the scope you installed with) and `--keep-data`
to keep the plugin's data directory.

**What to remove from a repository.** Uninstalling does not touch the repository. To take the
factory out of it, in a pull request: remove the required `sdlc-gate` check from the ruleset first
(or the PR cannot merge), then delete `.claude/` (or just `sdlc.json`, `rules/`, `work/`,
`baseline.json` and the `extraKnownMarketplaces` and `enabledPlugins` entries of `settings.json`),
`.github/workflows/sdlc-gate.yml` and `sdlc-evidence.yml`, the PR template and issue templates init
added, the working-agreement section of `CLAUDE.md`, `REVIEW.md`, and the generated files under
`docs/` (`onboarding-report.md`, `maturity-assessment.md`, `security-assessment.md`, `reviews/` and
the policy documents). If you provisioned a graph database, remove it separately (for the local
one, stop its container with `docker compose`).

## 8. Troubleshooting

These come from `docs/dogfood-findings.md`.

- **A push from a session fails** ("could not read Username for 'https://github.com'"). Git's
  credential helper (`gh auth git-credential`, or the keychain) cannot read its credentials inside
  the sandbox. The settings init writes carry the exact form `git push -u origin HEAD` in
  `sandbox.excludedCommands`, so it runs outside the sandbox (the guard asks you first in an
  interactive session). Only that exact form is excluded: a different push, or a command line that
  starts with `cd ... &&`, runs inside the sandbox and fails. Use the exact form, or push from a
  normal terminal. If it still fails, check that you copied the staged settings (section 4) and
  restarted the session.
- **`gh` works for a one-line command but not a multi-line one.** `gh` as the first word of its own
  line runs outside the sandbox; a `gh pr create` with a multi-line `--body`, or one after
  `cd ... &&`, does not. Pass bodies with `--body-file`.
- **I changed the sandbox settings and nothing changed.** Restart the session; sandbox settings are
  read when it starts.
- **A headless `/factory:init --headless` reports denied commands** ("Ignoring … permissions.allow
  entries … this workspace has not been trusted"). Claude Code ignores a folder's settings until the
  folder is trusted, so an unattended init in a fresh clone cannot use the allow rules it just wrote;
  it records the denials under Failures and finishes. Start Claude Code in the folder interactively
  once and accept the trust dialog before an unattended run (finding #50). Interactive runs are not
  affected.
- **Evals fail with EPERM, or report zero runs.** Run evals from a normal terminal, not from inside
  a sandboxed session (a nested `claude` cannot write `~/.claude`).
- **The generated CI gate fails on its first run** ("the plugin ref must be an exact 40-character commit SHA").
  Init writes a SHA when it can resolve one; when it printed `could not resolve ... to a commit SHA`, put one in
  `.github/workflows/sdlc-gate.yml` (both `ref:` lines and the `ref=` check) or rerun init with
  `--marketplace-ref <sha>`. `git ls-remote https://github.com/ishpr/lathe.git main` prints the SHA.
- **Init printed `staged: <file> is not writable here`.** The sandbox protects human-owned files (settings, `.mcp.json`,
  `.claude/public_routes.txt`). Init wrote the content to `.sdlc-stage/<same path>`, finished every other step and
  listed a `cp` line for each staged file in its summary. Read the staged file, then run its `cp` from a normal
  terminal (finding #51).
- **The baseline will not record.** It is protected on purpose. A human runs the command init
  printed (`ratchet.py --update`) from a normal terminal.
- **Updating from a factory session fails.** The sandbox blocks writes to `~/.claude/plugins`. Turn
  on auto-update (section 3), or run the two update commands from a normal terminal.
- **`git worktree add` fails with "Operation not permitted".** The sandbox forbids creating
  `.mcp.json` in a checkout. Create the worktree from a normal terminal with the command init
  printed.
- **The graph tools answer "graph not configured".** The graph is on but its database is not
  reachable. Run `/factory:graph status`, then `/factory:graph provision local` (needs Docker) or
  `/factory:graph url` to store a connection string, or turn the graph off with `/factory:graph off`.
- **Pushing a PR branch fails with `GH013`.** A ruleset targets all branches; retarget it to the
  default branch (section 5).
- **A skill or agent is missing after install.** Restart the session or run `/reload-plugins`. If
  `factory:maturity-assessor` or `factory:security-assessor` is still missing, the plugin is damaged:
  reinstall with `claude plugin install factory@lathe`.
