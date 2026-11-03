"""Dependency extractor: packages declared in ``requirements*.txt`` (any directory) and in
``pyproject.toml`` (``[project].dependencies`` and optional-dependencies, via ``tomllib``).

Emitted symbols:
  package  qualname = normalised distribution name (PEP 503: lowercase, runs of -_. -> "-"),
           path = the manifest file; attrs {spec: "fastapi==0.115.0", manifest_kind:
           "requirements"|"pyproject", extras: [...], group: "main"|"<optional group>"}
Emitted edges: none from ``extract``.
``link`` adds ``depends_on_package`` (module -> package) when a module's external import's top
-level name equals the normalised package name (or a small built-in alias table, e.g.
``yaml -> pyyaml``, ``sqlalchemy -> sqlalchemy``); other matches get attrs.confidence "low".
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from sdlc_graph.codegraph.extract_python import SKIP_DIRS
from sdlc_graph.shapes import Edge, ExtractResult, Sym

#: import top-level name -> distribution name, for the common cases where they differ.
ALIASES: dict[str, str] = {
    "yaml": "pyyaml", "pil": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "jwt": "pyjwt", "dateutil": "python-dateutil", "psycopg2": "psycopg2-binary",
    "attr": "attrs", "jose": "python-jose", "multipart": "python-multipart", "git": "gitpython",
    "serial": "pyserial", "openssl": "pyopenssl", "crypto": "pycryptodome", "pytest_cov": "pytest-cov",
    "magic": "python-magic", "ldap": "python-ldap", "slugify": "python-slugify", "google": "protobuf",
    "mysqldb": "mysqlclient", "markdown": "markdown", "docx": "python-docx", "pptx": "python-pptx",
    "fitz": "pymupdf", "skimage": "scikit-image", "zmq": "pyzmq", "socks": "pysocks",
}

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9._-]+)")


def normalise(name: str) -> str:
    """PEP 503 normalisation: ``"SQLAlchemy" -> "sqlalchemy"``, ``"python_dotenv" -> "python-dotenv"``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _walk(repo_root: Path):
    for p in sorted(repo_root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(repo_root).parts
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        yield p, p.relative_to(repo_root).as_posix()


def _is_requirements(p: Path) -> bool:
    name = p.name.lower()
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    return p.suffix == ".txt" and p.parent.name.lower() in {"requirements", "requirements.d"}


def _requirements_group(p: Path) -> str:
    stem = p.stem.lower()
    if p.parent.name.lower() in {"requirements", "requirements.d"} and not stem.startswith("requirements"):
        return "main" if stem in {"base", "main", "prod", "production", "common"} else stem
    rest = re.sub(r"^requirements[-_.]?", "", stem)
    return rest or "main"


def _parse_requirement(line: str) -> tuple[str, list[str], bool] | None:
    """(name, extras, low_confidence) from one requirement spec, or None."""
    low = False
    if line.startswith(("-e ", "--editable")):
        egg = _EGG_RE.search(line)
        return (normalise(egg.group(1)), [], True) if egg else None
    if "://" in line.split(";")[0] and "@" not in line.split("://")[0]:
        egg = _EGG_RE.search(line)
        return (normalise(egg.group(1)), [], True) if egg else None
    m = _NAME_RE.match(line)
    if not m:
        return None
    extras = [e.strip() for e in (m.group(2) or "")[1:-1].split(",") if e.strip()]
    return normalise(m.group(1)), extras, low


def _package(name: str, rel: str, spec: str, kind: str, extras: list[str], group: str, low: bool = False) -> Sym:
    attrs = {"spec": spec, "manifest_kind": kind, "extras": extras, "group": group}
    if low:
        attrs["confidence"] = "low"
    return {"kind": "package", "name": name, "qualname": name, "path": rel, "line_start": None, "line_end": None,
            "attrs": attrs}


def _from_requirements(p: Path, rel: str) -> list[Sym]:
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    group = _requirements_group(p)
    out: list[Sym] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.startswith("-") and not line.startswith(("-e ", "-e=", "--editable")):
            continue  # -r/-c/-i/-f/--hash and other pip options
        parsed = _parse_requirement(line)
        if parsed is None:
            continue
        name, extras, low = parsed
        sym = _package(name, rel, line, "requirements", extras, group, low)
        sym["line_start"] = sym["line_end"] = i
        out.append(sym)
    return out


def _from_pyproject(p: Path, rel: str) -> list[Sym]:
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    out: list[Sym] = []

    def add(specs, group):
        for spec in specs or []:
            if not isinstance(spec, str):
                continue
            parsed = _parse_requirement(spec.strip())
            if parsed:
                out.append(_package(parsed[0], rel, spec.strip(), "pyproject", parsed[1], group, parsed[2]))

    project = data.get("project") or {}
    add(project.get("dependencies"), "main")
    for group, specs in (project.get("optional-dependencies") or {}).items():
        add(specs, str(group))
    for group, specs in (data.get("dependency-groups") or {}).items():
        add([s for s in specs if isinstance(s, str)], str(group))
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for group, table in [("main", poetry.get("dependencies"))] + [
            (g, (v or {}).get("dependencies")) for g, v in (poetry.get("group") or {}).items()]:
        for name, spec in (table or {}).items():
            if name.lower() == "python":
                continue
            out.append(_package(normalise(name), rel, f"{name} {spec}" if isinstance(spec, str) else name,
                                "pyproject", [], group))
    return out


def _from_package_json(p: Path, rel: str) -> list[Sym]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[Sym] = []
    for field, group in (("dependencies", "main"), ("devDependencies", "dev"), ("peerDependencies", "peer"),
                         ("optionalDependencies", "optional")):
        table = data.get(field)
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            out.append(_package(str(name).lower(), rel, f"{name}@{spec}", "package.json", [], group))
    return out


def extract(repo_root: Path) -> ExtractResult:
    """Extract package symbols from every manifest under ``repo_root`` (SKIP_DIRS excluded).

    Errors:
        Unparseable lines (``-r other.txt``, ``-e .``, URLs) are kept as package symbols only when
        a name can be read, with attrs.confidence "low"; otherwise skipped. Never raises for
        content problems.
    """
    root = Path(repo_root)
    seen: dict[tuple[str, str], Sym] = {}
    for p, rel in _walk(root):
        syms: list[Sym] = []
        if _is_requirements(p):
            syms = _from_requirements(p, rel)
        elif p.name == "pyproject.toml":
            syms = _from_pyproject(p, rel)
        elif p.name == "package.json":
            syms = _from_package_json(p, rel)
        for s in syms:
            seen.setdefault((s["qualname"], s["path"]), s)
    symbols = sorted(seen.values(), key=lambda s: (s["path"], s["line_start"] or 0, s["qualname"]))
    return {"symbols": symbols, "edges": []}


def link(merged: ExtractResult) -> list[Edge]:
    """``depends_on_package`` edges from modules (via ``attrs.external_imports``) to packages."""
    py_pkgs: dict[str, list[Sym]] = {}
    js_pkgs: dict[str, list[Sym]] = {}
    for s in merged["symbols"]:
        if s["kind"] != "package":
            continue
        (js_pkgs if s["attrs"].get("manifest_kind") == "package.json" else py_pkgs).setdefault(s["qualname"], []).append(s)
    edges: dict[tuple, Edge] = {}
    for s in merged["symbols"]:
        if s["kind"] != "module":
            continue
        frontend = s["attrs"].get("lang") is not None
        table = js_pkgs if frontend else py_pkgs
        for top in s["attrs"].get("external_imports") or []:
            matches: list[tuple[Sym, bool]] = []
            if frontend:
                matches = [(p, False) for p in table.get(top.lower(), [])]
            else:
                norm = normalise(top)
                exact = norm if norm in table else ALIASES.get(top.lower()) if ALIASES.get(top.lower()) in table else None
                if exact:
                    matches = [(p, False) for p in table[exact]]
                else:
                    for name, pkgs in table.items():
                        if name.startswith(norm + "-") or norm.startswith(name + "-"):
                            matches.extend((p, True) for p in pkgs)
            for pkg, low in matches:
                key = (s["qualname"], s["path"], pkg["qualname"], pkg["path"])
                attrs = {"confidence": "low"} if low else {}
                edges.setdefault(key, {
                    "src": {"kind": "module", "qualname": s["qualname"], "path": s["path"]},
                    "dst": {"kind": "package", "qualname": pkg["qualname"], "path": pkg["path"]},
                    "rel": "depends_on_package", "attrs": attrs})
    return sorted(edges.values(), key=lambda e: (e["src"]["path"], e["src"]["qualname"], e["dst"]["path"], e["dst"]["qualname"]))
