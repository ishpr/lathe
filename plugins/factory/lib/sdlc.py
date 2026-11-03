"""Shared helpers for the sdlc hooks and scripts.

Hooks run with `python3 -I`, so the plugin's own directory is not on sys.path; each entry
point inserts `<plugin>/lib` explicitly before importing this module. Nothing here imports
from the project under review.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys

STACKS = ("python", "node", "go", "rust")
CONFIG_REL = os.path.join(".claude", "sdlc.json")
CONTRACT_REL = os.path.join(".claude", "work", "current.json")
STAMP_REL = os.path.join(".claude", "work", "last_verify.json")
BLOCKS_REL = os.path.join(".claude", "work", "blocks.jsonl")

# Paths that force a contract to tier high (REC-54). A project overrides the list with
# "tier_high_paths" in .claude/sdlc.json.
TIER_HIGH_DEFAULT = [
    "app/auth/**",
    "app/models/**",
    "alembic/**",
    ".github/**",
    "docker-compose*.yml",
    "compose*.yml",
    ".devcontainer/**",
    ".claude/**",
    ".mcp.json",
]

# The graph block (/factory:init writes it; /factory:graph flips it). The connection string is never here: it is
# read from the variable url_env names (shell, .claude/settings.local.json env, or .env). sdlc_graph/graphcfg.py
# holds the same defaults for the MCP server; a test keeps the two equal.
GRAPH_PROVIDERS = ("local", "neon", "aws", "spanner")
GRAPH_DEFAULTS: dict = {"enabled": True, "provider": "local", "url_env": "DATABASE_URL",
                        "spanner": {"project": "", "instance": "", "database": ""}}
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")

DEFAULTS: dict = {
    "version": 1,
    "stack": "generic",
    "paths": {
        "app": ["src/**"],
        "tests": ["tests/**", "test/**"],
        "protected": [],
        "protected_branches": ["main", "master"],
    },
    "extensions": [],
    "commands": {},
    "lint_format": "",
    "typecheck_format": "",
    "checkers": [],
    "auth": {"dependency_names": [], "public_routes_file": ".claude/public_routes.txt"},
    "require_tests": True,
    "ratchet_baseline": ".claude/baseline.json",
    "secret_patterns_extra": [],
    "tier_high_paths": list(TIER_HIGH_DEFAULT),
    "graph": json.loads(json.dumps(GRAPH_DEFAULTS)),
}

ALWAYS_PROTECTED = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".envrc",
    "**/.envrc",
    ".claude/sdlc.json",
    ".claude/baseline.json",
    ".claude/work/last_verify.json",
    ".claude/work/**/last_verify.json",
    ".claude/work/blocks.jsonl",
    ".claude/public_routes.txt",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/**",
    ".claude/rules/**",
    ".claude/agents/**",
    ".claude/skills/**",
    ".git/config",
    ".git/hooks/**",
    "CLAUDE.md",
    ".mcp.json",
    "scripts/freeze_tests.py",
    "scripts/acceptance.py",
]
# .env templates are committed and editable; everything else named .env* is a secret store.
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tmpl", ".tpl")

SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY", "private key"),
    (r"\bsk_(?:live|test)_[0-9A-Za-z]{16,}", "Stripe secret key"),
    (r"\bxox[abp]-[0-9A-Za-z-]{10,}", "Slack token"),
    (r"\bgh[pousr]_[0-9A-Za-z]{30,}", "GitHub token"),
    (r"\bgithub_pat_[0-9A-Za-z_]{22,}", "GitHub fine-grained token"),
    (r"\bsk-ant-[0-9A-Za-z_-]{20,}", "Anthropic API key"),
    (r"\bsk-proj-[0-9A-Za-z_-]{20,}", "OpenAI API key"),
    (r"\bsk-[0-9A-Za-z]{32,}", "API secret key"),
    (r"\bnpm_[0-9A-Za-z]{30,}", "npm token"),
    (r"\bpypi-[0-9A-Za-z_-]{30,}", "PyPI token"),
    (r"\bAIza[0-9A-Za-z_-]{30,}", "Google API key"),
    (r"://[^/\s:@\"']+:[^/\s@\"']{6,}@", "URL with a password"),
    (
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*[\"'][^\"'\s]{20,}[\"']",
        "hard-coded credential",
    ),
]
# Labels skipped under tests/ and in .md files, where fixtures and documentation hold example values.
SECRET_FIXTURE_LABELS = ("hard-coded credential", "URL with a password")
# A match that says what it is (the AWS docs example key, "your-api-key-goes-here", xxxx...) is not a secret.
SECRET_PLACEHOLDER = re.compile(
    r"(?i)example|replace|placeholder|your[-_ ]|changeme|change-me|dummy|fake|sample|redacted|xxxx|\*{4}|<[^>]+>|\$\{?\w+\}?|\{\{|:(?:password|pass|secret|pwd|user|token)@"
)
_SAME_CHAR_RUN = re.compile(r"(.)\1{9,}")


# ---------- locations ----------

def plugin_root() -> str:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def project_root(hint: str | None = None) -> str:
    """CLAUDE_PROJECT_DIR first (it does not follow a `cd` into a subdirectory), then the git
    toplevel of the hook's cwd (`git rev-parse --show-toplevel`, which also works in a git worktree,
    where .git is a file), then that cwd itself. Never a parent directory: a guard rooted above the
    repo would read another project's config, and one rooted in a subdirectory would treat files
    outside it as not in the project."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    start = os.path.abspath(hint or os.getcwd())
    top = ""
    if os.path.isdir(start):
        try:
            top = subprocess.run(
                ["git", "-C", start, "rev-parse", "--show-toplevel"], capture_output=True, text=True, env=safe_git_env(), timeout=30
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            top = ""
    return os.path.abspath(top or start)


def real(path: str) -> str:
    """Absolute path with symlinks resolved (the existing prefix of a not-yet-created file too)."""
    return os.path.realpath(os.path.abspath(path))


def rel_to(root: str, path: str) -> str:
    """Repo-relative path of `path` with symlinks resolved on both sides, so a link into the repo
    (or one inside it) cannot hide the file it points at. '..'-prefixed when outside."""
    r = real(root)
    p = real(path)
    if p.lower() == r.lower():
        return "."
    if p.lower().startswith(r.lower().rstrip("/") + "/"):  # prefix compared case-insensitively: macOS disks are
        return p[len(r):].lstrip("/")
    return os.path.relpath(p, r).replace(os.sep, "/")


def git_toplevel(root: str) -> str:
    return real(git(root, "rev-parse", "--show-toplevel").strip() or root)


# ---------- processes ----------

def safe_git_env() -> dict[str, str]:
    """Environment for the guards' own git calls: repo config must not run code (core.fsmonitor,
    core.pager) in a hook that runs outside the sandbox, and a status check must not take locks."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="core.fsmonitor",
        GIT_CONFIG_VALUE_0="false",
        GIT_CONFIG_KEY_1="core.pager",
        GIT_CONFIG_VALUE_1="cat",
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
    )
    env.pop("GIT_EXTERNAL_DIFF", None)
    return env


def run(cmd, cwd: str | None = None, shell: bool = False, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True, input=input, env=env)


def git(root: str, *args: str) -> str:
    return run(["git", "-C", root, *args], env=safe_git_env()).stdout


def changed_paths(root: str, *pathspecs: str) -> list[str]:
    """Paths `git status` reports (untracked files listed one by one), optionally limited to pathspecs."""
    out = []
    cmd = ["status", "--porcelain", "-uall"]
    if pathspecs:
        cmd += ["--", *pathspecs]
    for line in git(root, *cmd).splitlines():
        if not line:
            continue
        path = line[3:].split(" -> ")[-1].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        out.append(path)
    return out


# ---------- config ----------

def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def stack_defaults(stack: str) -> dict:
    path = os.path.join(plugin_root(), "stacks", f"{stack}.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_LIST_OF_STR = ("tier_high_paths", "extensions", "checkers", "secret_patterns_extra")
_PATH_LISTS = ("app", "tests", "protected", "protected_branches")


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def graph_block(value) -> tuple[dict, list[str]]:
    """(usable graph block with defaults filled in, problems). A bad key falls back to its default and is reported."""
    out = json.loads(json.dumps(GRAPH_DEFAULTS))
    if value is None:
        return out, []
    if not isinstance(value, dict):
        return out, ["graph must be an object"]
    problems: list[str] = []
    for key, item in value.items():
        if key == "enabled":
            if isinstance(item, bool):
                out[key] = item
            else:
                problems.append("graph.enabled must be true or false")
        elif key == "provider":
            if item in GRAPH_PROVIDERS:
                out[key] = item
            else:
                problems.append(f"graph.provider must be one of {', '.join(GRAPH_PROVIDERS)}")
        elif key == "url_env":
            if isinstance(item, str) and _ENV_NAME.match(item):
                out[key] = item
            else:
                problems.append("graph.url_env must be an environment variable name (A-Z, 0-9, _)")
        elif key == "spanner":
            if isinstance(item, dict) and all(isinstance(item.get(k, ""), str) for k in ("project", "instance", "database")):
                out[key] = {**out["spanner"], **{k: v for k, v in item.items() if k in ("project", "instance", "database")}}
            else:
                problems.append("graph.spanner must be an object of strings (project, instance, database)")
        else:
            out[key] = item
    return out, problems


def sanitize_config(data: dict) -> tuple[dict, list[str]]:
    """(usable config, problems): a key of the wrong type falls back to its default and is reported,
    so one bad key cannot switch off the rest or crash every guard."""
    out: dict = {}
    problems: list[str] = []
    for key, value in data.items():
        if key == "paths":
            if not isinstance(value, dict):
                problems.append("paths must be an object")
                continue
            paths = {}
            for sub, items in value.items():
                if sub in _PATH_LISTS and not _is_str_list(items):
                    problems.append(f"paths.{sub} must be a list of strings")
                    continue
                paths[sub] = items
            out[key] = paths
        elif key in _LIST_OF_STR:
            if not _is_str_list(value):
                problems.append(f"{key} must be a list of strings")
                continue
            if key == "secret_patterns_extra":
                bad = []
                for pat in value:
                    try:
                        re.compile(pat)
                    except re.error as exc:
                        bad.append(f"{pat!r} ({exc})")
                if bad:
                    problems.append("secret_patterns_extra has invalid regexes: " + ", ".join(bad))
                    continue
            out[key] = value
        elif key in ("commands", "auth"):
            if not isinstance(value, dict):
                problems.append(f"{key} must be an object")
                continue
            out[key] = value
        elif key == "stack":
            if not isinstance(value, str):
                problems.append("stack must be a string")
                continue
            out[key] = value
        elif key == "graph":
            block, bad = graph_block(value)
            problems += bad
            out[key] = block
        elif key == "require_tests":
            if not isinstance(value, bool):
                problems.append("require_tests must be true or false")
                continue
            out[key] = value
        else:
            out[key] = value
    return out, problems


def load_config(root: str) -> dict:
    """Defaults + stack defaults + .claude/sdlc.json. `_config_error` is set when the file exists
    but cannot be used (bad JSON, wrong shape); the guards deny until a human repairs it, because a
    silently ignored config would drop every protection it defines."""
    path = os.path.join(root, CONFIG_REL)
    project: dict = {}
    configured = False
    error = ""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            configured = True
            if isinstance(data, dict):
                project, problems = sanitize_config(data)
                if problems:
                    error = "; ".join(problems)
            else:
                error = "the top level must be a JSON object"
        except (OSError, ValueError) as exc:
            error = f"cannot be read as JSON ({exc})"
    stack = project.get("stack") or "generic"
    merged = deep_merge(DEFAULTS, stack_defaults(stack) if stack in STACKS else {})
    merged = deep_merge(merged, project)
    merged["_configured"] = configured
    merged["_config_error"] = error
    merged["_root"] = root
    merged["_path"] = path
    return merged


def load_contract(root: str) -> dict | None:
    path = os.path.join(root, CONTRACT_REL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def load_stamp(root: str) -> dict | None:
    path = os.path.join(root, STAMP_REL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ---------- paths and globs ----------

def match_one(rel: str, pattern: str, fold: bool = False) -> bool:
    if fold:
        rel, pattern = rel.lower(), pattern.lower()
    pattern = pattern.strip().rstrip("/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern or pattern == ".":
        return True
    if fnmatch.fnmatchcase(rel, pattern):
        return True
    if pattern.endswith("/**") and (rel == pattern[:-3] or rel.startswith(pattern[:-3] + "/")):
        return True
    if "*" not in pattern and (rel == pattern or rel.startswith(pattern + "/")):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(rel, pattern[3:]):
        return True
    return False


def matches(rel: str, patterns: list[str], fold: bool = False) -> bool:
    """Glob match. fold=True compares case-insensitively: macOS and Windows disks do, so
    `.claude/BASELINE.json` is the baseline there."""
    return any(match_one(rel, p, fold) for p in patterns or [])


def is_protected(rel: str, extra: list[str] | None = None) -> bool:
    """True for the files only a human edits: ALWAYS_PROTECTED plus the project's paths.protected.
    Case-folded; .env templates (.env.example ...) are ordinary files."""
    if rel.lower().endswith(ENV_TEMPLATE_SUFFIXES) and rel.rsplit("/", 1)[-1].lower().startswith(".env"):
        return False
    return matches(rel, list(ALWAYS_PROTECTED) + list(extra or []), fold=True)


def is_test_path(cfg: dict, rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    if matches(rel, cfg["paths"].get("tests", [])):
        return True
    return bool(
        re.search(r"(^|/)(tests?|__tests__|spec)/", rel)
        or base.startswith(("test_", "conftest"))
        or base.endswith(("_test.py", "_test.go", "_test.rs"))
        or ".test." in base
        or ".spec." in base
    )


def is_app_path(cfg: dict, rel: str) -> bool:
    if is_test_path(cfg, rel):
        return False
    return matches(rel, cfg["paths"].get("app", []))


def has_stack_extension(cfg: dict, rel: str) -> bool:
    exts = cfg.get("extensions") or []
    return any(rel.endswith(e) for e in exts)


def walk_files(root: str, patterns: list[str], exts: list[str] | None = None) -> list[str]:
    out = []
    skip = {".git", "node_modules", ".venv", "venv", "target", "dist", "build", "__pycache__", ".claude", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")  # not realpath: a linked file keeps its own name
            if exts and not any(rel.endswith(e) for e in exts):
                continue
            if matches(rel, patterns):
                out.append(rel)
    return sorted(out)


# ---------- hooks ----------

def read_hook_input() -> dict | None:
    """The hook's stdin as a JSON object.

    Returns {} when stdin is empty (the caller decides what a missing field means) and None when
    stdin cannot be read or is not a JSON object. PreToolUse guards treat None, and a missing
    tool_input, as a reason to block (REC-41: fail closed); the other hooks skip and say so.
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return None
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def deny(message: str) -> None:
    sys.stderr.write("Blocked: " + message.strip() + "\n")
    sys.exit(2)


_SCRUBS = [
    (re.compile(r"(://[^/\s:@\"']+:)[^/\s@\"']+@"), r"\1***@"),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)\S+"), r"\1***"),
    (re.compile(r"(?i)(\b[A-Za-z0-9_]*(?:secret|token|passw(?:or)?d|credential|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*=)[^\s\"']+"), r"\1***"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
]


def scrub(text: str) -> str:
    """Remove secrets from a command before it is written to the block log: credentials in URLs,
    Authorization headers, NAME=value for secret-looking names, and anything SECRET_PATTERNS knows."""
    text = str(text or "")
    for rx, repl in _SCRUBS:
        text = rx.sub(repl, text)
    for pattern, _label in SECRET_PATTERNS:
        text = re.sub(pattern, "***", text)
    return text


def log_block(root: str | None, hook: str, tool: str, rule: str, detail: str) -> None:
    """Append one JSON line per deny to .claude/work/blocks.jsonl (REC-46). Never raises.

    Only written when the project already has a .claude directory, so an uninitialised repo
    does not grow one because a guard fired. The detail is scrubbed of secrets before it is
    cut to 200 characters (a cut first could leave half a token), and the file is created 0600.
    For credential-read denies only the first words of the command are kept.
    """
    try:
        root = root or project_root()
        if not os.path.isdir(os.path.join(root, ".claude")):
            return
        path = os.path.join(root, BLOCKS_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        detail = str(detail or "")
        if rule == "credential-read":
            detail = " ".join(detail.split()[:2]) + " ..."
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hook": hook,
            "tool": tool,
            "rule": rule,
            "detail": scrub(detail)[:200],
        }
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def block(root: str | None, hook: str, tool: str, rule: str, detail: str, message: str) -> None:
    """log_block, then deny: every guard deny goes through here."""
    log_block(root, hook, tool, rule, detail)
    deny(message)


def load_frozen_tests(root: str, contract: dict | None = None) -> dict[str, str]:
    """{repo-relative path: sha256} from current.json frozen_tests (REC-07). A list of paths is
    accepted too (sha ""). Empty when there is no contract or nothing is frozen."""
    if contract is None:
        contract = load_contract(root)
    frozen = (contract or {}).get("frozen_tests") or {}
    if isinstance(frozen, list):
        frozen = {str(p): "" for p in frozen}
    if not isinstance(frozen, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in frozen.items():
        rel = str(key).replace(os.sep, "/")
        while rel.startswith("./"):
            rel = rel[2:]
        out[rel] = str(value or "")
    return out


def tier_high_paths(cfg: dict) -> list[str]:
    """Globs that force tier high (REC-54): the config's tier_high_paths, else the defaults."""
    paths = cfg.get("tier_high_paths")
    if not isinstance(paths, list) or not paths:
        return list(TIER_HIGH_DEFAULT)
    return [str(p) for p in paths]


def find_secrets(text: str, cfg: dict, rel: str) -> tuple[str, str] | None:
    patterns = list(SECRET_PATTERNS)
    for p in cfg.get("secret_patterns_extra") or []:
        patterns.append((p, "project secret pattern"))
    fixture = is_test_path(cfg, rel) or rel.endswith(".md") or rel.lower().endswith(ENV_TEMPLATE_SUFFIXES)
    for pattern, label in patterns:
        if label in SECRET_FIXTURE_LABELS and fixture:
            continue
        try:
            found = list(re.finditer(pattern, text))
        except re.error:
            continue
        for m in found:
            value = m.group(0)
            if SECRET_PLACEHOLDER.search(value) or (label in SECRET_FIXTURE_LABELS and _SAME_CHAR_RUN.search(value)):
                continue
            return label, value[:12]
    return None
