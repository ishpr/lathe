#!/usr/bin/env python3
"""Fingerprint of the working tree's changes against HEAD.

Covers every path that differs from HEAD in the index or the working tree, plus untracked
(non-ignored) files; ignores .claude/work/ (contracts and evidence are notes, not code). Each path is
hashed by its WORKING-TREE bytes (and executable bit), or a deleted marker, so the value does not
change when files are staged: verify, then `git add -A`, then commit sees the same fingerprint.
`scripts/verify.sh` (plugin: verify.py) writes it into .claude/work/last_verify.json when the gate
is green; the Bash guard refuses `git commit` unless the current fingerprint still matches. Change a
byte after verifying and you verify again.

Usage: python3 -I scripts/worktree_fingerprint.py [ROOT]
Exit: 0 printed a fingerprint, 2 ROOT is not a git work tree or git failed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

SKIP_PREFIX = b".claude/work/"


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv and argv[0].startswith("-"):
        print("worktree_fingerprint: unknown option %s" % argv[0], file=sys.stderr)
        return 2
    root = os.path.abspath(argv[0] if argv else os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", root, *args], capture_output=True)

    probe = git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != b"true":
        print("worktree_fingerprint: %s is not a git work tree" % root, file=sys.stderr)
        return 2
    changed = git("diff", "HEAD", "--name-only", "--no-renames", "-z")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode != 0 or (changed.returncode != 0 and git("rev-parse", "--verify", "-q", "HEAD").returncode == 0):
        print("worktree_fingerprint: git failed: %s" % (changed.stderr + untracked.stderr).decode(errors="replace").strip(), file=sys.stderr)
        return 2
    paths = sorted({p for p in changed.stdout.split(b"\0") + untracked.stdout.split(b"\0") if p and not p.startswith(SKIP_PREFIX)})
    digest = hashlib.sha256()
    for rel in paths:
        digest.update(b"\0" + rel + b"\0")
        full = os.path.join(root, rel.decode(errors="surrogateescape"))
        try:
            if os.path.islink(full):
                digest.update(b"L" + os.readlink(full).encode(errors="surrogateescape"))
                continue
            with open(full, "rb") as fh:
                data = fh.read()
            digest.update((b"x" if os.stat(full).st_mode & 0o111 else b"-") + data)
        except OSError:
            digest.update(b"DELETED")
    print(digest.hexdigest()[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
