"""Contract capabilities -> Claude Code settings fragment (layer 3 at build time).

Input (``node.capabilities``)::

    {"paths": ["app/routes/**", "tests/test_routes.py"],   # writable paths, repo-relative
     "egress": ["pypi.org"],                                # network domains
     "commands": ["python3 -m pytest tests/routes -q"],      # shell commands the change needs (compiled to Bash rules)
     "data": ["postgres:ro"],                               # data access declarations (recorded, not compiled)
     "secrets": [],                                         # secrets the change may read (paths exempt from the deny rules)
     "test_command": "python -m pytest"}                    # optional; default allows pytest and python -m pytest

Output uses ONLY keys the verified fact sheet marks verified
(the Claude Code settings, sandbox and permissions documentation):

    {"sandbox": {"enabled": true,
                 "filesystem": {"allowWrite": ["./app/routes", "./tests/test_routes.py"]},
                 "network": {"allowedDomains": ["pypi.org"]}},
     "permissions": {"deny": ["Read(./.env)", "Read(./.env.local)", "Read(./.env.test)", "Read(./.env.*.local)",
                              "Read(./*.env)", ..., "Read(~/.aws/**)", "Read(~/.config/gh/**)", "Read(~/.ssh/**)"],
                     "allow": ["Bash(python -m pytest *)", "Bash(pytest *)", "Bash(python3 -m pytest tests/routes -q *)",
                               "Edit(./app/routes/**)", ...]}}

``commands`` (the contract's ``## Capabilities`` commands line, docs/DESIGN.md section 14) compile to
``Bash(<command> *)``, which also matches the bare command (the Claude Code permissions documentation). A command is one plain
invocation: no shell operators, substitutions or redirections, no wrapper that runs arbitrary text
(``sudo``, ``eval``, ``env``, ``bash -c``, ``python3 -c``), no wildcard and no bare interpreter, so a
contract can pre-approve ``bash scripts/smoke_rate_limit.sh`` but never ``bash``. Paths compile to
``Edit(./<path>)`` only: Edit rules cover every built-in editing tool and ``Write(...)`` rules "never
match" (the Claude Code permissions documentation), so no Write rule is emitted.

Path syntax differs between sandbox paths and Read/Edit rules (the Claude Code sandbox and permissions documentation): in both,
``./x`` is project-relative here; ``~/x`` is the home directory. Sandbox ``allowWrite`` takes plain
paths (a directory covers its children), so a glob is reduced to its literal directory prefix
(``app/**/x.py`` becomes ``./app``): the fragment can be WIDER than the glob, and the summary says
so. Edit rules keep the glob as written.

What this cannot do, said plainly: the working directory is already writable under the sandbox, so
``allowWrite`` adds nothing for paths inside the project and the Edit allow rules only pre-approve
edits; they do not forbid edits elsewhere. ``data`` and ``secrets`` have no Claude Code setting, and
a ``denyRead`` sandbox entry does not stop the Read tool (hence the ``Read(...)`` deny rules).
:func:`compile_full` returns those caveats next to the fragment; the fragment itself carries no
key outside the verified list.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

import psycopg

#: Read-deny rules always emitted (credential files), dropped only when ``secrets`` exempts them.
DEFAULT_READ_DENY: tuple[str, ...] = (
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.config/gh/**)",
    "Read(./.env)",
    "Read(./.env.local)",
    "Read(./.env.development)",
    "Read(./.env.production)",
    "Read(./.env.staging)",
    "Read(./.env.test)",
    "Read(./.env.*.local)",
    "Read(./*.env)",
)
# Real env files are denied by explicit name, never by a ``.env.*`` wildcard: the rule syntax has no
# verified negation, and a wildcard would also deny the templates (.env.example, .env.sample,
# .env.template, .env.dist) that an app's own tests and verify step read.

#: Bash deny rules every compiled fragment carries: what the design says no executor does (the same set as the pack's
#: settings template). A bare ``git push`` is not listed because the ship step publishes the branch with the
#: pack's own ``git push -u origin HEAD`` allow rule; the forms that rewrite or delete are.
HUMAN_ONLY_DENY: tuple[str, ...] = (
    "Bash(git push --force *)", "Bash(git push -f *)", "Bash(git push *--force*)", "Bash(git push *--mirror*)",
    "Bash(git push *--prune*)", "Bash(git push *--delete*)", "Bash(git push -d *)", "Bash(git push * main*)",
    "Bash(git push * master*)", "Bash(git commit *--no-verify*)", "Bash(git commit -n *)", "Bash(git reset --hard *)",
    "Bash(gh pr merge *)", "Bash(gh api *)", "Bash(rm -rf *)",
)

#: Default test-runner allow rules when ``test_command`` is not given.
DEFAULT_TEST_ALLOW: tuple[str, ...] = ("Bash(python -m pytest *)", "Bash(pytest *)")

#: The only test runners ``test_command`` may name; anything else would pre-approve an arbitrary command.
TEST_COMMANDS: frozenset[str] = frozenset({"pytest", "python -m pytest", "python3 -m pytest", "uv run pytest",
                                          "uv run python -m pytest", ".venv/bin/python -m pytest",
                                          ".venv/bin/pytest"})

_KEYS = ("paths", "commands", "egress", "data", "secrets")
_OPTIONAL = ("test_command",)
_GLOB = "*?["
#: Characters and forms a ``commands`` entry may not carry (operators, substitution, redirection, globbing).
_CMD_FORBIDDEN = re.compile(r"[;&|`<>*?\n\r\\$]")
#: First words that run arbitrary text, so a rule naming them pre-approves anything. ``timeout``, ``nice`` and the
#: other launchers take a whole command as their argument; ``awk`` runs ``system()``; none can be told from the
#: command they launch by a prefix rule.
_CMD_WRAPPERS = frozenset({"sudo", "doas", "eval", "exec", "env", "xargs", "nohup", "time", "command", "builtin", "su",
                           "timeout", "nice", "ionice", "stdbuf", "setsid", "watch", "chroot", "unshare",
                           "awk", "gawk", "mawk", "nawk", "uvx", "bunx", "pipx", "dlx"})
#: Interpreters: allowed only with a script or module argument, never bare or with an inline-code flag.
_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "fish", "python", "python3", "node", "deno", "bun", "ruby",
                           "perl", "php", "pwsh", "powershell", "osascript", "npx"})
_INLINE_FLAGS = frozenset({"-c", "-e", "--eval", "-m", "-p", "--print", "-x"})
#: Per interpreter family: the single-letter flags that run text given on the command line (or load code), looked for
#: in every flag cluster before the script (``-lc``, ``-Ic``, ``-le``). ``E`` is an inline flag for perl only (python's
#: ``-E`` ignores PYTHON* variables), ``r`` for node, ruby and php only.
_INLINE_LETTERS = {"python": "c", "shell": "c", "node": "epr", "ruby": "er", "perl": "eEMm", "php": "rRBEF",
                   "pwsh": "cC", "osascript": "e"}
_LONG_INLINE = frozenset({"--eval", "--print", "--require", "--command", "--exec", "--import", "--loader", "--input-type"})
#: Python options that take the next word as their value (the value is not the script).
_PY_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
#: Test runners ``npx`` may launch; any other package would run whatever it downloads.
_NPX_OK = frozenset({"vitest", "jest", "tsc", "eslint", "prettier", "playwright", "mocha", "ts-node"})
_HOST = re.compile(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:\d{1,5})?$")
#: git subcommands a contract may never pre-approve: they publish, rewrite history or change configuration.
_GIT_DENIED = frozenset({"push", "reset", "clean", "rebase", "filter-branch", "filter-repo", "update-ref", "config", "remote",
                         "gc", "reflog", "replace", "revert", "cherry-pick", "merge", "pull", "restore", "checkout", "switch",
                         "stash", "tag", "worktree", "submodule", "am", "apply"})
#: gh subcommands (and ``pr``/``issue`` verbs) a contract may never pre-approve.
_GH_DENIED = frozenset({"api", "auth", "secret", "repo", "release", "workflow", "run", "ssh-key", "gpg-key", "codespace",
                        "extension", "alias", "config", "variable", "ruleset", "cache", "label", "project"})
_GH_PR_DENIED = frozenset({"merge", "close", "reopen", "ready", "review", "edit", "create", "comment", "checkout", "lock", "unlock",
                           "update-branch", "revert", "delete", "transfer", "develop"})
#: Flags that make any command a force, a bypass or a delete.
_DANGEROUS_FLAGS = re.compile(r"^(--force(?:-with-lease|-if-includes)?|--no-verify|--no-gpg-sign|--hard|--delete|--admin|--mirror|"
                              r"--prune|--yes|--exec|--exec-path|-f|--output|--output-file)(?:=|$)")
_RUNNERS = (("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("rye", "run"),
            ("pnpm", "exec"), ("yarn", "exec"), ("npm", "exec"), ("npm", "run"), ("pnpm", "run"), ("yarn", "run"),
            ("bundle", "exec"), ("python3", "-m", "pipenv"))


def _interpreter_family(first: str) -> str | None:
    if re.fullmatch(r"python3?(\.\d+)?", first) or first == "python":
        return "python"
    if first in ("bash", "sh", "zsh", "dash", "fish"):
        return "shell"
    if first in ("node", "deno", "bun"):
        return "node"
    return first if first in ("ruby", "perl", "php", "pwsh", "powershell", "osascript") and first != "powershell" else (
        "pwsh" if first == "powershell" else None)


def _inline_flag(words: list[str], fam: str) -> str | None:
    """The flag among the interpreter's own options (those before the script or module) that runs inline text."""
    letters = _INLINE_LETTERS.get(fam, "ce")
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            return None
        if not w.startswith("-") or w == "-":
            return None                              # the script or module: the options end here
        if w == "-m" and fam == "python":
            return None                              # a module; its own arguments are not interpreter flags
        if w.startswith("--"):
            if w.split("=")[0] in _LONG_INLINE:
                return w
        elif any(c in letters for c in w[1:]):
            return w
        i += 2 if (fam == "python" and w in _PY_VALUE_FLAGS) else 1
    return None


def command_problem(command: str) -> str | None:
    """Why ``command`` cannot become a ``Bash(<command> *)`` allow rule, or None when it can.

    Besides the shape rules (one plain invocation, no operators) a contract can never pre-approve what the autonomy
    design says is impossible for an executor: publishing (``git push``, ``gh pr merge``, ``gh api``), history rewrites
    (``reset``, ``--force``, ``--no-verify``, ``--hard``), file deletion, or any wrapper or interpreter flag that runs
    other text (``timeout 5 sh -c id``, ``python3 -Ic ...``, ``bash -lc ...``, ``perl -le ...``, ``find -exec``)."""
    if not isinstance(command, str) or not command.strip() or command != command.strip():
        return "must be a non-empty one-line string without leading or trailing spaces"
    if _CMD_FORBIDDEN.search(command):
        return "may not contain shell operators, substitutions, redirections, wildcards or backslashes (; & | ` < > * ? $ \\)"
    words = command.split()
    for runner in _RUNNERS:
        if tuple(words[:len(runner)]) == runner:
            if len(words) <= len(runner):
                return f"{command!r} alone runs anything"
            return command_problem(" ".join(words[len(runner):]))
    first = words[0].rsplit("/", 1)[-1]
    if first in _CMD_WRAPPERS:
        return f"may not start with {words[0]!r}, which runs arbitrary text"
    rest_all = words[1:]
    flag = next((w for w in rest_all if _DANGEROUS_FLAGS.match(w)), None)
    if flag and not (flag == "-f" and first not in ("git", "gh", "rm")):
        return f"{flag} forces, bypasses or deletes; a contract cannot pre-approve it"
    if first == "git":
        sub = next((w for w in rest_all if not w.startswith("-")), "")
        if sub in _GIT_DENIED:
            return f"git {sub} publishes, rewrites history or changes the tree or configuration; a human or the orchestrator does that"
        if sub == "commit" and any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", w) for w in rest_all if w != "-m"):
            return "git commit -n skips the hooks"
        if sub == "branch" and any(w in ("-d", "-D", "-m", "-M", "-f") for w in rest_all):
            return "git branch -d/-D/-m rewrites or deletes branches"
        if rest_all[:1] == ["-c"] or (rest_all[:1] and rest_all[0].startswith("--config")):
            return "git -c sets configuration for the command (hooks path, aliases that run text)"
    if first == "gh":
        sub = next((w for w in rest_all if not w.startswith("-")), "")
        if sub in _GH_DENIED:
            return f"gh {sub} reaches the GitHub API or settings; only the orchestrator's ship step publishes"
        if sub in ("pr", "issue"):
            verb = [w for w in rest_all if not w.startswith("-")][1:2]
            if verb and verb[0] in _GH_PR_DENIED:
                return f"gh {sub} {verb[0]} changes or merges; a contract cannot pre-approve it"
    if first in ("rm", "rmdir", "shred", "unlink", "dd", "mkfs", "truncate"):
        return f"{first} deletes files; a contract cannot pre-approve it"
    if first in ("chmod", "chown", "ln", "mv", "cp", "install", "rsync", "tee", "curl", "wget", "scp", "ssh", "nc", "ncat", "kill", "pkill"):
        return f"{first} writes outside the contract's paths or reaches the network; declare egress and paths instead"
    if first == "find" and any(w in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls")
                               for w in rest_all):
        return "find -exec/-delete/-fprint runs or removes things"
    if first == "sed" and any(re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*|--in-place(?:=.*)?", w) for w in rest_all):
        return "sed -i edits files in place"
    if first == "npx":
        pkg = next((w for w in rest_all if not w.startswith("-")), "")
        if pkg not in _NPX_OK:
            return f"npx {pkg or '<nothing>'} runs whatever package it downloads; name one of {', '.join(sorted(_NPX_OK))}"
        return None
    fam = _interpreter_family(first)
    if fam or first in _INTERPRETERS:
        rest = [w for w in words[1:] if w not in ("-I", "-u", "-B", "-q")]
        if not rest:
            return f"a bare interpreter ({words[0]!r}) would pre-approve any script; name the script or module"
        if rest[0] == "-m" and len(rest) < 2:
            return f"{words[0]} -m needs a module name"
        bad = _inline_flag(words[1:], fam or "python")
        if bad:
            return f"{words[0]} {bad} runs inline code or loads code by flag; name a script or module instead"
    return None


def validate(capabilities: dict[str, Any]) -> None:
    """Check shape: only the five list keys (plus optional ``test_command``), each a list of strings;
    paths repo-relative (no ``..``, no leading ``/`` or ``~``); egress entries are hostnames
    (optional ``*.`` prefix and ``:port``); commands pass :func:`command_problem`.

    Raises:
        ValueError: the message names the offending key and value.
    """
    if not isinstance(capabilities, dict):
        raise ValueError(f"capabilities must be an object, got {type(capabilities).__name__}")
    for key in capabilities:
        if key not in _KEYS and key not in _OPTIONAL:
            raise ValueError(f"capabilities.{key}: unknown key (allowed: {', '.join(_KEYS + _OPTIONAL)})")
    for key in _KEYS:
        value = capabilities.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"capabilities.{key}: must be a list of non-empty strings, got {value!r}")
    cmd = capabilities.get("test_command")
    if cmd is not None and (not isinstance(cmd, str) or not cmd.strip() or "\n" in cmd):
        raise ValueError(f"capabilities.test_command: must be a one-line string, got {cmd!r}")
    if cmd is not None and cmd.strip() not in TEST_COMMANDS:
        raise ValueError(f"capabilities.test_command: {cmd!r} is not an allowed test runner "
                         f"(one of: {', '.join(sorted(TEST_COMMANDS))})")
    for p in capabilities.get("paths", []):
        if p.startswith(("/", "~")) or ".." in p.split("/"):
            raise ValueError(f"capabilities.paths: {p!r} must be repo-relative (no leading / or ~, no ..)")
    for command in capabilities.get("commands", []):
        why = command_problem(command)
        if why:
            raise ValueError(f"capabilities.commands: {command!r} {why}")
    for host in capabilities.get("egress", []):
        if not _HOST.match(host):
            raise ValueError(f"capabilities.egress: {host!r} is not a hostname (optional *. prefix and :port)")


def _clean(path: str) -> str:
    return path.removeprefix("./")


def _sandbox_path(path: str) -> tuple[str, bool]:
    """``(sandbox path, widened)``: a glob reduces to its literal directory prefix."""
    path = _clean(path)
    if not any(c in path for c in _GLOB):
        return "./" + path.rstrip("/") if path.rstrip("/") else "./", False
    literal: list[str] = []
    for seg in path.split("/"):
        if any(c in seg for c in _GLOB):
            break
        literal.append(seg)
    base = "/".join(literal)
    plain_dir_glob = path.endswith("/**") and path[:-3] == base
    return ("./" + base if base else "./"), not plain_dir_glob


def _norm_target(text: str) -> str:
    text = text.removeprefix("./")
    for suffix in ("/**", "/"):
        text = text.removesuffix(suffix)
    return text


def _exempt(rule: str, secrets: list[str]) -> bool:
    """A secret exempts a deny rule only when it names the rule's target exactly (``.env`` lifts
    ``Read(./.env)``). A deny cannot carve an exception out of a wildcard, so ``.env.local`` does not
    lift ``Read(./.env.*.local)``; list ``.env.*.local`` to lift that one. Credential stores outside the
    project (``~/...``) are never lifted: a contract cannot unlock ``~/.ssh`` or ``~/.aws``."""
    if rule.startswith("Read(~"):
        return False
    target = _norm_target(rule[len("Read("):-1])
    return any(_norm_target(secret) == target for secret in secrets)


def _compile(capabilities: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    validate(capabilities)
    paths = list(capabilities.get("paths", []))
    secrets = list(capabilities.get("secrets", []))
    write: set[str] = set()
    notes: list[str] = []
    for p in paths:
        sp, widened = _sandbox_path(p)
        write.add(sp)
        if widened:
            notes.append(f"sandbox.filesystem.allowWrite: glob {p!r} reduced to directory {sp!r} (wider than the glob)")
    allow = [f"Edit(./{_clean(p)})" for p in paths]
    cmd = capabilities.get("test_command")
    allow = ([f"Bash({cmd.strip()} *)"] if cmd else list(DEFAULT_TEST_ALLOW)) + allow
    allow += command_rules(capabilities.get("commands", []))
    deny = [r for r in DEFAULT_READ_DENY if not _exempt(r, secrets)] + list(HUMAN_ONLY_DENY)
    exempted = [r for r in DEFAULT_READ_DENY if r not in deny]
    for secret in secrets:
        if any(fnmatch.fnmatchcase(_norm_target(secret), _norm_target(r[5:-1])) and r not in exempted
               for r in DEFAULT_READ_DENY if any(c in _norm_target(r[5:-1]) for c in _GLOB)):
            notes.append(f"permissions.deny: secret {secret!r} stays denied by a wildcard rule; "
                         "list the wildcard itself (for example '.env.*.local') to lift it")
    for secret in secrets:
        if secret.startswith("~"):
            notes.append(f"permissions.deny: secret {secret!r} is outside the project and never lifts a deny rule")
    if exempted:
        notes.append("permissions.deny: dropped " + ", ".join(exempted) + " because secrets lists them")
    settings = {
        "sandbox": {
            "enabled": True,
            "filesystem": {"allowWrite": sorted(write)},
            "network": {"allowedDomains": sorted(set(capabilities.get("egress", [])))},
        },
        "permissions": {"deny": sorted(deny), "allow": sorted(set(allow))},
    }
    return settings, notes


def command_rules(commands: list[str]) -> list[str]:
    """``Bash(<command> *)`` for each command (the trailing `` *`` also matches the bare command)."""
    return [f"Bash({c} *)" for c in commands]


def compile_capabilities(capabilities: dict[str, Any]) -> dict[str, Any]:
    """Compile a capabilities dict into the settings fragment above. Pure and deterministic
    (sorted lists). Raises ValueError via :func:`validate`."""
    return _compile(capabilities)[0]


def summarise(capabilities: dict[str, Any], settings: dict[str, Any], notes: list[str]) -> str:
    """Plain-text summary a human can read before merging the fragment."""
    sb, perm = settings["sandbox"], settings["permissions"]
    lines = [
        "Sandbox on. allowWrite (the contract's write paths; see the caveat on the working directory): " + (", ".join(sb["filesystem"]["allowWrite"]) or "nothing"),
        "Network egress allowed to: " + (", ".join(sb["network"]["allowedDomains"]) or "nothing (all outbound blocked)"),
        "Pre-approved: " + (", ".join(perm["allow"]) or "nothing"),
        "Reads refused (Read tool and Edit/Write too): " + (", ".join(perm["deny"]) or "nothing"),
    ]
    data, secrets = capabilities.get("data", []), capabilities.get("secrets", [])
    if data:
        lines.append("Declared data access, NOT enforced by Claude Code settings: " + ", ".join(data))
    if secrets:
        lines.append("Declared secrets, NOT enforced (only used to lift deny rules): " + ", ".join(secrets))
    lines.append("Caveats: the working directory is writable under the sandbox, so allowWrite does not narrow "
                 "writes inside the project; the Edit rules only pre-approve. Deny rules do not apply to "
                 "subprocesses that read files without naming them.")
    lines += notes
    return "\n".join(lines)


def compile_full(capabilities: dict[str, Any]) -> dict[str, Any]:
    """``{"settings": fragment, "unenforced": {"data", "secrets"}, "notes": [...], "summary": str}``."""
    settings, notes = _compile(capabilities)
    return {
        "settings": settings,
        "unenforced": {"data": list(capabilities.get("data", [])), "secrets": list(capabilities.get("secrets", []))},
        "notes": notes,
        "summary": summarise(capabilities, settings, notes),
    }


def compile_for(conn: psycopg.Connection, slug: str) -> dict[str, Any]:
    """Compile a contract's capabilities.

    Returns:
        ``{"slug", "capabilities", "settings", "unenforced", "notes", "summary"}``.

    Raises:
        LookupError: unknown slug. ValueError: invalid capabilities.
    """
    row = conn.execute("SELECT capabilities FROM node WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"unknown node {slug!r}")
    caps = row["capabilities"] or {}
    return {"slug": slug, "capabilities": caps, **compile_full(caps)}
