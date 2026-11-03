#!/usr/bin/env python3
"""Scan agent configuration for hidden or hostile content (REC-04, ISS: prompt-injection surface).

Agent config (.claude/**, CLAUDE.md, AGENTS.md, .mcp.json, .github/workflows/**) is read by the
model as instructions, so anything a human cannot see or easily review is a risk. This fails on:
  * invisible or bidirectional code points: the Unicode Default_Ignorable set (U+00AD, U+034F,
    U+061C, U+115F-1160, U+17B4-17B5, U+180B-180F, U+200B-200F, U+2028-202E, U+2060-206F, U+3164,
    U+FE00-FE0F variation selectors (emoji VS16 after a symbol is allowed), U+FEFF, U+FFA0,
    U+FFF0-FFF8, U+1D173-1D17A), U+2800 braille blank, and the whole Unicode tag block
    U+E0000-E0FFF (which hides ASCII text); a decoded tag payload is shown in the finding,
  * NUL bytes and files that are not valid UTF-8 (a NUL no longer hides the rest of a file),
  * base64-looking runs of 60+ characters, or two or more consecutive base64-only lines of 40+
    (sha256/sha512 hex digests, SRI "sha512-..." values, "integrity=" fields and URLs are exempt),
  * `curl ... | sh|bash` (also across backslash line continuations), `wget ... | sh`,
    `eval "$(curl`, `sh -c "$(curl ...)"`, `bash <(curl ...)`, `base64 -d | sh`,
  * HTML comments and markdown link-reference comments (`[//]: # (...)`) that address an assistant
    ("ignore previous", "you must", "do not tell", ...).

Each finding prints as `path:line: reason: <offending text escaped as \\uXXXX>`.
Exit codes: 0 clean, 1 findings, 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata

DEFAULT_PATHS = [".claude", "CLAUDE.md", "AGENTS.md", ".mcp.json", ".github/workflows"]
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
SKIP_PREFIXES = (".claude/work/", ".claude/worktrees/")  # runtime logs, not config

INVISIBLE_RANGES = [
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
]
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".pyc", ".so", ".webp"}
B64_MIN = 60
B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}" % B64_MIN)
B64_LINE = re.compile(r"^\s*[A-Za-z0-9+/]{40,}={0,2}\s*$")
HEX_DIGEST = re.compile(r"^(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{128})$")
SRI_BEFORE = re.compile(r"(?:sha(?:256|384|512)[-:=]|integrity\W{0,3}|hash\W{0,3}|digest\W{0,3})$", re.I)
SHELL_PATTERNS = [
    (re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"), "pipes a download into a shell"),
    (re.compile(r"\beval\s+[\"']?\$\(\s*(?:curl|wget)\b"), "evals a downloaded script"),
    (re.compile(r"\b(?:ba|z|da)?sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b"), "runs a downloaded script through sh -c"),
    (re.compile(r"\b(?:ba|z|da)?sh\s+<\(\s*(?:curl|wget)\b"), "runs a downloaded script through process substitution"),
    (re.compile(r"\bsource\s+<\(\s*(?:curl|wget)\b"), "sources a downloaded script"),
    (re.compile(r"\bbase64\s+(?:-d|-D|--decode)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"), "decodes base64 into a shell"),
]
HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
LINK_COMMENT = re.compile(r"^\s*\[(?://|comment|_)\]:\s*(?:#|<>)\s*(?:\((.*)\)|\"(.*)\"|'(.*)')\s*$", re.M)
IMPERATIVE = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above)|disregard|you must|you should|you are now|"
    r"do not tell|don'?t tell|do not mention|do not reveal|system prompt|override (?:your|the) |"
    r"new instructions|forget (?:your|all|everything)",
    re.I,
)


def is_invisible(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in INVISIBLE_RANGES)


def hidden_chars(line: str) -> list[str]:
    """Invisible characters in `line`, except an emoji variation selector that follows a symbol."""
    bad = []
    prev = ""
    for ch in line:
        cp = ord(ch)
        if is_invisible(cp):
            emoji_vs = cp in (0xFE0E, 0xFE0F) and prev and (unicodedata.category(prev) in ("So", "Sk", "Sm", "No") or prev in "#*0123456789")
            if not emoji_vs:
                bad.append(ch)
        prev = ch
    return bad


def tag_payload(line: str) -> str:
    """ASCII hidden in Unicode tag characters (U+E0020..E007E), as written by the 'ASCII smuggling' trick."""
    return "".join(chr(ord(c) - 0xE0000) for c in line if 0xE0020 <= ord(c) <= 0xE007E)


def escape(text: str, limit: int = 120) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        if is_invisible(cp) or cp > 0x7E or cp < 0x20:
            out.append("\\u%04X" % cp if cp <= 0xFFFF else "\\U%08X" % cp)
        else:
            out.append(ch)
    s = "".join(out)
    return s if len(s) <= limit else s[:limit] + "..."


def collect_files(root: str, paths: list[str]) -> list[str]:
    found: list[str] = []
    for rel in paths:
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            found.append(rel)
        elif os.path.isdir(full):
            for dp, dns, fns in os.walk(full):
                dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
                for fn in sorted(fns):
                    r = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
                    if not r.startswith(SKIP_PREFIXES):
                        found.append(r)
    return sorted(set(found))


def b64_exempt(line: str, m: re.Match) -> bool:
    run = m.group(0)
    if HEX_DIGEST.match(run):
        return True
    # inside a URL: the whitespace-delimited token that holds the run contains a scheme
    start = line.rfind(" ", 0, m.start()) + 1
    token = line[start:m.end()]
    if "://" in token:
        return True
    if SRI_BEFORE.search(line[max(0, m.start() - 20):m.start()]):
        return True
    # a path or identifier is not a blob: need upper, lower and digit all present
    core = run.rstrip("=")
    if not (re.search(r"[A-Z]", core) and re.search(r"[a-z]", core) and re.search(r"\d", core)):
        return True
    if core.count("/") > len(core) // 4 and "+" not in core:
        return True
    return False


def logical_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Join backslash-continued lines so a `curl ... \\\n | sh` split across lines still matches."""
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        start, text = i + 1, lines[i]
        while text.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            text = text.rstrip()[:-1] + " " + lines[i].strip()
        out.append((start, text))
        i += 1
    return out


def scan_text(rel: str, text: str) -> list[str]:
    findings: list[str] = []
    lines = text.split("\n")
    if "\x00" in text:
        n = text[: text.index("\x00")].count("\n") + 1
        findings.append("%s:%d: NUL byte in a text file (a NUL can hide content from tools that stop at it)" % (rel, n))
    for i, line in enumerate(lines, 1):
        bad = hidden_chars(line)
        if bad:
            cps = ",".join("U+%04X" % ord(c) for c in dict.fromkeys(bad))
            payload = tag_payload(line)
            extra = " hidden tag text %r" % payload if payload else ""
            findings.append("%s:%d: invisible or bidi character(s) %s:%s %s" % (rel, i, cps, extra, escape(line)))
            if payload:
                for rx, why in SHELL_PATTERNS:
                    if rx.search(payload):
                        findings.append("%s:%d: hidden tag text %s: %s" % (rel, i, why, escape(payload)))
                hit = IMPERATIVE.search(payload)
                if hit:
                    findings.append("%s:%d: hidden tag text addresses an assistant (%r)" % (rel, i, hit.group(0).lower()))
        for m in B64_RUN.finditer(line):
            if not b64_exempt(line, m):
                findings.append("%s:%d: base64-looking run of %d chars: %s" % (rel, i, len(m.group(0)), escape(m.group(0))))
    # a blob wrapped at 76 columns (the `base64` default) is many short runs: flag 2+ consecutive lines
    run = 0
    for i, line in enumerate(lines, 1):
        if B64_LINE.match(line) and not HEX_DIGEST.match(line.strip()):
            run += 1
            if run == 2:
                findings.append("%s:%d: wrapped base64-looking block (consecutive lines of 40+ base64 characters): %s" % (rel, i - 1, escape(lines[i - 2].strip())))
        else:
            run = 0
    for start, line in logical_lines(lines):
        for rx, why in SHELL_PATTERNS:
            m = rx.search(line)
            if m:
                findings.append("%s:%d: %s: %s" % (rel, start, why, escape(m.group(0))))
    for m in HTML_COMMENT.finditer(text):
        body = m.group(1)
        hit = IMPERATIVE.search(body)
        if hit:
            line_no = text.count("\n", 0, m.start(1) + hit.start()) + 1
            findings.append("%s:%d: HTML comment addresses an assistant (%r): %s" % (rel, line_no, hit.group(0).lower(), escape(body.strip())))
    for m in LINK_COMMENT.finditer(text):
        body = next((g for g in m.groups() if g), "")
        hit = IMPERATIVE.search(body)
        if hit:
            line_no = text.count("\n", 0, m.start()) + 1
            findings.append("%s:%d: markdown comment addresses an assistant (%r): %s" % (rel, line_no, hit.group(0).lower(), escape(body.strip())))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fail on invisible characters, base64 blobs, curl|sh and assistant-directed HTML comments in agent config.",
        epilog="Exit: 0 clean, 1 findings, 2 misuse. Default paths: " + ", ".join(DEFAULT_PATHS),
    )
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--paths", nargs="+", metavar="PATH", help="files or directories relative to --root (replaces the default list)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("check_agent_config: --root %s is not a directory" % root, file=sys.stderr)
        return 2
    files = collect_files(root, args.paths or DEFAULT_PATHS)
    findings: list[str] = []
    scanned = 0
    for rel in files:
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            print("check_agent_config: cannot read %s: %s" % (rel, exc), file=sys.stderr)
            return 2
        scanned += 1
        if os.path.splitext(rel)[1].lower() in BINARY_EXTS:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            findings.append("%s:1: not valid UTF-8 (byte %d); agent config must be plain UTF-8 text" % (rel, exc.start))
            text = raw.decode("utf-8", errors="replace")
        findings += scan_text(rel, text)
    for f in findings:
        print(f)
    if findings:
        print("check_agent_config: %d finding(s) in %d file(s) scanned" % (len(findings), scanned))
        return 1
    print("check_agent_config: ok (%d file(s) scanned)" % scanned)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("check_agent_config: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
