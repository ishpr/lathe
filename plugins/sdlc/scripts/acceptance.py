#!/usr/bin/env python3
"""Run the contract's executable acceptance lines and print evidence.

Why: acceptance criteria written as prose were never run (REC-09: a
README listed routes that did not exist). Under `## Acceptance` in contract.md, write pairs:

    - $ python3 -m pytest tests/ratelimit -q
      => exit 0
    - $ bash scripts/smoke_rate_limit.sh http://localhost:8000 demo-key 101
      => contains "429"

Expectations: `exit <n>`, `contains <text>` (quotes optional), `matches <regex>` (searched in
stdout+stderr). Each command runs in the repo root, 300 s timeout. Prints every command before it
runs, then a table and a final `ACCEPTANCE passed=N failed=M` line (verify parses it).

Safety: contract.md is written by the agent and verify runs it unattended, so acceptance lines are
an unguarded command channel unless restricted. Commands are therefore NOT given to a shell: each is
split with shlex and run directly, and only these forms are allowed (anything else FAILS with
"command not allowed", it is never run): `python3 -m pytest ...`, `pytest ...`,
`bash scripts/smoke_<name>.sh ...`, `curl ...` (without -o/-O/-T/-K/--output/--config/--upload-file),
`make test|check|lint`, the Node forms below, and the no-op builtins `echo`, `printf`, `true`, `false`, `test`. Humans-only
actions (ratchet --update, commits, --no-verify, writing files) cannot be reached this way.

Node forms (a repo whose tests are run by a package manager), exactly these and no others: `pnpm test [args]`,
`pnpm exec vitest run [args]`, `pnpm vitest run [args]`, `pnpm exec playwright test [args]`, `pnpm lint`,
`pnpm typecheck`, `npm test [args]`, `npm run test [args]`, `npx vitest run [args]`,
`npx --no-install vitest run [args]`, `yarn test [args]`, `yarn vitest run [args]`, `bun test [args]`,
`bunx vitest run [args]`. Arguments may not hold a shell operator (`&& ; | & < >`, a backtick or `$(`), a config
or output option (`-c`, `--config`, `--root`, `--outputFile`, `--output`) or a snapshot update (`-u`, `--update`,
`--update-snapshots`). A shell is never involved, so `pnpm test && x` is refused.

DevTools lines (Foldkit frontends). A line `- mcp: foldkit_dispatch_messages <json array of message objects>` followed
by `=> model.<dotted.path> == <json value>` (or `!=`) drives a running app through its DevTools relay, then compares one
path of the Model. It runs only when FOLDKIT_DEVTOOLS_URL (an http(s) URL on localhost or 127.0.0.1; the relay is
dev-only) is set; when it is unset the item FAILS with "devtools relay not configured" (fail closed, never skipped). The
relay's documented interface (@foldkit/devtools-mcp) is an MCP server over stdio that talks to the dev server's WebSocket
relay, not HTTP: acceptance starts that server as a subprocess (FOLDKIT_DEVTOOLS_MCP_CMD, a command line, else
node_modules/.bin/foldkit-devtools-mcp, else `npx --no-install @foldkit/devtools-mcp`) with FOLDKIT_DEVTOOLS_MCP_HOST and
FOLDKIT_DEVTOOLS_MCP_PORT taken from the URL (port 9988 when the URL has none), speaks JSON-RPC 2.0 to it (initialize,
tools/call foldkit_dispatch_messages, tools/call foldkit_get_model with path root.<path> and expand true) and closes it.

Runner-wrapped forms are accepted for a project whose tests need its environment: `uv run [--project X]
[--no-sync] <allowed command>`, `poetry run <allowed command>`, `pdm run <allowed command>`. The inner command
is checked by the rules above (`uv run pytest tests -q`, `uv run python -m pytest ...`); other runner options
and a runner inside a runner are refused.

`--ready` runs nothing: it checks that the contract can execute without a question (the autonomy
policy, "ask before, never during"): every `## Decisions` row has a real Value (an answer that is one of
the row's Options or its recommendation, never "see above", "tbd", "none", "ask product" or the like; cells
split on unescaped pipes only), the section exists (a table or the line "None."), no `## ... questions`
section is left open, and `## Defaults when ambiguous` carries the standard four lines verbatim and no line
that asks, stops or overrides a decision. A decision recorded as "recommended, unconfirmed" is a `FILL` at
tier medium or high (current.json) or when it touches money, security, data or customer-visible behaviour,
and a `NOTE` for the reviewer otherwise. It prints one `FILL` line per gap, saying exactly what to write,
then `READY` or `NOT READY`; a missing `## Capabilities` block or capability line is a `WARN`.
/feature runs it before any stage.

Usage: acceptance.py [--root DIR] [--contract PATH] [--require] [--ready]
The contract defaults to .claude/work/<slug>/contract.md, slug from .claude/work/current.json.
Exit: 0 all pass (or no lines; with --ready: ready), 1 any fail (not ready), 2 misuse, or no
lines with --require, or no contract with --ready.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import urllib.parse

TIMEOUT = 300
NOOPS = {"echo", "printf", "true", "false", "test"}
PY_NAMES = {"python", "python3"}
RUNNERS = {"uv", "poetry", "pdm"}
CURL_DENY = {"-o", "-O", "-T", "-K", "--output", "--output-dir", "--remote-name", "--upload-file", "--config", "--create-dirs"}
MAKE_TARGETS = {"test", "check", "lint"}
CMD_RE = re.compile(r"^\s*[-*]\s*\$\s+(.*\S)\s*$")
MCP_RE = re.compile(r"^\s*[-*]\s*mcp:\s+(.*\S)\s*$")
EXP_RE = re.compile(r"^\s*=>\s*(.*\S)\s*$")


def git_root() -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return out or os.getcwd()


def parse(text: str) -> list[tuple[str, str | None]]:
    """Return [(command, expectation-or-None)] from the ## Acceptance section."""
    lines = text.splitlines()
    in_sec = False
    out: list[tuple[str, str | None]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^#{1,6}\s", line):
            in_sec = bool(re.match(r"^##\s+Acceptance\b", line, re.I))
        elif in_sec:
            m = CMD_RE.match(line)
            cmd = m.group(1) if m else None
            if cmd is None:
                mm = MCP_RE.match(line)
                cmd = "mcp: " + mm.group(1) if mm else None
            if cmd is not None:
                exp = None
                if i + 1 < len(lines):
                    e = EXP_RE.match(lines[i + 1])
                    if e:
                        exp = e.group(1)
                        i += 1
                out.append((cmd, exp))
        i += 1
    return out


STANDARD_DEFAULTS = (
    "1. Make the smallest change that satisfies the acceptance criteria.",
    "2. Follow the nearest existing pattern in the code being changed.",
    "3. Prefer the option the Decisions above imply.",
    "4. When none applies, choose the reversible option.",
)
CAPABILITY_KEYS = ("paths", "commands", "egress", "data")
# A Value that is not an answer. Matched against the whole value after markup, a trailing period and a
# trailing parenthesis are removed. "see above", "to be decided", "decide later", "ask product", "none",
# "unknown", "not yet known", "tbd - ask product" and their kin are all placeholders (review finding: the
# old list took only ''/-/?/tbd/todo/n/a/open/pending, so every one of these read READY).
PLACEHOLDER_VALUE = re.compile(
    r"^(?:[-—–?.\s]*"
    r"|<[^>]*>"
    r"|(?:tbd|tbc|todo|n/?a|none|nil|null|open|pending|unknown|unclear|undecided|unsure|later|maybe|whatever|any|either)\b.*"
    r"|(?:see|as\s+per|per)\s+(?:above|below|ticket|recommendation|discussion|thread|slack|notes?)\b.*"
    r"|(?:to\s+be|will\s+be|needs?\s+to\s+be|yet\s+to\s+be)\s+(?:decided|determined|confirmed|defined|agreed|discussed|clarified)\b.*"
    r"|(?:decide|confirm|ask|check|clarify|discuss|figure|revisit|defer)\b.*"
    r"|not\s+(?:yet\s+)?(?:known|decided|sure|defined|confirmed|determined|clear)\b.*"
    r"|no\s+(?:answer|decision)\b.*"
    r")$", re.I)
EMPTY_VALUE = PLACEHOLDER_VALUE  # kept for callers that imported the old name
# Decisions a human must confirm even at a low tier (money, security, data, customer-visible behaviour).
SENSITIVE = re.compile(
    r"\b(money|payments?|prices?|pricing|billing|invoice\w*|refunds?|charge[sd]?|secur\w*|auth\w*|permissions?|credentials?|secrets?|"
    r"tokens?|encrypt\w*|pii|personal|privacy|gdpr|data|retention|delet\w*|migrat\w*|schema|customers?|user[- ]facing|"
    r"user[- ]visible|visible|public|notif\w*|emails?)\b", re.I)
KNOWN_SECTIONS = {"outcome", "acceptance criteria", "acceptance", "decisions", "defaults when ambiguous", "capabilities",
                  "out of scope", "allowed paths", "risk tier", "evidence plan"}
QUESTION_SECTION = re.compile(r"questions?|unresolved|open\s+(?:issues?|items?|points?)|to\s+(?:decide|confirm)|clarif|unknown|tbd|todo", re.I)
# A "default" that asks, stops, or overrides a decision is not a default (review finding: `Ask the human whenever
# something is unclear.` and `Ignore the decisions above and choose the cheapest option` read READY).
BAD_DEFAULT = re.compile(
    r"^\W*(?:ask|stop|escalate|wait|pause|halt|confirm|check\s+with|consult|defer|prompt|query)\b"
    r"|\b(?:ignore|override|disregard|overrule|bypass|contradict|supersede)\b[^.]*\bdecisions?\b"
    r"|\bdecisions?\b[^.]*\b(?:ignored|overridden|disregarded|overruled|bypassed|do\s+not\s+apply|don't\s+apply)\b"
    r"|\bwhenever\b[^.]*\b(?:unclear|unsure|ambiguous)\b[^.]*\b(?:ask|stop|escalate)\b"
    r"|\b(?:ask|escalate\s+to)\s+(?:the\s+)?(?:human|user|product|owner|reviewer|team|me)\b", re.I)


def sections(text: str) -> dict[str, list[str]]:
    """`## Heading` (lower-cased) -> its lines, HTML comments removed."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1).strip().lower()
            out.setdefault(cur, [])
        elif re.match(r"^#\s", line):
            cur = None
        elif cur is not None:
            out[cur].append(line)
    return out


def cells(line: str) -> list[str]:
    """Table cells split on unescaped pipes only; `\\|` inside a cell is a literal pipe."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def _clean(v: str) -> str:
    v = re.sub(r"[*_`]", "", v).strip()
    v = re.sub(r"\s*\([^)]*\)\s*$", "", v).strip()  # a trailing "(why)"
    return v.rstrip(".").strip()


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(v).lower()).strip()


def option_list(cell: str) -> list[str]:
    """The options named in an Options cell: split on middots, slashes, semicolons, pipes, commas and ' or '."""
    parts = re.split(r"\s*(?:·|;|\||/|,|\bor\b)\s*", cell)
    out = []
    for part in parts:
        part = re.sub(r"^\W*(?:[A-Za-z]|\d+)[.):]\s+", "", part.strip())  # "a) ", "1. ", "A: "
        part = re.sub(r"^\W*(?:[A-Za-z]|\d+)\s*:\s*", "", part)
        n = _norm(part.split(":")[0] if ":" in part else part)
        if n:
            out.append(n)
    return out


def matches_option(value: str, options: list[str], recommendation: str) -> bool:
    v = _norm(value)
    if not v:
        return False
    pool = list(options)
    rec = re.search(r"\*\*(.+?)\*\*", recommendation or "")
    pool.append(_norm(rec.group(1)) if rec else _norm((recommendation or "").split(":")[0]))
    for o in pool:
        if not o:
            continue
        if v == o or (len(o) >= 3 and (v.startswith(o) or o in v)) or (len(v) >= 3 and o.startswith(v)):
            return True
    return False


def check_value(value: str, options_cell: str, recommendation: str) -> str | None:
    """None when Value is a real answer, else why it is not."""
    raw = re.sub(r"[*_`]", "", value).strip()
    if not raw or re.fullmatch(r"<[^>]*>|[-—–?.\s]+", raw):
        return "Value is empty"
    options = option_list(options_cell)
    if len(options) >= 2 and matches_option(raw, options, recommendation):
        return None
    base = _clean(value)
    if PLACEHOLDER_VALUE.match(raw.rstrip(".").strip()) or PLACEHOLDER_VALUE.match(base):
        return f"Value '{raw}' is a placeholder, not an answer"
    if len(base) < 2:
        return f"Value '{raw}' is too short to be an answer"
    if len(options) >= 2 and not matches_option(raw, options, recommendation):
        return f"Value '{raw}' is not one of the Options ({' / '.join(options)}); write one of them, or add yours to Options"
    return None


def autonomy_check(text: str, tier: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """Return (fills, warns, notes) for the autonomy check (--ready).

    `tier` is current.json's tier when known; an unconfirmed decision is a FILL at tier medium or high, or when
    the question touches money, security, data or anything customer-visible, and a NOTE otherwise."""
    sec = sections(text)
    fills: list[str] = []
    warns: list[str] = []
    notes: list[str] = []
    tier = (tier or "").strip().lower()
    dec = sec.get("decisions")
    if dec is None:
        fills.append("add a '## Decisions' section: a table with columns # | Question | Options | Recommendation | "
                     "Value | Decided by, or the line 'None.'")
    else:
        body = [ln for ln in dec if ln.strip()]
        rows = [ln for ln in body if ln.lstrip().startswith("|")]
        if not rows:
            # the ledger renders decided rows as "- <question> -> <choice> (<by>)"
            bullets = [ln for ln in body if re.match(r"^\s*[-*]\s", ln)]
            if bullets and len(bullets) == len(body):
                for ln in bullets:
                    m = re.search(r"\s(?:->|→)\s*(.*)$", ln)
                    q = re.sub(r"^\s*[-*]\s*", "", ln.split("->")[0].split("→")[0]).strip()
                    why = check_value(m.group(1), "", "") if m else "has no '->' answer"
                    if m and why is None:
                        why = None
                    if why:
                        fills.append(f"Decisions item '{q}': {why}; write '- <question> -> <choice>'")
            elif not body or not re.match(r"^\s*none\.?\s*$", body[0], re.I) or len(body) > 1:
                fills.append("Decisions has no table; write the table (with a Value column) or the line 'None.'")
        else:
            head = [c.lower() for c in cells(rows[0])]
            data = [r for r in rows[1:] if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", r)]
            if "value" not in head:
                fills.append("Decisions table has no Value column; add 'Value' after 'Recommendation' and fill one per row")
            else:
                vi = head.index("value")
                qi = head.index("question") if "question" in head else (1 if len(head) > 1 else 0)
                ri = head.index("recommendation") if "recommendation" in head else None
                oi = head.index("options") if "options" in head else None
                di = head.index("decided by") if "decided by" in head else None
                for r in data:
                    c = cells(r)
                    rid = c[0] or "?"
                    if len(c) != len(head):
                        fills.append(f"Decisions row {rid}: {len(c)} cells but the table has {len(head)} columns; "
                                     "write a literal pipe inside a cell as \\|")
                        continue
                    rec = c[ri] if ri is not None else ""
                    why = check_value(c[vi], c[oi] if oi is not None else "", rec)
                    if why:
                        fills.append(f"Decisions row {rid} '{c[qi]}': {why}; write the chosen option "
                                     f"(recommendation: {rec or 'none given'})")
                    marks = " ".join(c[di:di + 1] if di is not None else c).lower()
                    if re.search(r"unconfirmed|\bassumed\b|\bunsure\b", marks) or (di is not None and not c[di].strip(" -—–?")):
                        topic = " ".join(c[qi:qi + 1] + (c[oi:oi + 1] if oi is not None else []) + c[vi:vi + 1])
                        hot = tier in ("medium", "high") or SENSITIVE.search(topic)
                        if hot:
                            fills.append(f"Decisions row {rid} '{c[qi]}' is unconfirmed but "
                                         + (f"the tier is {tier}" if tier in ("medium", "high") else "it touches money, security, data or "
                                            "something customers see")
                                         + ": a human confirms it; set Decided by to the @handle who answered")
                        else:
                            notes.append(f"Decisions row {rid} '{c[qi]}' is recommended, unconfirmed: the reviewer confirms it")
    for name, lines in sec.items():
        content = [ln.strip() for ln in lines if ln.strip()]
        if name in KNOWN_SECTIONS or not content:
            continue
        if QUESTION_SECTION.search(name) and not (len(content) == 1 and re.match(r"^none\.?$", content[0], re.I)):
            fills.append(f"section '## {name}' holds open questions; answer each in Decisions (or write 'None.' here)")
        elif any(ln.endswith("?") for ln in content):
            fills.append(f"section '## {name}' holds a question; every question belongs in Decisions with a Value")
    dflt = sec.get("defaults when ambiguous")
    if dflt is None:
        fills.append("add a '## Defaults when ambiguous' section with the standard four lines (then any "
                     "per-contract lines):\n  " + "\n  ".join(STANDARD_DEFAULTS))
    elif not any(ln.strip() for ln in dflt):
        fills.append("'## Defaults when ambiguous' is empty; write the standard four lines:\n  " + "\n  ".join(STANDARD_DEFAULTS))
    else:
        flat = [re.sub(r"\s+", " ", re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", ln)).strip().lower() for ln in dflt if ln.strip()]
        missing = [d for d in STANDARD_DEFAULTS
                   if re.sub(r"^\d+\.\s*", "", d).lower() not in flat]
        if missing:
            fills.append("'## Defaults when ambiguous' must carry the standard four lines verbatim; missing:\n  " + "\n  ".join(missing))
        std = {re.sub(r"^\d+\.\s*", "", d).lower() for d in STANDARD_DEFAULTS}
        for ln in dflt:
            t = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", ln).strip()
            if not t or t.lower().rstrip(":") == "per contract" or re.sub(r"\s+", " ", t).lower() in std:
                continue
            if BAD_DEFAULT.search(t):
                fills.append(f"Defaults line '{t}' tells the agent to ask, stop or override a Decision; a default settles "
                             "ambiguity on its own (delete the line or rewrite it as a rule the agent can apply)")
    caps = sec.get("capabilities")
    if caps is None:
        warns.append("no '## Capabilities' section (- Paths: / - Commands: / - Egress: / - Data:); execution assumes "
                     "the allowed paths, the stack's commands, no egress and no data access")
    else:
        have = {m.group(1).lower() for ln in caps
                for m in [re.match(r"^\s*[-*]\s*\**\s*(paths|commands|egress|data)\b", ln, re.I)] if m}
        for k in CAPABILITY_KEYS:
            if k not in have:
                warns.append(f"Capabilities has no '- {k.capitalize()}:' line")
    return fills, warns, notes


def allowed(cmd: str, root: str) -> tuple[list[str] | None, str]:
    """Return (argv, "") when `cmd` is an allowed simple command, else (None, reason)."""
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return None, f"unparseable command: {exc}"
    if not argv:
        return None, "empty command"
    inner, why = strip_runner(argv)
    if why:
        return None, why
    ok, reason = allowed_argv(inner)
    return (argv if ok is not None else None), reason


def strip_runner(argv: list[str]) -> tuple[list[str], str]:
    """`uv run [--project X] [--no-sync] CMD`, `poetry run CMD`, `pdm run CMD` -> (CMD argv, ""); anything else passes through.

    Only those options are skipped, so a runner cannot be given an option that changes what runs (--with, --directory,
    --script, --env-file and the like). The inner command is then checked by the same rules as an unwrapped one, and a
    runner inside a runner is refused."""
    if len(argv) < 3 or argv[1] != "run" or argv[0] not in RUNNERS:
        return argv, ""
    i = 2
    if argv[0] == "uv":
        while i < len(argv) and argv[i].startswith("-"):
            opt = argv[i]
            if opt == "--no-sync":
                i += 1
            elif opt == "--project" and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 2
            elif opt.startswith("--project=") and len(opt) > len("--project="):
                i += 1
            else:
                return [], f"runner option {opt} is not allowed in acceptance (allowed: uv run [--project X] [--no-sync])"
    inner = argv[i:]
    if not inner:
        return [], f"{argv[0]} run needs a command"
    if inner[0] in RUNNERS:
        return [], "a runner inside a runner is not allowed in acceptance"
    return inner, ""


# Node forms: (program, fixed words after it, whether arguments may follow). Matched from the left, so a package
# manager option before the subcommand (`pnpm --dir x test`, `pnpm -C x test`) is refused.
NODE_FORMS = (
    ("pnpm", ("test",), True),
    ("pnpm", ("exec", "vitest", "run"), True),
    ("pnpm", ("vitest", "run"), True),
    ("pnpm", ("exec", "playwright", "test"), True),
    ("pnpm", ("lint",), False),
    ("pnpm", ("typecheck",), False),
    ("npm", ("test",), True),
    ("npm", ("run", "test"), True),
    ("npx", ("vitest", "run"), True),
    ("npx", ("--no-install", "vitest", "run"), True),
    ("yarn", ("test",), True),
    ("yarn", ("vitest", "run"), True),
    ("bun", ("test",), True),
    ("bunx", ("vitest", "run"), True),
)
NODE_HEADS = {f[0] for f in NODE_FORMS}
NODE_SHELL = re.compile(r"[;|&<>`\n]|\$\(")
# Options that point the tool at another config or write files, and snapshot updates (they rewrite the frozen tests).
NODE_DENY = {"-c", "--config", "--root", "-r", "--dir", "--outputFile", "--output", "-u", "--update", "--update-snapshots", "--update-source-snapshots"}


def allowed_node(argv: list[str]) -> tuple[bool, str]:
    for head, words, takes_args in NODE_FORMS:
        n = len(words)
        if argv[0] != head or tuple(argv[1:1 + n]) != words:
            continue
        rest = argv[1 + n:]
        if rest and not takes_args:
            return False, f"{' '.join(argv[:1 + n])} takes no arguments in acceptance"
        for a in rest:
            if NODE_SHELL.search(a):
                return False, f"argument {a!r} holds a shell operator; acceptance runs one plain command without a shell"
            if a.split("=", 1)[0] in NODE_DENY:
                return False, f"option {a.split('=', 1)[0]} points at another config, writes files or updates snapshots"
        return True, ""
    return False, "node command not allowed in acceptance (allowed: " + "; ".join(f"{h} {' '.join(w)}" + (" [args]" if a else "") for h, w, a in NODE_FORMS) + ")"


def allowed_argv(argv: list[str]) -> tuple[list[str] | None, str]:
    head = argv[0]
    if head in NOOPS:
        return argv, ""
    if head in NODE_HEADS:
        ok, why = allowed_node(argv)
        return (argv, "") if ok else (None, why)
    if head == "pytest" or (head in PY_NAMES and argv[1:3] == ["-m", "pytest"]) or (head in PY_NAMES and argv[1:4] == ["-I", "-m", "pytest"]):
        return argv, ""
    if head == "bash" and len(argv) >= 2:
        script = argv[1]
        rel = os.path.normpath(script)
        if re.fullmatch(r"scripts/smoke_[\w.-]+\.sh", rel.replace(os.sep, "/")) and not rel.startswith(".."):
            return argv, ""
    if head == "curl":
        bad = [a for a in argv[1:] if a in CURL_DENY or (a.startswith("-") and not a.startswith("--") and any(c in a[1:] for c in "oOTK"))]
        if not bad:
            return argv, ""
        return None, f"curl option {bad[0]} writes files or reads config"
    if head == "make" and len(argv) == 2 and argv[1] in MAKE_TARGETS:
        return argv, ""
    return None, "command not allowed in acceptance (allowed: python3 -m pytest, pytest, bash scripts/smoke_*.sh, curl, make test|check|lint, pnpm/npm/npx/yarn/bun/bunx test and vitest/playwright/lint/typecheck forms (see --help), echo/printf/true/false/test; wrapped as uv run [--project X] [--no-sync] / poetry run / pdm run <one of those>)"


# ---------- DevTools lines: `- mcp: foldkit_dispatch_messages [...]` + `=> model.a.b == <json>` ----------

DEVTOOLS_TIMEOUT = 60
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
DEFAULT_RELAY_PORT = 9988
MODEL_EXP = re.compile(r"^model\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\s*(==|!=)\s*(.+)$")


def devtools_target(environ: dict[str, str]) -> tuple[tuple[str, str] | None, str]:
    """((host, port), "") from FOLDKIT_DEVTOOLS_URL, or (None, why). Fails closed: unset and non-local are both refusals."""
    raw = (environ.get("FOLDKIT_DEVTOOLS_URL") or "").strip()
    if not raw:
        return None, "devtools relay not configured (set FOLDKIT_DEVTOOLS_URL to the local relay, for example http://localhost:9988)"
    try:
        u = urllib.parse.urlsplit(raw)
        port = u.port
    except ValueError as exc:
        return None, f"FOLDKIT_DEVTOOLS_URL is not a URL: {exc}"
    if u.scheme not in ("http", "https") or not u.hostname:
        return None, "FOLDKIT_DEVTOOLS_URL must be an http(s) URL"
    if u.hostname.lower() not in LOCAL_HOSTS or u.username or u.password:
        return None, "FOLDKIT_DEVTOOLS_URL refused: the relay is dev-only, so only localhost or 127.0.0.1 is accepted"
    return (u.hostname.lower(), str(port or DEFAULT_RELAY_PORT)), ""


def devtools_command(root: str, environ: dict[str, str]) -> list[str]:
    configured = (environ.get("FOLDKIT_DEVTOOLS_MCP_CMD") or "").strip()
    if configured:
        return shlex.split(configured)
    local = os.path.join(root, "node_modules", ".bin", "foldkit-devtools-mcp")
    if os.path.isfile(local):
        return [local]
    return ["npx", "--no-install", "@foldkit/devtools-mcp"]


def mcp_calls(cmd: list[str], env: dict[str, str], cwd: str, calls: list[tuple[str, dict]], timeout: float) -> list[dict]:
    """Start an MCP server on stdio (newline-delimited JSON-RPC 2.0), initialize it, run each tools/call, return the results."""
    import queue
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    inbox: queue.Queue = queue.Queue()
    tail: list[str] = []

    def pump_out() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            try:
                inbox.put(json.loads(line))
            except ValueError:
                tail.append(line.strip())
        inbox.put(None)

    def pump_err() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line.strip())

    for fn in (pump_out, pump_err):
        threading.Thread(target=fn, daemon=True).start()
    deadline = [timeout]

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]

    def rpc(i: int, method: str, params: dict) -> dict:
        send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        while True:
            try:
                msg = inbox.get(timeout=deadline[0])
            except queue.Empty:
                raise RuntimeError(f"relay did not answer {method} within {int(deadline[0])}s") from None
            if msg is None:
                raise RuntimeError("relay process exited" + (": " + " | ".join(t for t in tail[-3:] if t)[:300] if tail else ""))
            if isinstance(msg, dict) and msg.get("id") == i and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message') if isinstance(msg['error'], dict) else msg['error']}")
                return msg["result"]

    results: list[dict] = []
    try:
        rpc(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "sdlc-acceptance", "version": "1"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        for n, (name, args) in enumerate(calls, start=2):
            results.append(rpc(n, "tools/call", {"name": name, "arguments": args}))
    finally:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def tool_text(result: dict) -> str:
    return "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict) and c.get("type") == "text")


def run_devtools(cmd: str, exp: str | None, root: str, environ: dict[str, str] | None = None) -> tuple[bool, str]:
    """Run one `mcp:` line: dispatch the messages, read the Model, compare one path. Never raises; every refusal is a FAIL."""
    environ = dict(os.environ if environ is None else environ)
    target, why = devtools_target(environ)
    if target is None:
        return False, why
    m = re.match(r"^mcp:\s+(\S+)\s*(.*)$", cmd, re.S)
    if not m or m.group(1) != "foldkit_dispatch_messages":
        return False, "only `mcp: foldkit_dispatch_messages <json array>` is allowed in acceptance"
    try:
        msgs = json.loads(m.group(2))
    except ValueError as exc:
        return False, f"the messages are not JSON: {exc}"
    if not isinstance(msgs, list) or not 1 <= len(msgs) <= 100 or not all(isinstance(x, dict) for x in msgs):
        return False, "the messages must be a JSON array of 1 to 100 objects"
    if exp is None:
        return False, "no `=>` expectation line"
    em = MODEL_EXP.match(exp)
    if not em:
        return False, "an mcp line expects `=> model.<dotted.path> == <json value>` (or `!=`)"
    path, op = em.group(1), em.group(2)
    try:
        want = json.loads(em.group(3))
    except ValueError as exc:
        return False, f"the expected value is not JSON: {exc}"
    env = dict(environ)
    env["FOLDKIT_DEVTOOLS_MCP_HOST"], env["FOLDKIT_DEVTOOLS_MCP_PORT"] = target
    try:
        res = mcp_calls(devtools_command(root, environ), env, root,
                        [("foldkit_dispatch_messages", {"messages": msgs}),
                         ("foldkit_get_model", {"path": "root." + path, "expand": True})], DEVTOOLS_TIMEOUT)
    except (OSError, RuntimeError, ValueError) as exc:
        return False, f"devtools relay error: {exc}"
    if res[0].get("isError"):
        return False, f"dispatch refused: {tool_text(res[0])[:200]}"
    if res[1].get("isError"):
        return False, f"get_model failed: {tool_text(res[1])[:200]}"
    try:
        body = json.loads(tool_text(res[1]))
    except ValueError:
        return False, f"get_model answered non-JSON: {tool_text(res[1])[:120]}"
    got = body["value"] if isinstance(body, dict) and "value" in body else body
    same = json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True)
    ok = same if op == "==" else not same
    return ok, f"model.{path} = {json.dumps(got)[:80]}"


def check(exp: str | None, code: int, output: str) -> tuple[bool, str]:
    if exp is None:
        return False, "no `=>` expectation line"
    m = re.match(r"^exit\s+(-?\d+)$", exp)
    if m:
        return code == int(m.group(1)), f"exit {code}"
    m = re.match(r"^contains\s+(.*)$", exp)
    if m:
        want = m.group(1).strip()
        if len(want) >= 2 and want[0] == want[-1] and want[0] in "\"'":
            want = want[1:-1]
        return want in output, f"exit {code}, {'found' if want in output else 'not found'}"
    m = re.match(r"^matches\s+(.*)$", exp)
    if m:
        try:
            hit = re.search(m.group(1).strip(), output, re.M) is not None
        except re.error as err:
            return False, f"bad regex: {err}"
        return hit, f"exit {code}, {'matched' if hit else 'no match'}"
    return False, "unknown expectation (use exit/contains/matches)"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="acceptance.py", description="Run contract acceptance lines. Allowed commands: python3 -m pytest, pytest, bash scripts/smoke_*.sh, curl, make test|check|lint, pnpm test, pnpm exec vitest run, pnpm vitest run, pnpm exec playwright test, pnpm lint, pnpm typecheck, npm test, npm run test, npx [--no-install] vitest run, yarn test, yarn vitest run, bun test, bunx vitest run, and the same wrapped as uv run [--project X] [--no-sync] / poetry run / pdm run. A line '- mcp: foldkit_dispatch_messages <json>' with '=> model.<path> == <json>' drives a local Foldkit DevTools relay (FOLDKIT_DEVTOOLS_URL).")
    ap.add_argument("--root", default=None)
    ap.add_argument("--contract", default=None)
    ap.add_argument("--require", action="store_true", help="exit 2 when there are no acceptance lines")
    ap.add_argument("--ready", action="store_true",
                    help="run nothing; check every Decision has a Value and the Defaults block exists")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    root = os.path.abspath(args.root or os.environ.get("CLAUDE_PROJECT_DIR") or git_root())
    path = args.contract
    if not path:
        try:
            with open(os.path.join(root, ".claude", "work", "current.json"), encoding="utf-8") as fh:
                slug = json.load(fh).get("slug")
        except (OSError, json.JSONDecodeError, AttributeError):
            slug = None
        if slug:
            path = os.path.join(root, ".claude", "work", slug, "contract.md")
    if args.ready:
        if not path or not os.path.isfile(path):
            print(f"acceptance: --ready: no contract{': ' + path if path else ' (no .claude/work/current.json slug)'}",
                  file=sys.stderr)
            return 2
        tier = None
        try:
            with open(os.path.join(root, ".claude", "work", "current.json"), encoding="utf-8") as fh:
                cur = json.load(fh)
            if isinstance(cur, dict) and (not cur.get("slug") or os.path.basename(os.path.dirname(path)) == cur.get("slug")):
                tier = cur.get("tier")
        except (OSError, ValueError):
            pass
        with open(path, encoding="utf-8") as fh:
            fills, warns, notes = autonomy_check(fh.read(), tier)
        for f in fills:
            print(f"FILL\t{f}")
        for w_ in warns:
            print(f"WARN\t{w_}")
        for n in notes:
            print(f"NOTE\t{n}")
        rel = os.path.relpath(path, root)
        print(f"NOT READY: {len(fills)} item(s) to fill in {rel}" if fills else f"READY: {rel}")
        return 1 if fills else 0
    pairs: list[tuple[str, str | None]] = []
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            pairs = parse(fh.read())
    elif args.contract:
        print(f"acceptance: contract not found: {path}", file=sys.stderr)
        return 2
    if not pairs:
        print("acceptance: no acceptance lines")
        print("ACCEPTANCE passed=0 failed=0")
        return 2 if args.require else 0

    rows = []
    passed = failed = 0
    for cmd, exp in pairs:
        print(f"acceptance: $ {cmd}")
        if cmd.startswith("mcp: "):
            ok, got = run_devtools(cmd, exp, root)
            passed += ok
            failed += not ok
            rows.append((cmd, exp or "(missing)", got, "PASS" if ok else "FAIL"))
            continue
        argv, why = allowed(cmd, root)
        if argv is None:
            ok, got = False, why
        else:
            try:
                proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
                ok, got = check(exp, proc.returncode, proc.stdout + proc.stderr)
            except subprocess.TimeoutExpired:
                ok, got = False, f"timeout after {TIMEOUT}s"
            except OSError as exc:
                ok, got = False, f"cannot run: {exc}"
        passed += ok
        failed += not ok
        rows.append((cmd, exp or "(missing)", got, "PASS" if ok else "FAIL"))
    w = [max(len(r[i]) for r in rows + [("command", "expected", "got", "")]) for i in range(3)]
    w = [min(x, 60) for x in w]
    fmt = lambda r: f"{r[0][:w[0]]:<{w[0]}}  {r[1][:w[1]]:<{w[1]}}  {r[2][:w[2]]:<{w[2]}}  {r[3]}"  # noqa: E731
    print(fmt(("command", "expected", "got", "result")))
    for r in rows:
        print(fmt(r))
    # The table cuts long cells at 60 columns; a cut row is repeated in full so no reason is lost.
    for r in rows:
        if any(len(r[i]) > w[i] for i in range(3)):
            print(f"{r[3]}: $ {r[0]}\n  expected: {r[1]}\n  got: {r[2]}")
    print(f"ACCEPTANCE passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
