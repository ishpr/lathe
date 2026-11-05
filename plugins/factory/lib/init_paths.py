"""Path facts shared by init and the commit guard: which directories hold a project's source, which of
the glob defaults match a file, and which files init itself generates (an onboarding-only commit).
Standard library plus lib/sdlc.py's glob matching."""

from __future__ import annotations

import glob
import os

import sdlc as cl

EXCLUDED_DIRS = frozenset(
    {
        "tests",
        "test",
        "docs",
        "fixtures",
        "examples",
        "templates",
        "build",
        "dist",
        "node_modules",
    }
)

INIT_GENERATED: tuple[str, ...] = (
    ".claude/sdlc.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/rules/sdlc-*.md",
    ".claude/public_routes.txt",
    ".claude/work/README.md",
    ".claude/KIT_VERSION",
    ".claude/baseline.json",
    "CLAUDE.md",
    ".github/pull_request_template.md",
    ".github/**",
    ".mcp.json",
    ".gitignore",
    "tests/QUARANTINE.md",
    "docs/onboarding-report.md",
    "docs/maturity-assessment.md",
    "docs/security-assessment.md",
)


def _holds_python(top: str) -> bool:
    for _dp, dns, fns in os.walk(top):
        dns[:] = [d for d in dns if not d.startswith(".") and d != "__pycache__"]
        if any(f.endswith(".py") for f in fns):
            return True
    return False


def source_dirs(root: str) -> list[str]:
    """Sorted top-level directories holding at least one .py file below them, minus EXCLUDED_DIRS, dot-dirs and __pycache__."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for name in names:
        if name in EXCLUDED_DIRS or name.startswith(".") or name == "__pycache__":
            continue
        top = os.path.join(root, name)
        if os.path.isdir(top) and not os.path.islink(top) and _holds_python(top):
            out.append(name)
    return sorted(out)


def existing_tier_globs(root: str, globs: list[str]) -> tuple[list[str], list[str]]:
    """(kept, dropped): a glob is kept when it matches at least one file under root. Order is preserved."""
    kept: list[str] = []
    dropped: list[str] = []
    for g in globs:
        pattern = g[:-3] + "/**/*" if g.endswith("/**") else g
        hit = any(
            os.path.isfile(p)
            for p in glob.glob(os.path.join(root, pattern), recursive=True)
        )
        (kept if hit else dropped).append(g)
    return kept, dropped


def onboarding_only(paths: list[str], cfg: dict) -> bool:
    """True when paths is non-empty, every path is a file init generates, and none is under paths.app or paths.tests."""
    if not paths:
        return False
    configured = cfg.get("paths") or {}
    owned = list(configured.get("app") or []) + list(configured.get("tests") or [])
    return all(
        cl.matches(p, list(INIT_GENERATED)) and not cl.matches(p, owned) for p in paths
    )
