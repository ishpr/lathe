"""Which JavaScript and TypeScript files the frontend and TypeScript extractors scan, and how they resolve imports.

The configured application paths come from the repository's ``.claude/sdlc.json``::

    {"paths": {"app": ["src/**"]},
     "frontend": {"framework": "foldkit", "programs": ["src/programs/**"], "api_table": "src/lib/api/endpoints.ts"}}

With no file (or no ``paths.app``) the application paths default to ``src/**``, ``app/**`` and ``lib/**``. Globs use
the dialect of the orchestrator's drift gate: ``**/`` any directories, ``**`` anything, ``*`` within one segment, ``?``
one character, a trailing ``/`` or a bare directory name matches everything below it. Pure and stdlib only.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_APP_GLOBS: tuple[str, ...] = ("src/**", "app/**", "lib/**")
DEFAULT_API_TABLE = "src/lib/api/endpoints.ts"
#: Directories never scanned (dependency trees, build output, caches).
SKIP_DIRS: frozenset[str] = frozenset({".git", ".venv", "venv", "node_modules", ".next", "dist", "build", "coverage", ".turbo",
                                       ".cache", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
MAX_BYTES = 1_000_000
TEST_FILE_RE = re.compile(r"\.(test|spec)\.[jt]sx?$|(^|/)__tests__/|(^|/)__mocks__/")
RESOLVE_EXTS = (".ts", ".tsx")


def glob_regex(glob: str) -> re.Pattern[str]:
    g = glob.strip().lstrip("./") if glob.startswith("./") else glob.strip()
    out, i = [], 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    body = "".join(out)
    return re.compile(body + r"(?:/.*)?$" if not g.endswith("/") else body + ".*$")


@dataclass
class AppConfig:
    app: list[str] = field(default_factory=lambda: list(DEFAULT_APP_GLOBS))
    programs: list[str] = field(default_factory=list)
    api_table: str | None = None
    framework: str | None = None
    #: ``(specifier prefix, directory prefix)`` pairs from tsconfig ``paths`` (``@/`` -> ``src/``)
    aliases: list[tuple[str, str]] = field(default_factory=lambda: [("@/", "src/"), ("~/", "src/")])

    def __post_init__(self) -> None:
        self._app = [glob_regex(g) for g in self.app]
        self._programs = [glob_regex(g) for g in self.programs]

    def in_app(self, rel: str) -> bool:
        return any(r.match(rel) for r in self._app)

    def program_of(self, rel: str) -> str | None:
        """The program a file belongs to: the first directory below the literal prefix of the matching ``programs`` glob
        (``src/programs/runs/update.ts`` with ``src/programs/**`` -> ``runs``); None outside the programs globs or
        for a file directly in the programs directory."""
        for glob, rx in zip(self.programs, self._programs):
            if not rx.match(rel):
                continue
            prefix = re.split(r"[*?\[]", glob, maxsplit=1)[0]
            prefix = prefix[: prefix.rfind("/") + 1]
            rest = rel[len(prefix):] if rel.startswith(prefix) else ""
            return rest.split("/", 1)[0] if "/" in rest else None
        return None


def _json_file(path: Path) -> Any:
    from sdlc_graph.codegraph.extract_frontend import _strip_comments

    try:
        text = _strip_comments(path.read_text(encoding="utf-8"))
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [v for v in value or [] if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def load_config(root: Path) -> AppConfig:
    """The scan configuration of the tree at ``root`` (defaults for anything the files do not say)."""
    root = Path(root)
    cfg = AppConfig()
    data = _json_file(root / ".claude" / "sdlc.json")
    if isinstance(data, dict):
        paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
        app = _strings(paths.get("app"))
        if app:
            cfg.app = app
        fe = data.get("frontend") if isinstance(data.get("frontend"), dict) else {}
        cfg.programs = _strings(fe.get("programs"))
        api = fe.get("api_table")
        cfg.api_table = api.strip().lstrip("./") if isinstance(api, str) and api.strip() else None
        fw = fe.get("framework")
        cfg.framework = fw if isinstance(fw, str) else None
    if cfg.api_table is None and (root / DEFAULT_API_TABLE).is_file():
        cfg.api_table = DEFAULT_API_TABLE
    ts = _json_file(root / "tsconfig.json")
    opts = ts.get("compilerOptions") if isinstance(ts, dict) and isinstance(ts.get("compilerOptions"), dict) else {}
    mapped = opts.get("paths") if isinstance(opts.get("paths"), dict) else {}
    base = posixpath.normpath(str(opts.get("baseUrl") or ".")).lstrip("./")
    aliases: list[tuple[str, str]] = []
    for pattern, targets in mapped.items():
        tgt = _strings(targets)
        if not pattern.endswith("*") or not tgt or not tgt[0].endswith("*"):
            continue
        directory = posixpath.normpath(posixpath.join(base or ".", tgt[0][:-1] or ".")).lstrip("./")
        aliases.append((pattern[:-1], directory + "/" if directory and directory != "." else ""))
    if aliases:
        cfg.aliases = aliases
    cfg.__post_init__()
    return cfg


def list_files(root: Path, exts: tuple[str, ...], cfg: AppConfig, *, extra: tuple[str, ...] = ()) -> list[str]:
    """Repo-relative POSIX paths of the files under ``root`` with one of ``exts`` that match the application
    paths (plus ``extra`` paths that exist), skipping :data:`SKIP_DIRS`, symlinks, declaration files and files
    over :data:`MAX_BYTES`. Sorted."""
    root = Path(root)
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        base = Path(dirpath).relative_to(root).as_posix()
        for name in filenames:
            if not name.endswith(exts) or name.endswith(".d.ts"):
                continue
            rel = name if base == "." else f"{base}/{name}"
            if cfg.in_app(rel) and not (Path(dirpath) / name).is_symlink():
                found.add(rel)
    for rel in extra:
        p = root / rel
        if rel.endswith(exts) and p.is_file() and not p.is_symlink():
            found.add(rel)
    return sorted(f for f in found if (root / f).stat().st_size <= MAX_BYTES)


def resolve_import(spec: str, from_rel: str, cfg: AppConfig, files: set[str]) -> str | None:
    """The scanned file a module specifier from ``from_rel`` names, or None (packages, files outside the scan)."""
    if spec.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), spec))
    else:
        base = None
        for prefix, directory in cfg.aliases:
            if spec.startswith(prefix):
                base = posixpath.normpath(directory + spec[len(prefix):])
                break
        if base is None:
            return None
    if base.startswith(".."):
        return None
    stem = re.sub(r"\.(?:m|c)?jsx?$", "", base)
    candidates = [base, *(stem + e for e in RESOLVE_EXTS), *(f"{base}/index{e}" for e in RESOLVE_EXTS)]
    return next((c for c in candidates if c in files), None)
