#!/usr/bin/env python3
"""PreToolUse guard for Bash. Exit 2 blocks; stderr says why. Fails closed: a crash blocks too.

- no --no-verify / -n on commit or push (any unique abbreviation too), no force push, no push to a
  protected branch, no destructive git, no hooksPath override, no merging or approving PRs from a
  session (also through `gh api`), no baseline updates by an agent, no rm -rf of root/home/the repo/.git;
- `git commit` needs a verify stamp that matches the tree (tests-only commits are exempt so
  test-first stays visible in git) and a message that passes check_commit_message.py, whether the
  message comes from -m, --message=, -F/--file or a heredoc. `--amend` always needs the stamp, and
  so do merge, cherry-pick, revert, am and rebase, which also create commits. A commit that names
  paths (`git commit -o a.py`) is judged on those paths;
- the files guard_edit protects (config, baseline, stamp, hooks, settings, frozen tests, current.json,
  .git/config, CLAUDE.md ...) are protected from the shell too: redirects, tee, cp, mv, sed -i, dd,
  rm, find -delete, git checkout/restore, `git diff|log|show --output=<file>`, and inline code or scripts that
  write them are denied
  (rule `shell-write` for test configuration and reverts: while tests are frozen, conftest.py anywhere,
  pytest.ini, tox.ini, setup.cfg, .coveragerc, pytest.toml and the [tool.pytest*]/[tool.coverage*]
  tables of pyproject.toml are frozen with them; a path with a variable or `..` is judged by its name);
- data stores (REC-18): dropdb, DROP/TRUNCATE or a DELETE without WHERE through psql/mysql/sqlite3
  (-c/-e, -f, heredoc, pipe), alembic downgrade/stamp, redis-cli FLUSHALL/FLUSHDB, compose down -v,
  rm -r of a data directory, prisma/rails/django resets; each message names the safe alternative;
- credential reads (REC-02): any command that names ~/.ssh keys, ~/.aws/credentials, ~/.config/gh/hosts.yml,
  ~/.netrc, .env* ... as an operand (metadata commands such as ls are fine, so are exclude patterns and
  grep patterns), `gh auth token`, a bare `env`;
- humans-only scripts: ratchet.py --update/--prune and freeze_tests.py --unfreeze, however spelled
  (module form, globs, variables, copies).

The analysis is on tokens, not raw text (see the shared core below): shell keywords (if/then/do/!/{),
prefixes (sudo, env, xargs, time, timeout, nice, command), runners (uv run, poetry run, npx, ...), `bash -c`,
`eval`, `| sh`, `find -exec` are unwrapped, and a
word inside a quoted message, a comment or a heredoc is not a command. This is a speed bump that says
what to do instead; the sandbox (denyRead/denyWrite), database roles and CI are the boundary.

Fail closed (REC-41): unreadable stdin, a missing tool_input or a crash blocks. Every deny appends
one line to .claude/work/blocks.jsonl (REC-46), secrets scrubbed.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402
import skeleton  # noqa: E402

# ======================= FORM GLUE (plugin) =======================
HOOK = "guard_bash"
NO_STAMP = "no verify stamp. Run /factory:verify, fix what it reports, then commit."
STALE_STAMP = "the tree changed since the last verify (stamp {stamp}, now {now}). Run /factory:verify again."
RERUN_VERIFY = "Run /factory:verify again."
ENV_TEMPLATE_SUFFIXES = cl.ENV_TEMPLATE_SUFFIXES
_CTX: dict = {"root": None, "detail": ""}


def _deny(rule: str, message: str) -> None:
    cl.block(_CTX["root"], HOOK, "Bash", rule, _CTX["detail"], message)


def git(root: str, *args: str) -> str:
    return cl.git(root, *args)


def changed_paths(root: str, *pathspecs: str) -> list[str]:
    return cl.changed_paths(root, *pathspecs)


def message_script() -> str:
    return os.path.join(cl.plugin_root(), "scripts", "check_commit_message.py")


def fingerprint(root: str) -> tuple[str, str]:
    script = os.path.join(cl.plugin_root(), "scripts", "worktree_fingerprint.py")
    p = subprocess.run([sys.executable, "-I", script, root], capture_output=True, text=True, env=cl.safe_git_env())
    return p.stdout.strip(), p.stderr.strip()[:200]


def project_root(hint: str | None) -> str:
    return cl.project_root(hint)


def read_input() -> dict | None:
    return cl.read_hook_input()


def build_policy(root: str) -> dict:
    cfg = cl.load_config(root)
    if cfg.get("_config_error"):
        _deny("config-invalid", f".claude/sdlc.json is invalid: {cfg['_config_error']}. A human repairs it (the file is protected); until then no command runs.")
    contract = cl.load_contract(root)
    extra = list(cfg["paths"].get("protected") or [])
    abs_protected: list[str] = []
    if os.path.isfile(os.path.join(root, ".git")):  # a worktree: .git/config is not at .git/config
        for flag in ("--git-common-dir", "--absolute-git-dir"):
            d = cl.git(root, "rev-parse", flag).strip()
            if d:
                d = cl.real(d if os.path.isabs(d) else os.path.join(root, d))
                abs_protected += [os.path.join(d, "config"), os.path.join(d, "hooks")]
    return {
        "protected": list(cl.ALWAYS_PROTECTED) + extra,
        "is_protected": lambda rel: cl.is_protected(rel, extra),
        "protected_branches": cfg["paths"].get("protected_branches", []),
        "frozen": cl.load_frozen_tests(root, contract) if isinstance(contract, dict) else {},
        "contract_rel": cl.CONTRACT_REL.replace(os.sep, "/"),
        "is_test_path": lambda rel: cl.is_test_path(cfg, rel),
        "stamp": lambda: cl.load_stamp(root),
        "plugin_root": cl.plugin_root(),
        "abs_protected": abs_protected,
    }


CRASH_HINT = "Fix the hook or disable the plugin; nothing runs until then."


# ======================================================================================
# SHARED CORE: one analysis, kept apart from the form glue above it (the plugin's lib/sdlc.py
# helpers and messages). The hook is assembled from the glue, this core and the tail, so a second
# form of the pack can share the core unchanged.
#
# The analysis works on tokens, not on raw text: the command line is split into pipeline
# stages (shlex, heredocs set aside), shell keywords and prefixes (if, then, do, !, {, sudo,
# env, xargs, time ...) are peeled off, and each rule looks at the command word and its
# options. Words that only appear inside a quoted message, a comment or a heredoc are not
# commands, so `git commit -m "docs: warn about rm -rf ~"` is just a commit. Regex over a
# shell is still not a security boundary: the sandbox, database roles and CI are (REC-18,
# REC-02, REC-41); this catches the ordinary spellings and says what to do instead.
# ======================================================================================

GIT_GLOBAL_WITH_ARG = {"--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix"}
GIT_GLOBAL_FLAGS = {"-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects", "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--no-lazy-fetch", "--no-advice", "--no-pager"}

HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n(.*?)(?:\n[ \t]*\2[ \t]*(?=[\n\"')]|$)|\Z)", re.S)
HEREDOC_TOKEN = re.compile(r"__SDLC_HEREDOC_(\d+)__")
SUBST_TOKEN = re.compile(r"__SDLC_SUBST_(\d+)__")
PUNCT = set("();<>|&\n")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def is_redirect(tok: str) -> bool:
    return tok == ">|" or (bool(tok) and set(tok) <= set("<>&") and ("<" in tok or ">" in tok))  # `>|` is a clobbering redirect, not a pipe


PREFIX_ARG_OPTS = {
    "sudo": {"-u", "-g", "-U", "-C", "-D", "-h", "-p", "-r", "-t", "-T"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S"},
    "timeout": {"-s", "-k"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a"},
    "time": set(), "nohup": set(), "exec": set(), "command": set(), "builtin": set(), "setsid": set(),
    "caffeinate": set(), "unbuffer": set(), "chronic": set(),
}
# Shell keywords that precede a command in the same stage: `if true; then dropdb x; fi` is a dropdb.
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "coproc"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
WRAPPERS = {"docker", "docker-compose", "podman", "podman-compose", "kubectl", "uv", "uvx", "poetry", "pipenv", "pdm", "hatch", "rye", "npx", "pnpm", "yarn", "bunx", "pipx"}
SQL_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "sqlite", "pgcli", "mycli", "litecli", "usql"}
MONGO_CLIS = {"mongosh", "mongo"}
REDIS_CLIS = {"redis-cli", "valkey-cli", "keydb-cli"}
DATA_TOOLS = SQL_CLIENTS | REDIS_CLIS | MONGO_CLIS | {"dropdb", "mysqladmin", "alembic", "flask", "prisma", "rails", "rake", "django-admin", "pg_restore"}
READ_ONLY_OK = {
    "cat", "head", "tail", "less", "more", "nl", "bat", "batcat", "grep", "egrep", "fgrep", "rg", "ag", "jq", "yq", "diff", "cmp", "comm",
    "ls", "stat", "wc", "file", "test", "[", "echo", "printf", "basename", "dirname", "realpath", "readlink", "sha256sum", "shasum",
    "md5", "md5sum", "cksum", "du", "which", "type",
}
# Commands that name a file without reading it: a credential path is fine as their operand.
METADATA_OK = {
    "ls", "stat", "wc", "file", "test", "[", "[[", "du", "df", "echo", "printf", "which", "type", "basename", "dirname", "realpath",
    "readlink", "chmod", "chown", "chgrp", "touch", "mkdir", "rm", "rmdir", "unlink", "cd", "pushd", "popd", "export", "unset", "true",
    "false", ":", "set", "exit", "return", "sleep", "kill", "wait", "alias", "unalias", "read", "local", "declare", "typeset",
}
# git subcommands that take the diff options, `--output=<file>` among them
GIT_OUTPUT_SUBS = {"diff", "log", "show", "whatchanged", "diff-tree", "diff-index", "diff-files", "range-diff"}
READ_GIT_SUBS = {"diff", "show", "log", "status", "ls-files", "blame", "check-ignore", "grep", "cat-file", "rev-parse", "rev-list", "ls-tree", "describe", "shortlog", "diff-tree", "diff-index", "branch", "remote", "config", "reflog", "stash", "fetch"}
GIT_CONTENT_SUBS = {"show", "cat-file", "diff", "log", "blame", "grep", "diff-index", "diff-tree", "archive", "stash", "format-patch", "show-branch"}
COPIERS = {"cp", "mv", "scp", "rsync", "install"}
INTERPRETERS = re.compile(r"^(python[0-9.]*|node|nodejs|deno|bun|ruby|perl|php)$")
CODE_FLAGS = {"-c", "-e", "-E", "-r", "-p", "--eval", "eval"}
GREPPERS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
SCRIPT_EDITORS = {"sed", "awk", "gawk", "mawk"}
EXCLUDE_OPTS = {"--exclude", "--exclude-dir", "--include", "--include-dir", "--exclude-from", "-x", "-g", "--glob", "--iglob", "--ignore", "--ignore-file", "--filter", "--files-from"}
DATA_DIR_SPECIFIC = re.compile(r"(?i)^\.?(pgdata|pg_data|dbdata|(postgres(ql)?|pg|mysql|mariadb|redis|mongo(db)?|sqlite|db|database)[-_]?data)$")
DATA_DIR_GENERIC = re.compile(r"(?i)^\.?(data|volumes?)$")
DATA_DIR_SYSTEM = re.compile(r"/var/lib/(postgresql|mysql|redis|mongodb|docker/volumes)")
HUMANS_ONLY = {"ratchet": ("--prune", "--update"), "freeze_tests": ("--unfreeze",)}
# Test configuration (shell mirror of guard_edit's test-config rule): once tests are frozen, conftest.py
# anywhere, pytest.ini, tox.ini, setup.cfg, .coveragerc, pytest.toml, .pytest.toml and the [tool.pytest*] /
# [tool.coverage*] tables of pyproject.toml are frozen with them (a fixture or an addopts `-k` can make a
# frozen test pass without the code changing).
TEST_CONFIG_NAMES = ("conftest.py", "pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg", ".coveragerc")
TEST_CONFIG_MENTION = re.compile(r"(?i)pytest|coverage|addopts|testpaths|--cov|deselect|fail_under|filterwarnings|norecursedirs|python_files|\[tool\b")
# A write in inline code or a script, narrower than the protected-file hint: no bare `>` and no `replace(`,
# so reading a test-config file in code is not a write.
CODE_WRITE = re.compile(
    r"(write|dump\(|unlink|remove\(|rmtree|rename|copy|move\(|truncate|chmod|symlink|sed |tee |os\.system|subprocess|popen"
    r"|\bopen\([^)]*[,=]\s*['\"][^'\"]*[wax+]|appendfile|createwritestream|file\.(write|open)|\bmv |\bcp )"
)
INPLACE_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache", "site-packages"}
DATA_ANY_OPERAND = (".claude/baseline.json", ".claude/work/last_verify.json", ".claude/sdlc.json")
FIND_FILTERS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex", "-type", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-user", "-group", "-perm", "-size", "-empty", "-lname", "-samefile", "-inum", "-links", "-uid", "-gid", "-newerat", "-newermt", "-newerct", "-newerbt", "-readable", "-writable", "-executable", "-xtype"}
WILD_ALL = {"*", ".*", ".[!.]*", ".??*", ".?*", "..?*", "[!.]*", "{,.}*"}
DANGEROUS_GIT_CONFIG = re.compile(r"(?i)^(core\.hookspath|core\.fsmonitor|include\.path|includeif\..*\.path)$")
ALIAS_RISKY = re.compile(r"(?i)(^!|\bpush\b|--force|--no-verify|-f\b|\bmerge\b)")

MSG_DROP = (
    "dropping or truncating a database or table is for a human. Schema changes go through a migration "
    "(alembic revision, then alembic upgrade head); to reset dev data, recreate it through the test fixtures."
)
MSG_DELETE = "DELETE without a WHERE clause empties the table. Add a WHERE that names the rows you mean, or reset dev data through the test fixtures."
MSG_ALEMBIC = (
    "alembic downgrade/stamp rewrites migration history and can drop data. Write a new forward migration that "
    "reverses the change (alembic revision), then alembic upgrade head; a human runs downgrade or stamp."
)
MSG_REDIS = (
    "FLUSHALL/FLUSHDB wipes every key. Delete the keys you mean "
    "(redis-cli --scan --pattern 'prefix:*' | xargs redis-cli UNLINK) or give the tests their own DB index."
)
MSG_VOLUMES = "this deletes the data volumes. `docker compose down` (without -v) stops the stack and keeps the data; a human removes volumes."
MSG_RMDATA = "rm -r of a data directory destroys the store. Stop the service with `docker compose down` (without -v) or reset through the test fixtures; a human deletes data directories."
MSG_CRED = (
    "reading credential files from a session is blocked (~/.ssh keys, ~/.aws credentials, ~/.config/gh/hosts.yml, "
    ".env*, ~/.netrc and similar). The app reads its settings from the environment; if you need a value, ask the human to export it."
)
MSG_ORM = "this command drops or resets the database. Write a forward migration instead; a human resets data."
MSG_RM = "rm -r of the filesystem root, the home directory, the repository (or a parent) or .git is blocked, and so is a wildcard that expands to them. Name what you mean to delete."


# ---------- tokens, pipelines, stages ----------

def tokenize(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to a rough split rather than skip the checks
        return re.findall(r"\|\||&&|[;&|()\n]|[<>]+&?|[^\s;&|()<>]+", text)


def strip_comments(text: str) -> str:
    """Drop `# ...` comments (a # that starts a word, outside quotes): a flag named in a comment is not a flag."""
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c == "\\" and i + 1 < n:
            out.append(c + text[i + 1])
            i += 2
            continue
        elif c in "'\"":
            quote = c
            out.append(c)
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|()"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def lift_substitutions(text: str, subs: list[str]) -> str:
    """Replace each unquoted $(...) or `...` with a __SDLC_SUBST_n__ word and append its inner command
    to `subs`: shlex would otherwise read the parentheses as a subshell and tear the word apart."""
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c == "\\" and i + 1 < n:
            out.append(c + text[i + 1])
            i += 2
            continue
        elif c in "'\"":
            quote = c
            out.append(c)
        elif c == "$" and text.startswith("$(", i) and not text.startswith("$((", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            subs.append(text[i + 2: j - 1 if depth == 0 else j])
            out.append(f"__SDLC_SUBST_{len(subs) - 1}__")
            i = j
            continue
        elif c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            subs.append(text[i + 1: j])
            out.append(f"__SDLC_SUBST_{len(subs) - 1}__")
            i = j + 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def pipelines(cmd: str, bodies: list[str], subs: list[str] | None = None) -> list[list[list[str]]]:
    """[pipeline [stage [tokens]]]. Heredoc bodies are appended to `bodies` (shared across nested
    checks, since a body can sit inside a quoted -c string or a $(...)) and each stage carries a
    __SDLC_HEREDOC_n__ token for its body."""

    def keep(m: re.Match) -> str:
        bodies.append(m.group(4))
        return f" __SDLC_HEREDOC_{len(bodies) - 1}__ {m.group(3)}\n"

    text = strip_comments(HEREDOC_RE.sub(keep, cmd))
    if subs is not None:
        text = lift_substitutions(text, subs)
    out: list[list[list[str]]] = [[[]]]
    for tok in tokenize(text):
        if tok and set(tok) <= PUNCT and not is_redirect(tok):
            if tok in ("|", "|&"):
                out[-1].append([])
            else:
                out.append([[]])
        else:
            out[-1][-1].append(tok)
    return [[s for s in pl if s] for pl in out if any(pl)]


def stage_parts(tokens: list[str], bodies: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    """(words, input-redirect targets, output-redirect targets, stdin texts) for one pipeline stage."""
    words: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    stdin: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = HEREDOC_TOKEN.fullmatch(tok)
        if m:
            n = int(m.group(1))
            stdin.append(bodies[n] if n < len(bodies) else "")
            i += 1
            continue
        if tok.isdigit() and i + 1 < len(tokens) and is_redirect(tokens[i + 1]):
            i += 1
            continue
        if is_redirect(tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if tok == "<<<":
                stdin.append(target)
            elif tok in ("<", "<>"):
                inputs.append(target)
            elif ">" in tok and not (tok.endswith("&") and (target.isdigit() or target == "-")):
                outputs.append(target)
            i += 2
            continue
        words.append(tok)
        i += 1
    return words, inputs, outputs, stdin


# Runners that start the command after them: `uv run git push -f` is a `git push -f`. Each entry is the
# subcommand that runs a command (None: the runner itself does, as npx does) and the options, before or
# after that subcommand, that take a separate value. `uv sync`, `poetry install` are not runner forms.
RUNNERS = {
    "uv": ("run", {"--with", "-w", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory",
                   "--env-file", "--extra", "--group", "--only-group", "--no-group", "--package", "--index", "--default-index",
                   "--index-url", "-i", "--extra-index-url", "--find-links", "-f", "--cache-dir", "--config-file", "--color",
                   "--refresh-package", "--upgrade-package", "-P", "--reinstall-package", "--index-strategy",
                   "--keyring-provider", "--resolution", "--prerelease", "--link-mode", "--exclude-newer",
                   "--python-platform", "--allow-insecure-host", "--no-binary-package", "--no-build-package"}),
    "poetry": ("run", {"-C", "--directory", "-P", "--project"}),
    "pipenv": ("run", set()),
    "pdm": ("run", {"-p", "--project", "-g", "--global"}),
    "rye": ("run", {"--pyproject"}),
    "npx": (None, {"-p", "--package", "--cache", "--userconfig", "-w", "--workspace"}),
}


def runner_end(words: list[str], i: int) -> int | list[str] | None:
    """Where the command behind the runner at words[i] starts (an index), the command itself when the
    runner spells it another way (`uv run -m mod` is `python3 -m mod`, `npx -c 'cmd'` is `bash -c 'cmd'`),
    or None when this is not a runner form (`uv sync`, `poetry install`)."""
    name = os.path.basename(words[i])
    sub, valopts = RUNNERS[name]
    k = i + 1
    if sub is not None:
        while k < len(words) and words[k].startswith("-") and words[k] != "--":
            k += 2 if words[k] in valopts else 1
        if k >= len(words) or words[k] != sub:
            return None
        k += 1
    while k < len(words) and words[k].startswith("-"):
        t = words[k]
        if t == "--":
            return k + 1
        if name == "uv" and t in ("-m", "--module"):
            return ["python3", "-m", *words[k + 1:]]
        if name == "npx" and t in ("-c", "--call"):
            return ["bash", "-c", words[k + 1]] if k + 1 < len(words) else []
        if name == "npx" and t.startswith("--call="):
            return ["bash", "-c", t.split("=", 1)[1]]
        k += 2 if t in valopts else 1
    return k


def unwrap(words: list[str]) -> list[str]:
    """Drop VAR=value assignments, shell keywords (if/then/do/!/{), prefixes such as sudo, env,
    timeout, nice, nohup, xargs, time, command, and runners (uv run, poetry run, npx ...)."""
    i = 0
    while i < len(words):
        w = words[i]
        if ASSIGN.match(w) or w in KEYWORDS:
            i += 1
            continue
        name = os.path.basename(w)
        if name in RUNNERS:
            end = runner_end(words, i)
            if end is None:
                break
            if isinstance(end, list):
                return unwrap(end)
            i = end
            continue
        if name not in PREFIX_ARG_OPTS:
            break
        i += 1
        while i < len(words) and (words[i].startswith("-") or ASSIGN.match(words[i])):
            i += 2 if words[i] in PREFIX_ARG_OPTS[name] else 1
        if name == "timeout" and i < len(words):
            i += 1
    return words[i:]


def parse_args(args: list[str], longs: tuple = (), val_longs: tuple = (), val_shorts: str = "") -> tuple[set, list[str], list[str]]:
    """(options, positionals, words after `--`). Options hold '--name' for a long option (an
    unambiguous prefix is resolved against `longs`, an ambiguous one yields every candidate, since
    git accepts `--no-veri` for --no-verify) and '-x' for each letter of a short cluster. The
    values of options that take one (`-m "msg"`) are skipped, not read as options."""
    opts: set[str] = set()
    pos: list[str] = []
    after: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            after = args[i + 1:]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            cands = [x for x in longs if x == name] or ([x for x in longs if x.startswith(name)] if len(name) >= 3 else [])
            opts.update(cands or [name])
            canon = cands[0] if len(cands) == 1 else name
            if "=" not in a and (canon in val_longs or name in val_longs) and i + 1 < len(args):
                i += 1
        elif a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                opts.add("-" + ch)
                if ch in val_shorts:
                    if j + 1 >= len(a) and i + 1 < len(args):
                        i += 1
                    break
                j += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos, after


def recursive(args: list[str]) -> bool:
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            if len(a) >= 5 and "--recursive".startswith(a.split("=")[0]):
                return True
        elif a.startswith("-") and re.search(r"[rR]", a[1:]):
            return True
    return False


# ---------- paths ----------

def real(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def read_text(path: str, limit: int = 1_000_000) -> str:
    try:
        if os.path.isfile(path) and os.path.getsize(path) <= limit:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    except OSError:
        pass
    return ""


def is_env_name(name: str) -> bool:
    name = name.lower()
    if name.startswith(".") and any(c in name for c in "*?["):
        return any(fnmatch.fnmatchcase(x, name) for x in (".env", ".env.local", ".envrc"))
    if not re.fullmatch(r"\.env(rc|\..+)?", name):
        return False
    return not name.endswith(ENV_TEMPLATE_SUFFIXES)


def is_credential_path(path: str, env_names: bool = True) -> bool:
    """A file that holds keys or tokens: ~/.ssh keys (not .pub, known_hosts, config), ~/.aws/credentials
    (not config), ~/.config/gh/hosts.yml (not config.yml), ~/.netrc, ~/.npmrc, ~/.pypirc, ~/.git-credentials,
    ~/.docker/config.json, ~/.kube/config, gpg private keys and .env* (templates such as .env.example are fine)."""
    parts = [p for p in path.strip().replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return False
    low = [p.lower() for p in parts]
    for i, part in enumerate(low):
        rest = low[i + 1:]
        if part == ".ssh":
            return not (rest and (rest[-1].endswith(".pub") or rest[-1] in ("known_hosts", "config", "authorized_keys")))
        if part == ".aws":
            return rest != ["config"]
        if part == ".config" and rest and rest[0] == "gh":
            return rest != ["gh", "config.yml"]
        if part == ".gnupg" and (not rest or rest[0].startswith("private-keys") or rest[0] in ("secring.gpg", "trustdb.gpg")):
            return True
        if part == ".docker" and rest == ["config.json"]:
            return True
        if part == ".kube" and rest == ["config"]:
            return True
    if low[-1] in (".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials"):
        return True
    return env_names and is_env_name(parts[-1])


def mentions_credentials(text: str) -> bool:
    if re.search(r"(?<![\w.])\.(ssh|aws)/(?!config\b|known_hosts\b)|(?<![\w.])\.config/gh/hosts", text):
        return True
    for m in re.finditer(r"(?<![\w.])(\.env(?:rc|\.[\w.-]+)?)(?![\w])", text):
        if is_env_name(m.group(1)):
            return True
    return False


READ_CALLEES = {
    "open", "read", "read_text", "read_bytes", "readfile", "readfilesync", "readlines", "readfilesync", "load_dotenv", "dotenv_values",
    "dotenv", "config", "file_get_contents", "fopen", "createreadstream", "path", "load", "slurp", "readfileutf8", "text", "readtextfile",
}


def string_literals(code: str) -> list[tuple[str, bool]]:
    """(text, handed to a read/open call) for each quoted string in inline code: open('.env') is a read,
    print('.env files are ignored') is not."""
    out = []
    for m in re.finditer(r"""(?:([\w.]+)\s*)?(\(\s*)?[rbfuRBFU]{0,2}(['"])((?:\\.|(?!\3).)*)\3""", code, re.S):
        callee = (m.group(1) or "").split(".")[-1].lower() if m.group(2) else ""
        out.append((m.group(4), callee in READ_CALLEES))
    return out


def literal_prefix(pattern: str) -> str:
    cut = min([pattern.index(c) for c in "*?[" if c in pattern] or [len(pattern)])
    head = pattern[:cut]
    return head[: head.rfind("/") + 1] if cut < len(pattern) else head


def destructive_sql(text: str) -> str | None:
    if re.match(r"^--?[A-Za-z-]+=", text):
        text = text.split("=", 1)[1]
    elif re.match(r"^-[ce].", text):
        text = text[2:]
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)  # comments hide nothing: DROP/**/TABLE is DROP TABLE,
    text = re.sub(r"--[^\n]*", " ", text)                # and `DELETE FROM t -- where` has no WHERE
    text = re.sub(r"'(?:[^']|'')*'", "''", text)         # a word inside a string literal is data, not SQL
    for stmt in text.split(";"):
        if re.search(r"(?i)\b(drop\s+[a-z]|truncate\b)", stmt):
            return MSG_DROP
        if re.search(r"(?i)\bdelete\s+from\b", stmt) and not re.search(r"(?i)\bwhere\b", stmt):
            return MSG_DELETE
    return None


def redis_command(args: list[str]) -> str:
    """First positional word of a redis-cli invocation, skipping options and their values."""
    takes = {"-h", "-p", "-a", "-n", "-u", "-s", "--user", "--pass", "-r", "-i", "-d", "-D", "--cacert", "--cert", "--key", "--sni", "-x", "--bigkeys"}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            i += 2 if a in takes and a != "--bigkeys" else 1
            continue
        return a
    return ""


# ---------- git option tables (long options a unique prefix of which git accepts) ----------

COMMIT_LONGS = ("--all", "--allow-empty", "--allow-empty-message", "--amend", "--author", "--branch", "--cleanup", "--date", "--dry-run",
                "--edit", "--file", "--fixup", "--gpg-sign", "--include", "--interactive", "--long", "--message", "--no-edit", "--no-gpg-sign",
                "--no-post-rewrite", "--no-signoff", "--no-status", "--no-verify", "--null", "--only", "--patch", "--pathspec-from-file",
                "--pathspec-file-nul", "--porcelain", "--quiet", "--reedit-message", "--reset-author", "--reuse-message", "--short", "--signoff",
                "--squash", "--status", "--template", "--trailer", "--untracked-files", "--verbose", "--verify")
COMMIT_VAL_LONGS = ("--author", "--cleanup", "--date", "--file", "--fixup", "--message", "--reedit-message", "--reuse-message", "--squash",
                    "--template", "--trailer", "--pathspec-from-file")
PUSH_LONGS = ("--all", "--atomic", "--delete", "--dry-run", "--exec", "--follow-tags", "--force", "--force-if-includes", "--force-with-lease",
              "--mirror", "--no-force-with-lease", "--no-verify", "--no-thin", "--porcelain", "--progress", "--prune", "--push-option",
              "--quiet", "--receive-pack", "--recurse-submodules", "--repo", "--set-upstream", "--signed", "--tags", "--thin", "--verbose", "--verify")
PUSH_VAL_LONGS = ("--exec", "--push-option", "--receive-pack", "--repo")
RESET_LONGS = ("--hard", "--soft", "--mixed", "--merge", "--keep", "--patch", "--quiet", "--recurse-submodules")
CLEAN_LONGS = ("--force", "--dry-run", "--quiet", "--interactive")
BRANCH_LONGS = ("--delete", "--force", "--move", "--copy", "--list", "--show-current", "--set-upstream-to", "--unset-upstream", "--track", "--no-track")
CHECKOUT_LONGS = ("--force", "--discard-changes", "--merge", "--detach", "--orphan", "--quiet", "--staged", "--worktree", "--source", "--patch", "--ours", "--theirs")
CONFIG_READ = ("--get", "--get-all", "--get-regexp", "--list", "-l", "--unset", "--unset-all", "--get-urlmatch", "--show-origin", "--name-only", "--remove-section", "--edit")
GIT_KNOWN_SUBS = {
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "checkout", "cherry-pick", "clean", "clone", "commit", "config",
    "describe", "diff", "fetch", "filter-branch", "format-patch", "gc", "grep", "init", "log", "ls-files", "ls-remote", "ls-tree", "merge",
    "mv", "notes", "pull", "push", "range-diff", "rebase", "reflog", "remote", "reset", "restore", "revert", "rev-list", "rev-parse", "rm",
    "shortlog", "show", "show-ref", "stash", "status", "submodule", "switch", "tag", "update-ref", "worktree", "cat-file", "check-ignore",
    "cherry", "count-objects", "diff-index", "diff-tree", "for-each-ref", "fsck", "hash-object", "ls-tree", "maintenance", "merge-base",
    "name-rev", "prune", "read-tree", "symbolic-ref", "update-index", "var", "version", "help", "gui", "filter-repo", "difftool", "mergetool",
}
STAMPED_SUBS = ("merge", "cherry-pick", "revert", "am", "rebase")
STAMP_EXEMPT = {"--abort", "--quit", "--skip", "--help", "-h"}


class Checker:
    """One analysis of one Bash command line. `policy` comes from the form glue: protected-file
    globs, frozen tests, protected branches, the verify stamp."""

    def __init__(self, root: str, cwd: str | None, policy: dict):
        self.root = real(root)
        self.cwd = real(cwd) if cwd and os.path.isdir(cwd) else self.root
        self.home_raw = os.path.expanduser("~")
        self.home = real(self.home_raw)
        self.policy = policy
        self.bodies: list[str] = []
        self.vars: dict[str, str] = {}
        self.add_everything = False
        self.add_specs: list[str] = []
        self.commits: list[tuple[list[str], tuple, list[str]]] = []
        self.stamped: list[str] = []
        self.all_subs: list[str] = []
        self.frozen_lower = {k.lower(): k for k in policy.get("frozen", {})}
        self.frozen_active = bool(self.frozen_lower)  # test configuration is frozen only while tests are
        self.cur_text = ""        # words and heredoc bodies of the stage being checked (pyproject edits are judged on it)
        self.inplace_safe = False  # the current sed/perl -i edit is a plain substitution that names no pytest setting
        literals = [p for p in policy["protected"] if not any(c in p for c in "*?[")]
        self.literals = literals + ([policy["contract_rel"]] if policy.get("contract_rel") else [])
        self.prefixes = [literal_prefix(p) for p in policy["protected"] if any(c in p for c in "*?[")]
        self.prefixes += [p.lower() for p in self.frozen_lower]

    # ----- expansion and path resolution -----

    def expand(self, word: str) -> str:
        w = word
        if w == "~" or w.startswith("~/"):
            w = self.home_raw + w[1:]

        def sub(m: re.Match) -> str:
            name = m.group(1) or m.group(2)
            if name == "HOME":
                return self.home_raw
            if name == "PWD":
                return self.cwd
            if name == "CLAUDE_PROJECT_DIR":
                return self.root
            return self.vars.get(name, m.group(0))

        return re.sub(r"\$(?:\{(\w+)\}|(\w+))", sub, w)

    def resolve(self, word: str) -> tuple[str, bool] | None:
        """(absolute path with symlinks resolved, has glob characters) or None when the word is not a
        path we can know (unset variable, command substitution, URL, option)."""
        w = self.expand(word)
        if w.startswith("@"):
            w = w[1:]
        if not w or "$" in w or "`" in w or "://" in w or w.startswith("-") or "\n" in w or len(w) > 4096:
            return None
        is_glob = any(c in w for c in "*?[")
        p = w if os.path.isabs(w) else os.path.join(self.cwd, w)
        return real(p), is_glob

    def rel_of(self, p: str) -> str | None:
        lp, lr = p.lower(), self.root.lower().rstrip("/")
        if lp == lr:
            return "."
        if lp.startswith(lr + "/"):
            return p[len(lr) + 1:]
        return None

    def classify(self, rel: str, destructive: bool) -> tuple[str, str] | None:
        low = rel.lower()
        if rel == ".":
            return ("ancestor", ".") if destructive else None
        if self.policy["is_protected"](rel):
            return ("protected", rel)
        if low in self.frozen_lower:
            return ("frozen", self.frozen_lower[low])
        if self.policy.get("contract_rel") and low == self.policy["contract_rel"].lower():
            return ("contract", rel)
        if self.frozen_active:
            base = low.rsplit("/", 1)[-1]
            if base in TEST_CONFIG_NAMES:
                return ("testconfig", rel)
            if base == "pyproject.toml":
                return ("pyproject", rel)
        if destructive and any(x.lower().startswith(low + "/") for x in self.literals + self.prefixes):
            return ("ancestor", rel)
        if destructive and self.frozen_active and self.holds_test_config(rel):
            return ("testconfig-dir", rel)
        return None

    def holds_test_config(self, rel: str) -> bool:
        """A directory that contains a conftest.py, pytest.ini ... (rm -r of it deletes test configuration).
        Virtualenvs, node_modules and caches are skipped: their tox.ini and setup.cfg are not ours."""
        if any(part in INPLACE_SKIP_DIRS for part in rel.lower().split("/")):
            return False
        top = os.path.join(self.root, rel)
        if not os.path.isdir(top):
            return False
        seen = 0
        for _dp, dns, fns in os.walk(top):
            dns[:] = [d for d in dns if d.lower() not in INPLACE_SKIP_DIRS]
            for f in fns:
                seen += 1
                if f.lower() in TEST_CONFIG_NAMES:
                    return True
            if seen > 5000:
                break
        return False

    def hit(self, word: str, destructive: bool = False) -> tuple[str, str] | None:
        res = self.resolve(word)
        if not res:
            return None
        p, is_glob = res
        rels: list[str] = []
        r = self.rel_of(p)
        if is_glob:
            for m in sorted(glob.glob(p))[:300]:
                rr = self.rel_of(real(m))
                if rr is not None:
                    rels.append(rr)
            if r is not None:
                rels += [lit for lit in self.literals if fnmatch.fnmatch(lit.lower(), r.lower())]
                rels += [k for k in self.frozen_lower.values() if fnmatch.fnmatch(k.lower(), r.lower())]
        elif r is not None:
            rels.append(r)
        for extra in self.policy.get("abs_protected", []):
            if p == extra or p.startswith(extra + "/"):
                return ("protected", p)
        for rel in rels:
            found = self.classify(rel, destructive)
            if found:
                return found
        return None

    # ----- denials -----

    def deny_hit(self, found: tuple[str, str], how: str) -> None:
        kind, rel = found
        if kind == "frozen":
            _deny("frozen-test", f"{rel} is frozen by /tests; if the test is wrong, stop and ask a human to run `python3 -I scripts/freeze_tests.py --unfreeze {rel}` ({how} is an edit too).")
        if kind == "contract":
            _deny("contract-lift", f"{rel} is changed by /ticket and /ship, not through the shell ({how}).")
        if kind == "ancestor":
            _deny("protected-file", f"{how} would delete {rel}, which holds protected files (config, baseline, stamp, hooks). Name the files you mean to delete; edits to the protected ones go through a human.")
        rule = "humans-only" if rel.lower() == ".claude/baseline.json" else "protected-file"
        _deny(rule, f"{rel} is maintained by humans; changing it through the shell ({how}) is the same as editing it. Say what you need changed and why, and stop.")

    def deny_test_config(self, found: tuple[str, str], how: str) -> None:
        kind, rel = found
        if kind == "pyproject":
            if self.inplace_safe:
                return  # `sed -i 's/0.1.0/0.2.0/' pyproject.toml`: a substitution that names no pytest or coverage setting
            _deny("shell-write", f"{rel} holds the [tool.pytest*] and [tool.coverage*] tables, which are frozen with the tests while /tests has frozen files; "
                  f"changing the file through the shell ({how}) could change them. Edits to test configuration go through a human: say what you need changed and why, and stop.")
        if kind == "testconfig-dir":
            _deny("shell-write", f"{how} would delete {rel}, which holds test configuration (conftest.py, pytest.ini ...) that is frozen with the tests. Name the files you mean to delete; edits to test configuration go through a human.")
        _deny("shell-write", f"{rel} is test configuration (pytest or coverage settings) and is frozen with the tests while /tests has frozen files; changing it through the shell ({how}) is the same as editing it. "
              f"Edits go through a human: ask them to change it or to run `python3 -I scripts/freeze_tests.py --unfreeze <test>`, and stop.")

    def check_unresolved(self, word: str, how: str) -> None:
        """A write target the guard cannot place: it has a variable or substitution in it (`$D/conftest.py`)
        or a `..` that leaves the project. When its last segment names a frozen test, a protected file or
        test configuration the stage is denied anyway."""
        w = self.expand(word)
        if "://" in w or w.startswith("-") or len(w) > 4096:
            return
        dotdot = ".." in w.replace("\\", "/").split("/")
        if not ("$" in w or "`" in w or dotdot):
            return
        base = w.rstrip("/").rsplit("/", 1)[-1].lower()
        if not base or "$" in base or "`" in base:
            return
        names = {os.path.basename(k).lower() for k in self.frozen_lower}
        names |= {os.path.basename(p).lower() for p in self.literals if len(os.path.basename(p)) >= 8 and not re.match(r"\.env", os.path.basename(p))}
        if self.frozen_active:
            names |= set(TEST_CONFIG_NAMES) | {"pyproject.toml"}
        if base in names:
            kind = "test configuration frozen with the tests" if base in TEST_CONFIG_NAMES or base == "pyproject.toml" else "a frozen or protected file"
            _deny("shell-write", f"{word} ends in {base}, which is {kind}; the path has a variable or a `..` in it, so the guard cannot tell where it points and treats it as that file ({how}). "
                  f"Write the literal path if it is yours to change; edits to frozen or protected files go through a human.")

    def check_write(self, word: str, how: str, destructive: bool = False, allow_stamped_rm: bool = False) -> None:
        found = self.hit(word, destructive)
        if not found:
            res = self.resolve(word)
            if res is None or self.rel_of(res[0]) is None:
                self.check_unresolved(word, how)
            return
        if found[0] in ("testconfig", "pyproject", "testconfig-dir"):
            self.deny_test_config(found, how)
            return
        if found[0] == "contract" and allow_stamped_rm and self.stamp_matches():
            return  # /ship deletes current.json after a green verify
        if found[0] == "protected" and re.search(r"(?i)(^|/)\.env", found[1]) and how.startswith(("cp ", "install ", "rsync ")):
            return  # bootstrapping .env from .env.example is the human workflow; reading it stays blocked
        self.deny_hit(found, how)

    # ----- the walk -----

    def run(self, cmd: str) -> None:
        self.walk(cmd, 0)
        self.finish()

    def walk(self, cmd: str, depth: int) -> None:
        if depth > 4:
            _deny("nesting", "the command nests shells too deeply to check; run the inner command directly.")
        start = len(self.all_subs)
        pls = pipelines(cmd, self.bodies, self.all_subs)
        for inner in list(self.all_subs[start:]):
            self.walk(inner, depth + 1)
        for pl in pls:
            parts = [stage_parts(stage, self.bodies) for stage in pl]
            for idx in range(len(parts)):
                self.stage(idx, parts, depth)

    def stage(self, idx: int, parts: list, depth: int) -> None:
        words, inputs, outputs, stdin = parts[idx]
        for tok in words + inputs + outputs:
            for a, b in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", tok):
                self.walk(a or b, depth + 1)
        raw = words
        # assignments: `R=scripts/ratchet.py; python3 $R --update`, `g=gh; $g pr merge 1`
        lead = 0
        while lead < len(raw) and (ASSIGN.match(raw[lead]) or (raw[lead] in ("export", "declare", "local", "readonly") and lead + 1 < len(raw) and ASSIGN.match(raw[lead + 1]))):
            if ASSIGN.match(raw[lead]):
                k, v = raw[lead].split("=", 1)
                self.vars[k] = self.expand(v)
                self.check_git_env(k, v)
            lead += 1
        words = [self.expand(w) for w in raw]
        self.cur_text = " ".join(words) + " " + " ".join(stdin)
        self.inplace_safe = False
        for tgt in outputs:
            self.check_write(tgt, "a redirect (>)")
        for src in inputs:
            res = self.resolve(src)
            if res and is_credential_path(res[0]):
                _deny("credential-read", MSG_CRED)
        cmdwords = words[lead:]
        if cmdwords and os.path.basename(cmdwords[0]) in ("env", "printenv") and not [a for a in cmdwords[1:] if not a.startswith("-")]:
            _deny("credential-read", "dumping the environment can print tokens and passwords. Print the one variable you need (printenv NAME) or ask the human.")
        w = unwrap(words)
        if not w:
            return
        self.command(w, idx, parts, depth, stdin, inputs)

    def command(self, w: list[str], idx: int, parts: list, depth: int, stdin: list[str], inputs: list[str]) -> None:
        name = os.path.basename(w[0])
        args = w[1:]
        self.cur_text = " ".join(w) + " " + " ".join(stdin)
        self.inplace_safe = False
        # --- shells: bash -c '...', docker exec db sh -c '...', `... | sh`, `cat <<EOF | bash` ---
        shell_at = 0 if name in SHELLS else None
        if name in WRAPPERS:
            shell_at = next((i for i, t in enumerate(w) if os.path.basename(t) in SHELLS), None)
        if shell_at is not None:
            inner = None
            for i, a in enumerate(w[shell_at + 1:], start=shell_at + 1):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a):
                    j = i + 1
                    while j < len(w) and w[j] == "--":
                        j += 1
                    inner = w[j] if j < len(w) else None
                    break
            if inner is not None:
                self.walk(inner, depth + 1)
            else:
                for body in stdin + self.piped_text(idx, parts):
                    self.walk(body, depth + 1)
        if name == "eval":
            self.walk(" ".join(args), depth + 1)
        if name == "cd" or name == "pushd":
            target = next((a for a in args if not a.startswith("-")), None)
            res = self.resolve(target) if target else (self.home, False)
            if res and not res[1]:  # a directory that is not there yet is still where the next words resolve
                self.cwd = res[0]
            return
        if name == "find":
            self.check_find(args, idx, parts, depth, stdin)
        self.check_writes(name, args)
        if name == "git":
            self.git_stage(args, depth, self.stdin_for(idx, parts, stdin, inputs))
        elif name == "gh":
            self.gh_stage(args)
        self.check_humans_only(w)
        self.check_rm(name, args)
        self.check_data_store(w, self.stdin_for(idx, parts, stdin, inputs))
        self.check_credentials(name, w, args)
        self.check_code(name, w, args, stdin)
        self.check_operands(name, w, args)

    def piped_text(self, idx: int, parts: list) -> list[str]:
        """Text a text-producing earlier stage (echo, printf, cat <<EOF, cat file) feeds this stage."""
        out = []
        for j in range(idx):
            pw, _pin, _pout, pstdin = parts[j]
            u = unwrap([self.expand(x) for x in pw])
            if not u:
                continue
            n = os.path.basename(u[0])
            if n in ("echo", "printf", "cat", "tee", "head", "tail", "tr", "sed", "awk", "yes"):
                out.append(" ".join(a for a in u[1:] if not a.startswith("-")))
                out += pstdin
                if n in ("cat", "head", "tail"):
                    for a in u[1:]:
                        res = self.resolve(a) if not a.startswith("-") else None
                        if res and not res[1] and self.rel_of(res[0]) is not None:
                            out.append(read_text(res[0]))
        return out

    def stdin_for(self, idx: int, parts: list, stdin: list[str], inputs: list[str]) -> list[str]:
        out = list(stdin) + self.piped_text(idx, parts)
        for path in inputs:
            res = self.resolve(path)
            if res and not res[1] and self.rel_of(res[0]) is not None:
                out.append(read_text(res[0]))
        return out

    # ----- find -----

    def check_find(self, args: list[str], idx: int, parts: list, depth: int, stdin: list[str]) -> None:
        starts = []
        for a in args:
            if a.startswith("-") or a in ("(", ")", "!"):
                break
            starts.append(a)
        filtered = any(a in FIND_FILTERS for a in args)
        deleting = "-delete" in args
        for i, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                sub = []
                for t in args[i + 1:]:
                    if t in (";", "+", "\\;"):
                        break
                    sub.append(t)
                if sub:
                    u = unwrap(sub)
                    if u and os.path.basename(u[0]) in ("rm", "unlink", "shred"):
                        deleting = True
                    if u:
                        self.command(u, idx, parts, depth, stdin, [])
        if deleting:
            for s in starts or ["."]:
                res = self.resolve(s)
                if not filtered and res and not res[1]:
                    self.check_rm_target(res[0], recursive_flag=True)
                    self.check_data_dir(s, True)
                self.check_write(s, "find -delete", destructive=not filtered)
        if any(a in ("-name", "-iname", "-path") for a in args) and any(os.path.basename(a) in ("cat", "head", "tail", "cp", "grep", "base64", "less") for a in args):
            for i, a in enumerate(args[:-1]):
                if a in ("-name", "-iname", "-path", "-ipath") and (is_credential_path(args[i + 1]) or is_env_name(os.path.basename(args[i + 1]))):
                    _deny("credential-read", MSG_CRED)

    # ----- writes through the shell -----

    def check_writes(self, name: str, args: list[str]) -> None:
        """Mirror of guard_edit: a write target that is a protected, frozen or contract file is denied."""
        plain = [a for a in args if not a.startswith("-")]
        opts = [a for a in args if a.startswith("-")]
        how = f"`{name}`"
        if name in ("tee", "sponge"):
            for a in plain:
                self.check_write(a, how)
        elif name in ("cp", "install", "rsync", "ln", "scp"):
            tdir = self.target_dir(args)
            srcs = list(plain)
            if tdir is not None and tdir in srcs:
                srcs.remove(tdir)
                dest = [tdir]
            else:
                dest = plain[-1:] if len(plain) >= 2 else []
                srcs = plain[:-1]
            if name == "rsync" and any(o.startswith("--delete") for o in opts) and plain:
                dest = plain[-1:]
            for a in dest:
                self.check_write(a, f"{name} {a}")
            if name != "scp":
                for a in self.into_dir(dest, srcs):
                    self.check_write(a, f"{name} {a}")
            if name in ("cp", "ln", "install", "rsync", "scp"):
                for a in plain[:-1]:
                    base = os.path.basename(a)
                    if os.path.splitext(base)[0] in HUMANS_ONLY or any(fnmatch.fnmatchcase(h + ".py", base) for h in HUMANS_ONLY):
                        _deny("humans-only", f"copying or linking {base} is a way round the humans-only rule on it. A human runs it; say what changed and why, and stop.")
        elif name == "mv":
            for a in plain:
                self.check_write(a, f"mv {a}", destructive=True)
            tdir = self.target_dir(args)
            srcs = [a for a in plain if a != tdir] if tdir is not None else plain[:-1]
            for a in self.into_dir([tdir] if tdir is not None else plain[-1:], srcs):
                self.check_write(a, f"mv {a}", destructive=True)
        elif name in ("sed", "perl", "ruby", "awk", "gawk") and (any(re.match(r"^-[A-Za-z]*i", o) for o in opts) or "--in-place" in opts or any(o.startswith("--in-place") for o in opts) or "inplace" in " ".join(args[:3])):
            scripts = [a for a in plain if not (self.resolve(a) and os.path.exists(self.resolve(a)[0]))]
            self.inplace_safe = bool(scripts) and all(re.match(r"^s[^\w\s]", sc) for sc in scripts) and not TEST_CONFIG_MENTION.search(" ".join(scripts))
            for a in plain:
                self.check_write(a, f"{name} -i")
            self.inplace_safe = False
        elif name == "dd":
            for a in args:
                if a.startswith("of="):
                    self.check_write(a[3:], "dd of=")
        elif name in ("truncate", "shred", "unlink", "chmod", "chown", "chgrp", "chattr", "touch", "setfacl", "patch", "mkfifo", "rmdir"):
            for a in plain:
                self.check_write(a, f"`{name}`", destructive=name in ("shred", "unlink", "rmdir"), allow_stamped_rm=(name == "unlink"))
        elif name == "curl":
            for i, a in enumerate(args[:-1]):
                if a in ("-o", "--output"):
                    self.check_write(args[i + 1], "curl -o")
        elif name == "wget":
            for i, a in enumerate(args[:-1]):
                if a in ("-O", "--output-document"):
                    self.check_write(args[i + 1], "wget -O")

    @staticmethod
    def target_dir(args: list[str]) -> str | None:
        """The directory named by `-t DIR` / `--target-directory=DIR` of cp, mv, install, ln, rsync."""
        for i, a in enumerate(args):
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                return args[i + 1]
            if a.startswith("--target-directory="):
                return a.split("=", 1)[1]
        return None

    def into_dir(self, dest: list[str], srcs: list[str]) -> list[str]:
        """`cp x.py tests/` writes tests/x.py: when the destination is an existing directory, the files it
        gets are the sources' base names inside it (a glob source is expanded)."""
        out: list[str] = []
        for d in dest:
            res = self.resolve(d)
            if not res or res[1] or not os.path.isdir(res[0]):
                continue
            for src in srcs:
                sres = self.resolve(src)
                if sres and sres[1]:
                    names = [os.path.basename(m) for m in sorted(glob.glob(sres[0]))[:300]]
                else:
                    names = [os.path.basename(src.rstrip("/"))]
                out += [os.path.join(self.expand(d), n) for n in names if n]
        return out

    def check_rm(self, name: str, args: list[str]) -> None:
        if name not in ("rm", "rmdir", "unlink", "shred"):
            return
        rec = recursive(args)
        _o, pos, after = parse_args(args)
        for a in pos + after:
            res = self.resolve(a)
            if rec and res:
                self.check_rm_target(res[0], res[1])
            self.check_data_dir(a, rec)
            self.check_write(a, f"`rm {a}`", destructive=True, allow_stamped_rm=True)

    def check_rm_target(self, p: str, is_glob: bool = False, recursive_flag: bool = True) -> None:
        """rm -r of /, home, the repo root or a parent, .git, or a wildcard expanding to those."""
        base = os.path.basename(p)
        dirs = [p]
        if is_glob and base in WILD_ALL:
            dirs = [os.path.dirname(p)]
            targets_all = True
        else:
            targets_all = False
        for d in dirs:
            dl = d.lower().rstrip("/") or "/"
            root = self.root.lower().rstrip("/")
            home = self.home.lower().rstrip("/")
            danger = (
                dl == "/"
                or dl == home
                or root == dl
                or root.startswith(dl.rstrip("/") + "/")
                or home.startswith(dl.rstrip("/") + "/")
                or (not targets_all and dl == root + "/.git")
            )
            if danger:
                _deny("rm-rf", MSG_RM)

    def check_data_dir(self, word: str, rec: bool) -> None:
        """rm -r of a data directory: a database-named directory always, a generic `data` or `volumes`
        only when it holds a store (PG_VERSION, *.sqlite, *.db, dump.rdb ...) or a compose file mounts it."""
        w = self.expand(word)
        if DATA_DIR_SYSTEM.search(w) and (rec or w.endswith("/*")):
            _deny("data-store", f"`rm {word}`: " + MSG_RMDATA)
        target, wildcard = w, False
        for suffix in ("/**", "/*", "/."):
            if target.endswith(suffix):
                target, wildcard = target[: -len(suffix)] or "/", True
                break
        if not (rec or wildcard):
            return
        target = target.rstrip("/") or "/"
        base = os.path.basename(target)
        if DATA_DIR_SPECIFIC.match(base):
            _deny("data-store", f"`rm -r {word}`: " + MSG_RMDATA)
        if DATA_DIR_GENERIC.match(base):
            res = self.resolve(target)
            if res and not res[1] and os.path.isdir(res[0]) and (self.looks_like_store(res[0]) or self.compose_mounts(res[0])):
                _deny("data-store", f"`rm -r {word}`: " + MSG_RMDATA)

    @staticmethod
    def looks_like_store(path: str) -> bool:
        try:
            seen = 0
            for entry in os.scandir(path):
                seen += 1
                n = entry.name.lower()
                if n in ("pg_version", "ibdata1", "mongod.lock", "dump.rdb", "appendonly.aof") or n.endswith((".sqlite", ".sqlite3", ".db", ".rdb")):
                    return True
                if entry.is_dir(follow_symlinks=False) and seen < 50:
                    for sub in os.scandir(entry.path):
                        if sub.name == "PG_VERSION":
                            return True
                if seen > 200:
                    break
        except OSError:
            pass
        return False

    def compose_mounts(self, path: str) -> bool:
        rel = self.rel_of(path)
        if not rel or rel == ".":
            return False
        for name in os.listdir(self.root) if os.path.isdir(self.root) else []:
            if re.fullmatch(r"(docker-)?compose[\w.-]*\.ya?ml", name):
                text = read_text(os.path.join(self.root, name))
                if re.search(r"(?m)^\s*-\s*[\"']?\.?/?" + re.escape(rel) + r"/?[\"']?\s*:", text):
                    return True
        return False

    # ----- humans-only scripts -----

    def check_humans_only(self, w: list[str]) -> None:
        for i, tok in enumerate(w):
            base = os.path.basename(tok)
            names: set[str] = set()
            stem = os.path.splitext(base)[0]
            if stem in HUMANS_ONLY and (base.endswith(".py") or base == stem):
                names.add(stem)
            if any(c in base for c in "*?["):
                names.update(h for h in HUMANS_ONLY if fnmatch.fnmatchcase(h + ".py", base) or fnmatch.fnmatchcase(h, base))
            if i > 0 and w[i - 1] == "-m":
                last = tok.split(".")[-1]
                if last in HUMANS_ONLY:
                    names.add(last)
            for h in names:
                flags = HUMANS_ONLY[h]
                for a in w[i + 1:]:
                    opt = a.split("=", 1)[0]
                    if len(opt) >= 3 and opt.startswith("--") and any(f.startswith(opt) for f in flags):
                        _deny("humans-only", f"`{h} {opt}` is run by a human only. Say what changed and why, and stop.")

    # ----- inline code and scripts -----

    def check_code(self, name: str, w: list[str], args: list[str], stdin: list[str]) -> None:
        """Inline code (python -c, node -e, a heredoc to python) and agent-written script files."""
        is_interp = INTERPRETERS.match(name) is not None
        if not is_interp and name not in SHELLS:
            return
        codes: list[str] = []
        if is_interp:
            for i, a in enumerate(args[:-1]):
                if a in CODE_FLAGS or (a.startswith("-") and not a.startswith("--") and len(a) <= 4 and a[-1] in "ce"):
                    codes.append(args[i + 1])
            codes += stdin
        if not codes:
            script = next((a for a in args if not a.startswith("-")), None)
            res = self.resolve(script) if script else None
            if res and not res[1] and os.path.isfile(res[0]):
                sp = res[0]
                inside = self.rel_of(sp)
                plugin_root = (self.policy.get("plugin_root") or "").rstrip("/")
                if not ((inside is not None and self.policy["is_protected"](inside)) or (plugin_root and sp.startswith(plugin_root + "/"))):
                    codes.append(read_text(sp, 300_000))
        for code in codes:
            self.scan_code(name, code)

    def scan_code(self, name: str, code: str) -> None:
        if not code.strip():
            return
        lits = string_literals(code)
        is_py_like = INTERPRETERS.match(name) is not None
        # credentials: only a path handed to a call (open('.env')) or in a key directory counts
        if is_py_like:
            for lit, first_arg in lits:
                if is_credential_path(lit, env_names=first_arg) or ("/" in lit and is_credential_path(os.path.expanduser(lit), env_names=False)):
                    _deny("credential-read", MSG_CRED)
        # humans-only scripts run from code
        for h, flags in HUMANS_ONLY.items():
            if re.search(r"\b" + h + r"\b", code) and re.search("|".join(re.escape(f) for f in flags), code):
                _deny("humans-only", f"inline code runs {h} with {flags[0]}-style options; a human does that. Say what changed and why, and stop.")
        # writes to protected files from inline code or an agent-written script
        low = code.lower().replace("\\", "/")
        needles = {p.lower() for p in self.literals if p.lower() != self.policy.get("contract_rel", "").lower()}
        needles |= {os.path.basename(p).lower() for p in self.literals if len(os.path.basename(p)) >= 8 and not re.match(r"\.env", os.path.basename(p))}
        needles |= {p.lower().rstrip("/") for p in self.prefixes if p.strip("/")}
        needles |= {k.lower() for k in self.frozen_lower}
        needles = {n for n in needles if n and not re.match(r"\.env", os.path.basename(n))}
        contract = (self.policy.get("contract_rel") or "").lower()
        if contract and contract in low:
            needles.add(contract)
        found = next((n for n in sorted(needles) if n in low), None)
        write_hint = re.search(
            r"(write|dump\(|unlink|remove\(|rmtree|rename|replace\(|copy|move\(|truncate|chmod|symlink|sed |tee |os\.system|subprocess|popen"
            r"|\bopen\([^)]*,\s*['\"][^'\"]*[wax+]|writefile|appendfile|createwritestream|file\.(write|open)|\bmv |\bcp |>)",
            low,
        )
        if found and write_hint:
            self.deny_hit(("protected", found), "inline code or a script that writes it")
        if self.frozen_active and CODE_WRITE.search(low):
            tc = next((n for n in TEST_CONFIG_NAMES + ("pyproject.toml",) if re.search(r"(?<![\w.-])" + re.escape(n) + r"(?![\w])", low)), None)
            if tc:
                self.deny_test_config(("pyproject" if tc == "pyproject.toml" else "testconfig", tc), "inline code or a script that writes it")
        for m in re.finditer(r"""(?:rmtree|rmdir|removeSync|unlink|remove|rm)\(\s*[rbfu]{0,2}(['"])(.*?)\1""", code):
            lit = m.group(2)
            res = self.resolve(lit)
            if res:
                self.check_rm_target(res[0], res[1])
            self.check_data_dir(lit, True)
            self.check_write(lit, "inline code", destructive=True)

    # ----- any operand that names state a session must not touch -----

    def check_operands(self, name: str, w: list[str], args: list[str]) -> None:
        if name in READ_ONLY_OK or name in ("git", "cd", "pushd"):
            if name != "git":
                return
        if name != "git" and any(os.path.basename(t).endswith((".py", ".sh")) and "scripts/" in t.replace("\\", "/") for t in w[:4]):
            return  # the pack's own scripts take these paths as arguments
        if name == "git":
            _g, rest = self.split_git(w[1:])
            if rest and rest[0] in READ_GIT_SUBS:
                return
        for a in args:
            if a.startswith("-") and "=" not in a:
                continue
            cand = a.split("=", 1)[1] if a.startswith("-") else a
            res = self.resolve(cand)
            if not res or res[1]:
                continue
            rel = self.rel_of(res[0])
            if rel is not None and rel.lower() in DATA_ANY_OPERAND:
                _deny("humans-only" if rel.lower() == ".claude/baseline.json" else "protected-file",
                      f"{rel} is maintained by humans; `{name}` on it is not allowed from a session (cat, grep, jq and git diff may read it). Say what you need changed and why, and stop.")

    # ----- credentials -----

    def check_credentials(self, name: str, w: list[str], args: list[str]) -> None:
        if name == "gh" and (args[:2] == ["auth", "token"] or (args[:1] == ["auth"] and any(a in ("--show-token", "-t") for a in args))):
            _deny("credential-read", "printing the GitHub token from a session is blocked. gh is already authenticated; use it directly.")
        if name == "aws" and "configure" in args and any(a in ("export-credentials", "get") for a in args):
            _deny("credential-read", MSG_CRED)
        if name == "git" and args[:2] in (["credential", "fill"], ["credential", "approve"], ["credential-store", "get"], ["credential-osxkeychain", "get"]):
            _deny("credential-read", MSG_CRED)
        if name in METADATA_OK:
            return
        if name == "git":
            _g, rest = self.split_git(args)
            if not rest or rest[0] not in GIT_CONTENT_SUBS:
                return
            for a in rest[1:]:
                for cand in (a, a.split(":", 1)[1] if ":" in a and not a.startswith(":") else a):
                    res = self.resolve(cand)
                    if res and is_credential_path(res[0]):
                        _deny("credential-read", MSG_CRED)
            return
        is_interp = INTERPRETERS.match(name) is not None
        skip: set[int] = set()
        opts, pos, _after = parse_args(args)
        if name in GREPPERS or name in SCRIPT_EDITORS:
            has_e = any(a in ("-e", "--regexp", "-f", "--file", "--expression") for a in args)
            if not has_e:
                first = next((i for i, a in enumerate(args) if not a.startswith("-")), None)
                if first is not None:
                    skip.add(first)
            for i, a in enumerate(args[:-1]):
                if a in ("-e", "--regexp", "--expression"):
                    skip.add(i + 1)
        if name in COPIERS:
            plain = [i for i, a in enumerate(args) if not a.startswith("-")]
            if len(plain) >= 2:
                skip.add(plain[-1])
        recursive_search = name in GREPPERS and (name in ("rg", "ag", "ack") or recursive(args))
        for i, a in enumerate(args):
            if i in skip or (i > 0 and args[i - 1] in EXCLUDE_OPTS):
                continue
            if a.startswith("--") and a.split("=", 1)[0] in EXCLUDE_OPTS:
                continue
            if a.startswith("-") and "=" not in a:
                continue
            if "$(" in a or "`" in a or SUBST_TOKEN.search(a):
                text = a
                for m in SUBST_TOKEN.finditer(a):
                    k = int(m.group(1))
                    text += " " + (self.all_subs[k] if k < len(self.all_subs) else "")
                if mentions_credentials(text):
                    _deny("credential-read", MSG_CRED)
                continue
            for cand in self.candidates(a):
                res = self.resolve(cand)
                if not res:
                    continue
                p, is_glob = res
                if is_glob:
                    if self.glob_may_match_credential(p):
                        _deny("credential-read", MSG_CRED)
                    continue
                if is_credential_path(p, env_names=not is_interp):
                    _deny("credential-read", MSG_CRED)
                if recursive_search and not is_interp:
                    pl = p.lower().rstrip("/") or "/"
                    if pl == "/" or pl == self.home.lower() or self.home.lower().startswith(pl + "/"):
                        _deny("credential-read", "a recursive search of the home directory reads ~/.ssh, ~/.aws and .env files. Search the repository (a path inside it).")
        if recursive_search and not [a for a in pos[1:]] and name in ("rg", "ag", "ack", "grep", "egrep", "fgrep") and (self.cwd.lower() == self.home.lower() or self.home.lower().startswith(self.cwd.lower().rstrip("/") + "/")):
            _deny("credential-read", "a recursive search from the home directory reads ~/.ssh, ~/.aws and .env files. Search the repository.")

    @staticmethod
    def candidates(tok: str) -> list[str]:
        cands = [tok]
        if "=" in tok:
            cands.append(tok.split("=", 1)[1])
        cands += [c[1:] for c in cands if c.startswith("@")]
        return cands

    def glob_may_match_credential(self, pattern: str) -> bool:
        for m in glob.glob(pattern)[:300]:
            if is_credential_path(real(m)):
                return True
        base = os.path.basename(pattern).lower()
        names = (".env", ".env.local", ".env.production", ".env.development", ".envrc", ".netrc", ".npmrc", ".pypirc", ".git-credentials")
        if base.startswith(".") and any(fnmatch.fnmatchcase(n, base) for n in names):
            return True
        samples = [".ssh/id_rsa", ".ssh/id_ed25519", ".ssh/id_ecdsa", ".aws/credentials", ".config/gh/hosts.yml", ".docker/config.json", ".kube/config"]
        pl = pattern.lower()
        return any(fnmatch.fnmatchcase((self.home + "/" + s).lower(), pl) for s in samples)

    # ----- data stores -----

    def check_data_store(self, w: list[str], stdin: list[str]) -> None:
        first = os.path.basename(w[0])
        args = w[1:]
        if first in ("docker", "podman", "docker-compose", "podman-compose"):
            rest = args[args.index("compose") + 1:] if "compose" in args else (args if first.endswith("-compose") else [])
            if "down" in rest and has_flag(rest[rest.index("down") + 1:], "-v", "--volumes"):
                _deny("data-store", "`compose down -v`: " + MSG_VOLUMES)
            for i, a in enumerate(args[:-1]):
                if a == "volume" and args[i + 1] in ("rm", "remove", "prune"):
                    _deny("data-store", f"`{first} volume {args[i + 1]}`: " + MSG_VOLUMES)
            if "system" in args and "prune" in args and "--volumes" in args:
                _deny("data-store", "`system prune --volumes`: " + MSG_VOLUMES)
        if first == "rsync" and any(a.startswith("--delete") for a in args):
            plain = [a for a in args if not a.startswith("-")]
            if plain:
                self.check_data_dir(plain[-1], True)
        found = self.data_tool(w)
        if not found:
            return
        tool, targs = found
        if "--help" in targs or "-h" in targs:
            return
        if tool == "dropdb" or (tool == "mysqladmin" and any(a.lower() == "drop" for a in targs)):
            _deny("data-store", f"`{tool}`: " + MSG_DROP)
        if tool == "alembic" and any(a in ("downgrade", "stamp") for a in targs):
            _deny("data-store", MSG_ALEMBIC)
        if tool == "flask" and "db" in targs and any(a in ("downgrade", "stamp") for a in targs):
            _deny("data-store", MSG_ALEMBIC)
        if tool == "prisma" and (("migrate" in targs and "reset" in targs) or "--force-reset" in targs or ("db" in targs and "drop" in targs)):
            _deny("data-store", MSG_ORM)
        if tool in ("rails", "rake") and any(re.fullmatch(r"db:(drop|reset|purge|schema:load|migrate:reset|setup|environment:set)(:\w+)?", a) for a in targs):
            _deny("data-store", MSG_ORM)
        if tool == "django-admin" and any(a in ("flush", "sqlflush", "reset_db") for a in targs):
            _deny("data-store", MSG_ORM)
        if tool == "pg_restore" and any(a in ("--clean", "-c") or re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a) for a in targs):
            _deny("data-store", "pg_restore --clean drops objects before recreating them. Restore into a fresh database (createdb, then pg_restore); a human runs --clean.")
        if tool in REDIS_CLIS:
            cmds = [redis_command(targs)] + [ln.split()[0] for chunk in stdin for ln in chunk.splitlines() if ln.split()]
            if any(c.lower() in ("flushall", "flushdb") for c in cmds if c):
                _deny("data-store", MSG_REDIS)
        if tool in MONGO_CLIS:
            for chunk in targs + stdin:
                if re.search(r"(?i)drop(database|collection)?\s*\(|\.deleteMany\(\s*\{\s*\}\s*\)|\.remove\(\s*\{\s*\}\s*\)", chunk):
                    _deny("data-store", MSG_DROP)
        if tool in SQL_CLIENTS or tool.startswith("pg_"):
            chunks = list(targs) + list(stdin)
            for i, a in enumerate(targs):
                path = targs[i + 1] if a in ("-f", "--file") and i + 1 < len(targs) else (a.split("=", 1)[1] if a.startswith("--file=") else None)
                if path:
                    res = self.resolve(path)
                    if res and not res[1]:
                        chunks.append(read_text(res[0]))
            for a in targs:
                for m in re.finditer(r"\$\(\s*cat\s+([^\s)]+)\s*\)", a):
                    res = self.resolve(m.group(1))
                    if res and not res[1]:
                        chunks.append(read_text(res[0]))
            for chunk in chunks:
                why = destructive_sql(chunk)
                if why:
                    _deny("data-store", why)

    @staticmethod
    def data_tool(words: list[str]) -> tuple[str, list[str]] | None:
        first = os.path.basename(words[0])
        if first in DATA_TOOLS or first.startswith("pg_"):
            return first, words[1:]
        if first.startswith("rails") or first == "bin/rails":
            return "rails", words[1:]
        if first in WRAPPERS or (re.fullmatch(r"python[0-9.]*", first) and ("-m" in words or any(os.path.basename(x) == "manage.py" for x in words))):
            for i, w in enumerate(words[1:], start=1):
                b = os.path.basename(w)
                if b in DATA_TOOLS or b.startswith("pg_"):
                    return b, words[i + 1:]
                if b == "manage.py":
                    return "django-admin", words[i + 1:]
        if first in ("manage.py", "bin/rails", "bundle") and len(words) > 1:
            return ("django-admin" if first == "manage.py" else "rails"), words[1:]
        return None

    # ----- git -----

    def split_git(self, args: list[str]) -> tuple[list[str], list[str]]:
        """(`-c key=value` entries, the args from the subcommand on). git's own global options
        (-C, -c, --git-dir, ...) are stripped first, so `git -C . commit` is still a commit."""
        cfg: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-c", "--config-env") and i + 1 < len(args):
                cfg.append(args[i + 1])
                i += 2
            elif a.startswith("--config-env="):
                cfg.append(a.split("=", 1)[1])
                i += 1
            elif a == "-C" and i + 1 < len(args):
                i += 2
            elif a in GIT_GLOBAL_WITH_ARG:
                i += 2
            elif a.startswith("-c") and len(a) > 2 and not a.startswith("--"):
                cfg.append(a[2:])
                i += 1
            elif a.startswith("-C") and len(a) > 2 and not a.startswith("--"):
                i += 1
            elif any(a.startswith(o + "=") for o in GIT_GLOBAL_WITH_ARG) or a in GIT_GLOBAL_FLAGS:
                i += 1
            else:
                break
        return cfg, args[i:]

    def check_git_env(self, key: str, value: str) -> None:
        if (key == "GIT_CONFIG_PARAMETERS" or key.startswith("GIT_CONFIG_KEY_")) and re.search(r"(?i)core\.hookspath|core\.fsmonitor|include(if)?\.", value):
            _deny("hooks-path", "git hook paths are not overridden from a session.")
        if key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM") and value != os.devnull:
            _deny("hooks-path", "pointing git at another config file can switch off hooks; not from a session.")

    def check_git_config_entry(self, entry: str) -> None:
        key, _, value = entry.partition("=")
        low = key.lower()
        if low == "core.hookspath":
            _deny("hooks-path", "git hook paths are not overridden from a session.")
        if DANGEROUS_GIT_CONFIG.match(low):
            _deny("hooks-path", f"git config {key} can run code or switch hooks off; a human sets it.")
        if low.startswith("alias.") and ALIAS_RISKY.search(value):
            _deny("git-alias", "a git alias that pushes, forces, skips hooks or runs a shell would hide a blocked command; a human defines aliases.")

    def git_stage(self, args: list[str], depth: int = 0, stdin_text: list[str] | None = None) -> None:
        if depth > 4:
            _deny("nesting", "git aliases nest too deeply to check.")
        cfg, rest = self.split_git(args)
        for entry in cfg:
            self.check_git_config_entry(entry)
        if not rest:
            return
        sub, a = rest[0], rest[1:]
        if "--help" in a or a == ["-h"]:
            return  # prints help, changes nothing
        if sub not in GIT_KNOWN_SUBS:  # an alias: check what it expands to
            value = next((e.split("=", 1)[1] for e in cfg if e.lower().startswith(f"alias.{sub.lower()}=")), None)
            if value is None:
                value = git(self.root, "config", "--get", f"alias.{sub}").strip()
            if value:
                if value.startswith("!"):
                    self.walk(value[1:] + " " + " ".join(shlex.quote(x) for x in a), depth + 1)
                else:
                    try:
                        expanded = shlex.split(value)
                    except ValueError:
                        expanded = value.split()
                    self.git_stage(expanded + a, depth + 1, stdin_text)
                return
        protected = list(self.policy["protected_branches"])
        if sub in GIT_OUTPUT_SUBS:
            self.git_output(sub, a)
        if sub == "push":
            self.git_push(a, protected)
        elif sub == "commit":
            self.git_commit(a, stdin_text)
        elif sub == "reset":
            opts, _p, _a = parse_args(a, RESET_LONGS)
            if "--hard" in opts:
                _deny("destructive-git", "destructive git (reset --hard); ask the human to do this.")
        elif sub in ("checkout", "restore", "switch"):
            self.git_checkout(sub, a)
        elif sub in ("rm", "mv"):
            _o, gpos, gafter = parse_args(a)
            for tgt in gpos + gafter:
                self.check_write(tgt, f"git {sub} {tgt}", destructive=True)
        elif sub == "clean":
            opts, _p, _a = parse_args(a, CLEAN_LONGS)
            if ("-f" in opts or "--force" in opts) and not ({"-n", "--dry-run"} & opts):
                _deny("destructive-git", "destructive git (clean -f); ask the human to do this.")
        elif sub == "branch":
            opts, pos, after = parse_args(a, BRANCH_LONGS, ("--set-upstream-to", "--track"), "ut")
            names = pos + after
            if "-D" in opts or (({"-d", "--delete"} & opts) and ({"-f", "--force"} & opts)):
                _deny("destructive-git", "destructive git (branch -D); ask the human to do this.")
            if "-M" in opts:
                _deny("destructive-git", "destructive git (branch -M, a forced rename); ask the human to do this.")
            if ({"-f", "--force", "-m", "--move"} & opts) and any(n in protected for n in names):
                _deny("destructive-git", "destructive git (moving or renaming a protected branch); ask the human to do this.")
        elif sub == "stash":
            if a and a[0] in ("drop", "clear"):
                _deny("destructive-git", "destructive git (stash drop/clear); ask the human to do this.")
        elif sub == "update-ref":
            opts, _p, _a = parse_args(a, ("--delete", "--no-deref", "--stdin"), (), "m")
            if "-d" in opts or "--delete" in opts:
                _deny("destructive-git", "destructive git (update-ref -d deletes a ref like branch -D); ask the human to do this.")
        elif sub == "reflog":
            if a and a[0] in ("expire", "delete"):
                _deny("destructive-git", "destructive git (reflog expire/delete erases the way back); ask the human to do this.")
        elif sub == "gc":
            if any(x.startswith("--prune=") and x.split("=", 1)[1] in ("now", "all") for x in a):
                _deny("destructive-git", "destructive git (gc --prune=now); ask the human to do this.")
        elif sub == "prune":
            if not ({"-n", "--dry-run"} & set(a)):
                _deny("destructive-git", "destructive git (prune deletes unreachable objects); ask the human to do this.")
        elif sub in ("filter-branch", "filter-repo"):
            _deny("destructive-git", f"destructive git ({sub} rewrites history); ask the human to do this.")
        elif sub == "config":
            self.git_config(a)
        elif sub == "add":
            self.git_add(a)
        elif sub in STAMPED_SUBS and not (set(a) & STAMP_EXEMPT):
            self.stamped.append(sub)

    def git_output(self, sub: str, a: list[str]) -> None:
        """`git diff|log|show --output=<file>` writes the file: the protected-file and frozen-test rules apply.
        git accepts a unique prefix of a long option, so `--outp=x` is `--output=x` (`--output-indicator-*` is not)."""
        i = 0
        while i < len(a):
            opt, eq, val = a[i].partition("=")
            if opt == "--":
                break
            if len(opt) >= 4 and "--output".startswith(opt):
                if eq:
                    self.check_write(val, f"git {sub} --output")
                elif i + 1 < len(a):
                    self.check_write(a[i + 1], f"git {sub} --output")
                    i += 1
            i += 1

    def git_add(self, a: list[str]) -> None:
        opts, pos, after = parse_args(a, ("--all", "--update", "--patch", "--intent-to-add", "--force", "--dry-run", "--verbose", "--no-all", "--ignore-removal"))
        specs = pos + after
        if ({"-A", "--all", "-u", "--update"} & opts) or any(s in (".", "./", ":/", ":/.", "*") or s.endswith("/.") for s in specs) or not specs:
            self.add_everything = True
        else:
            self.add_specs += specs

    def git_push(self, a: list[str], protected: list[str]) -> None:
        opts, pos, after = parse_args(a, PUSH_LONGS, PUSH_VAL_LONGS, "o")
        refs = pos + after
        if ({"--force", "--force-with-lease", "--force-if-includes", "-f"} & opts) or any(r.startswith("+") for r in refs):
            _deny("force-push", "no force pushes.")
        if "--no-verify" in opts:
            _deny("no-verify", "git hooks are not optional here (push --no-verify).")
        if ({"--mirror", "--all", "--delete", "-d"} & opts) or any(r.startswith(":") for r in refs):
            _deny("destructive-git", "destructive git (push --mirror, --all or --delete rewrites or removes remote refs); ask the human to do this.")
        if any(r == b or r.endswith(":" + b) or r.endswith("/" + b) for r in refs for b in protected):
            _deny("push-protected", "no pushes to a protected branch; open a PR.")
        if git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip() in protected and not any(r.startswith("HEAD:") for r in refs):
            _deny("push-protected", "you are on a protected branch; branch, then push the branch.")

    def git_checkout(self, sub: str, a: list[str]) -> None:
        longs = CHECKOUT_LONGS + ("--create", "--force-create", "--guess", "--no-guess", "--track")
        opts, pos, after = parse_args(a, longs, ("--source", "--orphan", "--track"), {"checkout": "bB", "switch": "cC", "restore": "s"}[sub])
        if sub in ("checkout", "switch") and ({"-f", "--force", "--discard-changes"} & opts):
            _deny("destructive-git", f"destructive git ({sub} -f discards local changes); ask the human to do this.")
        if sub == "restore" and ({"--staged", "-S"} & opts) and not ({"--worktree", "-W"} & opts):
            return  # unstaging only
        if {"-b", "-B", "-c", "-C", "--orphan", "--create", "--force-create"} & opts:
            return  # creating a branch, not discarding edits
        if sub in ("checkout", "restore"):
            for spec in pos + after:
                found = self.hit(spec)
                if found and found[0] != "ancestor":
                    what = "frozen by /tests" if found[0] == "frozen" else ("test configuration frozen with the tests" if found[0] in ("testconfig", "pyproject") else "protected")
                    _deny("shell-write", f"git {sub} of {found[1]} puts the committed version back over it, and {found[1]} is {what}: reverting it is a change too. "
                          f"Edits (and reverts) go through a human: say what you need and why, and stop.")
        specs = after if after else (pos if sub == "restore" else [p for p in pos if self.wide_spec(p)])
        if sub != "switch" and any(self.wide_spec(s) for s in specs):
            _deny("destructive-git", f"destructive git ({sub} of the whole tree or a directory discards edits); ask the human to do this.")

    def wide_spec(self, spec: str) -> bool:
        if spec in (".", "./", ":/", ":/.", "*", ":(top)", ":/*") or spec.endswith(("/.", "/*")):
            return True
        res = self.resolve(spec)
        return bool(res and not res[1] and os.path.isdir(res[0]))

    def git_config(self, a: list[str]) -> None:
        if any(x in CONFIG_READ for x in a):
            return
        pos = [x for x in a if not x.startswith("-")]
        setting = False
        if pos and pos[0] == "set":
            pos, setting = pos[1:], True
        if not pos:
            return
        if len(pos) >= 2 or setting or "--add" in a or "--replace-all" in a:
            self.check_git_config_entry(pos[0] + "=" + (pos[1] if len(pos) > 1 else ""))

    def git_commit(self, a: list[str], stdin_text: list[str] | None = None) -> None:
        parsed = parse_args(a, COMMIT_LONGS, COMMIT_VAL_LONGS, "mFCct")
        opts = parsed[0]
        if "-n" in opts or "--no-verify" in opts:
            _deny("no-verify", "git hooks are not optional here (commit -n / --no-verify).")
        self.commits.append((a, parsed, list(stdin_text or [])))

    # ----- gh -----

    def gh_stage(self, args: list[str]) -> None:
        pos: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-R", "--repo", "--hostname"):
                i += 2
                continue
            if not a.startswith("-"):
                pos.append(a)
            i += 1
        head = pos[:2]
        if head == ["pr", "merge"]:
            _deny("pr-merge", "PRs are merged by a reviewer in GitHub, never from this session.")
        if head == ["pr", "review"]:
            opts, _p, _a = parse_args(args, ("--approve", "--comment", "--request-changes", "--body", "--body-file", "--repo"), ("--body", "--body-file", "--repo"), "bFR")
            if "--approve" in opts or "-a" in opts:
                _deny("pr-approve", "a session does not approve PRs.")
        if head[:1] == ["alias"] and pos[1:2] in (["set"], ["import"]) and re.search(r"(?i)merge|approv|\bapi\b|!", " ".join(args)):
            _deny("pr-merge", "a gh alias that merges, approves or shells out would hide a blocked command; a human defines aliases.")
        if head[:1] == ["api"]:
            self.gh_api(args, pos)

    def gh_api(self, args: list[str], pos: list[str]) -> None:
        text = " ".join(args)
        _o, ppos, _a = parse_args(args[1:], ("--method", "--header", "--field", "--raw-field", "--input", "--jq", "--template", "--hostname", "--cache", "--preview"),
                                  ("--method", "--header", "--field", "--raw-field", "--input", "--jq", "--template", "--hostname", "--cache", "--preview"), "XHfFqt")
        endpoint = ppos[0] if ppos else ""
        method = ""
        for i, a in enumerate(args[:-1]):
            if a in ("-X", "--method"):
                method = args[i + 1].upper()
        if not method:
            method = "POST" if any(a in ("-f", "-F", "--field", "--raw-field", "--input") or re.match(r"^-[fF]\S", a) for a in args) else "GET"
        if re.search(r"/pulls/\d+/merge$", endpoint) and method != "GET":
            _deny("pr-merge", "PRs are merged by a reviewer in GitHub, never from this session.")
        if re.search(r"/pulls/\d+/reviews(/\d+/events)?$", endpoint) and method != "GET" and re.search(r"(?i)\bAPPROVE", text):
            _deny("pr-approve", "a session does not approve PRs.")
        if "graphql" in endpoint:
            if re.search(r"mergePullRequest|enablePullRequestAutoMerge", text):
                _deny("pr-merge", "PRs are merged by a reviewer in GitHub, never from this session.")
            if re.search(r"addPullRequestReview", text) and re.search(r"(?i)APPROVE", text):
                _deny("pr-approve", "a session does not approve PRs.")

    # ----- the commit gate -----

    def stamp_matches(self) -> bool:
        stamp = self.policy["stamp"]()
        if not stamp or not stamp.get("ok"):
            return False
        fp, _err = fingerprint(self.root)
        return bool(fp) and fp == stamp.get("fingerprint")

    def require_stamp(self, detail: str = "") -> None:
        stamp = self.policy["stamp"]()
        if not stamp or not stamp.get("ok"):
            _deny("no-stamp", NO_STAMP + detail)
        fp, err = fingerprint(self.root)
        if not fp:
            _deny("stale-stamp", "the tree fingerprint could not be computed" + (f" ({err})" if err else "") + ". " + RERUN_VERIFY)
        if fp != stamp.get("fingerprint"):
            _deny("stale-stamp", STALE_STAMP.format(stamp=stamp.get("fingerprint"), now=fp))

    def verify_frozen(self) -> None:
        """A frozen test that no longer matches the hash /tests recorded is a tampered test, however it was changed."""
        for rel, sha in self.policy.get("frozen", {}).items():
            if not sha:
                continue
            path = os.path.join(self.root, rel)
            try:
                with open(path, "rb") as fh:
                    now = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            if now != sha:
                _deny("frozen-test", f"{rel} differs from the version /tests froze (sha256 {sha[:12]}..., now {now[:12]}...). Restore it (git checkout is for a human here) or ask a human to run `python3 -I scripts/freeze_tests.py --unfreeze {rel}`.")

    def check_commit(self, args: list[str], parsed: tuple, stdin_text: list[str] | None = None) -> None:
        opts, pos, after = parsed
        root = self.root
        self.verify_frozen()
        amend = "--amend" in opts
        pathspecs = pos + after
        staged = [ln for ln in git(root, "diff", "--cached", "--name-only").splitlines() if ln]
        if self.add_everything or "-a" in opts or "--all" in opts:
            paths = changed_paths(root)
        elif pathspecs:  # `git commit -o a.py`, `-i a.py`, `-- a.py`: exactly these paths (plus the index with -i)
            paths = changed_paths(root, *pathspecs)
            if "-i" in opts or "--include" in opts:
                paths = paths + staged
        else:
            paths = staged + (changed_paths(root, *self.add_specs) if self.add_specs else [])
        paths = list(dict.fromkeys(paths))
        is_test = self.policy["is_test_path"]
        # A tests-only commit needs no stamp, and it may carry the skeletons /tests wrote for the modules the new
        # tests import: new files whose every body only raises or throws (lib/skeleton.py), so no behaviour lands
        # unverified. Anything else in the commit is app code and waits for /verify.
        # `git commit` alone commits the index; -a, --all, a pathspec (-o, -i, --) or a preceding `git add` commit
        # what is in the worktree, so that is the content judged.
        from_index = not (self.add_everything or "-a" in opts or "--all" in opts or pathspecs or self.add_specs)
        not_skeletons = [f"{p} ({self.skeleton_problem(p, staged, from_index)})" for p in paths
                         if not (is_test(p) or p.startswith(".claude/work/")) and self.skeleton_problem(p, staged, from_index)]
        exempt = bool(paths) and not amend and not not_skeletons
        if (paths or amend) and not exempt:
            detail = ""
            if not_skeletons:
                detail = (" A tests commit may also carry skeletons (new files whose bodies only raise NotImplementedError or "
                          "throw new Error(\"not implemented\")); not a skeleton here: " + "; ".join(not_skeletons[:3]) + ".")
            self.require_stamp(detail)
        message = self.commit_message(args, stdin_text)
        if message.strip():
            script = message_script()
            if not os.path.exists(script):
                _deny("commit-message", f"check_commit_message.py could not run: {script} is missing. Fix the install; the message is not checked until then.")
            proc = subprocess.run([sys.executable, "-I", script, "--files", *paths], input=message, capture_output=True, text=True, cwd=root)
            if proc.returncode == 1:
                _deny("commit-message", "commit message problems:\n" + proc.stdout.strip() + "\nRewrite the message to describe the diff.")
            if proc.returncode != 0:
                _deny("commit-message", f"check_commit_message.py could not run (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")

    def skeleton_problem(self, rel: str, staged: list[str], from_index: bool) -> str:
        """'' when rel is a new file whose committed content is a skeleton; otherwise why it is not one."""
        if git(self.root, "ls-tree", "--name-only", "HEAD", "--", rel).strip():
            return "exists at HEAD; a skeleton is a new file"
        if from_index and rel in staged:
            text = git(self.root, "show", f":{rel}")
        else:
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                return f"unreadable: {exc}"
        ok, why = skeleton.is_skeleton(rel, text)
        return "" if ok else why

    def commit_message(self, args: list[str], stdin_text: list[str] | None = None) -> str:
        """The message from -m / --message / -F / --file (clusters such as -am and unique long-option
        prefixes included); a `$(cat <<'EOF' ... EOF)` body is read from the heredoc set aside earlier.
        `-F -` (or /dev/stdin) reads the stage's stdin: a heredoc, a here-string or an echo/printf/cat
        piped in; a stdin the guard cannot see is denied rather than waved through unchecked."""
        parts: list[str] = []

        def read_file(path: str) -> str:
            if path in ("-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"):
                text = "\n".join(t for t in (stdin_text or []) if t.strip())
                if not text.strip():
                    _deny("commit-message", "git commit -F - reads the message from a stdin the guard cannot see. Pass the message with -m \"$(cat <<'EOF' ... EOF)\" or -F <file>.")
                return text
            res = self.resolve(path)
            return read_text(res[0]) if res else ""

        i = 0
        while i < len(args):
            a = args[i]
            if a == "--":
                break
            if a.startswith("--"):
                name, eq, val = a.partition("=")
                cands = [x for x in COMMIT_LONGS if x == name] or [x for x in COMMIT_LONGS if len(name) >= 3 and x.startswith(name)]
                canon = cands[0] if len(cands) == 1 else name
                if canon in ("--message", "--file"):
                    if not eq and i + 1 < len(args):
                        val = args[i + 1]
                        i += 1
                    parts.append(val if canon == "--message" else read_file(val))
                elif canon in COMMIT_VAL_LONGS and not eq:
                    i += 1
            elif a.startswith("-") and len(a) > 1:
                for j in range(1, len(a)):
                    ch = a[j]
                    if ch in "mFCct":
                        val = a[j + 1:]
                        if not val and i + 1 < len(args):
                            val = args[i + 1]
                            i += 1
                        if ch == "m":
                            parts.append(val)
                        elif ch == "F":
                            parts.append(read_file(val))
                        break
            i += 1
        message = "\n\n".join(parts)

        def body(m: re.Match) -> str:
            n = int(m.group(1))
            return self.bodies[n] if n < len(self.bodies) else ""

        heredoc = re.search(r"\$\(\s*cat\s+__SDLC_HEREDOC_(\d+)__\s*\)", message)
        if heredoc:
            return re.sub(r"\$\(\s*cat\s+__SDLC_HEREDOC_(\d+)__\s*\)", body, message).strip("\n")
        return message

    def finish(self) -> None:
        for args, parsed, stdin_text in self.commits:
            self.check_commit(args, parsed, stdin_text)
        if self.stamped:
            self.verify_frozen()
            self.require_stamp()


def has_flag(args: list[str], *flags: str) -> bool:
    short = {f[1:] for f in flags if re.fullmatch(r"-[a-zA-Z]", f)}
    for a in args:
        if a in flags:
            return True
        if short and re.fullmatch(r"-[a-zA-Z]+", a) and any(ch in short for ch in a[1:]):
            return True
    return False


# ======================= main =======================

def main() -> int:
    try:
        data = read_input()
        ti = data.get("tool_input") if isinstance(data, dict) else None
        if not isinstance(ti, dict) or not isinstance(ti.get("command", ""), str):
            _deny("input", "hook input unreadable; refusing")
        cmd = ti.get("command") or ""
        _CTX["detail"] = cmd
        if not cmd.strip():
            return 0
        hint = data.get("cwd") if isinstance(data.get("cwd"), str) else None
        root = project_root(hint)
        _CTX["root"] = root
        if not os.path.isdir(root):
            _deny("input", f"the project directory {root} does not exist; refusing until it does.")
        Checker(root, hint, build_policy(root)).run(cmd)
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a broken guard must not become an open door
        _deny("crash", f"the Bash guard crashed ({exc.__class__.__name__}: {exc}). {CRASH_HINT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
