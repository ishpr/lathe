#!/usr/bin/env python3
"""Lint the permission rules unattended runs depend on (docs/DESIGN.md sections 14 and 18.5): every required deny rule
is present, no allow rule is a blanket runner, and every Bash command a skill or an agent names is allowed or denied.

Why: unattended runs use `--permission-mode dontAsk`, which denies every tool call that would otherwise prompt. The
settings open commands by category (read-only inspection such as `cat`, `rg`, `find`, `sed -n`, `jq`; read-only `git`;
the stack's test, lint, format and typecheck commands), so a model may run commands no skill names, and a denial that
still happens escalates at runtime with the missing rule named (sdlc_graph/orchestrator/autonomy.py). What must hold
statically is what keeps the categories safe, and what a skill or agent says explicitly:

1. The deny-list lint. Every rule of the required deny list (`--required-denies`, default
   `templates/required_denies.json` under `--root`, else the pack's own next to this script) is in the
   `permissions.deny` of the settings read (all `--settings` files together). Claude Code evaluates deny before allow
   and applies a deny rule to every part of a chain or pipe, so these rules close each category's write and run forms
   (`find -delete`, `sed -i`, `rg --pre` ...) and the human-only forms (force-push, hard reset, `gh pr merge`, ratchet
   updates, `.env` reads). Each missing rule is a `MISSING-DENY` line.
2. No blanket runner. An allow rule that lets a runner run anything (`Bash`, `Bash(*)`, `Bash(uv run *)`, `Bash(npx *)`,
   `Bash(make *)`, `Bash(bash *)`, `Bash(sh *)`, `Bash(python3 *)`, `bash -c`, `sh -c` ...) is a `BLANKET-RUNNER` line:
   a runner runs anything and guard_bash cannot see through it.
3. Named commands. Every command a skill or agent body names must be covered by an allow rule, denied by a deny rule
   (human-only) or negated in prose, or it is UNCOVERED: a skill that names a forbidden command, or one no category
   opens, still fails. The check no longer requires that skills name every command a model runs, and it does not
   reject chains or pipes: each part of a compound command is judged on its own, as Claude Code does, and a part whose
   first word is no known program is left to the runtime checks (guard_bash checks every part of every call).

What it reads:
- every `SKILL.md` under each `--skills-dir` (repeatable): the frontmatter `allowed-tools`, and
  the commands in the body: inline code spans and fenced-block lines whose first word is a program
  (PROGRAMS: python3, bash, git, gh, make, uv, npx, go, cargo, pytest, sdlc-graph; OTHER_PROGRAMS:
  rm, mkdir, docker, alembic, curl, head, tail, ... any shell command a skill is likely to name).
  Bare program names such as `gh` and spans in a negated phrase such as "never `git add -A`" are
  not commands to run. A compound part that starts with a word in neither list is not judged here (the
  runtime denial names its rule if no category opens it);
- the Write tool: a skill that names `.sdlc-stage`, `assumptions.md` or `questions.md` (the
  files an execution skill writes through the staging directory and workfile.py) must be covered by
  an `Edit(.sdlc-stage/...)` rule (or a bare `Write` or `Edit`), in its own allowed-tools or the settings,
  or it is UNCOVERED. An Edit rule covers every file-editing tool, the Write tool included; a
  `Write(<path>)` rule never matches in Claude Code (probed on 2.1.316: `Write(.sdlc-stage/**)`,
  `Write(/.sdlc-stage/**)`, `Write(./.sdlc-stage/**)` and an exact file path were all denied under
  dontAsk while `Edit(.sdlc-stage/**)` allowed the same Write), so it counts for nothing here;
- every `*.md` under each `--agents-dir` (repeatable; default `agents/` under `--root` when it exists): the
  commands in the body, found exactly as in a skill body (same programs, same negation and bare-name
  rules, same human-only and prohibited-in-prose handling). An agent has no `allowed-tools` that counts:
  its frontmatter, the `tools:` line included (it names tools, not rules), is never read, so each command
  must be covered by a settings allow rule alone. The Write-tool check above is for skills only;
- `permissions.allow` and `permissions.deny` from `--settings` (repeatable).

Normalising: `${CLAUDE_PLUGIN_ROOT}`, `$CLAUDE_PLUGIN_ROOT` and the installer placeholder
`{plugin_root}` are one token; `<placeholder>` and a trailing `...` become the argument `ARG`;
compound commands are split on `&&`, `||`, `;` and `|` (each part needs its own rule, as in Claude
Code). Other `{placeholders}` in a settings rule are filled from `--set name=value`, and a rule
whose placeholder has no value is dropped, as init_project.py does.

Matching follows the documented Bash rule semantics (https://code.claude.com/docs/en/permissions):
a `*` matches any text anywhere; a single trailing ` *` also matches the bare command; a trailing `:*` is
the legacy spelling of ` *`; a rule without `*` matches exactly one command; deny is evaluated before
allow and wins, and a compound command runs only when every part is allowed. Quotes are part of
the text. A command matching a deny rule is reported as human-only (dontAsk denies it, it never
prompts); a command a skill says only a human runs, which no deny rule matches in that spelling, is UNCOVERED
(a form the deny rules miss is a form the agent could run); a command with `$(...)` or a redirection to a file is uncovered whatever the rules say,
because Claude Code checks those separately.

Output: one `MISSING-DENY` line per required deny rule the settings lack and one `BLANKET-RUNNER` line per
runner allow rule, then the rules summary line; one `UNCOVERED` line per command with the file
(`skills/<name>/SKILL.md` or `agents/<name>.md`), the line, the normalised command and a rule that would cover it,
tab-separated; then a summary line with the skill and agent counts. `--verbose` also lists covered and human-only
commands.

`--own-only` counts only each skill's own `allowed-tools`: a `claude -p` run in a folder nobody
has trusted ignores the project's allow rules but still applies a skill's `allowed-tools`. An agent has
no such source of its own, so under `--own-only` agents are still checked against the settings.

Usage: check_skill_permissions.py [--root DIR] [--skills-dir DIR]... [--agents-dir DIR]... [--settings FILE]...
                                  [--required-denies FILE] [--set NAME=VALUE]... [--own-only] [--verbose]
Defaults (relative to --root): skills `skills/` or `.claude/skills/`; agents `agents/` when it exists;
settings `templates/settings.project.json` or `.claude/settings.json`; required denies
`templates/required_denies.json`, else the pack's own.
Exit codes: 0 every check holds; 1 a required deny rule is missing, an allow rule is a blanket runner or a named
command is uncovered; 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROGRAMS = ("python3", "bash", "git", "gh", "make", "uv", "npx", "go", "cargo", "pytest", "sdlc-graph")
# Shell commands a skill or an agent may name besides the pack's own programs. They start a command (an inline span or a
# fenced line) just as PROGRAMS do, so an `rm -rf` or a `docker compose up` in a skill or agent body is checked, not ignored.
OTHER_PROGRAMS = ("rm", "mkdir", "mv", "cp", "cat", "cd", "curl", "wget", "docker", "docker-compose", "alembic", "pip",
                  "pip3", "npm", "pnpm", "yarn", "node", "ls", "tail", "head", "wc", "sort", "touch", "chmod", "sed",
                  "awk", "echo", "kill", "env", "ruff", "mypy", "sh", "source", "export", "psql", "redis-cli", "xargs",
                  "tee", "find", "grep", "timeout", "nice", "poetry", "pipenv", "uvx", "ln", "tar", "kubectl",
                  "terraform", "aws", "gcloud", "neonctl", "cdk", "rg", "jq", "diff", "stat", "tree", "uniq")
COMMANDS = PROGRAMS + OTHER_PROGRAMS
STAGE_USERS = re.compile(r"\.sdlc-stage|(?:writ(?:e|es|ten)|record(?:s|ed)?|append(?:s|ed)?)[^\n.]{0,80}(?:assumptions|questions)\.md")
ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"
ROOT_FORMS = ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT", "{plugin_root}")
TICK = re.compile(r"(`+)(.+?)\1")
PLACEHOLDER = re.compile(r"<[^<>`]*>")
SETTINGS_PH = re.compile(r"\{[a-z_]+\}")
# A negation right before a span ("never `git add -A`", "do not run `gh pr merge`") marks it as a prohibition.
NEGATION = re.compile(
    r"(?:\b(?:never|not|no|nor|don't|without|instead\s+of|rather\s+than|forbidden|refuses?|denies|denied|blocked)"
    r"(?:\s+(?:run|use|call|type|name|add|with|a|an|the|even))*\s*[:(]?\s*)$", re.I)
# A command a human runs ("ask a human to run `...`", "humans only: `...`") is not one Claude runs.
HUMAN = re.compile(r"\b(?:ask(?:s|ing)?\s+a\s+human\s+to(?:\s+run)?|a\s+human\s+(?:runs?|types?)|humans?\s+only|only\s+a\s+human"
                   r"(?:\s+(?:runs?|may\s+run))?)\b[^`]*$", re.I)
CHAIN = re.compile(r"^\s*(?:,|or|nor|and|,\s*or|,\s*and)\s*$", re.I)


class Misuse(Exception):
    pass


# ---------- rules ----------

def canon(text: str) -> str:
    for form in ROOT_FORMS:
        text = text.replace(form, ROOT_TOKEN)
    return text


def bash_patterns(rules: list[str], values: dict[str, str]) -> list[str]:
    """`Bash(...)` contents from a rule list; '*' for a bare `Bash` rule; unfilled placeholders drop the rule."""
    out = []
    for rule in rules:
        if not isinstance(rule, str):
            continue
        rule = rule.strip()
        if rule == "Bash":
            out.append("*")
            continue
        if not (rule.startswith("Bash(") and rule.endswith(")")):
            continue
        pat = canon(rule[5:-1])
        missing = [p for p in SETTINGS_PH.findall(pat) if not values.get(p[1:-1])]
        if missing:
            continue
        pat = SETTINGS_PH.sub(lambda m: values[m.group(0)[1:-1]], pat)
        out.append(pat)
    return out


def write_patterns(rules: list[str]) -> list[str]:
    """Path patterns that let the Write tool write: `Edit(...)` rules (an Edit rule covers every file-editing
    tool, Write included) and '**' for a bare `Write` or `Edit`. A `Write(<path>)` rule never matches in Claude
    Code, so it contributes nothing."""
    out = []
    for rule in rules:
        rule = rule.strip() if isinstance(rule, str) else ""
        if rule in ("Write", "Edit"):
            out.append("**")
            continue
        tool = "Edit("
        if rule.startswith(tool) and rule.endswith(")"):
            out.append(rule[len(tool):-1].strip().removeprefix("./").lstrip("/") if not rule[len(tool):].startswith("//") else rule[len(tool):-1])
    return out


def write_matches(pattern: str, path: str) -> bool:
    """gitignore-like: `**` crosses directories, `*` does not."""
    rx = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            rx += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            rx += ".*"
            i += 2
        elif pattern[i] == "*":
            rx += "[^/]*"
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    return re.match("^" + rx + "$", path) is not None


def rule_matches(pattern: str, cmd: str) -> bool:
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    if "*" not in pattern:
        return cmd == pattern
    if pattern.endswith(" *") and pattern.count("*") == 1 and cmd == pattern[:-2]:
        return True
    rx = "^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$"
    return re.match(rx, cmd, re.S) is not None


def suggest(cmd: str) -> str:
    """The narrowest prefix rule that covers cmd: program, its flags, the subcommand (two for gh) or script."""
    toks = cmd.split()
    keep = [toks[0]]
    words = 2 if toks[0] == "gh" else 1
    i = 1
    while i < len(toks) and words:
        keep.append(toks[i])
        if not toks[i].startswith("-"):
            words -= 1
        i += 1
    if i >= len(toks):
        return "Bash(%s)" % " ".join(keep)
    return "Bash(%s *)" % " ".join(keep)


# ---------- skill parsing ----------

def split_frontmatter(text: str) -> tuple[str, list[str], int]:
    """Return (frontmatter text, body lines, line number of the first body line)."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[1:i]), lines[i + 1:], i + 2
    return "", lines, 1


def split_tools(value: str) -> list[str]:
    """Split an inline allowed-tools string on spaces/commas outside parentheses."""
    out, buf, depth = [], "", 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (ch.isspace() or ch == ","):
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace('\\"', '"') if v[0] == '"' else inner
    return v


def allowed_tools(front: str) -> list[str]:
    tools: list[str] = []
    lines = front.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^allowed-tools\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            tools += [unquote(t) for t in split_tools(rest.strip("[]")) if t.strip()]
        elif rest:
            tools += split_tools(unquote(rest))
        else:
            for item in lines[i + 1:]:
                im = re.match(r"^\s*-\s+(.*)$", item)
                if not im:
                    if item.strip() and not item.startswith((" ", "\t")):
                        break
                    continue
                tools.append(unquote(im.group(1)))
    return tools


def first_word(text: str) -> str:
    return text.strip().split(" ", 1)[0] if text.strip() else ""


def candidates(body: list[str], first_line: int) -> list[tuple[int, str, str]]:
    """[(line, raw command, kind)]; kind is 'run', 'prohibited' (negated prose) or 'human' (a human runs it).

    The phrase before a span may start on the previous line of the same paragraph, so the text
    after the previous line's last span is carried over (a blank line or a fence resets it)."""
    found: list[tuple[int, str, str]] = []
    fence = None
    carry, prev_kind = "", None
    for off, line in enumerate(body):
        no = first_line + off
        stripped = line.strip()
        fm = re.match(r"^(```+|~~~+)", stripped)
        if fm:
            fence = None if fence and stripped.startswith(fence) else (fence or fm.group(1))
            carry, prev_kind = "", None
            continue
        if fence:
            if first_word(stripped) in COMMANDS and len(stripped.split()) > 1:
                found.append((no, stripped, "run"))
            continue
        if not stripped:
            carry, prev_kind = "", None
            continue
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s", line):
            carry, prev_kind = "", None  # a new list item starts a new phrase
        prev_end = 0
        for m in TICK.finditer(line):
            span = m.group(2).strip()
            before = (carry + " " + line[:m.start()])[-80:]
            between = (carry if prev_end == 0 else "") + " " + line[prev_end:m.start()]
            prev_end = m.end()
            carry = ""
            if first_word(span) not in COMMANDS or len(span.split()) < 2:
                prev_kind = None
                continue
            if HUMAN.search(before) or (prev_kind == "human" and CHAIN.match(between)):
                kind = "human"
            elif NEGATION.search(before) or (prev_kind == "prohibited" and CHAIN.match(between)):
                kind = "prohibited"
            else:
                kind = "run"
            prev_kind = kind
            found.append((no, span, kind))
        carry = line[prev_end:] if prev_end else (carry + " " + line)[-80:]
    return found


def split_compound(cmd: str) -> list[str]:
    """Split on && || ; | outside quotes and $(...)."""
    parts, buf, quote, depth, i = [], "", None, 0, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif cmd.startswith("$(", i):
            depth += 1
            buf += "$("
            i += 2
            continue
        elif ch == ")" and depth:
            depth -= 1
            buf += ch
        elif depth == 0 and (cmd.startswith("&&", i) or cmd.startswith("||", i)):
            parts.append(buf)
            buf = ""
            i += 2
            continue
        elif depth == 0 and ch in ";|":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def normalise(cmd: str) -> str:
    cmd = canon(cmd.strip())
    cmd = PLACEHOLDER.sub("ARG", cmd)
    cmd = re.sub(r"\s*(\.\.\.|…)$", " ARG", cmd)
    return re.sub(r"\s+", " ", cmd).strip()


def redirect_target(cmd: str) -> str | None:
    """A redirection to a file (not /dev/null, not an fd duplication), outside quotes."""
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "", cmd)
    for m in re.finditer(r"(?<![<>=])\d?>>?\s*(&\d|\S+)", bare):
        tgt = m.group(1)
        if not tgt.startswith("&") and tgt != "/dev/null":
            return tgt
    return None


# ---------- main ----------

def load_settings(paths: list[str]) -> tuple[list[str], list[str]]:
    allow: list[str] = []
    deny: list[str] = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            raise Misuse("cannot read settings %s: %s" % (p, exc))
        except ValueError as exc:
            raise Misuse("settings %s is not valid JSON: %s" % (p, exc))
        perms = data.get("permissions", {}) if isinstance(data, dict) else {}
        allow += [r for r in perms.get("allow", []) if isinstance(r, str)]
        deny += [r for r in perms.get("deny", []) if isinstance(r, str)]
    return allow, deny


def skill_files(dirs: list[str]) -> list[str]:
    out = []
    for d in dirs:
        if not os.path.isdir(d):
            raise Misuse("skills dir %s is not a directory" % d)
        for dp, dns, fns in os.walk(d):
            dns.sort()
            if "SKILL.md" in fns:
                out.append(os.path.join(dp, "SKILL.md"))
    return sorted(out)


def agent_files(dirs: list[str]) -> list[str]:
    """Every `*.md` under each agents dir: one markdown file is one agent, its body the prompt."""
    out = []
    for d in dirs:
        if not os.path.isdir(d):
            raise Misuse("agents dir %s is not a directory" % d)
        for dp, dns, fns in os.walk(d):
            dns.sort()
            out += [os.path.join(dp, fn) for fn in sorted(fns) if fn.endswith(".md")]
    return sorted(set(out))


PACK_REQUIRED_DENIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "required_denies.json")
# Programs that run whatever text follows them: an allow rule that opens one with a wildcard pre-approves anything.
RUNNERS = ("uv run", "uvx", "npx", "pnpm exec", "pnpm dlx", "yarn exec", "yarn dlx", "bunx", "bun run", "poetry run",
           "pdm run", "pipx run", "make", "bash", "sh", "zsh", "dash", "python", "python3", "node", "env", "xargs",
           "eval", "exec", "sudo", "timeout", "nice", "nohup", "time", "command", "watch")
INLINE = re.compile(r"^(?:bash|sh|zsh|dash) +-[a-z]*c\b|^(?:python3?|node|perl|ruby) +-[a-zA-Z]*[ce]\b")


def load_required(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise Misuse("cannot read the required deny rules %s: %s" % (path, exc))
    except ValueError as exc:
        raise Misuse("required deny rules %s is not valid JSON: %s" % (path, exc))
    rules = data.get("deny") if isinstance(data, dict) else None
    if not isinstance(rules, list) or not rules or not all(isinstance(r, str) and r for r in rules):
        raise Misuse("required deny rules %s has no 'deny' list of rules" % path)
    return rules


def blanket_runner(rule: str) -> bool:
    """Whether an allow rule lets a runner run anything: a bare `Bash`, `Bash(*)`, a runner followed only by a wildcard
    (`uv run *`, `npx *`, `make *`, `bash *`, `python3 *`), or an inline-code form (`bash -c *`, `python3 -c *`)."""
    rule = rule.strip()
    if rule == "Bash":
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    pat = rule[5:-1].strip()
    if pat.endswith(":*"):
        pat = pat[:-2] + " *"
    if pat.replace("*", "").strip() == "":
        return True
    if INLINE.match(pat) and "*" in pat:
        return True
    head = pat.split("*", 1)[0].strip()
    return "*" in pat and head in RUNNERS


def lint_rules(settings: list[str], required: list[str], allow_rules: list[str], deny_rules: list[str], out) -> int:
    """MISSING-DENY and BLANKET-RUNNER lines and the rules summary; returns the number of problems."""
    have = {r.strip() for r in deny_rules}
    missing = [r for r in required if r.strip() not in have]
    where = ", ".join(os.path.basename(s) for s in settings)
    for r in missing:
        print("MISSING-DENY\t%s\t%s\trequired by templates/required_denies.json; add it to permissions.deny" % (where, r), file=out)
    blanket = [r for r in allow_rules if isinstance(r, str) and blanket_runner(r)]
    for r in blanket:
        print("BLANKET-RUNNER\t%s\t%s\ta runner runs anything and guard_bash cannot see through it; allow the exact "
              "tool (Bash(uv run pytest *)) instead" % (where, r), file=out)
    print("check_skill_permissions: %d required deny rule(s), %d missing; %d blanket runner(s) allowed"
          % (len(required), len(missing), len(blanket)), file=out)
    return len(missing) + len(blanket)


def check(skill_dirs: list[str], settings: list[str], values: dict[str, str], root: str,
          verbose: bool = False, own_only: bool = False, out=sys.stdout, agent_dirs: list[str] | None = None,
          required: list[str] | None = None) -> int:
    allow_rules, deny_rules = load_settings(settings)
    problems = lint_rules(settings, required, allow_rules, deny_rules, out) if required is not None else 0
    allow = bash_patterns(allow_rules, values)
    deny = bash_patterns(deny_rules, values)
    files = skill_files(skill_dirs)
    agents = agent_files(agent_dirs or [])
    counts = {"covered": 0, "human-only": 0, "prohibited": 0, "uncovered": 0}
    seen: set[tuple[str, str]] = set()

    def review(rel: str, body: list[str], first: int, own: list[str] | None, noun: str) -> None:
        """Judge every command in one body. `own` holds a skill's own allowed-tools patterns; None marks an agent,
        which has none that counts (a subagent's tool calls are approved by the session's rules), so only the
        settings can cover its commands, with or without --own-only."""
        for line, raw, kind in candidates(body, first):
            parts = split_compound(raw)
            for part in parts:
                cmd = normalise(part)
                key = (rel, cmd)
                if key in seen:
                    continue
                seen.add(key)
                if first_word(cmd) not in COMMANDS and kind == "run":
                    # a part of a chain or pipe that starts with no known program (`| frob -1`): not judged here; a
                    # category may open it, and if none does the runtime denial names its rule (DESIGN section 18.5)
                    if verbose:
                        print("unjudged\t%s:%d\t%s\tunknown program %r, left to the runtime checks" % (rel, line, cmd, first_word(cmd)),
                              file=out)
                    continue
                if kind == "prohibited":
                    counts["prohibited"] += 1
                    if verbose:
                        print("prohibited\t%s:%d\t%s" % (rel, line, cmd), file=out)
                    continue
                denied = next((r for r in deny if rule_matches(r, cmd)), None)
                if kind == "human":
                    counts["human-only"] += 1
                    if not denied:
                        # Claude is told not to run it, but nothing stops it: a failure, as the model may run it anyway
                        counts["uncovered"] += 1
                        print("UNCOVERED\t%s:%d\t%s\thuman-only, but no deny rule matches this form; add one (the quoted plugin-root form too)" % (rel, line, cmd), file=out)
                    elif verbose:
                        print("human-only\t%s:%d\t%s\tdenied by Bash(%s)" % (rel, line, cmd, denied), file=out)
                    continue
                if denied:
                    counts["human-only"] += 1
                    if verbose:
                        print("human-only\t%s:%d\t%s\tdenied by Bash(%s)" % (rel, line, cmd, denied), file=out)
                    continue
                why = None
                if "$(" in cmd or "`" in cmd:
                    why = "uses command substitution; run the inner command on its own and pass its output"
                elif redirect_target(cmd):
                    why = "redirects to %s; Claude Code checks the target separately" % redirect_target(cmd)
                pool = allow if own is None else (own if own_only else own + allow)
                rule = None if why else next((r for r in pool if rule_matches(r, cmd)), None)
                if rule is not None:
                    counts["covered"] += 1
                    if verbose:
                        src = "allowed-tools" if own is not None and rule in own else "settings"
                        print("covered\t%s:%d\t%s\t%s Bash(%s)" % (rel, line, cmd, src, rule), file=out)
                    continue
                counts["uncovered"] += 1
                print("UNCOVERED\t%s:%d\t%s\t%s" % (rel, line, cmd, why or "add %s" % suggest(cmd)), file=out)

    for path in files:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        front, body, first = split_frontmatter(text)
        own = bash_patterns(allowed_tools(front), values)
        rel = os.path.relpath(path, root)
        if STAGE_USERS.search("\n".join(body)):
            own_raw = allowed_tools(front)
            pool_w = write_patterns(own_raw if own_only else own_raw + allow_rules)
            if not any(write_matches(r, ".sdlc-stage/x") for r in pool_w):
                counts["uncovered"] += 1
                hint = " (a Write(...) rule never matches; use Edit(.sdlc-stage/**))" if any(
                    isinstance(r, str) and r.strip().startswith("Write(") for r in (own_raw if own_only else own_raw + allow_rules)) else ""
                print("UNCOVERED\t%s:1\tWrite(.sdlc-stage/x)\tthe skill stages work files but no Edit(.sdlc-stage/**) rule covers it; add Edit(.sdlc-stage/**)%s" % (rel, hint), file=out)
        review(rel, body, first, own, "skill")
    for path in agents:
        # an agent's frontmatter is never read (its `tools:` line names tools, not rules): its commands face the settings alone
        with open(path, encoding="utf-8") as fh:
            _, body, first = split_frontmatter(fh.read())
        review(os.path.relpath(path, root), body, first, None, "agent")
    print("check_skill_permissions: %d skill(s), %d agent(s); %d covered, %d human-only (denied), %d prohibited in prose, %d uncovered"
          % (len(files), len(agents), counts["covered"], counts["human-only"], counts["prohibited"], counts["uncovered"]), file=out)
    return 1 if counts["uncovered"] or problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_skill_permissions.py",
                                 description="Fail when a skill or an agent names a Bash command no allow rule covers (dontAsk would deny it).")
    ap.add_argument("--root", default=".", help="base for relative paths and the defaults (default: .)")
    ap.add_argument("--skills-dir", action="append", default=[], help="directory holding <skill>/SKILL.md (repeatable)")
    ap.add_argument("--agents-dir", action="append", default=[],
                    help="directory holding <agent>.md files, whose bodies are checked against the settings alone "
                         "(repeatable; default: agents/ under --root when it exists)")
    ap.add_argument("--settings", action="append", default=[], help="settings JSON with permissions.allow/deny (repeatable)")
    ap.add_argument("--required-denies", metavar="FILE",
                    help="JSON with the 'deny' rules the settings must all carry (default: templates/required_denies.json "
                         "under --root, else the pack's own)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="fill a {placeholder} in settings rules, e.g. test_command='python3 -m pytest'")
    ap.add_argument("--own-only", action="store_true",
                    help="count only each skill's own allowed-tools (a -p run in an untrusted folder ignores project allow rules); "
                         "agents are still checked against the settings")
    ap.add_argument("--verbose", action="store_true", help="also list covered, human-only and prohibited commands")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("check_skill_permissions: --root %s is not a directory" % args.root, file=sys.stderr)
        return 2

    def at(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(root, p)

    dirs = [at(d) for d in args.skills_dir] or [at(d) for d in ("skills", ".claude/skills") if os.path.isdir(at(d))][:1]
    sets = [at(s) for s in args.settings] or [at(s) for s in ("templates/settings.project.json", ".claude/settings.json")
                                              if os.path.isfile(at(s))][:1]
    if not dirs or not sets:
        print("check_skill_permissions: no skills dir or settings file found under %s; pass --skills-dir and --settings" % root,
              file=sys.stderr)
        return 2
    default_agents = [at("agents")] if os.path.isdir(at("agents")) else []
    agent_dirs = [at(d) for d in args.agents_dir] or default_agents
    values: dict[str, str] = {}
    for kv in args.set:
        if "=" not in kv:
            print("check_skill_permissions: --set needs NAME=VALUE, got %r" % kv, file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        values[k.strip()] = v
    req_path = at(args.required_denies) if args.required_denies else next(
        (p for p in (at("templates/required_denies.json"), PACK_REQUIRED_DENIES) if os.path.isfile(p)), PACK_REQUIRED_DENIES)
    try:
        required = load_required(req_path)
        return check(dirs, sets, values, root, args.verbose, args.own_only, agent_dirs=agent_dirs, required=required)
    except Misuse as exc:
        print("check_skill_permissions: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - exit 2 on any crash, per the pack's script contract
        print("check_skill_permissions: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
