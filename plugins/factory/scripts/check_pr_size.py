#!/usr/bin/env python3
"""Warn or fail on oversized changes (REC-17: agent PRs tend to be large and go unreviewed).

Runs `git diff --numstat -M <base>...<head>`, drops files matching the exclude globs (lock files,
minified assets), sums added + deleted lines and compares with the thresholds: --warn/--fail, else
`pr_size` in .claude/sdlc.json, else 400 / 1000. Prints the totals and the 10 largest files.
Past warn: prints WARN, exit 0. Past fail: exit 1, unless --label-present (the `large-change`
label is on the PR), which prints the override and exits 0.

Exit codes: 0 ok / warn / overridden, 1 past fail, 2 misuse or git failure.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys

DEFAULTS = {"warn": 400, "fail": 1000, "exclude": ["*.lock", "package-lock.json", "poetry.lock", "*.min.js"]}


def load_pr_size(root: str) -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(root, ".claude", "sdlc.json"), encoding="utf-8") as fh:
            data = json.load(fh).get("pr_size", {})
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in cfg})
    except (OSError, ValueError, AttributeError):
        pass
    return cfg


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def parse_numstat(out: str) -> list[tuple[str, int, int]]:
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        rows.append((path, int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0))  # "-" = binary
    return rows


def excluded(path: str, globs: list[str]) -> bool:
    if "=>" in path:  # rename: judge by the new name
        path = path.split("=>")[-1].strip(" }")
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(os.path.basename(path), g) for g in globs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Count lines changed against --base and warn or fail past thresholds.",
        epilog="Exit: 0 ok/warn/overridden, 1 past fail, 2 misuse.",
    )
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--base", required=True, help="base ref, e.g. origin/main")
    ap.add_argument("--head", default="HEAD", help="head ref (default HEAD)")
    ap.add_argument("--warn", type=int, help="warn threshold (lines changed)")
    ap.add_argument("--fail", type=int, help="fail threshold (lines changed)")
    ap.add_argument("--label-present", action="store_true", help="the large-change label is on the PR: do not fail")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    root = os.path.abspath(args.root)
    cfg = load_pr_size(root)
    warn = args.warn if args.warn is not None else int(cfg["warn"])
    fail = args.fail if args.fail is not None else int(cfg["fail"])
    if warn > fail:
        print("check_pr_size: warn (%d) is above fail (%d)" % (warn, fail), file=sys.stderr)
        return 2
    res = git(root, "diff", "--numstat", "-M", "%s...%s" % (args.base, args.head))
    if res.returncode != 0:  # no merge base (shallow clone, unrelated history): fall back to a plain diff
        res = git(root, "diff", "--numstat", "-M", args.base, args.head)
    if res.returncode != 0:
        print("check_pr_size: git diff failed: %s" % res.stderr.strip(), file=sys.stderr)
        return 2
    rows = parse_numstat(res.stdout)
    kept = [r for r in rows if not excluded(r[0], cfg["exclude"])]
    skipped = len(rows) - len(kept)
    total = sum(a + d for _, a, d in kept)
    added = sum(a for _, a, _ in kept)
    deleted = sum(d for _, _, d in kept)
    print("check_pr_size: %d line(s) changed (+%d -%d) in %d file(s); %d excluded; warn %d, fail %d"
          % (total, added, deleted, len(kept), skipped, warn, fail))
    for path, a, d in sorted(kept, key=lambda r: -(r[1] + r[2]))[:10]:
        print("  %6d  +%-5d -%-5d  %s" % (a + d, a, d, path))
    if total > fail:
        if args.label_present:
            print("OVERRIDDEN: %d lines is past the fail threshold %d but the large-change label is present" % (total, fail))
            return 0
        print("FAIL: %d lines is past %d. Split the change, or have a maintainer add the large-change label." % (total, fail))
        return 1
    if total > warn:
        print("WARN: %d lines is past %d. Consider splitting the change; reviewers may reject it as is." % (total, warn))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("check_pr_size: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
