"""Python extractor: modules, classes, functions, methods, FastAPI routes, SQLAlchemy models,
environment-variable reads and pytest tests, from ``*.py`` files under the repo root, using the
standard ``ast`` module only (no imports of the target code, no third-party parsers).

Reference logic: plugins/factory/scripts/checkers/python/
{route_auth,module_state,dead_code,api_contract}.py (read-only).

Emitted symbols (see docs/graph-architecture.md "Symbol kinds and attrs" for every attrs key):
  module    qualname "app.routes.a", path "app/routes/a.py"
  class     "app.models.Item";   function "app.routes.a.list_items";  method "app.models.Item.save"
  route     qualname "GET /items" (full path after router and include_router prefixes), path = the
            file holding the decorator; attrs {method, path, router, router_prefix, include_prefix,
            mounted, handler, dependencies, auth, confidence}
  model     same qualname/path as its class; attrs {table, base}
  env_var   qualname = variable name, path = the reading file (one per var per file);
            attrs {access: "environ[]"|"environ.get"|"getenv", default, in_config: bool}
  test      qualname "tests.test_a.test_list_items"; attrs {nodeid: "tests/test_a.py::test_list_items"}
Emitted edges: imports (module->module, resolved only to modules inside the repo; external
imports go in module attrs.external_imports), defines (module->class/function/test,
class->method, class->model), calls (function/method -> function/method/class, best effort,
resolved through imports; calling a class is a ``calls`` edge to the class; unresolved calls
are dropped, not guessed), exposes_route (handler->route),
includes_router (including module -> router module, attrs {prefix, router}), reads_env
(function or module -> env_var), uses_model (function/method -> model it names),
tests (test -> symbol it imports or calls).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from sdlc_graph.shapes import Edge, ExtractResult, Sym, SymKey, key_dict, sym_key

#: Directories never scanned (relative path parts).
SKIP_DIRS: frozenset[str] = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
                                       ".mypy_cache", ".pytest_cache", ".ruff_cache"})

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
ROUTER_CTORS = frozenset({"APIRouter", "FastAPI"})
DEPENDS_NAMES = frozenset({"Depends", "Security"})
#: A dependency whose name matches this is treated as an auth dependency (attrs.auth).
AUTH_NAME_RE = re.compile(r"auth|require_|current_user|get_user|verify_|api_?key|permission|jwt|bearer|login_required",
                          re.IGNORECASE)
#: Attribute names too common to resolve by name alone (builtin/container/stdlib methods).
COMMON_METHODS = frozenset({
    "get", "set", "add", "append", "extend", "pop", "update", "items", "keys", "values", "join", "split", "strip",
    "format", "read", "write", "close", "open", "send", "run", "start", "stop", "copy", "clear", "remove", "insert",
    "index", "count", "sort", "encode", "decode", "lower", "upper", "replace", "find", "save", "load", "dump",
    "dumps", "loads", "commit", "rollback", "flush", "query", "filter", "first", "all", "one", "delete", "execute",
    "post", "put", "patch", "head", "json", "text", "status", "result", "wait", "submit", "cancel", "done", "reset",
    "setdefault", "discard", "issubset", "startswith", "endswith", "isoformat", "total_seconds", "now", "today",
})
STDLIB: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
SRC_ROOTS: tuple[str, ...] = ("src", "lib")

_Ref = tuple  # ("sym", qualname) | ("module", dotted) | ("modattr", module, name)


def module_name(rel_path: str) -> str:
    """Dotted module name for a repo-relative ``.py`` path.

    ``"app/routes/a.py" -> "app.routes.a"``, ``"app/__init__.py" -> "app"``.

    Raises:
        ValueError: the path does not end in ``.py``.
    """
    if not rel_path.endswith(".py"):
        raise ValueError(f"not a Python file path: {rel_path!r}")
    parts = rel_path[:-3].replace("\\", "/").strip("/").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "__init__"


# --------------------------------------------------------------------------- small AST helpers


def _last_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _chain(node: ast.AST) -> tuple[str | None, list[str]]:
    """Split ``a.b.c`` into ("a", ["b", "c"]); a non-name base gives (None, [attrs...])."""
    attrs: list[str] = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    attrs.reverse()
    if isinstance(node, ast.Name):
        return node.id, attrs
    return None, attrs


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _unparse(node: ast.AST, limit: int = 120) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "?"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _depends_names(nodes) -> list[str]:
    """Names inside ``Depends(<name>)`` / ``Security(<name>)`` found anywhere under ``nodes``."""
    found: list[str] = []
    for root in nodes:
        if root is None:
            continue
        for n in ast.walk(root):
            if isinstance(n, ast.Call) and _last_name(n.func) in DEPENDS_NAMES and n.args:
                name = _last_name(n.args[0])
                if name:
                    found.append(name)
    return found


def _arg_defaults_and_annotations(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    a = fn.args
    nodes: list[ast.AST] = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
    for arg in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]:
        if arg.annotation is not None:
            nodes.append(arg.annotation)
    return nodes


def _end(node: ast.AST) -> int | None:
    return getattr(node, "end_lineno", None)


def _start(node) -> int:
    lines = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
    return min(lines)


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


def _in_config(rel: str) -> bool:
    parts = rel.split("/")
    return parts[-1] in {"config.py", "settings.py"} or any(p in {"config", "settings"} for p in parts[:-1])


# --------------------------------------------------------------------------- per-file info


class _File:
    """One parsed source file plus the tables the resolver needs."""

    def __init__(self, rel: str, module: str, tree: ast.Module | None, error: str | None, lines: int):
        self.rel = rel
        self.module = module
        self.is_pkg = rel.endswith("/__init__.py") or rel == "__init__.py"
        self.tree = tree
        self.error = error
        self.lines = lines
        self.is_test = _is_test_file(rel)
        self.top_aliases: dict[str, tuple] = {}   # module-level imports
        self.all_aliases: dict[str, tuple] = {}   # + function-level imports
        self.imports: list[tuple[str, str | None]] = []  # (module dotted, imported name or None)
        self.top_defs: dict[str, str] = {}        # name -> kind ("function"|"class")
        self.routers: dict[str, dict] = {}


class _Extractor:
    def __init__(self, root: Path):
        self.root = root
        self.files: dict[str, _File] = {}          # rel -> file
        self.by_module: dict[str, _File] = {}
        self.methods: dict[str, dict[str, str]] = {}   # class qualname -> {name: qualname}
        self.class_bases: dict[str, tuple[str, list[tuple[str | None, list[str]]]]] = {}  # class -> (module, base chains)
        self.methods_by_name: dict[str, list[str]] = {}
        self.sym_kind: dict[str, str] = {}         # qualname -> function|method|class|test
        self.sym_path: dict[str, str] = {}
        self.model_quals: set[str] = set()
        self.class_nodes: dict[str, ast.ClassDef] = {}
        self.fn_targets: dict[str, set[tuple]] = {}     # function qualname -> production targets it references
        self.fn_args: dict[str, list[str]] = {}         # function qualname -> parameter names
        self.fn_is_fixture: set[str] = set()
        self.scope_targets: dict[str, set[tuple]] = {}  # file rel -> targets referenced at module scope
        self.module_vars: dict[str, set[str]] = {}      # file rel -> names assigned at module scope
        self.pending_tests: list[tuple] = []            # (test key, file, qualname, cls, ref heads)
        self.routers: dict[tuple[str, str], dict] = {}
        self.symbols: dict[tuple[str, str, str], Sym] = {}
        self.edges: dict[tuple, Edge] = {}

    # ----------------------------------------------------------------- loading

    def load(self) -> None:
        paths: list[Path] = []
        for p in self.root.rglob("*.py"):
            rel_parts = p.relative_to(self.root).parts
            if any(part in SKIP_DIRS for part in rel_parts[:-1]) or not p.is_file():
                continue
            paths.append(p)
        for p in sorted(paths):
            rel = p.relative_to(self.root).as_posix()
            module = module_name(rel)
            tree = None
            error = None
            lines = 1
            try:
                text = p.read_text(encoding="utf-8")
                lines = max(1, text.count("\n") + 1)
                tree = ast.parse(text, filename=rel)
            except (SyntaxError, ValueError, RecursionError, UnicodeDecodeError, OSError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            f = _File(rel, module, tree, error, lines)
            self.files[rel] = f
            self.by_module.setdefault(module, f)
        for f in self.files.values():
            if f.tree is not None:
                self._collect_imports(f)
                self._collect_defs(f)
        for f in self.files.values():
            if f.tree is not None:
                self._collect_routers(f)

    def _canon(self, dotted: str) -> str | None:
        """The in-repo module name for an absolute dotted import, or None."""
        if dotted in self.by_module:
            return dotted
        for root in SRC_ROOTS:
            if f"{root}.{dotted}" in self.by_module:
                return f"{root}.{dotted}"
        return None

    def _collect_imports(self, f: _File) -> None:
        pkg = f.module if f.is_pkg else f.module.rpartition(".")[0]
        top_level = set(map(id, f.tree.body))
        for node in ast.walk(f.tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            is_top = id(node) in top_level
            if isinstance(node, ast.Import):
                for a in node.names:
                    f.imports.append((a.name, None))
                    if a.asname:
                        entry = ("import", a.name)
                        name = a.asname
                    else:
                        entry = ("import", a.name.split(".")[0])
                        name = a.name.split(".")[0]
                    f.all_aliases.setdefault(name, entry)
                    if is_top:
                        f.top_aliases.setdefault(name, entry)
                continue
            if node.level:
                parts = pkg.split(".") if pkg else []
                up = node.level - 1
                if up > len(parts):
                    continue
                base_parts = parts[: len(parts) - up] if up else parts
                base = ".".join(base_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            for a in node.names:
                if a.name == "*":
                    f.imports.append((base, None))
                    continue
                f.imports.append((base, a.name))
                entry = ("from", base, a.name)
                name = a.asname or a.name
                f.all_aliases.setdefault(name, entry)
                if is_top:
                    f.top_aliases.setdefault(name, entry)

    def _register(self, qual: str, kind: str, path: str) -> None:
        self.sym_kind[qual] = kind
        self.sym_path[qual] = path

    def _collect_defs(self, f: _File) -> None:
        for node in f.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "test" if f.is_test and node.name.startswith("test") else "function"
                f.top_defs.setdefault(node.name, "function")
                self._register(f"{f.module}.{node.name}", kind, f.rel)
            elif isinstance(node, ast.ClassDef):
                f.top_defs.setdefault(node.name, "class")
                self._collect_class(f, node, f.module)

    def _collect_class(self, f: _File, node: ast.ClassDef, prefix: str) -> None:
        qual = f"{prefix}.{node.name}"
        self._register(qual, "class", f.rel)
        self.class_bases[qual] = (f.module, [_chain(b) for b in node.bases])
        self.class_nodes[qual] = node
        meths = self.methods.setdefault(qual, {})
        is_test_cls = f.is_test and node.name.startswith("Test")
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mq = f"{qual}.{stmt.name}"
                kind = "test" if is_test_cls and stmt.name.startswith("test") else "method"
                meths.setdefault(stmt.name, mq)
                self._register(mq, kind, f.rel)
                if kind == "method":
                    self.methods_by_name.setdefault(stmt.name, []).append(mq)
            elif isinstance(stmt, ast.ClassDef):
                self._collect_class(f, stmt, qual)

    # ----------------------------------------------------------------- resolution

    def lookup_in_module(self, module: str, name: str, seen: frozenset = frozenset()) -> _Ref | None:
        """What ``module.name`` refers to: a definition, a re-export, a submodule, or a bare attribute."""
        canon = self._canon(module)
        if canon is None:
            return None
        key = (canon, name)
        if key in seen:
            return None
        seen = seen | {key}
        f = self.by_module[canon]
        if name in f.top_defs:
            return ("sym", f"{canon}.{name}")
        alias = f.top_aliases.get(name)
        if alias is not None:
            via = self._alias_target(alias, seen)
            if via is not None:
                return via
        sub = self._canon(f"{canon}.{name}")
        if sub is not None:
            return ("module", sub)
        return ("modattr", canon, name)

    def _alias_target(self, alias: tuple, seen: frozenset = frozenset()) -> _Ref | None:
        if alias[0] == "import":
            canon = self._canon(alias[1])
            return ("module", canon) if canon else None
        _, base, name = alias
        if self._canon(base) is None:
            return None
        return self.lookup_in_module(base, name, seen)

    def _descend(self, ref: _Ref | None, rest: list[str]) -> _Ref | None:
        for seg in rest:
            if ref is None:
                return None
            if ref[0] == "module":
                ref = self.lookup_in_module(ref[1], seg)
            elif ref[0] == "sym" and self.sym_kind.get(ref[1]) == "class":
                m = self.find_method(ref[1], seg)
                ref = ("sym", m) if m else None
            else:
                return ref if ref[0] == "modattr" else None
        return ref

    def find_method(self, cls: str, name: str, seen: frozenset = frozenset()) -> str | None:
        if cls in seen:
            return None
        m = self.methods.get(cls, {}).get(name)
        if m:
            return m
        module, bases = self.class_bases.get(cls, (None, []))
        if module is None:
            return None
        f = self.by_module[module]
        for head, rest in bases:
            if head is None:
                continue
            ref = self.resolve_chain(f, head, rest, None)
            if ref and ref[0] == "sym" and self.sym_kind.get(ref[1]) == "class":
                found = self.find_method(ref[1], name, seen | {cls})
                if found:
                    return found
        return None

    def resolve_chain(self, f: _File, head: str, rest: list[str], cls: str | None) -> _Ref | None:
        if head in ("self", "cls") and cls:
            if not rest:
                return ("sym", cls)
            m = self.find_method(cls, rest[0])
            return ("sym", m) if m else None
        if head in f.top_defs:
            return self._descend(("sym", f"{f.module}.{head}"), rest)
        alias = f.all_aliases.get(head)
        if alias is not None:
            return self._descend(self._alias_target(alias), rest)
        return None

    def is_external_head(self, f: _File, head: str | None) -> bool:
        """True when ``head`` is an import (any origin) or a local def: resolution failure is then
        final and the name-based fallback must not guess."""
        return head is not None and (head in f.all_aliases or head in f.top_defs)

    # ----------------------------------------------------------------- routers

    def _collect_routers(self, f: _File) -> None:
        for node in f.tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.Call) and _last_name(value.func) in ROUTER_CTORS:
                prefix_node = _kw(value, "prefix")
                prefix = _const_str(prefix_node) or ""
                known = prefix_node is None or _const_str(prefix_node) is not None
                info = {"kind": "app" if _last_name(value.func) == "FastAPI" else "router", "prefix": prefix,
                        "prefix_known": known, "deps": _depends_names([_kw(value, "dependencies")]),
                        "module": f.module, "rel": f.rel}
                for t in targets:
                    if isinstance(t, ast.Name):
                        info = dict(info, var=t.id, line=node.lineno)
                        f.routers[t.id] = info
                        self.routers[(f.module, t.id)] = info

    def router_of(self, f: _File, node: ast.AST) -> tuple[str, str] | None:
        """The (module, var) router/app registry key an expression refers to."""
        head, rest = _chain(node)
        if head is None:
            return None
        ref = self.resolve_chain(f, head, rest, None)
        if ref is None:
            if not rest and head in f.routers:
                return (f.module, head)
            return None
        if ref[0] == "modattr" and (ref[1], ref[2]) in self.routers:
            return (ref[1], ref[2])
        return None

    def compute_mounts(self) -> dict[tuple[str, str], dict]:
        """Walk include_router calls from every FastAPI app; return {router key: mount info} for
        every reachable router, and the include records."""
        includes: list[dict] = []
        for f in self.files.values():
            if f.tree is None:
                continue
            for n in ast.walk(f.tree):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "include_router"):
                    continue
                parent = self.router_of(f, n.func.value)
                child_node = n.args[0] if n.args else _kw(n, "router")
                child = self.router_of(f, child_node) if child_node is not None else None
                if parent is None or child is None:
                    continue
                pnode = _kw(n, "prefix")
                includes.append({"parent": parent, "child": child, "module": f.module, "rel": f.rel,
                                 "prefix": _const_str(pnode) or "", "line": n.lineno,
                                 "deps": _depends_names([_kw(n, "dependencies")]),
                                 "child_expr": _unparse(child_node)})
        includes.sort(key=lambda i: (i["rel"], i["line"], i["child"]))
        self.includes = includes
        mounts: dict[tuple[str, str], dict] = {}
        queue: list[tuple[str, str]] = []
        for key in sorted(self.routers):
            if self.routers[key]["kind"] == "app":
                mounts[key] = {"mount": "", "deps": []}
                queue.append(key)
        while queue:
            parent = queue.pop(0)
            pm = mounts[parent]
            for inc in includes:
                if inc["parent"] != parent or inc["child"] in mounts:
                    continue
                own = self.routers[parent]["prefix"]
                mounts[inc["child"]] = {"mount": pm["mount"] + own + inc["prefix"],
                                        "deps": pm["deps"] + self.routers[parent]["deps"] + inc["deps"]}
                queue.append(inc["child"])
        return mounts

    # ----------------------------------------------------------------- emission

    def add_sym(self, kind: str, name: str, qualname: str, path: str, ls: int | None, le: int | None,
                attrs: dict) -> None:
        key = (kind, qualname, path)
        if key not in self.symbols:
            self.symbols[key] = {"kind": kind, "name": name, "qualname": qualname, "path": path,
                                 "line_start": ls, "line_end": le, "attrs": attrs}

    def add_edge(self, src: tuple, dst: tuple, rel: str, attrs: dict | None = None) -> None:
        if src == dst:
            return
        key = (rel, src, dst)
        attrs = attrs or {}
        old = self.edges.get(key)
        if old is None:
            self.edges[key] = {"src": {"kind": src[0], "qualname": src[1], "path": src[2]},
                               "dst": {"kind": dst[0], "qualname": dst[1], "path": dst[2]}, "rel": rel,
                               "attrs": dict(attrs)}
        elif old["attrs"].get("confidence") == "low" and attrs.get("confidence") != "low":
            old["attrs"] = dict(attrs)

    def key_of(self, qual: str) -> tuple[str, str, str]:
        return (self.sym_kind[qual], qual, self.sym_path[qual])

    def emit(self) -> ExtractResult:
        mounts = self.compute_mounts()
        self._find_models()
        for rel in sorted(self.files):
            f = self.files[rel]
            mod_key = ("module", f.module, f.rel)
            if f.tree is None:
                self.add_sym("module", f.module.rpartition(".")[2], f.module, f.rel, 1, f.lines,
                             {"parse_error": f.error, "confidence": "low"})
                continue
            self._emit_module(f, mod_key, mounts)
        self._link_test_context()
        for inc in self.includes:
            src = ("module", inc["module"], inc["rel"])
            child_mod = inc["child"][0]
            dst_file = self.by_module[child_mod]
            self.add_edge(src, ("module", child_mod, dst_file.rel), "includes_router",
                          {"prefix": inc["prefix"], "router": inc["child"][1]})
        symbols = sorted(self.symbols.values(),
                         key=lambda s: (s["path"], s["line_start"] or 0, s["kind"], s["qualname"]))
        edges = sorted(self.edges.values(),
                       key=lambda e: (e["rel"], sym_key(e["src"]), sym_key(e["dst"])))
        return {"symbols": symbols, "edges": edges}

    def _production_target(self, ref: _Ref) -> tuple | None:
        """Edge target key for a resolved reference, excluding tests and test files."""
        if ref[0] == "sym":
            q = ref[1]
            if self.sym_kind[q] == "test" or _is_test_file(self.sym_path[q]):
                return None
            return self.key_of(q)
        mod = ref[1]
        mf = self.by_module.get(mod)
        if mf is None or mf.is_test:
            return None
        return ("module", mod, mf.rel)

    def _link_test_context(self) -> None:
        """Tests reach code through module-level variables (``client = TestClient(app)``) and
        pytest fixtures (same file or a ``conftest.py`` above it); add those ``tests`` edges."""
        for key, f, qual, heads in self.pending_tests:
            extra_low: set[tuple] = set()
            if heads & self.module_vars.get(f.rel, set()):
                extra_low |= self.scope_targets.get(f.rel, set())
            extra_fix: set[tuple] = set()
            seen: set[str] = set()
            frontier = list(self.fn_args.get(qual, []))
            depth = 0
            while frontier and depth < 4:
                nxt: list[str] = []
                for name in frontier:
                    for cf in self._fixture_files(f):
                        fq = f"{cf.module}.{name}"
                        if fq in self.fn_is_fixture and fq not in seen:
                            seen.add(fq)
                            extra_fix |= self.fn_targets.get(fq, set())
                            nxt.extend(self.fn_args.get(fq, []))
                            break
                frontier = nxt
                depth += 1
            for t in sorted(extra_fix):
                self.add_edge(key, t, "tests", {"via": "fixture"})
            for t in sorted(extra_low):
                self.add_edge(key, t, "tests", {"via": "module_var", "confidence": "low"})

    def _fixture_files(self, f: _File) -> list[_File]:
        files = [f]
        parts = f.rel.split("/")[:-1]
        for i in range(len(parts), -1, -1):
            rel = "/".join(parts[:i] + ["conftest.py"])
            cf = self.files.get(rel)
            if cf is not None and cf is not f and cf.tree is not None:
                files.append(cf)
        return files

    def _find_models(self) -> None:
        decl_names = {"Base"}
        for f in self.files.values():
            if f.tree is None:
                continue
            for node in ast.walk(f.tree):
                if isinstance(node, ast.ClassDef) and any(_last_name(b) in {"DeclarativeBase", "DeclarativeBaseNoMeta"}
                                                          for b in node.bases):
                    decl_names.add(node.name)
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                        and _last_name(node.value.func) == "declarative_base":
                    decl_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.decl_names = decl_names
        changed = True
        while changed:
            changed = False
            for qual in sorted(self.class_nodes):
                node = self.class_nodes[qual]
                if qual in self.model_quals:
                    continue
                has_table = any(isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__"
                                                                  for t in s.targets) for s in node.body)
                abstract = any(isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__abstract__"
                                                                 for t in s.targets) for s in node.body)
                base_names = {_last_name(b) for b in node.bases}
                if base_names & {"DeclarativeBase", "DeclarativeBaseNoMeta"}:
                    continue  # the declarative base itself, not a model
                by_base = bool(base_names & decl_names) or any(q.rpartition(".")[2] in base_names
                                                               for q in self.model_quals)
                if abstract and not has_table:
                    continue
                if has_table or by_base:
                    self.model_quals.add(qual)
                    changed = True

    def _import_ancestors(self, mod_key: tuple, importer: str, imported: str) -> None:
        """Importing ``a.b.c`` also runs ``a/__init__.py`` and ``a/b/__init__.py`` first, so the
        importer depends on those package modules too (a change to one breaks everything that imports
        anything beneath it). ``imports`` edge from the importer to each in-repo ancestor package."""
        parts = imported.split(".")
        for i in range(1, len(parts)):
            anc = ".".join(parts[:i])
            target = self.by_module.get(anc)
            if target is not None and anc != importer:
                self.add_edge(mod_key, ("module", anc, target.rel), "imports", {"via": "package_init"})

    def _emit_module(self, f: _File, mod_key: tuple, mounts: dict) -> None:
        ext: set[str] = set()
        for base, name in f.imports:
            canon = self._canon(base) if base else None
            top = base.split(".")[0] if base else ""
            if canon is not None:
                self.add_edge(mod_key, ("module", canon, self.by_module[canon].rel), "imports")
                self._import_ancestors(mod_key, f.module, canon)
                if name is not None:
                    sub = self._canon(f"{canon}.{name}")
                    if sub is not None:
                        self.add_edge(mod_key, ("module", sub, self.by_module[sub].rel), "imports")
                        self._import_ancestors(mod_key, f.module, sub)
            elif top and top not in STDLIB and self._canon(top) is None:
                ext.add(top)
        self.add_sym("module", f.module.rpartition(".")[2], f.module, f.rel, 1, f.lines,
                     {"external_imports": sorted(ext)})
        env_seen: dict[str, tuple] = {}

        def env_symbol(var: str, access: str, default, line: int) -> tuple:
            key = ("env_var", var, f.rel)
            if var not in env_seen:
                env_seen[var] = key
                self.add_sym("env_var", var, var, f.rel, line, line,
                             {"access": access, "default": default, "in_config": _in_config(f.rel)})
            return key

        def reads_env(owner: tuple, subtree_nodes) -> None:
            for n in subtree_nodes:
                for var, access, default, line in self._env_reads(f, n):
                    self.add_edge(owner, env_symbol(var, access, default, line), "reads_env")

        # module-level (and class-body-level) statements
        scope_nodes: list[ast.AST] = []

        def collect_scope(stmts):
            for s in stmts:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(s, ast.ClassDef):
                    collect_scope(s.body)
                else:
                    scope_nodes.append(s)

        collect_scope(f.tree.body)
        reads_env(mod_key, scope_nodes)
        _calls, scope_refs = _walk_refs(scope_nodes)
        targets: set[tuple] = set()
        for head, rest in scope_refs:
            ref = self.resolve_chain(f, head, rest, None) if head is not None else None
            t = self._production_target(ref) if ref is not None else None
            if t is not None:
                targets.add(t)
        self.scope_targets[f.rel] = targets
        names: set[str] = set()
        for st in f.tree.body:
            if isinstance(st, ast.Assign):
                names.update(t.id for t in st.targets if isinstance(t, ast.Name))
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                names.add(st.target.id)
        self.module_vars[f.rel] = names

        for node in f.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._emit_function(f, node, f.module, None, mod_key, mounts, reads_env)
            elif isinstance(node, ast.ClassDef):
                self._emit_class(f, node, f.module, mod_key, mounts, reads_env)

    def _emit_class(self, f: _File, node: ast.ClassDef, prefix: str, parent_key: tuple, mounts: dict,
                    reads_env) -> None:
        qual = f"{prefix}.{node.name}"
        ckey = ("class", qual, f.rel)
        self.add_sym("class", node.name, qual, f.rel, _start(node), _end(node),
                     {"bases": [_unparse(b) for b in node.bases]})
        self.add_edge(parent_key, ckey, "defines")
        if qual in self.model_quals:
            table = None
            for s in node.body:
                if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets):
                    table = _const_str(s.value)
            mkey = ("model", qual, f.rel)
            self.add_sym("model", node.name, qual, f.rel, _start(node), _end(node),
                         {"table": table, "base": _unparse(node.bases[0]) if node.bases else None})
            self.add_edge(ckey, mkey, "defines")
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._emit_function(f, stmt, qual, qual, ckey, mounts, reads_env)
            elif isinstance(stmt, ast.ClassDef):
                self._emit_class(f, stmt, qual, ckey, mounts, reads_env)

    def _emit_function(self, f: _File, node, prefix: str, cls: str | None, parent_key: tuple, mounts: dict,
                       reads_env) -> None:
        qual = f"{prefix}.{node.name}"
        kind = self.sym_kind.get(qual, "method" if cls else "function")
        key = (kind, qual, f.rel)
        attrs: dict = {}
        if kind in ("function", "method"):
            attrs = {"decorators": [_unparse(d) for d in node.decorator_list],
                     "async": isinstance(node, ast.AsyncFunctionDef)}
        else:
            nodeid = f.rel + "::" + qual[len(f.module) + 1:].replace(".", "::")
            attrs = {"nodeid": nodeid}
            requests = _http_requests(node)
            if requests:  # literal URLs the test sends over HTTP; ``link`` ties them to routes
                attrs["requests"] = requests
        self.add_sym(kind, node.name, qual, f.rel, _start(node), _end(node), attrs)
        self.add_edge(parent_key, key, "defines")
        reads_env(key, [node])

        calls, refs = _walk_refs([node])

        # calls
        is_test = kind == "test"
        for c in calls:
            head, rest = _chain(c.func)
            if head is None and not rest:
                continue
            target = None
            low = False
            if head is not None:
                ref = self.resolve_chain(f, head, rest, cls)
                if ref is not None and ref[0] == "sym":
                    target = ref[1]
            if target is None and rest and not self.is_external_head(f, head) and head not in ("self", "cls"):
                cands = self.methods_by_name.get(rest[-1], [])
                if len(cands) == 1 and rest[-1] not in COMMON_METHODS and not rest[-1].startswith("__"):
                    target, low = cands[0], True
            elif target is None and rest and head in ("self", "cls") and len(rest) >= 2 \
                    and rest[-1] not in COMMON_METHODS and not rest[-1].startswith("__"):
                cands = self.methods_by_name.get(rest[-1], [])
                if len(cands) == 1:
                    target, low = cands[0], True
            if target is not None and self.sym_kind.get(target) in ("function", "method", "class", "test"):
                self.add_edge(key, self.key_of(target), "calls", {"confidence": "low"} if low else {})
            if _last_name(c.func) in DEPENDS_NAMES and c.args:
                dh, dr = _chain(c.args[0])
                if dh is not None:
                    ref = self.resolve_chain(f, dh, dr, cls)
                    if ref and ref[0] == "sym" and self.sym_kind.get(ref[1]) in ("function", "method"):
                        self.add_edge(key, self.key_of(ref[1]), "calls", {"via": "Depends"})

        # model usage and test targets
        targets: set[tuple] = set()
        heads: set[str] = set()
        for head, rest in refs:
            if head is None:
                continue
            heads.add(head)
            ref = self.resolve_chain(f, head, rest, cls)
            if ref is None:
                continue
            t = self._production_target(ref)
            if t is not None:
                targets.add(t)
            if ref[0] == "sym" and ref[1] in self.model_quals and not is_test:
                self.add_edge(key, ("model", ref[1], self.sym_path[ref[1]]), "uses_model")
        self.fn_targets[qual] = targets
        a = node.args
        self.fn_args[qual] = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
        if any(_last_name(d.func if isinstance(d, ast.Call) else d) == "fixture" for d in node.decorator_list):
            self.fn_is_fixture.add(qual)
        if is_test:
            for t in sorted(targets):
                self.add_edge(key, t, "tests")
            self.pending_tests.append((key, f, qual, heads))

        # routes
        if cls is None and kind in ("function",):
            self._emit_routes(f, node, qual, key, mounts)

    def _emit_routes(self, f: _File, node, qual: str, key: tuple, mounts: dict) -> None:
        param_deps = _depends_names(_arg_defaults_and_annotations(node))
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            rkey = self.router_of(f, dec.func.value)
            if rkey is None:
                continue
            verb = dec.func.attr
            if verb in HTTP_METHODS:
                methods = [verb.upper()]
            elif verb in ("api_route", "route"):
                m_node = _kw(dec, "methods")
                methods = [m.upper() for m in (_const_str(e) for e in getattr(m_node, "elts", [])) if m] or ["GET"]
            else:
                continue
            path_node = dec.args[0] if dec.args else _kw(dec, "path")
            sub = _const_str(path_node)
            low = sub is None and path_node is not None
            sub = sub or ""
            router = self.routers[rkey]
            mount = mounts.get(rkey)
            mounted = mount is not None
            inc_prefix = mount["mount"] if mount else ""
            full = _join_path(inc_prefix, router["prefix"], sub)
            deps = sorted(set(router["deps"] + (mount["deps"] if mount else []) + _depends_names([_kw(dec, "dependencies")])
                              + param_deps))
            auth = any(AUTH_NAME_RE.search(d) for d in deps)
            for method in methods:
                rq = f"{method} {full}"
                attrs = {"method": method, "path": full, "router": rkey[1], "router_prefix": router["prefix"],
                         "include_prefix": inc_prefix, "mounted": mounted, "handler": qual, "dependencies": deps,
                         "auth": auth}
                if low or not router["prefix_known"]:
                    attrs["confidence"] = "low"
                self.add_sym("route", node.name, rq, f.rel, _start(node), _end(node), attrs)
                self.add_edge(key, ("route", rq, f.rel), "exposes_route")

    def _env_reads(self, f: _File, root: ast.AST):
        """Environment reads located *directly* under ``root`` (a def or a module-scope statement)."""
        out = []
        env_names = {n for n, a in f.all_aliases.items() if a == ("from", "os", "environ")}
        getenv_names = {n for n, a in f.all_aliases.items() if a == ("from", "os", "getenv")}
        os_names = {n for n, a in f.all_aliases.items() if a == ("import", "os")}

        def is_environ(n: ast.AST) -> bool:
            if isinstance(n, ast.Attribute) and n.attr == "environ" and isinstance(n.value, ast.Name) \
                    and n.value.id in os_names:
                return True
            return isinstance(n, ast.Name) and n.id in env_names

        for n in ast.walk(root):
            if isinstance(n, ast.Subscript) and is_environ(n.value) and isinstance(n.ctx, ast.Load):
                sl = n.slice
                var = _const_str(sl)
                if var:
                    out.append((var, "environ[]", None, n.lineno))
            elif isinstance(n, ast.Call):
                fn = n.func
                var = _const_str(n.args[0]) if n.args else None
                if var is None:
                    continue
                default_node = n.args[1] if len(n.args) > 1 else _kw(n, "default")
                default = _unparse(default_node, 80) if default_node is not None else None
                if isinstance(fn, ast.Attribute) and fn.attr == "get" and is_environ(fn.value):
                    out.append((var, "environ.get", default, n.lineno))
                elif (isinstance(fn, ast.Attribute) and fn.attr == "getenv" and isinstance(fn.value, ast.Name)
                      and fn.value.id in os_names) or (isinstance(fn, ast.Name) and fn.id in getenv_names):
                    out.append((var, "getenv", default, n.lineno))
        return out


def _url_text(node: ast.AST | None) -> str | None:
    """A string literal or f-string as URL text, each formatted value becoming ``{}``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values)
    return None


def _http_requests(fn: ast.AST) -> list[dict[str, str]]:
    """``[{"method", "path"}]`` for ``client.get("/x")``-style calls in a test body (the first argument
    is a literal path starting with ``/``; ``client.request("POST", "/x")`` too). Sorted, de-duplicated."""
    seen: set[tuple[str, str]] = set()
    for c in ast.walk(fn):
        if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)):
            continue
        verb = c.func.attr
        if verb in HTTP_METHODS and c.args:
            method, url = verb.upper(), _url_text(c.args[0])
        elif verb == "request" and len(c.args) >= 2:
            m = _const_str(c.args[0])
            method, url = (m.upper() if m else ""), _url_text(c.args[1])
        else:
            continue
        if method and url and url.startswith("/"):
            seen.add((method, url.split("?", 1)[0].split("#", 1)[0]))
    return [{"method": m, "path": p} for m, p in sorted(seen)]


def _walk_refs(roots) -> tuple[list[ast.Call], list[tuple[str | None, list[str]]]]:
    """Every Call node, and every maximal Name/Attribute chain, under ``roots``."""
    calls: list[ast.Call] = []
    refs: list[tuple[str | None, list[str]]] = []
    stack: list[ast.AST] = list(roots)
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Attribute):
            head, rest = _chain(n)
            refs.append((head, rest))
            base: ast.AST = n
            while isinstance(base, ast.Attribute):
                base = base.value
            if not isinstance(base, ast.Name):
                stack.append(base)
            continue
        if isinstance(n, ast.Name):
            refs.append((n.id, []))
            continue
        if isinstance(n, ast.Call):
            calls.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return calls, refs


def _join_path(*parts: str) -> str:
    joined = "/".join(p for p in parts if p)
    joined = re.sub(r"/{2,}", "/", joined)
    if not joined.startswith("/"):
        joined = "/" + joined
    if len(joined) > 1 and joined.endswith("/"):
        joined = joined.rstrip("/") or "/"
    return joined


def extract(repo_root: Path) -> ExtractResult:
    """Extract every Python symbol and edge under ``repo_root``.

    Args:
        repo_root: directory holding the checked-out tree (a temp export of one SHA, never the
            pristine checkout). Paths in the result are relative to it, POSIX separators.

    Returns:
        ExtractResult with deterministic ordering (symbols sorted by (path, line_start, kind,
        qualname); edges by (rel, src key, dst key)). Running twice on the same tree returns
        equal results.

    Errors:
        A file that fails to parse is not fatal: it yields a ``module`` symbol with
        ``attrs = {"parse_error": "<message>", "confidence": "low"}`` and nothing else.
        Raises FileNotFoundError only when ``repo_root`` does not exist.
    """
    root = Path(repo_root)
    if not root.exists():
        raise FileNotFoundError(f"repo root does not exist: {root}")
    ex = _Extractor(root)
    ex.load()
    return ex.emit()


def link(merged: ExtractResult) -> list[Edge]:
    """``tests`` edges from a test to each mounted route whose method and path its literal HTTP
    requests (``attrs.requests``) hit; ``attrs = {"via": "http", "match": "exact"|"param"}``. A test
    that drives a handler through a TestClient reaches it by URL, not by an import or a call."""
    from sdlc_graph.codegraph.extract_frontend import _segments_match, normalise_url

    routes = [s for s in merged["symbols"] if s["kind"] == "route" and s["attrs"].get("mounted", True)]
    out: list[Edge] = []
    for t in merged["symbols"]:
        if t["kind"] != "test":
            continue
        for req in t["attrs"].get("requests", []):
            url = normalise_url(req["path"])
            for r in routes:
                if r["attrs"].get("method") != req["method"]:
                    continue
                how = _segments_match(url, normalise_url(r["attrs"].get("path", "")))
                if how:
                    out.append({"src": {"kind": "test", "qualname": t["qualname"], "path": t["path"]},
                                "dst": {"kind": "route", "qualname": r["qualname"], "path": r["path"]},
                                "rel": "tests", "attrs": {"via": "http", "match": how}})
    out.sort(key=lambda e: (e["src"]["path"], e["src"]["qualname"], e["dst"]["qualname"], e["dst"]["path"]))
    return out
