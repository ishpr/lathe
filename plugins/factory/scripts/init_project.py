#!/usr/bin/env python3
"""Initialise a repository for the SDLC working agreement, or upgrade an initialised one.

Detects the stack, writes .claude/sdlc.json from the stack's defaults, and copies the pack's
templates: path-scoped rules, the public-routes file (Python), the PR template, issue templates,
dependabot.yml, docs/**, REVIEW.md, tests/QUARANTINE.md, .mcp.json, the CI gate and the advisory
Claude review. It merges templates/settings.project.json into .claude/settings.json (objects
merge, arrays union, existing scalars win, placeholders are filled from the stack config); every
allow rule that embeds this machine's plugin root path goes to .claude/settings.local.json instead (gitignored),
so settings.json holds only rules a team can commit; run_headless.sh merges the two for --settings,
appends the working agreement to CLAUDE.md (idempotent), writes .claude/KIT_VERSION, adds
gitignore lines, and records today's debt as the ratchet baseline the first time only.
Nothing is committed; the output lists what to review. When .claude/baseline.json exists after the run
(recorded now or kept), one line `baseline by check: <N> key(s), <M> violation(s); <prefix> <count>, ...`
gives the violations by key prefix (the text before the first `|`), so a report needs no shell pipe to count.

--upgrade (REC-38): re-run after a plugin update. It shows a diff of what would change, creates
files that are missing, merges new settings and config keys without removing anything, and
writes <file>.new beside a file that differs from the template instead of overwriting it. It
never re-runs `ratchet.py --update`; the baseline is not touched.

--codeowners '@a,@b' (REC-43): writes .github/CODEOWNERS from the template with real owners.
At least two distinct handles are required; placeholder handles (@your-org/..., @app-...,
@owner, @example) are refused. Without the flag no CODEOWNERS file is written, because the
template holds placeholders.

--marketplace-repo owner/repo names the marketplace repository the CI gate checks the scripts out of
(default: the public `ishpr/lathe`); --marketplace-ref REF is the ref it checks out. The gate refuses a ref
that is not a 40-character commit SHA, so init writes a SHA: REF resolved with `git ls-remote` (a tag or a
branch), or without the flag the commit this plugin was installed from. Rerunning with --marketplace-repo (a fork or a private mirror) replaces the
default in a gate file you have not edited. A private marketplace repository needs a `token:` on that
checkout (the file has the line, commented).

The baseline is recorded with `ratchet.py --update --require-tools`: if the stack's lint or type
tools are missing, init stops with exit 2 after writing the files and names what to install, because a
baseline recorded without them makes the first CI run report every existing finding as new.
--allow-partial-baseline records it anyway (CI stays red until a human re-baselines with the tools).

--graph on|off and --graph-provider local|neon|aws|spanner (defaults: on, local) write the `graph` block of
.claude/sdlc.json: {"enabled", "provider", "url_env": "DATABASE_URL", "spanner": {"project", "instance", "database"}}.
They are the init skill's two ask-once questions (headless: the defaults, listed under Assumptions). No connection
string is ever written here: it lives in the shell, .env or .claude/settings.local.json (see /factory:graph). A kept
config keeps its block; /factory:graph on|off flips it later.

--headless is accepted and ignored: this script never asks anything, so `/factory:init --headless` can pass
its arguments through unchanged. The skill, not the script, changes behaviour when it is set.

Usage: init_project.py [--root DIR] [--stack python|node|go|rust] [--marketplace-repo owner/repo]
                       [--marketplace-ref REF] [--headless] [--no-ci] [--dry-run] [--force] [--upgrade]
                       [--codeowners '@a,@b'] [--allow-partial-baseline] [--cleanup-days N]
                       [--graph on|off] [--graph-provider local|neon|aws|spanner] [--project DIR]... [--help]

A monorepo with no stack marker at the root: init looks up to two directories down (never into hidden or dependency
directories), takes the projects that hold the stack's marker (--project DIR, repeatable, picks them by hand), and derives
paths.app, paths.tests and the commands from those projects; they are listed in the config's `projects` key. Paths that
match nothing are named in the output, never written silently.
Exit: 0 ok, 2 misuse or refusal.

sdlc: REC-38, REC-43, REC-137.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
)
import e2e as e2e_lib
import frontend_foldkit as fk
import sdlc as cl

MARKERS = {
    "python": [
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
    ],
    "node": ["package.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
}
MARKER_START = "<!-- sdlc:start -->"
MARKER_END = "<!-- sdlc:end -->"

GITIGNORE_LINES = [
    ".claude/work/*",  # every local hand-off, spawn and verify log; only the README is committed
    "!.claude/work/README.md",
    ".claude/settings.local.json",
    ".claude/worktrees/",
    ".sdlc-stage/",
]
ECOSYSTEM = {"python": "pip", "node": "npm", "go": "gomod", "rust": "cargo"}
# Python runners, first match wins: (name, command prefix, lock file, pyproject table). A project that has one of
# these needs its commands wrapped, because `python3 -m pytest` only works when the project environment is already
# on PATH, which a fresh worktree, a runner or a daemon started from another virtualenv never has.
PY_RUNNERS = (
    ("uv", "uv run", "uv.lock", "tool.uv"),
    ("poetry", "poetry run", "poetry.lock", None),
    ("pdm", "pdm run", "pdm.lock", None),
)
# Node runners, first match wins: (name, lock files, command that runs a package script, executor for a local binary).
# npm is the fallback and keeps the stack table's spelling (`npx --no-install <tool>`); the others rewrite it.
NODE_RUNNERS = (
    ("pnpm", ("pnpm-lock.yaml",), "pnpm", "pnpm exec"),
    ("yarn", ("yarn.lock",), "yarn", "yarn exec"),
    ("bun", ("bun.lockb", "bun.lock"), "bun run", "bunx"),
)
NODE_TOOL_CMD = re.compile(r"^npx --no-install (eslint|prettier|tsc)\b")
# commands.setup: installs dependencies into a fresh worktree (run_headless.sh runs it once before the first session).
# Exact strings only: run_headless.sh refuses a setup command that is not one of these, and the settings template
# carries one allow rule for each.
SETUP_COMMANDS = (
    "uv sync",
    "uv sync --frozen",
    "poetry install",
    "pdm install",
    "pnpm install --frozen-lockfile",
    "yarn install --immutable",
    "bun install --frozen-lockfile",
    "npm ci",
    "go mod download",
    "cargo fetch",
)
PY_TOOL_CMD = re.compile(r"^python3 -m (pytest|ruff|mypy)\b")
INSTALL_CMD = {
    "python": "pip install",
    "node": "npm install",
    "go": "go get",
    "rust": "cargo add",
}
PACKAGE_DOMAINS = {
    "python": ["pypi.org", "files.pythonhosted.org"],
    "node": ["registry.npmjs.org"],
    "go": ["proxy.golang.org", "sum.golang.org"],
    "rust": ["crates.io", "index.crates.io", "static.crates.io"],
}
# Files a person edits after init: never replaced, never given a .new, even with --force.
USER_OWNED = {
    os.path.join("tests", "QUARANTINE.md"),
    os.path.join(".claude", "public_routes.txt"),
    os.path.join(".claude", "work", "README.md"),
}
# Exact placeholder strings only, so a real account such as @todoist or @example-corp is accepted.
PLACEHOLDER_HANDLE = re.compile(
    r"(?i)^@(your-org(/.*)?|your-team|app-reviewers|app-security-owner|owner|org/(team|name|reviewers|security-owner)"
    r"|team/(name|reviewers)|placeholder|changeme|example|example/.*|todo)$"
)
# Files init and the ratchet generate: a PR that adds or refreshes them is measured on hand-written lines only.
GENERATED_PR_SIZE_EXCLUDE = (".claude/baseline.json", "docs/reviews/**")
# Directories a marker search never enters.
SKIP_DIRS = {
    "node_modules",
    "venv",
    "env",
    "vendor",
    "dist",
    "build",
    "target",
    "site-packages",
    "__pycache__",
}
DEFAULT_REPO = (
    "ishpr/lathe"  # the public marketplace; --marketplace-repo names a fork or a mirror
)
HANDLE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$")


class Report:
    def __init__(self, root: str, dry: bool, force: bool, upgrade: bool) -> None:
        self.root, self.dry, self.force, self.upgrade = root, dry, force, upgrade
        self.created: list[str] = []
        self.kept: list[str] = []
        self.same = 0
        self.new: list[str] = []
        self.merged: list[str] = []
        self.diffs: list[tuple[str, str]] = []
        self.settings_paths: list[str] = []
        self.worktrees: list[str] = []
        self.staged: list[
            tuple[str, str]
        ] = []  # (the file init meant to write, where its content went instead)

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def _write_or_stage(self, path: str, content: str) -> bool:
        """Write the file; when the operating system refuses (Claude Code's sandbox protects a settings file it loaded
        when the session started, headless runs included), put the same content under .sdlc-stage/<name> for a human
        to copy and return False, so an upgrade finishes its other steps instead of dying here."""
        try:
            self._write(path, content)
            return True
        except PermissionError:
            staged = os.path.join(self.root, ".sdlc-stage", os.path.basename(path))
            self._write(staged, content)
            self.staged.append((self.rel(path), self.rel(staged)))
            return False

    def _write(self, path: str, content: str, mode: int | None = None) -> None:
        if self.dry:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        if mode is not None:
            os.chmod(path, mode)

    def _diff(self, path: str, old: str, new: str) -> None:
        if self.upgrade:
            text = "".join(
                difflib.unified_diff(
                    old.splitlines(True),
                    new.splitlines(True),
                    f"a/{self.rel(path)}",
                    f"b/{self.rel(path)} (template)",
                    n=1,
                )
            )
            self.diffs.append((self.rel(path), text))

    def put(self, path: str, content: str, owned: bool = False) -> None:
        """Create a file; if it exists and differs: keep it, replace it (--force), or write <path>.new (--upgrade)."""
        key = os.path.relpath(path, self.root)
        if not os.path.exists(path):
            self.created.append(self.rel(path))
            self._write(path, content)
            return
        with open(path, encoding="utf-8", errors="replace") as fh:
            old = fh.read()
        if old == content:
            self.same += 1
            return
        if key in USER_OWNED or owned:
            self.kept.append(self.rel(path) + " (yours; never replaced)")
            return
        if self.force:
            self.created.append(self.rel(path) + " (replaced, --force)")
            self._write(path, content)
        elif self.upgrade:
            self._diff(path, old, content)
            self.new.append(self.rel(path) + ".new")
            self._write(path + ".new", content)
        else:
            self.kept.append(self.rel(path))

    def put_file(
        self, src: str, dest: str, transform=None, owned: bool = False
    ) -> None:
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        self.put(dest, transform(content) if transform else content, owned)


# ---------- stack and config ----------


def find_projects(root: str, depth: int = 2) -> dict[str, list[str]]:
    """Stack -> project directories (relative, sorted) that hold a marker below the root, at most ``depth`` levels down.
    For a monorepo with no marker at the root; hidden directories and dependency directories are not entered."""
    found: dict[str, list[str]] = {}

    def walk(rel: str, level: int) -> None:
        base = os.path.join(root, rel)
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return
        for name in names:
            if (
                name.startswith(".")
                or name in SKIP_DIRS
                or not os.path.isdir(os.path.join(base, name))
            ):
                continue
            child = os.path.join(rel, name) if rel else name
            for stack in detect_stack(os.path.join(root, child)):
                found.setdefault(stack, []).append(child.replace(os.sep, "/"))
            if level < depth:
                walk(child, level + 1)

    walk("", 1)
    return found


def project_defaults(
    root: str, stack: str, projects: list[str], defaults: dict
) -> dict:
    """paths.app, paths.tests, entry_modules and env_access_allowed for projects below the root: derived from each
    project's own layout and kept only where they exist (a project with no recognisable layout is its whole directory)."""
    app: list[str] = []
    tests: list[str] = []
    for p in projects:
        base = os.path.join(root, p)
        own = detect_app_paths(base, stack, [])
        if not own:
            own = [
                g
                for g in defaults["paths"]["app"]
                if os.path.isdir(os.path.join(base, g.split("/")[0]))
            ] or ["**"]
        app += [f"{p}/{g}" for g in own]
        found = [
            g
            for g in defaults["paths"]["tests"]
            if os.path.isdir(os.path.join(base, g.split("/")[0]))
        ]
        tests += [f"{p}/{g}" for g in (found or defaults["paths"]["tests"][:1])]
    out: dict = {"app": app, "tests": tests}
    for key in ("entry_modules", "env_access_allowed"):
        out[key] = [
            f"{p}/{f}"
            for p in projects
            for f in defaults.get(key, [])
            if os.path.exists(os.path.join(root, p, f))
        ]
    return out


def missing_paths(root: str, globs: list[str]) -> list[str]:
    """Configured globs whose leading directory does not exist: init says so rather than writing them silently."""
    return [
        g
        for g in globs
        if not os.path.exists(os.path.join(root, g.split("*")[0].rstrip("/") or "."))
    ]


def detect_stack(root: str) -> list[str]:
    return [
        s
        for s, files in MARKERS.items()
        if any(os.path.exists(os.path.join(root, f)) for f in files)
    ]


def detect_python_runner(root: str) -> tuple[str, str]:
    """(runner name, why) for a Python repo: uv.lock or a [tool.uv] table, poetry.lock, pdm.lock; else ('', '')."""
    pyproject = ""
    try:
        with open(
            os.path.join(root, "pyproject.toml"), encoding="utf-8", errors="replace"
        ) as fh:
            pyproject = fh.read()
    except OSError:
        pass
    for name, _prefix, lock, table in PY_RUNNERS:
        if os.path.isfile(os.path.join(root, lock)):
            return name, lock
        if table and re.search(
            rf"^\s*\[{re.escape(table)}(\.[^\]]*)?\]", pyproject, re.MULTILINE
        ):
            return name, f"[{table}] in pyproject.toml"
    return "", ""


def apply_python_runner(commands: dict, runner: str, project: str = "") -> dict:
    """The stack table spells tools `python3 -m <tool>`; under a runner the same argument shape follows the runner prefix.
    ``project``: a directory below the root; uv then runs `uv run --project <dir>` so the command works from the root."""
    prefix = next((p for n, p, _l, _t in PY_RUNNERS if n == runner), "")
    if not prefix:
        return commands
    if project and runner == "uv":
        prefix = f"uv run --project {project}"
    return {
        k: PY_TOOL_CMD.sub(lambda m: f"{prefix} {m.group(1)}", v)
        if isinstance(v, str)
        else v
        for k, v in commands.items()
    }


def detect_node_runner(root: str) -> tuple[str, str]:
    """(runner name, why) for a Node repo: pnpm-lock.yaml, yarn.lock, bun.lockb or bun.lock, else npm (precedence in that order)."""
    for name, locks, _run, _exec in NODE_RUNNERS:
        for lock in locks:
            if os.path.isfile(os.path.join(root, lock)):
                return name, lock
    if os.path.isfile(os.path.join(root, "package-lock.json")):
        return "npm", "package-lock.json"
    return "npm", "no lock file; npm is the default"


def node_scripts(root: str) -> dict:
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
            scripts = json.load(fh).get("scripts")
    except (OSError, ValueError, AttributeError):
        return {}
    return scripts if isinstance(scripts, dict) else {}


def node_ci_values(
    root: str, setup_command: str = "", test_command: str = ""
) -> dict[str, str]:
    """What the node CI gate template needs for this repository's package manager: the setup step that puts it on
    the runner (corepack for pnpm and yarn, at the version package.json's ``packageManager`` pins), the
    ``cache:`` line for actions/setup-node, the lockfile the pair check watches and the install. The install is
    the project's ``commands.setup`` when it has one (what a fresh checkout needs, the same command the
    orchestrator runs in a new worktree: for a Next.js app that is the install and then ``next typegen``, without
    which the typecheck reports the generated route types as unknown names), else the lockfile-only install.
    The console's gate had died in setup-node with "Dependencies lock file is not found" because the template
    cached with npm while the repository used pnpm, so the gate never ran; once it ran it failed on the
    missing route types."""
    runner, _why = detect_node_runner(root)
    if setup_command.strip() or test_command.strip():
        values = node_ci_values(root)
        if setup_command.strip():
            values["install"] = setup_command.strip()
        if test_command.strip():
            values["test"] = test_command.strip()
        return values
    pinned = ""
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
            pm = json.load(fh).get("packageManager")
        if (
            isinstance(pm, str)
            and pm.split("@", 1)[0] == runner
            and re.fullmatch(r"[a-z]+@\d[\w.-]*", pm.split("+", 1)[0])
        ):
            pinned = pm.split("+", 1)[0]
    except (OSError, ValueError):
        pass
    corepack = (
        "      - name: {name} (corepack)\n        run: |\n          corepack enable\n"
        "          corepack prepare {spec} --activate\n"
    )
    if runner == "pnpm":
        return {
            "setup": corepack.format(name="pnpm", spec=pinned or "pnpm@9"),
            "cache": "          cache: pnpm\n",
            "lockfile": "pnpm-lock.yaml",
            "install": "pnpm install --frozen-lockfile",
            "test": "pnpm test",
            "audit": "pnpm audit --audit-level=high",
        }
    if runner == "yarn":
        return {
            "setup": corepack.format(name="yarn", spec=pinned or "yarn@1.22.22"),
            "cache": "          cache: yarn\n",
            "lockfile": "yarn.lock",
            "install": "yarn install --immutable",
            "test": "yarn test",
            "audit": "yarn npm audit --severity high",
        }
    if runner == "bun":
        lock = (
            "bun.lock"
            if os.path.exists(os.path.join(root, "bun.lock"))
            else "bun.lockb"
        )
        return {
            "setup": "      - name: bun\n        run: npm install -g bun@1\n",
            "cache": "",
            "lockfile": lock,
            "install": "bun install --frozen-lockfile",
            "test": "bun test",
            "audit": "bun audit",
        }
    return {
        "setup": "",
        "cache": "          cache: npm\n",
        "lockfile": "package-lock.json",
        "install": "npm ci",
        "test": "npm test --silent",
        "audit": "npm audit --audit-level=high",
    }


def apply_node_runner(
    commands: dict, runner: str, scripts: dict
) -> tuple[dict, list[str]]:
    """Spell the stack's npm commands for the detected runner; returns (commands, notes saying what was chosen and why)."""
    notes: list[str] = []
    spec = next((r for r in NODE_RUNNERS if r[0] == runner), None)
    out = dict(commands)
    if spec:
        _n, _l, run, ex = spec
        out = {
            k: NODE_TOOL_CMD.sub(lambda m: f"{ex} {m.group(1)}", v)
            if isinstance(v, str)
            else v
            for k, v in out.items()
        }
        out["test"] = f"{runner} test"
    run = spec[2] if spec else "npm run"
    if "typecheck" in scripts:
        out["typecheck"] = (
            f"{runner} typecheck" if runner in ("pnpm", "yarn") else f"{run} typecheck"
        )
        notes.append(
            f"typecheck: package.json script 'typecheck' -> {out['typecheck']}"
        )
    elif "typecheck" in commands:
        notes.append(f"typecheck: no 'typecheck' script -> {out['typecheck']}")
    if "lint:json" in scripts:
        out["lint_report"] = (
            f"{runner} lint:json" if runner in ("pnpm", "yarn") else f"{run} lint:json"
        )
        notes.append(
            f"lint_report: package.json script 'lint:json' -> {out['lint_report']}"
        )
    elif "lint" in scripts:
        notes.append(
            f"lint_report: kept {out.get('lint_report')} (the 'lint' script is not assumed to emit JSON; add a 'lint:json' script to use it)"
        )
    return out, notes


PLAYWRIGHT_CONFIGS = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
    "playwright.config.mts",
    "playwright.config.cts",
)
E2E_URL = re.compile(r"""(?:baseURL|url)\s*:\s*["'`](?P<url>https?://[^"'`\s]+)["'`]""")


def detect_e2e(root: str, runner: str, scripts: dict) -> dict | None:
    """The `e2e` block for a Node repo with a Playwright config (lib/e2e.py reads it): the e2e script as the command
    (`test:e2e`, `e2e` or `test:playwright`, else `playwright test` through the runner), `build` and `start` scripts as
    build and serve, and the url the config names (`baseURL` or `webServer.url`; http://localhost:3000 when it names
    none). None when there is no Playwright config. The gate builds once and serves the built app itself, because a dev
    server with a file watcher does not run inside Claude Code's sandbox."""
    config = next(
        (c for c in PLAYWRIGHT_CONFIGS if os.path.isfile(os.path.join(root, c))), None
    )
    if config is None:
        return None
    spec = next((r for r in NODE_RUNNERS if r[0] == runner), None)
    run, ex = (spec[2], spec[3]) if spec else ("npm run", "npx --no-install")
    script = next(
        (n for n in ("test:e2e", "e2e", "test:playwright") if n in scripts), None
    )
    block: dict = {"command": f"{run} {script}" if script else f"{ex} playwright test"}
    if "build" in scripts:
        block["build"] = f"{run} build"
    if "start" in scripts:
        block["serve"] = (
            f"{runner} start" if runner in ("pnpm", "yarn", "npm") else f"{run} start"
        )
    url = "http://localhost:3000"
    try:
        with open(os.path.join(root, config), encoding="utf-8", errors="replace") as fh:
            m = E2E_URL.search(fh.read())
        if m:
            url = m.group("url")
    except OSError:
        pass
    block["url"] = url
    block["ready_timeout"] = 120
    return block


ROUTE_PATTERN = re.compile(
    r"\b(?:FastAPI|APIRouter|Blueprint|Flask|web\.RouteTableDef)\(|@\w+\.(?:route|get|post|put|patch|delete)\(|urlpatterns\s*="
)


def has_http_routes(root: str) -> bool:
    """True when a Python file under the root defines an HTTP app or route (FastAPI, Flask, Django urls, aiohttp)."""
    seen = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d not in ("node_modules", "venv", "env", "site-packages", "__pycache__", "tests", "test")
        ]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            seen += 1
            if seen > 2000:
                return False
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as fh:
                    if ROUTE_PATTERN.search(fh.read(200000)):
                        return True
            except OSError:
                continue
    return False


def python_install_lines(root: str, config: dict) -> list[str]:
    """Shell lines that install every project the config names (the root when none), for the gate's install step."""
    lines: list[str] = []
    for proj in config.get("projects") or ["."]:
        base = os.path.join(root, proj)
        arg = proj if proj == "." else f"./{proj.strip('/')}"
        if os.path.isfile(os.path.join(base, "pyproject.toml")):
            lines.append(f'pip install -e "{arg}[test]" || pip install -e "{arg}"')
        elif os.path.isfile(os.path.join(base, "requirements.txt")):
            lines.append(f'pip install -r "{arg}/requirements.txt"')
    return lines or ["true  # no pyproject.toml or requirements.txt found: add the install line for this project"]


def fill_python_ci(text: str, root: str, config: dict) -> str:
    """Fill __PY_INSTALL__; the route-sweep region between its markers is deleted when no HTTP routes exist, and its
    marker lines are removed either way."""
    indent = " " * 10
    text = text.replace("__PY_INSTALL__", ("\n" + indent).join(python_install_lines(root, config)))
    if not has_http_routes(root):
        text = re.sub(
            r"^[ \t]*# __ROUTES_BEGIN__.*?# __ROUTES_END__[^\n]*\n", "", text, flags=re.MULTILINE | re.DOTALL
        )
    # With routes the region stays; only its two marker lines go.
    return re.sub(r"^[ \t]*# __ROUTES_(?:BEGIN|END)__[^\n]*\n", "", text, flags=re.MULTILINE)


def detect_setup_command(root: str, stack: str) -> str:
    """The command that installs dependencies into a fresh checkout; '' when there is nothing to install."""
    if stack == "python":
        runner = detect_python_runner(root)[0]
        if runner == "uv":
            return (
                "uv sync --frozen"
                if os.path.isfile(os.path.join(root, "uv.lock"))
                else "uv sync"
            )
        return {"poetry": "poetry install", "pdm": "pdm install"}.get(runner, "")
    if stack == "node":
        return {
            "pnpm": "pnpm install --frozen-lockfile",
            "yarn": "yarn install --immutable",
            "bun": "bun install --frozen-lockfile",
        }.get(detect_node_runner(root)[0], "npm ci")
    return {"go": "go mod download", "rust": "cargo fetch"}.get(stack, "")


def detect_app_paths(root: str, stack: str, defaults: list[str]) -> list[str]:
    if stack == "python":
        pkgs = sorted(
            d
            for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and os.path.exists(os.path.join(root, d, "__init__.py"))
            and d not in ("tests", "test", "docs", "scripts")
        )
        if pkgs:
            return [f"{p}/**" for p in pkgs]
    if stack == "node":
        present = [
            p
            for p in ("src/**", "app/**", "lib/**", "server/**")
            if os.path.isdir(os.path.join(root, p.split("/")[0]))
        ]
        if present:
            return present
    return defaults


def graph_config(enabled: bool = True, provider: str = "local") -> dict:
    """The graph block init writes: the library defaults with the two answers on top."""
    block, _ = cl.graph_block({"enabled": enabled, "provider": provider})
    return block


def build_config(
    root: str,
    stack: str,
    defaults: dict,
    repo: str,
    graph: dict | None = None,
    projects: list[str] | None = None,
) -> dict:
    """Stack defaults first, then the project-specific keys on top. ``projects``: directories below the root that hold
    the stack's marker (a monorepo with none at the root); paths and commands then come from them."""
    sub = [p for p in (projects or []) if p]
    first = os.path.join(root, sub[0]) if sub else root
    derived = project_defaults(root, stack, sub, defaults) if sub else {}
    config = {
        k: v
        for k, v in defaults.items()
        if k not in ("stack", "extensions", "paths", "lint_format", "typecheck_format")
    }
    config["lint_format"] = defaults.get("lint_format", "")
    config["typecheck_format"] = defaults.get("typecheck_format", "")
    config.update(
        {
            "version": 1,
            "stack": stack,
            "paths": {
                "app": derived["app"]
                if sub
                else detect_app_paths(root, stack, defaults["paths"]["app"]),
                "tests": derived["tests"] if sub else defaults["paths"]["tests"],
                "protected": [],
                "protected_branches": ["main", "master"],
            },
            "commands": apply_python_runner(
                dict(defaults["commands"]),
                detect_python_runner(first)[0],
                sub[0] if sub else "",
            )
            if stack == "python"
            else dict(defaults["commands"]),
            "checkers": defaults["checkers"],
            "require_tests": defaults.get("require_tests", True),
            "auth": defaults.get("auth", {}),
            "marketplace_repo": repo,
            "graph": graph or graph_config(),
        }
    )
    if stack == "node":
        runner = detect_node_runner(first)[0]
        scripts = node_scripts(first)
        config["commands"] = apply_node_runner(config["commands"], runner, scripts)[0]
        e2e = detect_e2e(root, runner, scripts)
        if e2e:
            config["e2e"] = e2e
    # the install command is an exact string run at the root; a project below the root installs through its own runner
    config["commands"]["setup"] = "" if sub else detect_setup_command(root, stack)
    if sub:
        config["projects"] = sub
        config["entry_modules"] = derived["entry_modules"]
        config["env_access_allowed"] = derived["env_access_allowed"]
    pr_size = dict(config.get("pr_size") or {})
    have = list(pr_size.get("exclude") or [])
    pr_size["exclude"] = have + [g for g in GENERATED_PR_SIZE_EXCLUDE if g not in have]
    config["pr_size"] = pr_size
    if stack == "python":
        # /migrate reads these from the project file; the skill does not see stacks/*.json (S1 report).
        config["commands"].setdefault(
            "migrate_sql", "alembic upgrade {from}:head --sql"
        )
        config["commands"].setdefault("migrate_check", "alembic check")
    return config


def merge_missing_keys(existing: dict, wanted: dict) -> list[str]:
    """Add keys that are absent (recursively into dicts); never change a present value."""
    added: list[str] = []
    for k, v in wanted.items():
        if k not in existing:
            existing[k] = v
            added.append(k)
        elif isinstance(v, dict) and isinstance(existing[k], dict):
            added += [f"{k}.{sub}" for sub in merge_missing_keys(existing[k], v)]
    return added


# ---------- settings merge ----------


def current_branch(root: str) -> str:
    """The checked-out branch name, or '' (not a git repository, or a detached HEAD)."""
    try:
        return cl.git(root, "branch", "--show-current").strip()
    except Exception:  # noqa: BLE001 - git missing or failing must never stop init
        return ""


def command_prefix(cmd: str) -> str:
    """'python3 -m pytest -q' -> 'python3 -m pytest'; 'npx --no-install eslint --fix {file}' -> 'npx eslint'."""
    toks = [t for t in cmd.split() if not (t.startswith("{") and t.endswith("}"))]
    if toks[:2] == ["uv", "run"] and toks[2:3] == ["--project"] and len(toks) > 4:
        # 'uv run --project plugins/factory pytest -q': the project is part of the command; without it the rule would be
        # the blanket `uv run *`, which runs anything
        return " ".join(toks[:5])
    if toks and toks[0] in ("npx", "pnpm", "yarn"):
        toks = [toks[0]] + [
            t
            for i, t in enumerate(toks[1:])
            if not (
                t.startswith("-")
                and not any(not x.startswith("-") for x in toks[1:][:i])
            )
        ]
    out = []
    for t in toks:
        if t.startswith("-") and t not in ("-m", "-I"):
            break
        out.append(t)
    return " ".join(out)


def read_dev_urls(root: str) -> dict[str, str]:
    """Dev-only DATABASE_URL and REDIS_URL from .env.example or a compose file; local or compose-service hosts only."""
    found: dict[str, str] = {}
    names = [
        ".env.example",
        ".env.sample",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    ]
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for var in ("DATABASE_URL", "REDIS_URL"):
            if var in found:
                continue
            m = re.search(
                rf"^\s*-?\s*{var}\s*[:=]\s*[\"']?([A-Za-z][A-Za-z0-9+.-]*://[^\s\"']+)",
                text,
                re.MULTILINE,
            )
            if not m or "$" in m.group(1):
                continue
            host = (
                re.sub(r"^[^@/]*://([^@/]*@)?", "", m.group(1))
                .split("/")[0]
                .split(":")[0]
            )
            if host in ("localhost", "127.0.0.1", "::1") or ("." not in host and host):
                found[var] = m.group(1)
    return found


def fill_settings(tpl: object, values: dict[str, object]) -> object:
    """Replace {placeholders}; drop list elements and dict entries whose placeholder has no value."""
    ph = re.compile(r"\{[a-z_]+\}")
    if isinstance(tpl, dict):
        out = {}
        for k, v in tpl.items():
            if k == "_comment":
                continue
            r = fill_settings(v, values)
            if r is not _DROP:
                out[k] = r
        return out
    if isinstance(tpl, list):
        out_l: list = []
        for v in tpl:
            r = fill_settings(v, values)
            if r is _DROP:
                continue
            if isinstance(r, list) and isinstance(v, str):
                out_l += r
            else:
                out_l.append(r)
        return out_l
    if isinstance(tpl, str) and ph.search(tpl):
        whole = ph.fullmatch(tpl)
        if whole:
            val = values.get(tpl[1:-1])
            return _DROP if val in (None, "", []) else val
        for name in ph.findall(tpl):
            if not values.get(name[1:-1]):
                return _DROP
        return ph.sub(lambda m: str(values[m.group(0)[1:-1]]), tpl)
    return tpl


_DROP = object()


def union_merge(existing: object, tpl: object, counts: list[int]) -> object:
    """Objects merge, arrays union, an existing scalar wins. counts[0] counts what was added."""
    if isinstance(existing, dict) and isinstance(tpl, dict):
        for k, v in tpl.items():
            if k in existing:
                existing[k] = union_merge(existing[k], v, counts)
            else:
                existing[k] = v
                counts[0] += 1
        return existing
    if isinstance(existing, list) and isinstance(tpl, list):
        for v in tpl:
            if v not in existing:
                existing.append(v)
                counts[0] += 1
        return existing
    return existing


# Allow-rule families by tool; init keeps only the family of the stack's runner (the template lists every stack's).
RULE_FAMILIES = {
    "uv": ("Bash(uv ",),
    "poetry": ("Bash(poetry ",),
    "pdm": ("Bash(pdm ",),
    "pip": ("Bash(python3 -m pytest",),
    "pnpm": ("Bash(pnpm ",),
    "yarn": ("Bash(yarn ",),
    "bun": ("Bash(bun ", "Bash(bunx "),
    "npm": ("Bash(npm ", "Bash(npx "),
    "go": ("Bash(go ",),
    "cargo": ("Bash(cargo ",),
}


def stack_families(stack: str, root: str, projects: list[str]) -> set[str]:
    """The rule families this repo uses."""
    base = os.path.join(root, projects[0]) if projects else root
    if stack == "python":
        return {detect_python_runner(base)[0] or "pip"}
    if stack == "node":
        return {detect_node_runner(base)[0]}
    return {"go"} if stack == "go" else {"cargo"}


def trim_allow(allow: list, families: set[str], setup: str, keep: set[str]) -> list:
    """Drop the other stacks' rules and every install rule but the repo's own setup command. ``keep`` is never dropped."""
    wanted = tuple(p for f in families for p in RULE_FAMILIES[f])
    all_prefixes = tuple(p for ps in RULE_FAMILIES.values() for p in ps)
    out = []
    for rule in allow:
        if not isinstance(rule, str) or rule in keep:
            out.append(rule)
            continue
        if (
            rule.startswith("Bash(")
            and rule.endswith(")")
            and rule[5:-1] in SETUP_COMMANDS
        ):
            if rule[5:-1] == setup:
                out.append(rule)
            continue
        if rule.startswith(all_prefixes) and not rule.startswith(wanted):
            continue
        out.append(rule)
    return out


VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+")


def version_wildcard(plugin_root: str) -> str:
    """The plugin cache directory ends in the version (.../factory/0.6.0); an allow rule that names it stops matching
    after an update. Replace the version with a wildcard; a checkout that is not versioned keeps its path."""
    head, tail = os.path.split(plugin_root.rstrip("/\\"))
    return f"{head}/*" if head and VERSION_DIR.match(tail) else plugin_root


def worktree_paths(root: str) -> list[str]:
    """Checkouts `git worktree list` knows that sit outside the project directory (the sandbox's `.` covers the rest)."""
    try:
        out = cl.git(root, "worktree", "list", "--porcelain")
    except Exception:  # noqa: BLE001 - no git must never stop init
        return []
    here = os.path.realpath(root)
    paths = []
    for line in out.splitlines():
        if not line.startswith("worktree "):
            continue
        path = line[len("worktree ") :].strip()
        real = os.path.realpath(path)
        if real == here or real.startswith(here + os.sep) or not os.path.isdir(real):
            continue
        paths.append(path)
    return paths


def marketplace_identity(plugin: str, repo: str) -> tuple[str, str]:
    """(marketplace name, plugin name) for enabledPlugins. The marketplace file sits two levels above a plugin checked
    out from its repository; an installed copy has none, so the repository's name stands in."""
    name = repo.rsplit("/", 1)[-1]
    try:
        with open(
            os.path.join(plugin, "..", "..", ".claude-plugin", "marketplace.json"),
            encoding="utf-8",
        ) as fh:
            name = json.load(fh).get("name") or name
    except (OSError, ValueError):
        pass
    pname = "factory"
    try:
        with open(
            os.path.join(plugin, ".claude-plugin", "plugin.json"), encoding="utf-8"
        ) as fh:
            pname = json.load(fh).get("name") or pname
    except (OSError, ValueError):
        pass
    return name, pname


def merge_settings(
    rep: Report,
    tpl_path: str,
    stack: str,
    config: dict,
    root: str,
    cleanup_days: int,
    repo: str = "",
) -> None:
    if not os.path.exists(tpl_path):
        return
    with open(tpl_path, encoding="utf-8") as fh:
        tpl = json.load(fh)
    cmds = config.get("commands", {})
    urls = read_dev_urls(root)
    plugin = cl.plugin_root()
    values = {
        "test_command": command_prefix(cmds.get("test", "")),
        "lint_command": command_prefix(
            cmds.get("lint_report") or cmds.get("lint_fix", "")
        ),
        "typecheck_command": command_prefix(cmds.get("typecheck", "")),
        "format_command": command_prefix(cmds.get("format", "")),
        "install_command": INSTALL_CMD.get(stack, ""),
        "package_domains": PACKAGE_DOMAINS.get(stack, []),
        "database_url": urls.get("DATABASE_URL", ""),
        "redis_url": urls.get("REDIS_URL", ""),
        "cleanup_days": cleanup_days,
        "plugin_root": version_wildcard(plugin),
    }
    filled = fill_settings(tpl, values)
    if not isinstance(filled, dict):
        return
    perms = filled.setdefault("permissions", {})
    generated = {
        f"Bash({values[k]} *)"
        for k in ("test_command", "lint_command", "typecheck_command", "format_command")
        if values[k]
    }
    if cmds.get("test"):
        generated.add(f"Bash({cmds['test']})")  # commands.test exactly, as configured
    perms["allow"] = trim_allow(
        list(perms.get("allow") or []),
        stack_families(stack, root, config.get("projects") or []),
        str(cmds.get("setup") or ""),
        generated,
    ) + sorted(g for g in generated if g not in (perms.get("allow") or []))
    if repo and not repo.startswith("<"):
        mname, pname = marketplace_identity(plugin, repo)
        filled["extraKnownMarketplaces"] = {
            mname: {"source": {"source": "github", "repo": repo}}
        }
        filled["enabledPlugins"] = {f"{pname}@{mname}": True}
    if config.get("e2e"):
        # the e2e runner leaves the sandbox (no browser launches inside it): the exclusion is the human's consent, kept
        # next to the e2e block init wrote; the absolute spelling is a machine rule and goes to settings.local.json
        sandbox = filled.setdefault("sandbox", {})
        have = list(sandbox.get("excludedCommands") or [])
        sandbox["excludedCommands"] = have + [
            p
            for p in e2e_lib.excluded_patterns(str(values["plugin_root"]))
            if p not in have
        ]
    portable, local = split_machine_rules(filled, str(values["plugin_root"]))
    trees = worktree_paths(root)
    if trees:  # absolute and per machine, so it is the local file's
        local = json.loads(json.dumps(local))
        local.setdefault("sandbox", {}).setdefault("filesystem", {})["allowWrite"] = (
            trees
        )
        rep.worktrees = trees
    write_settings(
        rep,
        os.path.join(root, ".claude", "settings.json"),
        portable,
        drop=is_machine_rule,
    )
    if local:
        write_settings(rep, os.path.join(root, ".claude", "settings.local.json"), local)
        rep.settings_paths = [".claude/settings.json", ".claude/settings.local.json"]
    else:
        rep.settings_paths = [".claude/settings.json"]


# An allow rule written by an earlier init, with the plugin's absolute path in it: Bash(python3 -I "/x/y/scripts/*) and kin.
MACHINE_RULE = re.compile(
    r'^Bash\((?:python3 -I|bash) "?(?:/|[A-Za-z]:[\\/])[^"]*/scripts/\*\)$'
)


def is_machine_rule(rule: object) -> bool:
    return isinstance(rule, str) and bool(MACHINE_RULE.match(rule))


def split_machine_rules(filled: dict, plugin_root: str) -> tuple[dict, dict]:
    """(portable, local): every permissions.allow rule and sandbox.excludedCommands entry that embeds the plugin root
    path moves to the local document."""
    if not plugin_root:
        return filled, {}
    allow = (filled.get("permissions") or {}).get("allow") or []
    excl = (filled.get("sandbox") or {}).get("excludedCommands") or []
    machine = [r for r in allow if isinstance(r, str) and plugin_root in r]
    machine_excl = [r for r in excl if isinstance(r, str) and plugin_root in r]
    if not machine and not machine_excl:
        return filled, {}
    portable = json.loads(json.dumps(filled))
    local: dict = {}
    if machine:
        portable["permissions"]["allow"] = [r for r in allow if r not in machine]
        local["permissions"] = {"allow": machine}
    if machine_excl:
        portable["sandbox"]["excludedCommands"] = [r for r in excl if r not in machine_excl]
        local["sandbox"] = {"excludedCommands": machine_excl}
    return portable, local


def write_settings(rep: Report, path: str, filled: dict, drop=None) -> None:
    """Create the file, or union-merge into it (objects merge, arrays union, existing scalars win). With ``drop``, an
    allow rule in the existing file that ``drop`` recognises is removed: init wrote it earlier and it now lives elsewhere."""
    if not os.path.exists(path):
        if rep._write_or_stage(path, json.dumps(filled, indent=2) + "\n"):
            rep.created.append(rep.rel(path))
        return
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if not isinstance(existing, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as exc:
        rep.kept.append(f"{rep.rel(path)} (not valid JSON: {exc}; fix it, then re-run)")
        return
    moved = 0
    perms = existing.get("permissions")
    if drop and isinstance(perms, dict) and isinstance(perms.get("allow"), list):
        kept_rules = [r for r in perms["allow"] if not drop(r)]
        moved = len(perms["allow"]) - len(kept_rules)
        perms["allow"] = kept_rules
    counts = [0]
    union_merge(existing, filled, counts)
    if counts[0] or moved:
        extra = (
            f"; {moved} machine-specific rule(s) moved to .claude/settings.local.json"
            if moved
            else ""
        )
        if rep._write_or_stage(path, json.dumps(existing, indent=2) + "\n"):
            rep.merged.append(
                f"{rep.rel(path)} (+{counts[0]} entries{extra}; nothing else removed)"
            )
    else:
        rep.same += 1


# ---------- dependabot, codeowners ----------


NODE_DEP_KEYS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)
DEPENDABOT_BLOCK = """  - package-ecosystem: "{eco}"
    directory: "{directory}"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
    open-pull-requests-limit: 5
    labels: ["dependencies"]
"""


def package_json_has_deps(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and any(
        isinstance(data.get(k), dict) and data[k] for k in NODE_DEP_KEYS
    )


def dependabot_entries(root: str, projects: list[str]) -> list[tuple[str, str]]:
    """(ecosystem, directory) for every manifest at the root or in a project: pip for a pyproject.toml, setup.py,
    Pipfile or requirements*.txt, npm only for a package.json that lists dependencies, gomod, cargo."""
    found: list[tuple[str, str]] = []
    for proj in dict.fromkeys(["."] + [p for p in projects if p not in (".", "")]):
        base = os.path.join(root, proj)
        directory = "/" if proj == "." else "/" + proj.strip("/")
        try:
            names = os.listdir(base)
        except OSError:
            continue
        if any(n in names for n in ("pyproject.toml", "setup.py", "Pipfile")) or any(
            re.fullmatch(r"requirements.*\.txt", n) for n in names
        ):
            found.append(("pip", directory))
        if "package.json" in names and package_json_has_deps(
            os.path.join(base, "package.json")
        ):
            found.append(("npm", directory))
        if "go.mod" in names:
            found.append(("gomod", directory))
        if "Cargo.toml" in names:
            found.append(("cargo", directory))
    return found


def fill_dependabot(text: str, entries: list[tuple[str, str]]) -> str:
    """Replace the __DEPENDABOT_ENTRIES__ line with one block per entry and drop the template's explanatory header."""
    blocks = "".join(DEPENDABOT_BLOCK.format(eco=e, directory=d) for e, d in entries)
    text = re.sub(
        r"^[ \t]*# __DEPENDABOT_ENTRIES__[^\n]*\n", lambda _m: blocks, text, flags=re.MULTILINE
    )
    _, sep, rest = text.partition("version: 2")
    header = "# Dependabot version updates: one entry per directory that holds a manifest, plus github-actions.\n"
    return header + sep + rest if sep else text


# ---------- the plugin ref the gate pins ----------

SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _git_out(*args: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def installed_commit(plugin: str) -> str:
    """The commit this plugin was installed from: Claude Code's installed_plugins.json, else the checkout's HEAD
    when a remote branch contains it (a commit only on this machine cannot be fetched by CI)."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    try:
        with open(os.path.join(cfg, "plugins", "installed_plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins", {})
        for entries in plugins.values():
            for e in entries if isinstance(entries, list) else []:
                if (
                    isinstance(e, dict)
                    and os.path.realpath(str(e.get("installPath", ""))) == os.path.realpath(plugin)
                    and SHA40.match(str(e.get("gitCommitSha", "")))
                ):
                    return str(e["gitCommitSha"])
    except (OSError, ValueError, AttributeError):
        pass
    head = _git_out("rev-parse", "HEAD", cwd=plugin)
    if SHA40.match(head) and _git_out("branch", "-r", "--contains", head, cwd=plugin):
        return head
    return ""


def remote_sha(repo: str, ref: str) -> str:
    """The commit a tag or branch of owner/repo names, by `git ls-remote` (an annotated tag's peeled commit)."""
    out = _git_out(
        "ls-remote", f"https://github.com/{repo}.git", ref, f"{ref}^{{}}", "refs/tags/" + ref + "^{}"
    )
    found = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if SHA40.match(sha):
            found[name] = sha
    for name in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}", ref):
        if name in found:
            return found[name]
    return ""


def resolve_pin(plugin: str, repo: str, ref: str | None) -> tuple[str, str]:
    """(ref to write, warning). The gate fails unless the ref is a 40-character commit SHA, so a SHA is resolved;
    when none can be, the best name is written with a warning that says how to pin it."""
    if ref and SHA40.match(ref):
        return ref, ""
    sha = remote_sha(repo, ref) if ref else installed_commit(plugin)
    if sha:
        return sha, ""
    name = ref or "main"
    return name, (
        f"warning: could not resolve {name!r} to a commit SHA, and the gate fails on anything else; "
        "rerun with --marketplace-ref <40-character sha> (git ls-remote https://github.com/"
        f"{repo}.git {name})"
    )


# ---------- rendering ----------

ROUTE_DEPS = re.compile(
    r"(express|fastify|koa|hono|@nestjs/core|\"next\"|@hapi/hapi|gin-gonic/gin|labstack/echo|go-chi/chi|gorilla/mux"
    r"|gofiber/fiber|axum|actix-web|rocket|warp|tide)"
)
MIGRATION_DIRS = ("alembic", "migrations", "db/migrate", "prisma/migrations", "app/migrations")


def _project_dirs(root: str, projects: list[str]) -> list[str]:
    return [
        os.path.join(root, p)
        for p in dict.fromkeys(["."] + [q for q in projects if q not in (".", "")])
    ]


def detect_routes(root: str, stack: str, projects: list[str]) -> bool:
    """True when the repository serves HTTP: a route in Python source, or a web framework in the manifest."""
    if stack == "python":
        return has_http_routes(root)
    name = {"node": "package.json", "go": "go.mod", "rust": "Cargo.toml"}.get(stack, "")
    for base in _project_dirs(root, projects):
        try:
            with open(os.path.join(base, name), encoding="utf-8", errors="replace") as fh:
                if ROUTE_DEPS.search(fh.read(200000)):
                    return True
        except OSError:
            continue
    return False


def detect_migrations(root: str, projects: list[str]) -> bool:
    """True when a migrations directory or alembic.ini exists at the root or in a project."""
    return any(
        os.path.exists(os.path.join(base, "alembic.ini"))
        or any(os.path.isdir(os.path.join(base, d)) for d in MIGRATION_DIRS)
        for base in _project_dirs(root, projects)
    )


def render_template(text: str, conds: dict[str, bool], values: dict[str, str]) -> str:
    """Resolve a template for one repository. `<!-- sdlc:if X -->...<!-- sdlc:end -->` keeps its body when X is true
    and is dropped when not (the markers go either way); `sdlc: REC-nn` provenance comments and `(REC-nn)`
    parentheticals are stripped; every token in `values` (`{stack}`, `__APP_DB__`) is replaced."""

    def block(m: re.Match) -> str:
        return m.group("body") if conds.get(m.group("name"), False) else ""

    # A marker alone on its line takes the line with it; an inline pair leaves the surrounding text.
    text = re.sub(
        r"^[ \t]*<!-- sdlc:if (?P<name>\w+) -->[ \t]*\n(?P<body>.*?)^[ \t]*<!-- sdlc:end -->[ \t]*\n",
        block,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    text = re.sub(
        r"<!-- sdlc:if (?P<name>\w+) -->(?P<body>.*?)<!-- sdlc:end -->",
        block,
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"^[ \t]*(?:<!--|--|#)[ \t]*sdlc:[ \t]*REC-[^\n]*\n?", "", text, flags=re.MULTILINE
    )
    text = re.sub(r" \(REC-[\w.,\- ]+\)", "", text)
    for token, val in values.items():
        text = text.replace(token, val)
    return text.rstrip("\n") + "\n"


def check_owners(arg: str) -> tuple[list[str], str]:
    handles = [h.strip() for h in arg.split(",") if h.strip()]
    bad = [h for h in handles if not HANDLE.match(h) or PLACEHOLDER_HANDLE.match(h)]
    if bad:
        return (
            [],
            f"refusing owner handle(s) {', '.join(bad)}: each must be a real @user or @org/team, not a placeholder",
        )
    distinct: dict[str, str] = {}
    for h in (
        handles
    ):  # GitHub handles are case-insensitive: @Alice and @alice are one person
        distinct.setdefault(h.lower(), h)
    if len(distinct) < 2:
        return (
            [],
            "--codeowners needs at least two distinct handles (compared case-insensitively), so a PR has a reviewer who is not its author",
        )
    return list(distinct.values()), ""


def baseline_by_check(path: str) -> str | None:
    """`baseline by check: N key(s), M violation(s); dead 3, lint 9` from a v2 baseline, or None if unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            counts = json.load(fh).get("counts")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(counts, dict):
        return None
    by: dict[str, int] = {}
    for key, n in counts.items():
        if isinstance(key, str) and isinstance(n, int):
            prefix = key.split("|", 1)[0]
            by[prefix] = by.get(prefix, 0) + n
    parts = ", ".join(
        f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return f"baseline by check: {len(counts)} key(s), {sum(by.values())} violation(s); {parts or 'none'}"


def missing_baseline_tools(plugin: str, config: dict, root: str) -> list[str]:
    """MISSING TOOL lines for the lint and type commands the baseline is recorded with.

    ratchet.py reads a missing `python3 -m ruff` as "no findings", so the check is made here, with the
    same logic the SessionStart hook uses to print MISSING TOOL lines."""
    import importlib.util

    path = os.path.join(plugin, "hooks", "session_start.py")
    try:
        spec = importlib.util.spec_from_file_location("sdlc_session_start", path)
        if spec is None or spec.loader is None:
            return []
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        commands = {
            k: v
            for k, v in (config.get("commands") or {}).items()
            if k in ("lint_report", "typecheck")
        }
        return list(mod.missing_tools({"commands": commands}, root))
    except Exception:  # noqa: BLE001 - the check is a safeguard; the ratchet's own --require-tools still runs
        return []


def render_codeowners(text: str, handles: list[str]) -> str:
    """GitHub counts an approval from ANY ONE owner on a CODEOWNERS line, so a line with the reviewers alone
    would leave the first owner with nobody who can approve their own PR. Single-owner reviewer lines get
    every owner; lines that name the security owner too keep that pair (one approval is what counts)."""
    reviewers = " ".join([handles[0]] + handles[2:])
    everyone = " ".join([handles[0], handles[1]] + handles[2:])
    text = re.sub(
        r"^# install\.sh refuses these placeholders.*\n",
        "# Owners set by init_project.py --codeowners; change them by pull request.\n",
        text,
        flags=re.MULTILINE,
    )
    text = text.replace(
        "@your-org/reviewers @your-org/security-owner", f"{reviewers} {handles[1]}"
    )
    return text.replace("@your-org/reviewers", everyone).replace(
        "@your-org/security-owner", handles[1]
    )


# ---------- main ----------


def usage() -> str:
    return (__doc__ or "").split("Usage:", 1)[1].split("sdlc:")[0].strip()


def parse(argv: list[str]) -> dict | None:
    o: dict = {
        "root": None,
        "stack": None,
        "repo": DEFAULT_REPO,
        "ref": None,
        "no_ci": False,
        "dry": False,
        "force": False,
        "upgrade": False,
        "owners": None,
        "cleanup": 30,
        "partial": False,
        "headless": False,
        "graph": "on",
        "graph_provider": "local",
        "graph_given": False,
        "project": [],
    }
    flags = {
        "--headless": "headless",
        "--no-ci": "no_ci",
        "--dry-run": "dry",
        "--force": "force",
        "--upgrade": "upgrade",
        "--allow-partial-baseline": "partial",
    }
    vals = {
        "--root": "root",
        "--stack": "stack",
        "--marketplace-repo": "repo",
        "--marketplace-ref": "ref",
        "--codeowners": "owners",
        "--cleanup-days": "cleanup",
        "--graph": "graph",
        "--graph-provider": "graph_provider",
        "--project": "project",
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in flags:
            o[flags[a]] = True
            i += 1
        elif a in vals:
            if i + 1 >= len(argv):
                print(f"{a} needs a value", file=sys.stderr)
                return None
            if a == "--project":
                o["project"].append(argv[i + 1].strip("/"))
            else:
                o[vals[a]] = argv[i + 1]
            if a in ("--graph", "--graph-provider"):
                o["graph_given"] = True
            i += 2
        else:
            print(f"unknown argument {a}", file=sys.stderr)
            return None
    if o["graph"] not in ("on", "off"):
        print("--graph takes on or off", file=sys.stderr)
        return None
    if o["graph_provider"] not in cl.GRAPH_PROVIDERS:
        print(
            f"--graph-provider takes one of {', '.join(cl.GRAPH_PROVIDERS)}",
            file=sys.stderr,
        )
        return None
    try:
        o["cleanup"] = max(1, int(o["cleanup"]))
    except ValueError:
        print("--cleanup-days needs a whole number", file=sys.stderr)
        return None
    return o


def migrate_graph(plugin: str, root: str, shown: dict) -> None:
    """With the graph on, bring a reachable database up to the schema this plugin ships (graph.py migrate; every DDL file
    is idempotent). An unreachable or off graph is a line, never a failure. SDLC_INIT_NO_MIGRATE=1 skips the step."""
    block, _ = cl.graph_block(shown.get("graph"))
    if not block["enabled"] or os.environ.get("SDLC_INIT_NO_MIGRATE") == "1":
        return
    proc = cl.run(
        [
            sys.executable,
            "-I",
            os.path.join(plugin, "scripts", "graph.py"),
            "--root",
            root,
            "migrate",
        ],
        cwd=root,
    )
    for line in (proc.stdout or proc.stderr).strip().splitlines():
        print(f"graph: {line}")


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print("Usage: " + usage())
        return 0
    o = parse(argv)
    if o is None:
        print("Usage: " + usage(), file=sys.stderr)
        return 2
    owners: list[str] = []
    if o["owners"] is not None:
        owners, err = check_owners(o["owners"])
        if err:
            print(err, file=sys.stderr)
            return 2
    root = cl.project_root(o["root"])
    plugin = cl.plugin_root()
    tpl = os.path.join(plugin, "templates")
    stack, repo = o["stack"], o["repo"]
    dry, upgrade = o["dry"], o["upgrade"]

    detected = detect_stack(root)
    projects: list[str] = []
    below: dict[str, list[str]] = {}
    if o["project"]:
        for p in o["project"]:
            if not os.path.isdir(os.path.join(root, p)):
                print(f"--project {p}: no such directory below {root}")
                return 2
        for p in o["project"]:
            for st in detect_stack(os.path.join(root, p)):
                below.setdefault(st, []).append(p)
        detected = sorted(below)
    elif not detected:
        below = find_projects(root)
        detected = sorted(below)
    if not stack:
        kept: dict = {}
        if upgrade and os.path.exists(os.path.join(root, cl.CONFIG_REL)):
            try:
                with open(os.path.join(root, cl.CONFIG_REL), encoding="utf-8") as fh:
                    kept = json.load(fh)
                stack = kept.get("stack")
            except (ValueError, OSError):
                stack = None
            if stack and isinstance(kept.get("projects"), list):
                projects = [str(p) for p in kept["projects"]]
        if not stack:
            if len(detected) == 1:
                stack = detected[0]
            elif not detected:
                print(
                    "no stack marker found (pyproject.toml, package.json, go.mod, Cargo.toml) at the root or up to two directories down; pass --stack, or --project DIR for the project that holds it"
                )
                return 2
            else:
                where = "; ".join(
                    f"{st}: {', '.join(below.get(st) or ['.'])}" for st in detected
                )
                print(
                    f"several stacks detected ({where}); pass --stack <one of them> (one config per repo; run again in a subdirectory for the other)"
                )
                return 2
    if below and not projects:
        projects = below.get(stack or "", [])
    if stack not in cl.STACKS:
        print(f"unknown stack {stack}; one of {', '.join(cl.STACKS)}")
        return 2

    defaults = cl.stack_defaults(stack)
    config = build_config(
        root,
        stack,
        defaults,
        repo,
        graph_config(o["graph"] == "on", o["graph_provider"]),
        projects,
    )
    frontend = fk.detect(
        root
    )  # package.json (root or a workspace package) depends on foldkit
    # --upgrade never adds it: new checkers without a re-baseline would report today's findings as new
    if frontend and not (upgrade and os.path.exists(os.path.join(root, cl.CONFIG_REL))):
        config["frontend"] = frontend
        high = list(
            config.get("tier_high_paths") or cl.TIER_HIGH_DEFAULT
        )  # a list replaces the defaults, so they are copied in; the auth and payment views join them
        config["tier_high_paths"] = high + [
            g for g in fk.tier_high_globs(frontend) if g not in high
        ]
        config["checkers"] = list(config.get("checkers") or []) + [
            n for n in fk.CHECKERS if n not in (config.get("checkers") or [])
        ]
    rep = Report(root, dry, o["force"], upgrade)
    cfg_path = os.path.join(root, cl.CONFIG_REL)
    # Without --upgrade or --force an existing config is the user's and is kept as it is, so what init reports
    # about the frontend must come from that file, not from the config it would have written.
    cfg_kept = os.path.exists(cfg_path) and not upgrade and not o["force"]

    # Config: created if absent; with --upgrade new keys are added, present values are never changed.
    if os.path.exists(cfg_path) and upgrade:
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                existing = json.load(fh)
            added = merge_missing_keys(existing, config)
            if isinstance(existing, dict) and isinstance(existing.get("pr_size"), dict):
                size = existing["pr_size"]
                have = (
                    size.get("exclude")
                    if isinstance(size.get("exclude"), list)
                    else None
                )
                if have is not None:
                    more = [g for g in GENERATED_PR_SIZE_EXCLUDE if g not in have]
                    if more:
                        have.extend(more)
                        added.append("pr_size.exclude (+" + ", ".join(more) + ")")
            if added:
                rep.merged.append(
                    f"{cl.CONFIG_REL.replace(os.sep, '/')} (+keys: {', '.join(added)})"
                )
                rep._write(cfg_path, json.dumps(existing, indent=2) + "\n")
            else:
                rep.same += 1
            config = existing if isinstance(existing, dict) else config
        except (ValueError, OSError) as exc:
            rep.kept.append(f"{cl.CONFIG_REL} (not valid JSON: {exc})")
    else:
        rep.put(cfg_path, json.dumps(config, indent=2) + "\n", owned=not o["force"])

    saved_repo = config.get("marketplace_repo", "")
    if repo.startswith("<") and saved_repo and not saved_repo.startswith("<"):
        repo = saved_repo  # re-runs and --upgrade keep the repo chosen at first init

    kept_projects = config.get("projects")
    proj_dirs = [str(p) for p in kept_projects] if isinstance(kept_projects, list) else projects
    tier_paths = [
        str(g) for g in (config.get("tier_high_paths") or cl.TIER_HIGH_DEFAULT)
    ]
    conds = {
        "routes": detect_routes(root, stack, proj_dirs),
        "migrate": detect_migrations(root, proj_dirs),
        "codeowners": bool(owners)
        or os.path.exists(os.path.join(root, ".github", "CODEOWNERS")),
    }
    app_db = re.sub(r"\W+", "_", os.path.basename(root).lower()).strip("_") or "app"
    tokens = {
        "{stack}": stack,
        "{test_command}": str(config.get("commands", {}).get("test", "")),
        "{tier_high_paths}": ", ".join(f"`{g}`" for g in tier_paths),
        "__TIER_HIGH_PATHS__": "\n".join(f"- `{g}`" for g in tier_paths),
        "__APP_DB__": f"{app_db}_dev",
    }
    render = lambda t: render_template(t, conds, tokens)

    for name in ("common", stack):
        src = os.path.join(tpl, "rules", f"{name}.md")
        if os.path.exists(src):
            rep.put_file(
                src, os.path.join(root, ".claude", "rules", f"sdlc-{name}.md"), render
            )

    if stack == "python":
        rep.put_file(
            os.path.join(tpl, "public_routes.txt"),
            os.path.join(root, ".claude", "public_routes.txt"),
        )
    rep.put_file(
        os.path.join(tpl, "work-README.md"),
        os.path.join(root, ".claude", "work", "README.md"),
    )
    rep.put_file(
        os.path.join(tpl, "pull_request_template.md"),
        os.path.join(root, ".github", "pull_request_template.md"),
        render,
    )

    # templates/github/** -> .github/ ; CODEOWNERS only with --codeowners; dependabot filtered to the stack.
    gh_src = os.path.join(tpl, "github")
    for dirpath, _, files in sorted(os.walk(gh_src)):
        for fn in sorted(files):
            src = os.path.join(dirpath, fn)
            relp = os.path.relpath(src, gh_src)
            dest = os.path.join(root, ".github", relp)
            if relp == "CODEOWNERS":
                continue
            if relp == "dependabot.yml":
                nested = [d for dirs in find_projects(root).values() for d in dirs]
                entries = dependabot_entries(root, sorted(set(proj_dirs) | set(nested)))
                rep.put_file(src, dest, lambda t: fill_dependabot(t, entries))
            else:
                rep.put_file(src, dest, render)
    co_dest = os.path.join(root, ".github", "CODEOWNERS")
    co_src = os.path.join(gh_src, "CODEOWNERS")
    if owners and os.path.exists(co_src):
        rep.put_file(co_src, co_dest, lambda t: render_codeowners(t, owners))
    elif os.path.exists(co_dest):
        with open(co_dest, encoding="utf-8", errors="replace") as fh:
            if re.search(r"@(your-org|app-reviewers|app-security-owner)", fh.read()):
                print(
                    "warning: .github/CODEOWNERS still holds placeholder handles; rerun with --codeowners '@a,@b'",
                    file=sys.stderr,
                )
    else:
        rep.kept.append(
            ".github/CODEOWNERS (not written: pass --codeowners '@a,@b' with two real owners)"
        )

    mcp_src = os.path.join(tpl, "mcp.json")
    if os.path.exists(mcp_src):
        mcp_dest = os.path.join(root, ".mcp.json")
        if os.path.exists(mcp_dest):
            rep.same += (
                1  # present: yours; the policy in docs/mcp-policy.md governs additions
            )
        else:
            rep.put_file(mcp_src, mcp_dest)

    # docs: templates/docs/** -> docs/, with REVIEW.md at the root and QUARANTINE.md under tests/.
    docs_src = os.path.join(tpl, "docs")
    for dirpath, _, files in sorted(os.walk(docs_src)):
        for fn in sorted(files):
            src = os.path.join(dirpath, fn)
            relp = os.path.relpath(src, docs_src)
            if relp == "REVIEW.md":
                dest = os.path.join(root, "REVIEW.md")
            elif relp == os.path.join("tests", "QUARANTINE.md"):
                dest = os.path.join(root, "tests", "QUARANTINE.md")
            else:
                dest = os.path.join(root, "docs", relp)
            rep.put_file(src, dest, render)

    if not o["no_ci"]:
        ref, ref_warning = resolve_pin(plugin, repo, o["ref"])
        if ref_warning:
            print(ref_warning, file=sys.stderr)
        cmds = config.get("commands") or {}
        node_ci = (
            node_ci_values(
                root, str(cmds.get("setup") or ""), str(cmds.get("test") or "")
            )
            if stack == "node"
            else {
                "setup": "",
                "cache": "",
                "lockfile": "",
                "install": "",
                "test": "",
                "audit": "",
            }
        )

        def fill_ci(t: str, repo_name: str, ref_name: str) -> str:
            t = t.replace("__MARKETPLACE_REPO__", repo_name).replace(
                "__MARKETPLACE_REF__", ref_name
            )
            if stack == "python":
                t = fill_python_ci(t, root, config)
            return render(
                t.replace("__NODE_PM_SETUP__", node_ci["setup"])
                .replace("__NODE_CACHE__", node_ci["cache"])
                .replace("__NODE_LOCKFILE__", node_ci["lockfile"])
                .replace("__NODE_INSTALL__", node_ci["install"])
                .replace("__NODE_TEST__", node_ci["test"])
                .replace("__NODE_AUDIT__", node_ci["audit"])
            )

        subst = lambda t: fill_ci(t, repo, ref)
        # sdlc-evidence.yml posts the gate's result to the ledger from a workflow_run job (the only job that holds
        # id-token: write for it); the ledger trusts it by this file name, so it is installed under its own name.
        for src_name, dest_name in (
            (f"{stack}.yml", "sdlc-gate.yml"),
            ("claude-review.yml", "sdlc-claude-review.yml"),
            ("sdlc-evidence.yml", "sdlc-evidence.yml"),
        ):
            src = os.path.join(tpl, "ci", src_name)
            if not os.path.exists(src):
                continue
            dest = os.path.join(root, ".github", "workflows", dest_name)
            # Rerun with --marketplace-repo: replace the default repository a first run wrote, but only in
            # a file that is otherwise the template (an edited gate file is never rewritten).
            if os.path.exists(dest) and repo != DEFAULT_REPO:
                with open(src, encoding="utf-8") as fh:
                    raw = fh.read()
                with open(dest, encoding="utf-8", errors="replace") as fh:
                    old = fh.read()
                had = re.search(r"^\s+ref: (\S+)$", old, flags=re.MULTILINE)
                as_default = fill_ci(raw, DEFAULT_REPO, had.group(1) if had else ref)
                if DEFAULT_REPO in old and old == as_default:
                    rep.merged.append(
                        f".github/workflows/{dest_name} (marketplace repo set to {repo})"
                    )
                    rep._write(dest, subst(raw))
                    continue
            rep.put_file(src, dest, subst)

    merge_settings(
        rep,
        os.path.join(tpl, "settings.project.json"),
        stack,
        config,
        root,
        o["cleanup"],
        repo,
    )

    # Version marker: created now; moved forward only by --upgrade or --force.
    try:
        with open(
            os.path.join(plugin, ".claude-plugin", "plugin.json"), encoding="utf-8"
        ) as fh:
            version = json.load(fh).get("version", "")
    except (OSError, ValueError):
        version = ""
    if version:
        vpath = os.path.join(root, ".claude", "KIT_VERSION")
        old_v = (
            open(vpath, encoding="utf-8").read().strip()
            if os.path.exists(vpath)
            else None
        )
        if old_v is None:
            rep.created.append(".claude/KIT_VERSION")
            rep._write(vpath, version + "\n")
        elif old_v != version and (upgrade or o["force"]):
            rep.merged.append(f".claude/KIT_VERSION ({old_v} -> {version})")
            rep._write(vpath, version + "\n")
        elif old_v != version:
            rep.kept.append(
                f".claude/KIT_VERSION (is {old_v}; this plugin is {version}; --upgrade moves it)"
            )
        else:
            rep.same += 1

    # CLAUDE.md: the working agreement lives between markers; an existing block is never rewritten here.
    with open(os.path.join(tpl, "CLAUDE.working-agreement.md"), encoding="utf-8") as fh:
        agreement = render(fh.read()).rstrip()
    claude_md = os.path.join(root, "CLAUDE.md")
    existing = ""
    if os.path.exists(claude_md):
        with open(claude_md, encoding="utf-8") as fh:
            existing = fh.read()
    if MARKER_START not in existing:
        rep.created.append("CLAUDE.md (appended)")
        if not dry:
            with open(claude_md, "a", encoding="utf-8") as fh:
                fh.write(
                    ("\n\n" if existing and not existing.endswith("\n\n") else "")
                    + f"{MARKER_START}\n{agreement}\n{MARKER_END}\n"
                )
    else:
        block = existing.split(MARKER_START, 1)[1].split(MARKER_END, 1)[0].strip()
        if block == agreement:
            rep.same += 1
        elif upgrade:
            rep._diff(claude_md, block + "\n", agreement + "\n")
            rep.new.append(
                "CLAUDE.working-agreement.md.new (replace the block in CLAUDE.md by hand)"
            )
            rep._write(
                os.path.join(root, "CLAUDE.working-agreement.md.new"), agreement + "\n"
            )
        else:
            rep.kept.append("CLAUDE.md (agreement already present)")

    gitignore = os.path.join(root, ".gitignore")
    present = ""
    if os.path.exists(gitignore):
        with open(gitignore, encoding="utf-8") as fh:
            present = fh.read()
    missing = [ln for ln in GITIGNORE_LINES if ln not in present.splitlines()]
    if missing:
        rep.merged.append(f".gitignore (+{len(missing)} lines)")
        if not dry:
            with open(gitignore, "a", encoding="utf-8") as fh:
                fh.write(
                    ("" if present.endswith("\n") or not present else "\n")
                    + "\n".join(missing)
                    + "\n"
                )

    shown = config
    if cfg_kept:
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                shown = json.load(fh)
        except (ValueError, OSError):
            shown = {}
        shown = shown if isinstance(shown, dict) else {}
    kept_cmds = shown.get("commands") if isinstance(shown.get("commands"), dict) else {}

    def detected_cmd(name: str, empty: str = "") -> str:
        """The command init detected, and what the kept config says when that differs (the kept file wins)."""
        got = config.get("commands", {}).get(name) or empty
        if cfg_kept and (kept_cmds.get(name) or empty) != got:
            return f"{got}  (detected; your {cl.CONFIG_REL.replace(os.sep, '/')} keeps {kept_cmds.get(name) or '(none)'})"
        return got

    print(
        f"stack: {stack}  (detected: {', '.join(detected) or 'none'})"
        + ("  [upgrade]" if upgrade else "")
        + ("  [dry run]" if dry else "")
    )
    rbase = os.path.join(root, projects[0]) if projects else root
    print("app paths:", config.get("paths", {}).get("app"))
    if projects:
        print(
            f"projects: {', '.join(projects)}  (no stack marker at the root; found below it. Paths come from these; "
            "commands use the first. --project DIR picks others; no install command is written)"
        )
    shown_paths = {} if cfg_kept else (config.get("paths") or {})
    gone = missing_paths(
        root, list(shown_paths.get("app") or []) + list(shown_paths.get("tests") or [])
    )
    if gone:
        print(
            f"note: these configured paths match nothing yet: {', '.join(gone)} (the stack's defaults; edit "
            f"{cl.CONFIG_REL.replace(os.sep, '/')} when your code lives elsewhere)"
        )
    runner, runner_why = detect_python_runner(rbase) if stack == "python" else ("", "")
    if stack == "python":
        print(
            f"python runner: {runner or 'none (python3 -m)'}"
            + (f"  ({runner_why})" if runner else "")
        )
        print(f"test command: {detected_cmd('test')}")
    if stack == "node":
        nrunner, nwhy = detect_node_runner(rbase)
        print(f"node runner: {nrunner}  ({nwhy})")
        for note in apply_node_runner(
            cl.stack_defaults(stack)["commands"], nrunner, node_scripts(rbase)
        )[1]:
            print(f"  {note}")
        print(f"test command: {detected_cmd('test')}")
    print(f"setup command: {detected_cmd('setup', '(none)')}")
    shown_graph, _ = cl.graph_block(shown.get("graph"))
    if cfg_kept and "graph" in shown:
        print(
            f"graph: {'on' if shown_graph['enabled'] else 'off'}, provider {shown_graph['provider']}, url_env "
            f"{shown_graph['url_env']}  (kept from {cl.CONFIG_REL.replace(os.sep, '/')}; /factory:graph on|off flips it)"
        )
    else:
        g = config.get("graph") or graph_config()
        how = "as answered" if o["graph_given"] else "defaults: on, local"
        print(
            f"graph: {'on' if g['enabled'] else 'off'}, provider {g['provider']}, url_env {g['url_env']}  ({how}; "
            "the questions: code graph and ledger on or off, and where the database lives: local, neon, aws or spanner)"
        )
        print(f'  {cl.CONFIG_REL.replace(os.sep, "/")} "graph": {json.dumps(g)}')
        print(
            f"  the connection string is never written to {cl.CONFIG_REL.replace(os.sep, '/')}: it is read from "
            f"${g['url_env']}, .claude/settings.local.json env or .env (/factory:graph provision {g['provider']} stores it)"
        )
    for p in rep.created:
        print("  + " + p)
    for p in rep.merged:
        print("  ~ " + p)
    for p in rep.new:
        print(
            "  ! " + p + "   (differs from the template; compare, then keep or delete)"
        )
    for p in rep.kept:
        print("  = kept  " + p)
    if upgrade and rep.diffs:
        print("\nWhat would change (template vs your file):")
        for name, text in rep.diffs:
            lines = text.splitlines()
            print(
                "\n".join(lines[:40])
                + (f"\n... ({len(lines) - 40} more lines)" if len(lines) > 40 else "")
            )
    print(
        f"\nsummary: {len(rep.created)} created, {len(rep.merged)} merged, {len(rep.new)} .new, {len(rep.kept)} kept, {rep.same} unchanged"
        + (
            f", runner {runner or 'python3 -m'}"
            if stack == "python"
            else f", runner {detect_node_runner(rbase)[0]}"
            if stack == "node"
            else ""
        )
    )
    if len(rep.settings_paths) == 2:
        print(
            f"settings: {rep.settings_paths[0]} holds the rules a team can commit; {rep.settings_paths[1]} holds the rules that "
            "embed this machine's plugin path (gitignored, never commit it); run_headless.sh merges both for --settings"
        )
    elif rep.settings_paths:
        print(f"settings: {rep.settings_paths[0]}")
    for want, staged in rep.staged:
        print(
            f"settings: {want} is not writable here (Claude Code's sandbox protects a settings file it loaded when the "
            f"session started); the merged file is staged at {staged}. A human runs: cp {staged} {want}"
        )
    if rep.worktrees:
        print(
            "sandbox: worktrees outside this checkout were added to allowWrite in the local settings file: "
            + ", ".join(rep.worktrees)
        )
    print(
        "worktrees: an agent cannot create a checkout under the sandbox (checking out a branch writes .mcp.json and other "
        "agent-config files, which it forbids). A human, or a script outside the sandbox, runs "
        "`git worktree add .claude/worktrees/<name> -b <branch>` and then `init_project.py --upgrade` to add the path to allowWrite."
    )
    branch = current_branch(root)
    protected = (
        (shown.get("paths") or {}).get("protected_branches")
        if isinstance(shown.get("paths"), dict)
        else None
    )
    if branch and branch in (
        protected if isinstance(protected, list) and protected else ["main", "master"]
    ):
        print(
            f"note: you are on {branch}, a protected branch: the edit guard refuses writes there. Run `git switch -c chore/onboarding` "
            "(chore/onboarding-2 if that name is taken) before committing what init wrote, then open a pull request from it; "
            "/factory:init does this first on its own."
        )
    fe_cfg = shown.get("frontend") if isinstance(shown.get("frontend"), dict) else {}
    if fe_cfg.get("framework") == fk.FRAMEWORK:
        fe_on = [n for n in fk.CHECKERS if n in (shown.get("checkers") or [])]
        print(
            f"frontend: foldkit  (programs {', '.join(fe_cfg.get('programs') or [])}; api table {fe_cfg.get('api_table')}; "
            f"oxlint {'on' if fe_cfg.get('oxlint') else 'off'}; checkers {' '.join(fe_on) or 'none'})"
        )
    elif frontend:
        why = (
            "by --upgrade"
            if upgrade
            else f"({cl.CONFIG_REL.replace(os.sep, '/')} exists and was kept)"
        )
        print(
            f'frontend: foldkit detected, not added {why}; add "frontend": {json.dumps(frontend)} and {" ".join(fk.CHECKERS)} '
            "to .claude/sdlc.json, then a human runs ratchet.py --update (see docs/frontend-contracts.md)"
        )
    if dry:
        print("dry run: nothing written")
        return 0

    migrate_graph(plugin, root, shown)

    baseline = os.path.join(root, ".claude", "baseline.json")
    setup = kept_cmds.get("setup") or config.get("commands", {}).get("setup") or ""
    install = (
        f"Install them (`{setup}` installs this repo's tools)"
        if setup
        else "Install them"
    )
    if upgrade:
        print("baseline: untouched (--upgrade never runs ratchet.py --update)")
    elif os.path.exists(baseline):
        print(
            "baseline: kept (.claude/baseline.json exists; a human re-baselines with ratchet.py --update)"
        )
    else:
        if not o["partial"]:
            gone = missing_baseline_tools(plugin, config, root)
            if gone:
                for line in gone:
                    print(line, file=sys.stderr)
                print(
                    "init: files are written but no baseline was recorded, because a baseline taken without these tools makes the first CI run report every existing finding as new.",
                    file=sys.stderr,
                )
                print(
                    f"  {install} and rerun this command, or pass --allow-partial-baseline (CI stays red until a human runs ratchet.py --update with the tools installed).",
                    file=sys.stderr,
                )
                return 2
        args = [
            sys.executable,
            "-I",
            os.path.join(plugin, "scripts", "ratchet.py"),
            "--root",
            root,
            "--update",
        ]
        if not o["partial"]:
            args.append("--require-tools")
        proc = cl.run(args, cwd=root)
        refused = proc.returncode != 0 and re.search(
            r"PermissionError|Operation not permitted|Read-only file system",
            proc.stderr + proc.stdout,
        )
        if refused:
            # the sandbox forbids writing the baseline by design: say what a human runs, not a traceback
            human = " ".join(["python3", "-I"] + [shlex.quote(a) for a in args[2:]])
            print(
                "baseline: not recorded; the sandbox forbids this session from writing it (by design, a human owns it). "
                "A human runs, outside the sandbox:"
            )
            print(f"  {human}")
            print("  then commits the file. Everything else init writes is in place.")
            return 0
        if proc.returncode == 2 and "required tool(s) missing" in proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
            print(
                f"init: files are written but no baseline was recorded; {install[0].lower() + install[1:]} and rerun, or pass --allow-partial-baseline.",
                file=sys.stderr,
            )
            return 2
        print(proc.stdout.strip() or proc.stderr.strip())
        for line in proc.stderr.splitlines():
            if line.startswith("note:"):
                print("  " + line)
        if o["partial"]:
            print(
                "warning: baseline recorded with --allow-partial-baseline; a missing ruff, mypy or other tool means CI will report its findings as new"
            )
    if not upgrade:
        line = baseline_by_check(baseline)
        if line:
            print(line)
    if any(".mcp.json" in x for x in rep.created) or any(
        "settings.json" in x for x in rep.created + rep.merged
    ):
        print(
            "note: the merged settings turn the sandbox on (macOS and Linux only; failIfUnavailable is true). Read docs/permission-modes.md before the team's first run."
        )
    if owners:
        print(
            f"CODEOWNERS: GitHub counts an approval from ANY ONE owner on a line, not all of them. Lines that list only the reviewers got {' '.join(owners)} so each can approve the other's PRs;"
        )
        print(
            "  to require a second person on the auth and data-model paths use a team with a required count or a ruleset (see docs/pack-governance.md)."
        )
    print(
        "\nNext: read the files above, adjust .claude/sdlc.json (app/test paths, commands, protected paths), then commit them."
    )
    print(
        "Then /factory:onboard to read the agreement, /factory:audit for the debt, /factory:design <ticket> to start work."
    )
    if repo == DEFAULT_REPO and not o["no_ci"]:
        print(
            f"CI: the gate checks the plugin's scripts out of the public {DEFAULT_REPO} at ref {ref}; rerun with --marketplace-repo owner/repo for a fork"
        )
        print(
            "    (a private marketplace repo also needs a read token: uncomment the `token:` line on the plugin checkout in .github/workflows/sdlc-gate.yml)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
