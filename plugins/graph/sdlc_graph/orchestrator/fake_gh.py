"""A fake ``gh`` for tests: ``ORCHESTRATOR_GH="<python> -m sdlc_graph.orchestrator.fake_gh"``.

It knows one pull request per worktree: the fake executor's ``ship`` step creates it by writing
``<git dir>/fake-pr`` (a number). ``gh pr view [<branch|url>] --json <fields>`` prints that PR as the real gh
would (``url`` on the origin remote, ``headRefName`` the current branch, ``state``, ``reviewDecision`` from
``FAKE_GH_REVIEW``, default none); ``gh pr merge`` writes ``<git dir>/fake-pr-merged``. ``FAKE_GH_LOG`` receives
every call. ``FAKE_GH_HEAD`` overrides ``headRefName`` and ``FAKE_GH_REPO`` the owner/repo of the URL, to
fake a PR that is not the contract's.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("FAKE_GH_LOG"):
        with open(os.environ["FAKE_GH_LOG"], "a") as fh:
            fh.write(" ".join(argv) + "\n")
    if argv[:2] not in (["pr", "view"], ["pr", "merge"]):
        return 0
    gitdir = Path(_git("rev-parse", "--git-dir")).resolve() if _git("rev-parse", "--git-dir") else None
    if gitdir is None:
        print("not a git repository", file=sys.stderr)
        return 1
    pr = gitdir / "fake-pr"
    if not pr.exists():
        print("no pull requests found for branch", file=sys.stderr)
        return 1
    merged = gitdir / "fake-pr-merged"
    if argv[1] == "merge":
        merged.write_text("1")
        print("merged")
        return 0
    m = re.search(r"[:/]+([^/:]+/[^/]+?)(?:\.git)?$", _git("remote", "get-url", "origin"))
    repo = os.environ.get("FAKE_GH_REPO") or (m.group(1) if m else "example/unknown")
    out = {"url": f"https://github.com/{repo}/pull/{pr.read_text().strip() or 1}",
           "headRefName": os.environ.get("FAKE_GH_HEAD") or _git("rev-parse", "--abbrev-ref", "HEAD"), "baseRefName": "main",
           "state": "MERGED" if merged.exists() else "OPEN", "reviewDecision": os.environ.get("FAKE_GH_REVIEW", ""),
           "mergeCommit": {"oid": "a" * 40} if merged.exists() else None}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
