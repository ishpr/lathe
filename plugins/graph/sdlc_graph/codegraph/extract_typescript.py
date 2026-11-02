"""TypeScript extractor: the symbols and edges of a Next.js / Foldkit application (regex based, stdlib only).

Scanned: ``.ts`` and ``.tsx`` files under the application paths (``paths.app`` of ``.claude/sdlc.json``; default
``src/**``, ``app/**``, ``lib/**``; see ``appscan``) outside ``node_modules``, ``.next``, ``dist`` and ``build``.
Test files (``*.test.*``, ``*.spec.*``, ``__tests__/``) yield only a module, a ``test`` symbol and edges.

Symbols (``qualname`` for everything but modules is ``<path>:<name>``)::

    module     one per file, qualname = path; the same symbol extract_frontend emits (so the two merge)
    function   a top-level function, or a const holding an arrow function or a value, attrs {exported, kind}
               where kind is "function" or "const"; non-exported ones are functions only
    message    a Foldkit Message tag: a key of ``defineMessageUnion({ Tag: {...} })`` (or ``m('Tag')``);
               attrs {union, fields, exported}
    command    ``const X = Command.define('X', { args, messages, execute })``; attrs {command, args, messages, endpoints,
               missing_endpoints}
    view       an exported function returning Html / Document (or taking the HtmlBuilder), best effort
    route_fe   a key of ``defineRouteUnion({ Route: {...} })`` (the Foldkit router)
    test       one per test file, qualname = path; attrs {runner: story|scene|unit, nodeid: path}

Foldkit symbols and the files of a program carry ``attrs.program`` (the directory below the ``frontend.programs``
glob, e.g. ``runs`` for ``src/programs/runs/update.ts``).

Edges::

    defines    module -> every symbol above
    imports    module/test -> module it imports (relative and tsconfig-alias specifiers; packages are not edges)
    handles    update module -> message tag whose name appears in the file (tags of the same directory and of the modules
               it imports); the update file is ``update.ts`` / ``update/`` or any file in a program that exports ``update``
    calls_api  command -> endpoint function (or const) exported by the ``frontend.api_table`` file, when the command's text
               references a name it imported from there. A name imported from the api file that it does not export is no edge:
               it is listed in the command's ``attrs.missing_endpoints``
    tests      story test -> message tag the test sends (``Message.Tag(...)``, ``Tag(...)`` or ``_tag: 'Tag'``);
               scene test -> view it references (a scene test that names none of its imported views gets the views of its own
               directory, ``attrs.confidence = "low"``)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sdlc_graph.codegraph import appscan
from sdlc_graph.codegraph.extract_frontend import _IMPORT_RE, _split_args, _strip_comments, module_symbol
from sdlc_graph.shapes import Edge, ExtractResult, Sym

_EXTS = (".ts", ".tsx")
_IDENT = r"[A-Za-z_$][\w$]*"
_FUNC_RE = re.compile(rf"^(?P<exp>export\s+)?(?P<def>default\s+)?(?:async\s+)?function\*?\s*(?P<name>{_IDENT})?\s*(?:<[^>(]*>)?\s*\(", re.M)
_CONST_RE = re.compile(rf"^(?P<exp>export\s+)?(?:const|let|var)\s+(?P<name>{_IDENT})\s*(?P<ann>:[^=\n]+?)?\s*=(?![=>])\s*", re.M)
_ARROW_RE = re.compile(r"(?:async\s+)?(?:<[^>()=]*>\s*)?(?:\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?\s*=>")
_FUNC_EXPR_RE = re.compile(r"(?:async\s+)?function\b")
_EXPORT_LIST_RE = re.compile(r"^export\s*(?:type\s*)?\{([^}]*)\}(?!\s*from)", re.M)
_EXPORT_TYPE_RE = re.compile(rf"^export\s+(?:declare\s+)?(?:abstract\s+)?(?:type|interface|enum|class|namespace)\s+(?P<name>{_IDENT})", re.M)
_EXPORT_DEFAULT_RE = re.compile(rf"^export\s+default\s+(?P<name>{_IDENT})\s*;?\s*$", re.M)
_IMPORT_STMT_RE = re.compile(
    rf"""\bimport\s+(?P<type>type\s+)?(?:(?P<default>{_IDENT})\s*,?\s*)?(?:\{{(?P<named>[^}}]*)\}}|\*\s*as\s+(?P<ns>{_IDENT}))?\s*from\s*["'](?P<spec>[^"'\n]+)["']""",
    re.S)
_UNION_RE = re.compile(r"(?<![\w$.])defineMessageUnion\s*(?:<[^>(]*>)?\s*\(")
_ROUTE_UNION_RE = re.compile(r"(?<![\w$.])defineRouteUnion\s*(?:<[^>(]*>)?\s*\(")
_COMMAND_RE = re.compile(r"(?<![\w$])(?:Command\s*\.\s*define|defineCommand)\s*(?:<[^>(]*>)?\s*\(")
_COMMAND_ANY_RE = re.compile(r"(?<![\w$])Command\s*\.\s*\w+\s*(?:<[^>(]*>)?\s*\(")
_M_RE = re.compile(r"""(?<![\w$.])m\s*\(\s*(["'])(?P<tag>[A-Za-z_$][\w$]*)\1""")
_VIEW_TYPE_RE = re.compile(r"\b(?:Html|Document|HtmlBuilder|VNode)\b")
_VIEW_NAME_RE = re.compile(r"^(?:view|[a-z]\w*View|View|render\w*)$")
_VIEW_FILE_RE = re.compile(r"(?:^|/)(?:view|views)(?:\.[tj]sx?$|/)|\.view\.[tj]sx?$")
_UPDATE_FILE_RE = re.compile(r"(?:^|/)update(?:\.tsx?$|/)")
_COMMAND_FILE_RE = re.compile(r"(?:^|/)commands?(?:\.tsx?$|/)")
_KEY_RE = re.compile(rf"^\s*(?:readonly\s+)?(?P<key>{_IDENT})\s*:")
_STRING_RE = re.compile(r"""^\s*(["'`])(?P<s>.*)\1\s*$""", re.S)


def _line(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


def _closer(src: str, open_idx: int) -> int:
    """Index of the bracket closing the one at ``open_idx`` (strings skipped); ``len(src)`` when unbalanced."""
    _args, end = _split_args(src, open_idx)
    return end


def _object_keys(src: str, open_idx: int) -> list[tuple[str, int, list[str]]]:
    """``(key, offset, field names)`` of each top-level entry of the object literal whose ``{`` is at ``open_idx``."""
    parts, _ = _split_args(src, open_idx)
    out, pos = [], open_idx + 1
    for part in parts:
        m = _KEY_RE.match(part)
        if m:
            value = part[m.end():].strip()
            fields: list[str] = []
            if value.startswith("{"):
                inner, _e = _split_args(value, 0)
                fields = [fm.group("key") for e in inner if (fm := _KEY_RE.match(e))]
            out.append((m.group("key"), pos + m.start("key"), fields))
        pos += len(part) + 1
    return out


def _top_level_starts(src: str) -> list[int]:
    return [m.start() for m in re.finditer(r"^(?=[A-Za-z@])", src, re.M)]


def _imports(src: str) -> list[dict[str, Any]]:
    out = []
    for m in _IMPORT_STMT_RE.finditer(src):
        named: dict[str, str] = {}
        for item in (m.group("named") or "").split(","):
            item = item.strip()
            if not item or item.startswith("type "):
                continue
            imported, _, local = item.partition(" as ")
            named[(local or imported).strip()] = imported.strip()
        out.append({"spec": m.group("spec"), "type_only": bool(m.group("type")), "default": m.group("default"),
                    "named": named, "ns": m.group("ns")})
    return out


def _word(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")


def _is_update_file(rel: str, exports_update: bool, in_program: bool) -> bool:
    return bool(_UPDATE_FILE_RE.search(rel)) or (in_program and exports_update)


class _File:
    __slots__ = ("rel", "src", "text", "is_test", "program", "imports", "resolved", "exports", "syms", "tags", "views")

    def __init__(self, rel: str, text: str, program: str | None) -> None:
        self.rel, self.text, self.program = rel, text, program
        self.src = _strip_comments(text)
        self.is_test = bool(appscan.TEST_FILE_RE.search(rel))
        self.imports: list[dict[str, Any]] = []
        self.resolved: set[str] = set()
        self.exports: set[str] = set()      # every exported name (values and types)
        self.syms: dict[tuple[str, str, str], Sym] = {}
        self.tags: dict[str, Sym] = {}
        self.views: dict[str, Sym] = {}


def _symbol(f: _File, kind: str, name: str, start: int, end: int, attrs: dict[str, Any], *, ends: list[int] | None = None) -> Sym:
    attrs = dict(attrs)
    if f.program:
        attrs["program"] = f.program
    line_start = _line(f.src, start)
    line_end = line_start
    if ends is not None:
        after = [e for e in ends if e > start]
        stop = min(after) if after else len(f.src)
        line_end = max(line_start, _line(f.src, max(start, len(f.src[:stop].rstrip()) - 1)))
    elif end > start:
        line_end = _line(f.src, end)
    sym: Sym = {"kind": kind, "name": name, "qualname": f"{f.rel}:{name}", "path": f.rel, "line_start": line_start,
                "line_end": line_end, "attrs": attrs}
    f.syms.setdefault((kind, sym["qualname"], f.rel), sym)
    return f.syms[(kind, sym["qualname"], f.rel)]


def _signature(src: str, start: int, rhs: re.Match[str] | None) -> str:
    return src[start:rhs.end()] if rhs else src[start:start + 240]


def _parse(f: _File, cfg: appscan.AppConfig) -> None:
    src = f.src
    starts = _top_level_starts(src)
    base_name = f.rel.rsplit("/", 1)[-1]
    taken: list[tuple[int, int]] = []     # regions already claimed by a more specific symbol

    for m in _EXPORT_LIST_RE.finditer(src):
        for item in m.group(1).split(","):
            item = item.strip()
            if item and not item.startswith("type "):
                f.exports.add(item.partition(" as ")[2].strip() or item)
    for m in _EXPORT_TYPE_RE.finditer(src):
        f.exports.add(m.group("name"))
    for m in _EXPORT_DEFAULT_RE.finditer(src):
        f.exports.add(m.group("name"))

    # message and route unions: const X = defineMessageUnion({ Tag: { field: ... } })
    for rx, kind, union_kind in ((_UNION_RE, "message", "message"), (_ROUTE_UNION_RE, "route_fe", "route")):
        for m in rx.finditer(src):
            open_paren = m.end() - 1
            brace = src.find("{", open_paren, open_paren + 40)
            if brace < 0 or src[open_paren + 1:brace].strip():
                continue
            end = _closer(src, open_paren)
            owner = re.search(rf"(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+(?P<n>{_IDENT})\s*(?::[^=\n]+)?=\s*$", src[max(0, m.start() - 200):m.start()])
            union = owner.group("n") if owner else None
            exported = bool(union and re.search(rf"^export\s+(?:const|let|var)\s+{re.escape(union)}\b", src, re.M)) or (union in f.exports if union else False)
            if union and exported:
                f.exports.add(union)
            for tag, off, fields in _object_keys(src, brace):
                sym = _symbol(f, kind, tag, off, off, {"union": union, "fields": fields, "exported": exported})
                if kind == "message":
                    f.tags.setdefault(tag, sym)
            taken.append((m.start(), end))
    for m in _M_RE.finditer(src):
        tag = m.group("tag")
        f.tags.setdefault(tag, _symbol(f, "message", tag, m.start(), m.start(), {"union": None, "fields": [], "exported": True}))

    # commands
    command_file = bool(_COMMAND_FILE_RE.search(f.rel))
    for m in _CONST_RE.finditer(src):
        name, exported = m.group("name"), bool(m.group("exp"))
        rhs = src[m.end():m.end() + 200]
        cm = _COMMAND_RE.match(rhs) or (_COMMAND_ANY_RE.match(rhs) if command_file else None)
        if not cm:
            continue
        open_paren = m.end() + cm.end() - 1
        end = _closer(src, open_paren)
        args, _ = _split_args(src, open_paren)
        first = _STRING_RE.match(args[0]) if args else None
        cmd_name = first.group("s") if first else name
        body = src[open_paren:end]
        attrs: dict[str, Any] = {"command": cmd_name, "exported": exported, "args": [], "messages": [], "endpoints": []}
        cfg_arg = args[1] if len(args) > 1 else ""
        am = re.search(r"(?<![\w$])args\s*:\s*\{", cfg_arg)
        if am:
            attrs["args"] = [k for k, _o, _f in _object_keys(cfg_arg, am.end() - 1)]
        mm = re.search(r"(?<![\w$])messages\s*:\s*\[", cfg_arg)
        if mm:
            items, _ = _split_args(cfg_arg, mm.end() - 1)
            attrs["messages"] = [i.strip() for i in items if i.strip()]
        if exported:
            f.exports.add(name)
        sym = _symbol(f, "command", name, m.start(), end, attrs)
        sym["attrs"]["_span"] = (open_paren, end)   # used by the api pass, removed before the result is returned
        taken.append((m.start(), end))

    def claimed(pos: int) -> bool:
        return any(a <= pos <= b for a, b in taken)

    # functions and consts
    for m in _FUNC_RE.finditer(src):
        if claimed(m.start()):
            continue
        name = m.group("name") or ("default" if m.group("def") else None)
        if not name:
            continue
        exported = bool(m.group("exp"))
        if exported:
            f.exports.add(name)
        close = _closer(src, m.end() - 1)
        brace = src.find("{", close)
        signature = src[m.start():brace if brace >= 0 else close + 1]
        _function(f, name, m.start(), signature, exported, starts)
    for m in _CONST_RE.finditer(src):
        if claimed(m.start()):
            continue
        name, exported = m.group("name"), bool(m.group("exp"))
        rhs_text = src[m.end():m.end() + 400]
        arrow = _ARROW_RE.match(rhs_text)
        fexpr = _FUNC_EXPR_RE.match(rhs_text)
        if exported:
            f.exports.add(name)
        if arrow or fexpr:
            signature = (m.group("ann") or "") + " " + (rhs_text[:arrow.end()] if arrow else rhs_text[:rhs_text.find("{") if "{" in rhs_text else 200])
            _function(f, name, m.start(), signature, exported, starts)
        elif exported:
            _symbol(f, "function", name, m.start(), m.start(), {"exported": True, "kind": "const"}, ends=starts)
    for sym in f.syms.values():   # `export { a, b }` after the declarations
        if sym["kind"] in ("function", "view") and sym["name"] in f.exports:
            sym["attrs"]["exported"] = True


def _function(f: _File, name: str, start: int, signature: str, exported: bool, starts: list[int]) -> None:
    kind = "function"
    attrs: dict[str, Any] = {"exported": exported, "kind": kind}
    is_view = exported and (bool(_VIEW_TYPE_RE.search(signature))
                            or (bool(_VIEW_NAME_RE.match(name)) and bool(_VIEW_FILE_RE.search(f.rel))))
    if f.rel.endswith(".tsx") and not _VIEW_TYPE_RE.search(signature):
        is_view = False   # a React component returns JSX, not Foldkit Html
    if is_view:
        sym = _symbol(f, "view", name, start, start, {"exported": True, "kind": kind}, ends=starts)
        f.views[name] = sym
    else:
        _symbol(f, "function", name, start, start, attrs, ends=starts)


def extract(repo_root: Path) -> ExtractResult:
    """Extract the TypeScript symbols and edges of the tree at ``repo_root`` (see the module docstring).

    Returns:
        ExtractResult, sorted like the other extractors. Empty when no ``.ts`` / ``.tsx`` file matches the application
        paths. An unreadable file yields only its module symbol with ``attrs.parse_error``. Never raises for content.
    """
    root = Path(repo_root)
    cfg = appscan.load_config(root)
    extra = (cfg.api_table,) if cfg.api_table else ()
    rels = appscan.list_files(root, _EXTS, cfg, extra=extra)
    known = set(rels)
    files: dict[str, _File] = {}
    symbols: dict[tuple[str, str, str], Sym] = {}
    edges: dict[tuple, Edge] = {}

    def add_edge(rel: str, src: Sym | tuple[str, str, str], dst: Sym | tuple[str, str, str], attrs: dict[str, Any] | None = None) -> None:
        sk = src if isinstance(src, tuple) else (src["kind"], src["qualname"], src["path"])
        dk = dst if isinstance(dst, tuple) else (dst["kind"], dst["qualname"], dst["path"])
        edges.setdefault((rel, sk, dk), {"src": {"kind": sk[0], "qualname": sk[1], "path": sk[2]},
                                         "dst": {"kind": dk[0], "qualname": dk[1], "path": dk[2]},
                                         "rel": rel, "attrs": dict(attrs or {})})

    for rel in rels:
        mod = ("module", rel, rel)
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            symbols[mod] = module_symbol(rel, None, exc)
            continue
        symbols[mod] = module_symbol(rel, text)
        f = _File(rel, text, cfg.program_of(rel))
        f.imports = _imports(f.src)
        for spec in {m.group(1) for m in _IMPORT_RE.finditer(f.src)}:
            target = appscan.resolve_import(spec, rel, cfg, known)
            if target and target != rel:
                f.resolved.add(target)
        files[rel] = f
        if not f.is_test:
            _parse(f, cfg)

    by_dir: dict[str, list[_File]] = {}
    for f in files.values():
        by_dir.setdefault(f.rel.rpartition("/")[0], []).append(f)

    for rel, f in files.items():
        mod = ("module", rel, rel)
        if not f.is_test:   # a test file's imports belong to its test symbol (below), as in the Python extractor
            for target in sorted(f.resolved):
                add_edge("imports", mod, ("module", target, target))
        for key, sym in f.syms.items():
            symbols[key] = sym
            add_edge("defines", mod, sym)

    api = files.get(cfg.api_table) if cfg.api_table else None
    api_names = {n for n in api.exports} if api else set()

    def peers(f: _File) -> list[_File]:
        """Files whose tags or views a file may use: the ones it imports and the ones in its directory."""
        found = {t: files[t] for t in f.resolved if t in files}
        for g in by_dir.get(f.rel.rpartition("/")[0], []):
            if g.rel != f.rel:
                found.setdefault(g.rel, g)
        return [found[k] for k in sorted(found)]

    for rel, f in sorted(files.items()):
        mod = ("module", rel, rel)
        # commands -> api endpoints
        for sym in [s for s in f.syms.values() if s["kind"] == "command"]:
            span = sym["attrs"].pop("_span", None)
            if not span or not api:
                continue
            body = f.src[span[0]:span[1]]
            used: set[str] = set()
            for imp in f.imports:
                if imp["type_only"] or appscan.resolve_import(imp["spec"], rel, cfg, known) != api.rel:
                    continue
                used |= {imported for local, imported in imp["named"].items() if _word(local).search(body)}
                if imp["ns"]:
                    used |= {mm.group("n") for mm in re.finditer(
                        rf"(?<![\w$.]){re.escape(imp['ns'])}\s*\.\s*(?P<n>{_IDENT})", body)}
                if imp["default"] and _word(imp["default"]).search(body):
                    used.add("default")
            reached, missing = [], []
            for name in sorted(used):
                if name not in api_names:
                    missing.append(name)
                    continue
                target = next((api.syms[k] for k in (("function", f"{api.rel}:{name}", api.rel),
                                                    ("view", f"{api.rel}:{name}", api.rel)) if k in api.syms), None)
                if target:   # an exported type has no symbol and no edge
                    reached.append(name)
                    add_edge("calls_api", sym, target)
            sym["attrs"]["endpoints"] = reached
            if missing:
                sym["attrs"]["missing_endpoints"] = missing
        # update -> message tags
        exports_update = "update" in f.exports
        if not f.is_test and _is_update_file(rel, exports_update, f.program is not None):
            for g in [f, *peers(f)]:
                for tag, tsym in sorted(g.tags.items()):
                    if _word(tag).search(f.src):
                        add_edge("handles", mod, tsym)
        # tests
        if f.is_test:
            runner = "story" if ".story." in rel else "scene" if ".scene." in rel else "unit"
            tsym: Sym = {"kind": "test", "name": rel.rsplit("/", 1)[-1], "qualname": rel, "path": rel, "line_start": 1,
                         "line_end": symbols[mod]["line_end"],
                         "attrs": {"lang": rel.rsplit(".", 1)[-1], "runner": runner, "nodeid": rel,
                                   **({"program": f.program} if f.program else {})}}
            symbols[("test", rel, rel)] = tsym
            add_edge("defines", mod, tsym)
            for target in sorted(f.resolved):
                add_edge("imports", tsym, ("module", target, target))
            if runner == "story":
                for g in peers(f):
                    for tag, msym in sorted(g.tags.items()):
                        sent = re.search(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)?{re.escape(tag)}\s*\(|_tag\s*:\s*[\"']{re.escape(tag)}[\"']", f.src)
                        if sent:
                            add_edge("tests", tsym, msym, {"via": "story"})
            elif runner == "scene":
                hit = False
                for g in peers(f):
                    for name, vsym in sorted(g.views.items()):
                        if g.rel in f.resolved and _word(name).search(f.src):
                            add_edge("tests", tsym, vsym, {"via": "scene"})
                            hit = True
                if not hit:
                    for g in by_dir.get(rel.rpartition("/")[0], []):
                        for name, vsym in sorted(g.views.items()):
                            add_edge("tests", tsym, vsym, {"via": "scene", "confidence": "low"})

    for sym in symbols.values():
        sym["attrs"].pop("_span", None)
    syms = sorted(symbols.values(), key=lambda s: (s["path"], s["line_start"] or 0, s["kind"], s["qualname"]))
    es = sorted(edges.values(), key=lambda e: (e["rel"], e["src"]["path"], e["src"]["qualname"], e["dst"]["path"], e["dst"]["qualname"]))
    return {"symbols": syms, "edges": es}


def link(merged: ExtractResult) -> list[Edge]:
    """Protocol stub: every edge is computed in :func:`extract` (it needs the whole file set, not the merged graph)."""
    return []
