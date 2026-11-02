#!/usr/bin/env python3
"""Prove that every Bash command a skill tells Claude to run is pre-approved (REC-51, REC-52).

Why: the pack's autonomy policy ("ask before, never during", docs/DESIGN.md section 14)
runs execution with `--permission-mode dontAsk`, which denies every tool call that would otherwise
prompt. A skill step whose command matches no allow rule therefore fails mid-run (REC-51: "today
every /ship step prompts"; REC-52: unattended runs use dontAsk). This script finds those steps
statically, so a new skill line cannot introduce a prompt; CI runs it.

What it reads:
- every `SKILL.md` under each `--skills-dir` (repeatable): the frontmatter `allowed-tools`, and
  the commands in the body: inline code spans and fenced-block lines whose first word is a program
  (PROGRAMS: python3, bash, git, gh, make, uv, npx, go, cargo, pytest, sdlc-graph; OTHER_PROGRAMS:
  rm, mkdir, docker, alembic, curl, head, tail, ... any shell command a skill is likely to name).
  Bare program names such as `gh` and spans in a negated phrase such as "never `git add -A`" are
  not commands to run. A compound part that starts with a word in neither list is reported
  UNCOVERED ("unknown program"), never dropped: a skipped part is a step dontAsk would deny
  (review finding: `| tail -1`, `rm -rf` in ship);
- the Write tool: a skill that names `.sdlc-stage`, `assumptions.md` or `questions.md` (the
  files an execution skill writes through the staging directory and workfile.py) must be covered by
  an `Edit(.sdlc-stage/...)` rule (or a bare `Write` or `Edit`), in its own allowed-tools or the settings,
  or it is UNCOVERED. An Edit rule covers every file-editing tool, the Write tool included; a
  `Write(<path>)` rule never matches in Claude Code (probed on 2.1.316: `Write(.sdlc-stage/**)`,
  `Write(/.sdlc-stage/**)`, `Write(./.sdlc-stage/**)` and an exact file path were all denied under
  dontAsk while `Edit(.sdlc-stage/**)` allowed the same Write), so it counts for nothing here;
- `permissions.allow` and `permissions.deny` from `--settings` (repeatable).

Normalising: `${CLAUDE_PLUGIN_ROOT}`, `$CLAUDE_PLUGIN_ROOT` and the installer placeholder
`{plugin_root}` are one token; `<placeholder>` and a trailing `...` become the argument `ARG`;
compound commands are split on `&&`, `||`, `;` and `|` (each part needs its own rule, as in Claude
Code). Other `{placeholders}` in a settings rule are filled from `--set name=value`, and a rule
whose placeholder has no value is dropped, as init_project.py does.

Matching follows the documented Bash rule semantics (the Claude Code permissions documentation):
a `*` matches any text; a single trailing ` *` also matches the bare command; a trailing `:*` is
the legacy spelling of ` *`; a rule without `*` matches exactly one command. Quotes are part of
the text. A command matching a deny rule is reported as human-only (dontAsk denies it, it never
prompts); a command a skill says only a human runs, which no deny rule matches in that spelling, is UNCOVERED
(a form the deny rules miss is a form the agent could run); a command with `$(...)` or a redirection to a file is uncovered whatever the rules say,
because Claude Code checks those separately.

Output: one `UNCOVERED` line per command with the skill, the line, the normalised command and a
rule that would cover it; then a summary line. `--verbose` also lists covered and human-only
commands.

`--own-only` counts only each skill's own `allowed-tools`: a `claude -p` run in a folder nobody
has trusted ignores the project's allow rules but still applies a skill's `allowed-tools`.

Usage: check_skill_permissions.py [--root DIR] [--skills-dir DIR]... [--settings FILE]...
                                  [--set NAME=VALUE]... [--own-only] [--verbose]
Defaults (relative to --root): skills `skills/` or `.claude/skills/`; settings
`templates/settings.project.json` or `.claude/settings.json`.
Exit codes: 0 every command covered, 1 any uncovered, 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROGRAMS = ("python3", "bash", "git", "gh", "make", "uv", "npx", "go", "cargo", "pytest", "sdlc-graph")
# Shell commands a skill may name besides the pack's own programs. They start a command (an inline span or a
# fenced line) just as PROGRAMS do, so an `rm -rf` or a `docker compose up` in a skill is checked, not ignored.
OTHER_PROGRAMS = ("rm", "mkdir", "mv", "cp", "cat", "cd", "curl", "wget", "docker", "docker-compose", "alembic", "pip",
                  "pip3", "npm", "pnpm", "yarn", "node", "ls", "tail", "head", "wc", "sort", "touch", "chmod", "sed",
                  "awk", "echo", "kill", "env", "ruff", "mypy", "sh", "source", "export", "psql", "redis-cli", "xargs",
                  "tee", "find", "grep", "timeout", "nice", "poetry", "pipenv", "uvx", "ln", "tar", "kubectl",
                  "terraform", "aws", "gcloud")
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


def check(skill_dirs: list[str], settings: list[str], values: dict[str, str], root: str,
          verbose: bool = False, own_only: bool = False, out=sys.stdout) -> int:
    allow_rules, deny_rules = load_settings(settings)
    allow = bash_patterns(allow_rules, values)
    deny = bash_patterns(deny_rules, values)
    files = skill_files(skill_dirs)
    counts = {"covered": 0, "human-only": 0, "prohibited": 0, "uncovered": 0}
    seen: set[tuple[str, str]] = set()
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
        for line, raw, kind in candidates(body, first):
            parts = split_compound(raw)
            for part in parts:
                cmd = normalise(part)
                key = (rel, cmd)
                if key in seen:
                    continue
                seen.add(key)
                if first_word(cmd) not in COMMANDS and kind == "run":
                    # a skipped part is a step dontAsk would deny: report it instead of dropping it
                    counts["uncovered"] += 1
                    print("UNCOVERED\t%s:%d\t%s\tunknown program %r; add a rule for it or take it out of the skill"
                          % (rel, line, cmd, first_word(cmd)), file=out)
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
                pool = own if own_only else own + allow
                rule = None if why else next((r for r in pool if rule_matches(r, cmd)), None)
                if rule is not None:
                    counts["covered"] += 1
                    if verbose:
                        src = "allowed-tools" if rule in own else "settings"
                        print("covered\t%s:%d\t%s\t%s Bash(%s)" % (rel, line, cmd, src, rule), file=out)
                    continue
                counts["uncovered"] += 1
                print("UNCOVERED\t%s:%d\t%s\t%s" % (rel, line, cmd, why or "add %s" % suggest(cmd)), file=out)
    print("check_skill_permissions: %d skill(s); %d covered, %d human-only (denied), %d prohibited in prose, %d uncovered"
          % (len(files), counts["covered"], counts["human-only"], counts["prohibited"], counts["uncovered"]), file=out)
    return 1 if counts["uncovered"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_skill_permissions.py",
                                 description="Fail when a skill names a Bash command no allow rule covers (dontAsk would deny it).")
    ap.add_argument("--root", default=".", help="base for relative paths and the defaults (default: .)")
    ap.add_argument("--skills-dir", action="append", default=[], help="directory holding <skill>/SKILL.md (repeatable)")
    ap.add_argument("--settings", action="append", default=[], help="settings JSON with permissions.allow/deny (repeatable)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="fill a {placeholder} in settings rules, e.g. test_command='python3 -m pytest'")
    ap.add_argument("--own-only", action="store_true",
                    help="count only each skill's own allowed-tools (a -p run in an untrusted folder ignores project allow rules)")
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
    values: dict[str, str] = {}
    for kv in args.set:
        if "=" not in kv:
            print("check_skill_permissions: --set needs NAME=VALUE, got %r" % kv, file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        values[k.strip()] = v
    try:
        return check(dirs, sets, values, root, args.verbose, args.own_only)
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
