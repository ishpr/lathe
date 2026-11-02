#!/usr/bin/env python3
"""Initialise a repository for the SDLC working agreement, or upgrade an initialised one.

Detects the stack, writes .claude/sdlc.json from the stack's defaults, and copies the pack's
templates: path-scoped rules, the public-routes file (Python), the PR template, issue templates,
dependabot.yml, docs/**, REVIEW.md, tests/QUARANTINE.md, .mcp.json, the CI gate and the advisory
Claude review. It merges templates/settings.project.json into .claude/settings.json (objects
merge, arrays union, existing scalars win, placeholders are filled from the stack config),
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
(default: the public `ishpr/lathe`); --marketplace-ref REF (default main; pin a release tag or SHA) is
the ref it checks out. Rerunning with --marketplace-repo (a fork or a private mirror) replaces the
default in a gate file you have not edited. A private marketplace repository needs a `token:` on that
checkout (the file has the line, commented).

The baseline is recorded with `ratchet.py --update --require-tools`: if the stack's lint or type
tools are missing, init stops with exit 2 after writing the files and names what to install, because a
baseline recorded without them makes the first CI run report every existing finding as new.
--allow-partial-baseline records it anyway (CI stays red until a human re-baselines with the tools).

--headless is accepted and ignored: this script never asks anything, so `/sdlc:init --headless` can pass
its arguments through unchanged. The skill, not the script, changes behaviour when it is set.

Usage: init_project.py [--root DIR] [--stack python|node|go|rust] [--marketplace-repo owner/repo]
                       [--marketplace-ref REF] [--headless] [--no-ci] [--dry-run] [--force] [--upgrade]
                       [--codeowners '@a,@b'] [--allow-partial-baseline] [--cleanup-days N] [--help]
Exit: 0 ok, 2 misuse or refusal.

sdlc: REC-38, REC-43, REC-137.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402

MARKERS = {
    "python": ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"],
    "node": ["package.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
}
MARKER_START = "<!-- sdlc:start -->"
MARKER_END = "<!-- sdlc:end -->"

GITIGNORE_LINES = [
    ".claude/work/last_verify.json",
    ".claude/work/.stop_nudged",
    ".claude/work/blocks.jsonl",
    ".claude/work/security-*.json",
    ".claude/work/headless/",
    ".claude/settings.local.json",
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
PY_TOOL_CMD = re.compile(r"^python3 -m (pytest|ruff|mypy)\b")
INSTALL_CMD = {"python": "pip install", "node": "npm install", "go": "go get", "rust": "cargo add"}
PACKAGE_DOMAINS = {
    "python": ["pypi.org", "files.pythonhosted.org"],
    "node": ["registry.npmjs.org"],
    "go": ["proxy.golang.org", "sum.golang.org"],
    "rust": ["crates.io", "index.crates.io", "static.crates.io"],
}
# Files a person edits after init: never replaced, never given a .new, even with --force.
USER_OWNED = {os.path.join("tests", "QUARANTINE.md"), os.path.join(".claude", "public_routes.txt"),
              os.path.join(".claude", "work", "README.md")}
# Exact placeholder strings only, so a real account such as @todoist or @example-corp is accepted.
PLACEHOLDER_HANDLE = re.compile(
    r"(?i)^@(your-org(/.*)?|your-team|app-reviewers|app-security-owner|owner|org/(team|name|reviewers|security-owner)"
    r"|team/(name|reviewers)|placeholder|changeme|example|example/.*|todo)$")
DEFAULT_REPO = "ishpr/lathe"  # the public marketplace; --marketplace-repo names a fork or a mirror
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

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace(os.sep, "/")

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
            text = "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), f"a/{self.rel(path)}", f"b/{self.rel(path)} (template)", n=1))
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

    def put_file(self, src: str, dest: str, transform=None, owned: bool = False) -> None:
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        self.put(dest, transform(content) if transform else content, owned)


# ---------- stack and config ----------

def detect_stack(root: str) -> list[str]:
    return [s for s, files in MARKERS.items() if any(os.path.exists(os.path.join(root, f)) for f in files)]


def detect_python_runner(root: str) -> tuple[str, str]:
    """(runner name, why) for a Python repo: uv.lock or a [tool.uv] table, poetry.lock, pdm.lock; else ('', '')."""
    pyproject = ""
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8", errors="replace") as fh:
            pyproject = fh.read()
    except OSError:
        pass
    for name, _prefix, lock, table in PY_RUNNERS:
        if os.path.isfile(os.path.join(root, lock)):
            return name, lock
        if table and re.search(rf"^\s*\[{re.escape(table)}(\.[^\]]*)?\]", pyproject, re.MULTILINE):
            return name, f"[{table}] in pyproject.toml"
    return "", ""


def apply_python_runner(commands: dict, runner: str) -> dict:
    """The stack table spells tools `python3 -m <tool>`; under a runner the same argument shape follows the runner prefix."""
    prefix = next((p for n, p, _l, _t in PY_RUNNERS if n == runner), "")
    if not prefix:
        return commands
    return {k: PY_TOOL_CMD.sub(lambda m: f"{prefix} {m.group(1)}", v) if isinstance(v, str) else v for k, v in commands.items()}


def detect_app_paths(root: str, stack: str, defaults: list[str]) -> list[str]:
    if stack == "python":
        pkgs = sorted(
            d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d)) and os.path.exists(os.path.join(root, d, "__init__.py"))
            and d not in ("tests", "test", "docs", "scripts")
        )
        if pkgs:
            return [f"{p}/**" for p in pkgs]
    if stack == "node":
        present = [p for p in ("src/**", "app/**", "lib/**", "server/**") if os.path.isdir(os.path.join(root, p.split("/")[0]))]
        if present:
            return present
    return defaults


def build_config(root: str, stack: str, defaults: dict, repo: str) -> dict:
    """Stack defaults first, then the project-specific keys on top."""
    config = {k: v for k, v in defaults.items() if k not in ("stack", "extensions", "paths", "lint_format", "typecheck_format")}
    config["lint_format"] = defaults.get("lint_format", "")
    config["typecheck_format"] = defaults.get("typecheck_format", "")
    config.update({
        "version": 1,
        "stack": stack,
        "paths": {
            "app": detect_app_paths(root, stack, defaults["paths"]["app"]),
            "tests": defaults["paths"]["tests"],
            "protected": [],
            "protected_branches": ["main", "master"],
        },
        "commands": apply_python_runner(dict(defaults["commands"]), detect_python_runner(root)[0]) if stack == "python" else dict(defaults["commands"]),
        "checkers": defaults["checkers"],
        "require_tests": defaults.get("require_tests", True),
        "auth": defaults.get("auth", {}),
        "marketplace_repo": repo,
    })
    if stack == "python":
        # /migrate reads these from the project file; the skill does not see stacks/*.json (S1 report).
        config["commands"].setdefault("migrate_sql", "alembic upgrade {from}:head --sql")
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

def command_prefix(cmd: str) -> str:
    """'python3 -m pytest -q' -> 'python3 -m pytest'; 'npx --no-install eslint --fix {file}' -> 'npx eslint'."""
    toks = [t for t in cmd.split() if not (t.startswith("{") and t.endswith("}"))]
    if toks and toks[0] in ("npx", "pnpm", "yarn"):
        toks = [toks[0]] + [t for i, t in enumerate(toks[1:]) if not (t.startswith("-") and not any(not x.startswith("-") for x in toks[1:][:i]))]
    out = []
    for t in toks:
        if t.startswith("-") and t not in ("-m", "-I"):
            break
        out.append(t)
    return " ".join(out)


def read_dev_urls(root: str) -> dict[str, str]:
    """Dev-only DATABASE_URL and REDIS_URL from .env.example or a compose file; local or compose-service hosts only."""
    found: dict[str, str] = {}
    names = [".env.example", ".env.sample", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for var in ("DATABASE_URL", "REDIS_URL"):
            if var in found:
                continue
            m = re.search(rf"^\s*-?\s*{var}\s*[:=]\s*[\"']?([A-Za-z][A-Za-z0-9+.-]*://[^\s\"']+)", text, re.M)
            if not m or "$" in m.group(1):
                continue
            host = re.sub(r"^[^@/]*://([^@/]*@)?", "", m.group(1)).split("/")[0].split(":")[0]
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


def merge_settings(rep: Report, tpl_path: str, stack: str, config: dict, root: str, cleanup_days: int) -> None:
    if not os.path.exists(tpl_path):
        return
    with open(tpl_path, encoding="utf-8") as fh:
        tpl = json.load(fh)
    cmds = config.get("commands", {})
    urls = read_dev_urls(root)
    values = {
        "test_command": command_prefix(cmds.get("test", "")),
        "lint_command": command_prefix(cmds.get("lint_report") or cmds.get("lint_fix", "")),
        "typecheck_command": command_prefix(cmds.get("typecheck", "")),
        "format_command": command_prefix(cmds.get("format", "")),
        "install_command": INSTALL_CMD.get(stack, ""),
        "package_domains": PACKAGE_DOMAINS.get(stack, []),
        "database_url": urls.get("DATABASE_URL", ""),
        "redis_url": urls.get("REDIS_URL", ""),
        "cleanup_days": cleanup_days,
        "plugin_root": cl.plugin_root(),
    }
    filled = fill_settings(tpl, values)
    path = os.path.join(root, ".claude", "settings.json")
    if not os.path.exists(path):
        rep.created.append(rep.rel(path))
        rep._write(path, json.dumps(filled, indent=2) + "\n")
        return
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if not isinstance(existing, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as exc:
        rep.kept.append(f"{rep.rel(path)} (not valid JSON: {exc}; fix it, then re-run)")
        return
    counts = [0]
    union_merge(existing, filled, counts)
    if counts[0]:
        rep.merged.append(f"{rep.rel(path)} (+{counts[0]} entries; nothing removed)")
        rep._write(path, json.dumps(existing, indent=2) + "\n")
    else:
        rep.same += 1


# ---------- dependabot, codeowners ----------

def filter_dependabot(text: str, stack: str) -> str:
    keep = {ECOSYSTEM.get(stack, ""), "github-actions"}
    lines = text.splitlines(True)
    head: list[str] = []
    blocks: list[list[str]] = []
    for ln in lines:
        if ln.startswith("  - package-ecosystem:"):
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)
        else:
            head.append(ln)
    out = list(head)
    for b in blocks:
        eco = re.search(r'package-ecosystem:\s*"?([\w-]+)', b[0])
        if eco and eco.group(1) in keep:
            out += b
    return "".join(out)


def check_owners(arg: str) -> tuple[list[str], str]:
    handles = [h.strip() for h in arg.split(",") if h.strip()]
    bad = [h for h in handles if not HANDLE.match(h) or PLACEHOLDER_HANDLE.match(h)]
    if bad:
        return [], f"refusing owner handle(s) {', '.join(bad)}: each must be a real @user or @org/team, not a placeholder"
    distinct: dict[str, str] = {}
    for h in handles:  # GitHub handles are case-insensitive: @Alice and @alice are one person
        distinct.setdefault(h.lower(), h)
    if len(distinct) < 2:
        return [], "--codeowners needs at least two distinct handles (compared case-insensitively), so a PR has a reviewer who is not its author"
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
    parts = ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))
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
        commands = {k: v for k, v in (config.get("commands") or {}).items() if k in ("lint_report", "typecheck")}
        return list(mod.missing_tools({"commands": commands}, root))
    except Exception:  # noqa: BLE001 - the check is a safeguard; the ratchet's own --require-tools still runs
        return []


def render_codeowners(text: str, handles: list[str]) -> str:
    """GitHub counts an approval from ANY ONE owner on a CODEOWNERS line, so a line with the reviewers alone
    would leave the first owner with nobody who can approve their own PR. Single-owner reviewer lines get
    every owner; lines that name the security owner too keep that pair (one approval is what counts)."""
    reviewers = " ".join([handles[0]] + handles[2:])
    everyone = " ".join([handles[0], handles[1]] + handles[2:])
    text = re.sub(r"^# install\.sh refuses these placeholders.*\n", "# Owners set by init_project.py --codeowners; change them by pull request.\n", text, flags=re.M)
    text = text.replace("@your-org/reviewers @your-org/security-owner", f"{reviewers} {handles[1]}")
    return text.replace("@your-org/reviewers", everyone).replace("@your-org/security-owner", handles[1])


# ---------- main ----------

def usage() -> str:
    return (__doc__ or "").split("Usage:", 1)[1].split("sdlc:")[0].strip()


def parse(argv: list[str]) -> dict | None:
    o: dict = {"root": None, "stack": None, "repo": DEFAULT_REPO, "ref": "main", "no_ci": False, "dry": False,
               "force": False, "upgrade": False, "owners": None, "cleanup": 30, "partial": False, "headless": False}
    flags = {"--headless": "headless", "--no-ci": "no_ci", "--dry-run": "dry", "--force": "force", "--upgrade": "upgrade", "--allow-partial-baseline": "partial"}
    vals = {"--root": "root", "--stack": "stack", "--marketplace-repo": "repo", "--marketplace-ref": "ref",
            "--codeowners": "owners", "--cleanup-days": "cleanup"}
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
            o[vals[a]] = argv[i + 1]
            i += 2
        else:
            print(f"unknown argument {a}", file=sys.stderr)
            return None
    try:
        o["cleanup"] = max(1, int(o["cleanup"]))
    except ValueError:
        print("--cleanup-days needs a whole number", file=sys.stderr)
        return None
    return o


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
    if not stack:
        if upgrade and os.path.exists(os.path.join(root, cl.CONFIG_REL)):
            try:
                with open(os.path.join(root, cl.CONFIG_REL), encoding="utf-8") as fh:
                    stack = json.load(fh).get("stack")
            except (ValueError, OSError):
                stack = None
        if not stack:
            if len(detected) == 1:
                stack = detected[0]
            elif not detected:
                print("no stack marker found (pyproject.toml, package.json, go.mod, Cargo.toml); pass --stack")
                return 2
            else:
                print(f"several stacks detected: {', '.join(detected)}; pass --stack <one of them> (one config per repo; run again in a subdirectory for the other)")
                return 2
    if stack not in cl.STACKS:
        print(f"unknown stack {stack}; one of {', '.join(cl.STACKS)}")
        return 2

    defaults = cl.stack_defaults(stack)
    config = build_config(root, stack, defaults, repo)
    rep = Report(root, dry, o["force"], upgrade)
    cfg_path = os.path.join(root, cl.CONFIG_REL)

    # Config: created if absent; with --upgrade new keys are added, present values are never changed.
    if os.path.exists(cfg_path) and upgrade:
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                existing = json.load(fh)
            added = merge_missing_keys(existing, config)
            if added:
                rep.merged.append(f"{cl.CONFIG_REL.replace(os.sep, '/')} (+keys: {', '.join(added)})")
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

    for name in ("common", stack):
        src = os.path.join(tpl, "rules", f"{name}.md")
        if os.path.exists(src):
            rep.put_file(src, os.path.join(root, ".claude", "rules", f"sdlc-{name}.md"))

    if stack == "python":
        rep.put_file(os.path.join(tpl, "public_routes.txt"), os.path.join(root, ".claude", "public_routes.txt"))
    rep.put_file(os.path.join(tpl, "work-README.md"), os.path.join(root, ".claude", "work", "README.md"))
    rep.put_file(os.path.join(tpl, "pull_request_template.md"), os.path.join(root, ".github", "pull_request_template.md"))

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
                rep.put_file(src, dest, lambda t: filter_dependabot(t, stack))
            else:
                rep.put_file(src, dest)
    co_dest = os.path.join(root, ".github", "CODEOWNERS")
    co_src = os.path.join(gh_src, "CODEOWNERS")
    if owners and os.path.exists(co_src):
        rep.put_file(co_src, co_dest, lambda t: render_codeowners(t, owners))
    elif os.path.exists(co_dest):
        with open(co_dest, encoding="utf-8", errors="replace") as fh:
            if re.search(r"@(your-org|app-reviewers|app-security-owner)", fh.read()):
                print("warning: .github/CODEOWNERS still holds placeholder handles; rerun with --codeowners '@a,@b'", file=sys.stderr)
    else:
        rep.kept.append(".github/CODEOWNERS (not written: pass --codeowners '@a,@b' with two real owners)")

    mcp_src = os.path.join(tpl, "mcp.json")
    if os.path.exists(mcp_src):
        mcp_dest = os.path.join(root, ".mcp.json")
        if os.path.exists(mcp_dest):
            rep.same += 1  # present: yours; the policy in docs/mcp-policy.md governs additions
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
            rep.put_file(src, dest)

    if not o["no_ci"]:
        ref = o["ref"]
        subst = lambda t: t.replace("__MARKETPLACE_REPO__", repo).replace("__MARKETPLACE_REF__", ref)  # noqa: E731
        for src_name, dest_name in ((f"{stack}.yml", "sdlc-gate.yml"), ("claude-review.yml", "sdlc-claude-review.yml")):
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
                had = re.search(r"^\s+ref: (\S+)$", old, flags=re.M)
                as_default = raw.replace("__MARKETPLACE_REPO__", DEFAULT_REPO).replace("__MARKETPLACE_REF__", had.group(1) if had else ref)
                if DEFAULT_REPO in old and old == as_default:
                    rep.merged.append(f".github/workflows/{dest_name} (marketplace repo set to {repo})")
                    rep._write(dest, subst(raw))
                    continue
            rep.put_file(src, dest, subst)

    merge_settings(rep, os.path.join(tpl, "settings.project.json"), stack, config, root, o["cleanup"])

    # Version marker: created now; moved forward only by --upgrade or --force.
    try:
        with open(os.path.join(plugin, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            version = json.load(fh).get("version", "")
    except (OSError, ValueError):
        version = ""
    if version:
        vpath = os.path.join(root, ".claude", "KIT_VERSION")
        old_v = open(vpath, encoding="utf-8").read().strip() if os.path.exists(vpath) else None
        if old_v is None:
            rep.created.append(".claude/KIT_VERSION")
            rep._write(vpath, version + "\n")
        elif old_v != version and (upgrade or o["force"]):
            rep.merged.append(f".claude/KIT_VERSION ({old_v} -> {version})")
            rep._write(vpath, version + "\n")
        elif old_v != version:
            rep.kept.append(f".claude/KIT_VERSION (is {old_v}; this plugin is {version}; --upgrade moves it)")
        else:
            rep.same += 1

    # CLAUDE.md: the working agreement lives between markers; an existing block is never rewritten here.
    with open(os.path.join(tpl, "CLAUDE.working-agreement.md"), encoding="utf-8") as fh:
        agreement = fh.read().replace("{stack}", stack).replace("{test_command}", config.get("commands", {}).get("test", "")).rstrip()
    claude_md = os.path.join(root, "CLAUDE.md")
    existing = ""
    if os.path.exists(claude_md):
        with open(claude_md, encoding="utf-8") as fh:
            existing = fh.read()
    if MARKER_START not in existing:
        rep.created.append("CLAUDE.md (appended)")
        if not dry:
            with open(claude_md, "a", encoding="utf-8") as fh:
                fh.write(("\n\n" if existing and not existing.endswith("\n\n") else "") + f"{MARKER_START}\n{agreement}\n{MARKER_END}\n")
    else:
        block = existing.split(MARKER_START, 1)[1].split(MARKER_END, 1)[0].strip()
        if block == agreement:
            rep.same += 1
        elif upgrade:
            rep._diff(claude_md, block + "\n", agreement + "\n")
            rep.new.append("CLAUDE.working-agreement.md.new (replace the block in CLAUDE.md by hand)")
            rep._write(os.path.join(root, "CLAUDE.working-agreement.md.new"), agreement + "\n")
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
                fh.write(("" if present.endswith("\n") or not present else "\n") + "\n".join(missing) + "\n")

    print(f"stack: {stack}  (detected: {', '.join(detected) or 'none'})" + ("  [upgrade]" if upgrade else "") + ("  [dry run]" if dry else ""))
    print("app paths:", config.get("paths", {}).get("app"))
    runner, runner_why = detect_python_runner(root) if stack == "python" else ("", "")
    if stack == "python":
        print(f"python runner: {runner or 'none (python3 -m)'}" + (f"  ({runner_why})" if runner else ""))
        print(f"test command: {config.get('commands', {}).get('test', '')}")
    for p in rep.created:
        print("  + " + p)
    for p in rep.merged:
        print("  ~ " + p)
    for p in rep.new:
        print("  ! " + p + "   (differs from the template; compare, then keep or delete)")
    for p in rep.kept:
        print("  = kept  " + p)
    if upgrade and rep.diffs:
        print("\nWhat would change (template vs your file):")
        for name, text in rep.diffs:
            lines = text.splitlines()
            print("\n".join(lines[:40]) + (f"\n... ({len(lines) - 40} more lines)" if len(lines) > 40 else ""))
    print(f"\nsummary: {len(rep.created)} created, {len(rep.merged)} merged, {len(rep.new)} .new, {len(rep.kept)} kept, {rep.same} unchanged" + (f", runner {runner or 'python3 -m'}" if stack == "python" else ""))
    if dry:
        print("dry run: nothing written")
        return 0

    baseline = os.path.join(root, ".claude", "baseline.json")
    if upgrade:
        print("baseline: untouched (--upgrade never runs ratchet.py --update)")
    elif os.path.exists(baseline):
        print("baseline: kept (.claude/baseline.json exists; a human re-baselines with ratchet.py --update)")
    else:
        if not o["partial"]:
            gone = missing_baseline_tools(plugin, config, root)
            if gone:
                for line in gone:
                    print(line, file=sys.stderr)
                print("init: files are written but no baseline was recorded, because a baseline taken without these tools makes the first CI run report every existing finding as new.", file=sys.stderr)
                print("  Install them and rerun this command, or pass --allow-partial-baseline (CI stays red until a human runs ratchet.py --update with the tools installed).", file=sys.stderr)
                return 2
        args = [sys.executable, "-I", os.path.join(plugin, "scripts", "ratchet.py"), "--root", root, "--update"]
        if not o["partial"]:
            args.append("--require-tools")
        proc = cl.run(args, cwd=root)
        if proc.returncode == 2 and "required tool(s) missing" in proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
            print("init: files are written but no baseline was recorded; install the tools and rerun, or pass --allow-partial-baseline.", file=sys.stderr)
            return 2
        print(proc.stdout.strip() or proc.stderr.strip())
        for line in proc.stderr.splitlines():
            if line.startswith("note:"):
                print("  " + line)
        if o["partial"]:
            print("warning: baseline recorded with --allow-partial-baseline; a missing ruff, mypy or other tool means CI will report its findings as new")
    if not upgrade:
        line = baseline_by_check(baseline)
        if line:
            print(line)
    if any(".mcp.json" in x for x in rep.created) or any("settings.json" in x for x in rep.created + rep.merged):
        print("note: the merged settings turn the sandbox on (macOS and Linux only; failIfUnavailable is true). Read docs/permission-modes.md before the team's first run.")
    if owners:
        print(f"CODEOWNERS: GitHub counts an approval from ANY ONE owner on a line, not all of them. Lines that list only the reviewers got {' '.join(owners)} so each can approve the other's PRs;")
        print("  to require a second person on the auth and data-model paths use a team with a required count or a ruleset (see docs/pack-governance.md).")
    print("\nNext: read the files above, adjust .claude/sdlc.json (app/test paths, commands, protected paths), then commit them.")
    print("Then /sdlc:onboard to read the agreement, /sdlc:audit for the debt, /sdlc:ticket <ticket> to start work.")
    if repo == DEFAULT_REPO and not o["no_ci"]:
        print(f"CI: the gate checks the plugin's scripts out of the public {DEFAULT_REPO} at ref {o['ref']}; pin a release tag, or rerun with --marketplace-repo owner/repo for a fork")
        print("    (a private marketplace repo also needs a read token: uncomment the `token:` line on the plugin checkout in .github/workflows/sdlc-gate.yml).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
