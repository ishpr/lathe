#!/usr/bin/env python3
"""CI: run check_commit_message.py over every non-merge commit in a range.

A bad ref or a failing `git log` is an error (exit 2), never "0 commits checked": a typo in the base
ref must not make the gate pass.

Usage: python3 -I scripts/check_commit_range.py [--root DIR] [RANGE]      (RANGE default origin/main..HEAD)
Exit: 0 all messages pass, 1 a commit's message fails its checks, 2 misuse or a bad range.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check_commit_range.py", description="Run check_commit_message.py over every non-merge commit in a range.",
                                 epilog="Exit: 0 ok, 1 a message fails, 2 misuse or bad range.")
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("range", nargs="?", default="origin/main..HEAD", help="git revision range (default origin/main..HEAD)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    rng = args.range
    if rng.startswith("-"):
        print("check_commit_range: %r is not a revision range" % rng, file=sys.stderr)
        return 2
    root = os.path.abspath(args.root)
    here = os.path.dirname(os.path.abspath(__file__))

    def git(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)

    log = git("log", "--no-merges", "--format=%H", rng, "--")
    if log.returncode != 0:
        print("check_commit_range: git log %s failed: %s" % (rng, log.stderr.strip()), file=sys.stderr)
        return 2
    shas = log.stdout.split()
    bad = 0
    for sha in shas:
        msg = git("log", "-1", "--format=%B", sha).stdout
        files = git("show", "--name-only", "--format=", sha).stdout.split()
        proc = subprocess.run([sys.executable, "-I", os.path.join(here, "check_commit_message.py"), "--files", *files],
                              input=msg, capture_output=True, text=True)
        if proc.returncode:
            bad += 1
            subject = msg.strip().splitlines()[0] if msg.strip() else "(empty)"
            print(f"{sha[:7]} {subject}\n{proc.stdout}")
    print(f"{len(shas)} commits checked, {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
