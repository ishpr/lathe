#!/usr/bin/env python3
"""Fail when CLAUDE.md or .claude/rules/*.md name a path that does not exist (REC-34).

Prose rules rot: CLAUDE.md told agents to delete code in a file the edit guard protects, and
named files that were never created. Every inline-backticked token that looks like a path
(contains "/" or ends in a known file extension; no spaces, no glob characters) must exist
relative to --root. Skipped: URLs, <angle-bracket> placeholders, absolute or ~ paths, MIME types,
and the runtime/secret files in --ignore (default: .env*, .claude/work/*, .claude/settings.local.json).
A token ending in /** or /* is treated as a directory prefix. A trailing :LINE is stripped.
Fenced code blocks are not scanned. A slash-less file name is also accepted when it names one of the
pack's own scripts (they live in the plugin, not in the project) or one of the per-ticket runtime
files the pack's CLAUDE.md block mentions (contract.md, current.json, last_verify.json, ...).

Exit codes: 0 all paths exist, 1 missing paths, 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import os
import re
import sys

EXTS = {
    "py", "js", "ts", "tsx", "jsx", "mjs", "cjs", "json", "jsonl", "md", "toml", "yaml", "yml", "sh",
    "txt", "cfg", "ini", "sql", "lock", "go", "mod", "sum", "rs", "html", "css", "env", "example",
    "ipynb", "xml", "csv", "dockerfile",
}
DEFAULT_IGNORE = [".env*", "*/.env*", ".claude/work/*", ".claude/settings.local.json"]
# runtime files the pack's working agreement names by bare name; they exist per ticket, not in the repo
RUNTIME_BARE = {"contract.md", "current.json", "last_verify.json", "blocks.jsonl", "evidence.md", "baseline.json"}


def pack_script_names() -> set[str]:
    """Bare names of the scripts next to this one (and under checkers/): the plugin's scripts are not in the project."""
    here = os.path.dirname(os.path.abspath(__file__))
    names: set[str] = set()
    for dp, _dns, fns in os.walk(here):
        names.update(fn for fn in fns if fn.endswith((".py", ".sh")))
    return names
MIME_PREFIX = ("application/", "text/", "image/", "multipart/", "audio/", "video/")
TICK = re.compile(r"`([^`\n]+)`")
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


def strip_fences(text: str) -> list[str]:
    out, fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            out.append("")
        else:
            out.append("" if fence else line)
    return out


def candidate(tok: str) -> str | None:
    """Return the path to check, or None when the token is not a path."""
    if not tok or re.search(r"\s", tok):
        return None
    if "://" in tok or "<" in tok or ">" in tok or tok.startswith(("/", "~", "$", "-", "@", "#")):
        return None
    if tok.startswith(MIME_PREFIX):
        return None
    tok = LINE_SUFFIX.sub("", tok)
    if tok.endswith(("/**", "/*")):
        tok = tok.rsplit("/", 1)[0]
        return tok or None
    if re.search(r"[*?\[\]{}()=,;|&$]", tok):
        return None
    if "/" in tok:
        return tok
    ext = tok.rsplit(".", 1)[-1].lower() if "." in tok.strip(".") else ""
    if ext in EXTS and re.fullmatch(r"[\w.-]+", tok):
        return tok
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Every backticked path in CLAUDE.md and .claude/rules/*.md must exist.",
        epilog="Exit: 0 ok, 1 missing paths, 2 misuse.",
    )
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--files", nargs="+", metavar="FILE", help="markdown files relative to --root (default: CLAUDE.md and .claude/rules/*.md)")
    ap.add_argument("--ignore", action="append", metavar="GLOB", help="extra path globs to skip (repeatable)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("check_claude_md_paths: --root %s is not a directory" % root, file=sys.stderr)
        return 2
    ignore = DEFAULT_IGNORE + (args.ignore or [])
    bare_ok = RUNTIME_BARE | pack_script_names()
    if args.files:
        files = args.files
    else:
        files = [p for p in ["CLAUDE.md"] if os.path.isfile(os.path.join(root, p))]
        files += sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in glob.glob(os.path.join(root, ".claude", "rules", "*.md")))
    if not files:
        print("check_claude_md_paths: no CLAUDE.md or .claude/rules/*.md under %s" % root)
        return 0
    missing = 0
    checked = 0
    for rel in files:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            print("check_claude_md_paths: cannot read %s" % rel, file=sys.stderr)
            return 2
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = strip_fences(fh.read())
        for n, line in enumerate(lines, 1):
            for m in TICK.finditer(line):
                tok = m.group(1).strip()
                p = candidate(tok)
                if p is None:
                    continue
                norm = p.rstrip("/")
                if "/" not in norm and norm in bare_ok:
                    continue
                if any(fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(os.path.basename(norm), g) for g in ignore):
                    continue
                checked += 1
                if not os.path.exists(os.path.join(root, norm)):
                    missing += 1
                    print("%s:%d: `%s` does not exist (no %s in the repo)" % (rel, n, tok, norm))
    if missing:
        print("check_claude_md_paths: %d missing path(s) of %d checked" % (missing, checked))
        return 1
    print("check_claude_md_paths: ok (%d path(s) checked)" % checked)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("check_claude_md_paths: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
