"""Frontend extractor: ``fetch(...)`` and ``axios.<method>(...)`` / ``axios(...)`` call sites in
``frontend/**/*.{ts,tsx,js,jsx}`` (regex based, stdlib only).

Emitted symbols:
  module         one per frontend file: qualname = path (e.g. "frontend/src/api.ts"), attrs {lang}
  frontend_call  qualname "<METHOD> <url>" e.g. "GET /api/missing", path = file;
                 attrs {method, url, raw, line, confidence}; template literals and concatenations
                 keep the static prefix in ``url`` and get confidence "low"
Emitted edges: defines (module -> frontend_call).
``link`` adds ``fetches`` (frontend_call -> route) when the call's method and normalised url
match a route's method and path (path parameters ``{id}`` match any segment). A call with no
matching route gets NO edge; ``queries`` reports those as unmatched fetches.
"""

from __future__ import annotations

import re
from pathlib import Path

from sdlc_graph.codegraph.extract_python import SKIP_DIRS
from sdlc_graph.shapes import Edge, ExtractResult, Sym

FRONTEND_GLOBS: tuple[str, ...] = ("frontend/**/*.ts", "frontend/**/*.tsx", "frontend/**/*.js", "frontend/**/*.jsx")
_EXTS = (".ts", ".tsx", ".js", ".jsx")
_TEST_FILE_RE = re.compile(r"\.(test|spec)\.[jt]sx?$|(^|/)__tests__/|(^|/)__mocks__/")
_CALL_RE = re.compile(r"(?<![\w$])(?:(?P<fetch>\$?fetch|fetcher)|axios(?:\.(?P<m>get|post|put|patch|delete|head|options|request))?)\s*\(")
_IMPORT_RE = re.compile(r"""(?:\bfrom\s+|\bimport\s+|\brequire\s*\(\s*)["']([^"'\n]+)["']""")
_METHOD_KW_RE = re.compile(r"""\bmethod\s*:\s*["'`]([A-Za-z]+)["'`]""")
_URL_KW_RE = re.compile(r"""\burl\s*:\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)""")
_IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_QUERY_RE = re.compile(r"[?#].*$")


def _strip_comments(src: str) -> str:
    """Blank out // and /* */ comments (keeping newlines so line numbers stay right); strings and
    template literals are left alone."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _split_args(src: str, open_paren: int) -> tuple[list[str], int]:
    """Top-level comma separated arguments of the call whose ``(`` is at ``open_paren``."""
    args: list[str] = []
    depth = 0
    start = open_paren + 1
    i, n = start, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                args.append(src[start:i])
                return args, i
            depth -= 1
        elif c == "," and depth == 0:
            args.append(src[start:i])
            start = i + 1
        i += 1
    args.append(src[start:])
    return args, n


def _lookup_const(src: str, name: str, depth: int = 0) -> str | None:
    """Resolve an identifier to a string value using ``const X = "..."`` and default-parameter
    ``x = DEFAULT`` forms in the same file. Returns the raw literal (quotes included) or None."""
    if depth > 5:
        return None
    pat = re.compile(r"(?<![\w$.])" + re.escape(name) + r"""\s*(?::[^=,)\n]*)?=(?![=>])\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`|[A-Za-z_$][\w$]*)""")
    for m in pat.finditer(src):
        val = m.group(1)
        if _IDENT_RE.match(val):
            if val == name:
                continue
            got = _lookup_const(src, val, depth + 1)
            if got is not None:
                return got
            continue
        return val
    return None


def _literal_text(lit: str) -> str:
    return lit[1:-1]


def _evaluate(expr: str, src: str) -> tuple[str, bool] | None:
    """(url, exact) for a first-argument expression, or None when nothing static is known.
    ``exact`` is False when a dynamic part was replaced by ``{}`` or the value came through an
    indirection (identifier lookup, concatenation)."""
    e = expr.strip()
    if not e:
        return None
    if e[0] in "\"'" and e[-1] == e[0] and len(e) >= 2 and e[0] not in e[1:-1].replace("\\" + e[0], ""):
        return _literal_text(e), True
    if e[0] == "`" and e[-1] == "`":
        body = e[1:-1]
        exact = True
        out: list[str] = []
        pos = 0
        for m in re.finditer(r"\$\{([^}]*)\}", body):
            out.append(body[pos:m.start()])
            inner = m.group(1).strip()
            val = None
            if _IDENT_RE.match(inner):
                lit = _lookup_const(src, inner)
                if lit is not None:
                    ev = _evaluate(lit, src)
                    val = ev[0] if ev else None
            if val is None:
                out.append("{}")
            else:
                out.append(val)
            exact = False
            pos = m.end()
        out.append(body[pos:])
        url = "".join(out)
        if not url.replace("{}", "").strip("/ "):
            return None
        return url, exact
    if _IDENT_RE.match(e):
        lit = _lookup_const(src, e)
        if lit is None:
            return None
        ev = _evaluate(lit, src)
        return (ev[0], False) if ev else None
    if "+" in e:  # "a" + b + "c": keep the literal prefix, mark the rest dynamic
        parts = [p.strip() for p in re.split(r"\s\+\s|\+(?=\s*[\"'`$\w])", e)]
        out = []
        for p in parts:
            ev = _evaluate(p, src)
            out.append(ev[0] if ev else "{}")
        url = "".join(out)
        if url.replace("{}", "").strip("/ "):
            return url, False
    return None


def normalise_url(url: str) -> str:
    """Normalise a fetched URL for route matching: strip scheme/host, query string and fragment,
    collapse duplicate slashes, drop a trailing slash (except ``/``). ``"/items/?q=1" -> "/items"``."""
    u = url.strip()
    u = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*", "", u)
    u = _QUERY_RE.sub("", u)
    u = re.sub(r"/{2,}", "/", u)
    if not u.startswith("/"):
        u = "/" + u
    if len(u) > 1 and u.endswith("/"):
        u = u.rstrip("/") or "/"
    return u


def _frontend_files(root: Path) -> list[Path]:
    base = root / "frontend"
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        if p.suffix not in _EXTS or not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


def _package_name(spec: str) -> str | None:
    if spec.startswith((".", "/", "@/", "~/", "#")) or ":" in spec.split("/")[0]:
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]
    return name.lower() if name else None


def extract(repo_root: Path) -> ExtractResult:
    """Extract frontend modules and call sites under ``repo_root/frontend``.

    Returns:
        ExtractResult, deterministic ordering as in extract_python. Empty when there is no
        ``frontend/`` directory.

    Errors:
        Unreadable or non-UTF-8 files are skipped with a module symbol carrying
        ``attrs.parse_error``. Never raises for content problems.
    """
    root = Path(repo_root)
    symbols: dict[tuple[str, str, str], Sym] = {}
    edges: dict[tuple, Edge] = {}
    for p in _frontend_files(root):
        rel = p.relative_to(root).as_posix()
        lang = p.suffix.lstrip(".")
        mod_key = ("module", rel, rel)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            symbols[mod_key] = {"kind": "module", "name": p.name, "qualname": rel, "path": rel, "line_start": 1,
                                "line_end": 1, "attrs": {"lang": lang, "parse_error": f"{type(exc).__name__}: {exc}",
                                                         "confidence": "low"}}
            continue
        src = _strip_comments(text)
        imports = sorted({n for n in (_package_name(m.group(1)) for m in _IMPORT_RE.finditer(src)) if n})
        symbols[mod_key] = {"kind": "module", "name": p.name, "qualname": rel, "path": rel, "line_start": 1,
                            "line_end": text.count("\n") + 1, "attrs": {"lang": lang, "external_imports": imports}}
        if _TEST_FILE_RE.search(rel):
            continue  # tests mock fetch; their call sites are not product traffic
        for m in _CALL_RE.finditer(src):
            args, _end = _split_args(src, m.end() - 1)
            if not args:
                continue
            first = args[0].strip()
            rest = ",".join(args[1:])
            method = (m.group("m") or "").upper()
            url_expr = first
            is_config = m.group("m") == "request" or (
                m.group(0).startswith("axios") and not m.group("m") and first.startswith("{"))
            if is_config:
                um = _URL_KW_RE.search(first)
                url_expr = um.group(1) if um else ""
                mm = _METHOD_KW_RE.search(first)
                method = mm.group(1).upper() if mm else "GET"
            if not method or method == "REQUEST":
                mm = _METHOD_KW_RE.search(rest) or (_METHOD_KW_RE.search(first) if first.startswith("{") else None)
                method = mm.group(1).upper() if mm else "GET"
            ev = _evaluate(url_expr, src)
            if ev is None:
                continue
            url_raw, exact = ev
            url = normalise_url(url_raw)
            line = src.count("\n", 0, m.start()) + 1
            attrs = {"method": method, "url": url, "raw": " ".join(first.split())[:200], "line": line}
            if not exact:
                attrs["confidence"] = "low"
            qual = f"{method} {url}"
            key = ("frontend_call", qual, rel)
            if key not in symbols:
                symbols[key] = {"kind": "frontend_call", "name": qual, "qualname": qual, "path": rel,
                                "line_start": line, "line_end": line, "attrs": attrs}
            ekey = ("defines", mod_key, key)
            edges.setdefault(ekey, {"src": {"kind": "module", "qualname": rel, "path": rel},
                                    "dst": {"kind": "frontend_call", "qualname": qual, "path": rel},
                                    "rel": "defines", "attrs": {}})
    syms = sorted(symbols.values(), key=lambda s: (s["path"], s["line_start"] or 0, s["kind"], s["qualname"]))
    es = sorted(edges.values(), key=lambda e: (e["rel"], e["src"]["path"], e["src"]["qualname"],
                                               e["dst"]["qualname"]))
    return {"symbols": syms, "edges": es}


def _segments_match(call_path: str, route_path: str) -> str | None:
    """"exact" / "param" when the paths match segment by segment, else None."""
    cs = [s for s in call_path.split("/") if s]
    rs = [s for s in route_path.split("/") if s]
    if len(cs) != len(rs):
        return None
    exact = True
    for c, r in zip(cs, rs):
        if c == r:
            continue
        if (r.startswith("{") and r.endswith("}")) or (c.startswith("{") and c.endswith("}")) or c == "{}":
            exact = False
            continue
        return None
    return "exact" if exact else "param"


def link(merged: ExtractResult) -> list[Edge]:
    """``fetches`` edges from frontend_call symbols to the route symbols they hit.

    Only mounted routes can be hit (a route on a router that is never included is unreachable).

    Args:
        merged: all extractors' symbols and edges for one snapshot.

    Returns:
        One edge per (call, route) match with ``attrs = {"match": "exact"|"param"}``.
    """
    routes = [s for s in merged["symbols"] if s["kind"] == "route" and s["attrs"].get("mounted", True)]
    out: list[Edge] = []
    for call in merged["symbols"]:
        if call["kind"] != "frontend_call":
            continue
        method = call["attrs"].get("method", "GET")
        url = normalise_url(call["attrs"].get("url", ""))
        for r in routes:
            if r["attrs"].get("method") != method:
                continue
            how = _segments_match(url, normalise_url(r["attrs"].get("path", "")))
            if how:
                out.append({"src": {"kind": "frontend_call", "qualname": call["qualname"], "path": call["path"]},
                            "dst": {"kind": "route", "qualname": r["qualname"], "path": r["path"]},
                            "rel": "fetches", "attrs": {"match": how}})
    out.sort(key=lambda e: (e["src"]["path"], e["src"]["qualname"], e["dst"]["qualname"], e["dst"]["path"]))
    return out
