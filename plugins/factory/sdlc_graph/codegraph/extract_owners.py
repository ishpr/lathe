"""CODEOWNERS parser and matcher. Looks in ``.github/CODEOWNERS``, ``CODEOWNERS``,
``docs/CODEOWNERS`` (GitHub's order; first file found wins).

Owners are not symbols: ``extract`` returns an empty ExtractResult and ``parse`` returns the
rules, which ``snapshot.index`` stores in the ``owner`` table. Matching follows GitHub
semantics: gitignore-style globs, a leading ``/`` anchors at the repo root, a trailing ``/``
matches everything below, and the LAST matching rule wins (a rule with no handles un-owns).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from sdlc_graph.shapes import Edge, ExtractResult, OwnerRule

CODEOWNERS_LOCATIONS: tuple[str, ...] = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def parse(repo_root: Path) -> list[OwnerRule]:
    """Read the CODEOWNERS file under ``repo_root``.

    Returns:
        Rules in file order (``ord`` 0..n-1), comments and blank lines skipped. ``[]`` when no
        CODEOWNERS file exists.
    """
    root = Path(repo_root)
    for loc in CODEOWNERS_LOCATIONS:
        p = root / loc
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return []
            break
    else:
        return []
    rules: list[OwnerRule] = []
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line or line.startswith("[") or line.startswith("^["):
            continue  # blank, or a GitLab section header
        parts = line.split()
        rules.append({"path_glob": parts[0], "handles": parts[1:], "ord": len(rules)})
    return rules


def _strip_comment(line: str) -> str:
    out = []
    escaped = False
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


@lru_cache(maxsize=1024)
def _compile(glob: str) -> re.Pattern[str]:
    pat = glob
    dir_only = pat.endswith("/")
    anchored = pat.startswith("/") or "/" in pat.rstrip("/")
    pat = pat.strip("/")
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out.append("/.*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    tail = "/.+" if dir_only else "(?:/.+)?"
    return re.compile(f"^{prefix}{body}{tail}$")


def match(path: str, rules: list[OwnerRule]) -> list[str]:
    """Handles owning repo-relative ``path`` under ``rules`` (last matching rule wins).

    Returns:
        The winning rule's handles (may be ``[]``), or ``[]`` when nothing matches.
    """
    p = path.replace("\\", "/")
    while p.startswith("./") or p.startswith("/"):
        p = p[2:] if p.startswith("./") else p[1:]
    for rule in sorted(rules, key=lambda r: r["ord"], reverse=True):
        if _compile(rule["path_glob"]).match(p):
            return list(rule["handles"])
    return []


def extract(repo_root: Path) -> ExtractResult:
    """Protocol stub: owners produce no symbols. Returns ``{"symbols": [], "edges": []}``."""
    return {"symbols": [], "edges": []}


def link(merged: ExtractResult) -> list[Edge]:
    """Protocol stub: returns ``[]``."""
    return []
