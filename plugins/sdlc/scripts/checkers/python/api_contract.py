#!/usr/bin/env python3
"""Python / FastAPI: every endpoint the frontend or README names must exist in the route table.

Why this exists (REC-09): the UI calls /api/workflows and
/api/api-keys, which the backend does not serve, and the README lists routes that do not exist.
The route table is built from decorators (@app.get, @router.post, api_route, add_api_route) with the router
prefix and include_router prefix, resolved statically. Calls come from frontend/**/*.{ts,tsx,js,jsx}
(fetch, axios.*, http/api/client.* with a string starting with "/", endpoint/url constants, template
literals) and from README.md lines like `GET /path`. Path parameters match any segment. Reports each
METHOD + path once. A router prefix that is a constant (settings.API_V1_STR) is resolved; one that cannot be
is matched by suffix. FastAPI's /docs, /redoc, /openapi.json, app.mount() paths and Next.js API routes count as served.
fetch() with no method: key is a GET; a `/items${qs}` tail is a query string. Optional: `--strip-prefix /api` (or config api_contract.strip_prefix) when a proxy removes
a prefix before the backend sees it.

Output: "contract|<METHOD PATH><TAB>file:line: message". Exit 0 none, 1 findings, 2 misuse/crash.
Usage: api_contract.py --root DIR [--config PATH]
"""

from __future__ import annotations

import ast
import os
import re
import tokenize
import sys

import json
import os
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402


class Env:
    """Where the checker looks: project root, app files, config, test/glob predicates."""

    def __init__(self, argv):
        opts = _opts(argv)
        # an explicit --root wins over CLAUDE_PROJECT_DIR (which project_root prefers)
        self.root = os.path.abspath(opts["--root"]) if opts.get("--root") else cl.project_root(None)
        if not os.path.isdir(self.root):
            raise ValueError(f"--root {self.root} is not a directory")
        cfg = cl.load_config(self.root)
        extra = opts.get("--config")
        if extra and os.path.isfile(extra):
            try:
                with open(extra, encoding="utf-8") as fh:
                    cfg = cl.deep_merge(cfg, json.load(fh))
            except (OSError, ValueError):
                pass
        self.cfg = cfg
        self.files = [
            rel
            for rel in cl.walk_files(self.root, cfg["paths"].get("app", []), [".py"])
            if not cl.is_test_path(cfg, rel) and _scannable(self.root, rel)
        ]
        self.opts = opts
        if not self.files:
            sys.stderr.write(f"{os.path.basename(sys.argv[0])}: no files to scan under {self.root} (check paths.app in .claude/sdlc.json); a clean result here means nothing\n")

    def setting(self, key, default):
        value = self.cfg.get(key)
        return default if value in (None, "") else value

    def match(self, rel, patterns):
        return cl.matches(rel, patterns)

    def read(self, rel):
        # tokenize.open honours a BOM and a PEP 263 coding line, like the interpreter does
        with tokenize.open(os.path.join(self.root, rel)) as fh:
            return fh.read()


def _scannable(root, rel):
    """A regular file, or a symlink (a broken one is reported rather than skipped); never a FIFO or device."""
    full = os.path.join(root, rel)
    return os.path.islink(full) or os.path.isfile(full)


PARSE_ERRORS = (SyntaxError, ValueError, OSError, RecursionError, MemoryError)  # UnicodeDecodeError is a ValueError
KNOWN_OPTS = ("--root", "--config", "--strip-prefix")


def _opts(argv):
    out = {}
    args = list(argv)
    i = 0
    while i < len(args):
        if args[i] in KNOWN_OPTS:
            if i + 1 >= len(args):
                raise ValueError(f"{args[i]} needs a value")
            out[args[i]] = args[i + 1]
            i += 2
        elif args[i].startswith("-"):
            raise ValueError(f"unknown option {args[i]} (options: {', '.join(KNOWN_OPTS)}, --help)")
        else:
            i += 1
    return out


METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
ROUTER_CTORS = {"APIRouter", "FastAPI"}
UNKNOWN = "\0"  # marks a prefix that is not a literal and could not be resolved


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _flatten(node):
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        inner = _flatten(node.value)
        return inner + [node.attr] if inner else None
    return None


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kw(call, key):
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


class Index:
    """Parsed app tree: module map, import bindings, router instances, include_router edges."""

    def __init__(self, env):
        self.env = env
        self.trees = {}
        self.errors = []
        self.mods = {}
        self.rel_mod = {}
        self.is_pkg = {}
        for rel in env.files:
            try:
                self.trees[rel] = ast.parse(env.read(rel), filename=rel)
            except PARSE_ERRORS as exc:
                self.errors.append((rel, f"{type(exc).__name__}: {exc}"))
                continue
            parts = rel[:-3].split("/")
            pkg = parts[-1] == "__init__"
            if pkg:
                parts = parts[:-1]
            if len(parts) > 1 and parts[0] == "src":
                parts = parts[1:]
                self.mods["src." + ".".join(parts)] = rel
            dotted = ".".join(parts)
            self.mods[dotted] = rel
            self.rel_mod[rel] = dotted
            self.is_pkg[rel] = pkg
        self.touched = {}
        self.bind = {}
        self.routers = {}
        self.edges = []
        self.consts = {}
        for tree in self.trees.values():
            self._scan_consts(tree)
        for rel, tree in self.trees.items():
            self._scan_imports(rel, tree)
            self._scan_routers(rel, tree)
        for rel, tree in self.trees.items():
            self._scan_includes(rel, tree)

    def _scan_consts(self, tree):
        """Module- and class-level NAME = "literal" strings, keyed by simple name (settings.API_V1_STR -> API_V1_STR)."""
        bodies = [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        for body in bodies:
            for node in body:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                else:
                    continue
                text = _str(value)
                for t in targets:
                    if isinstance(t, ast.Name) and text is not None:
                        # two different values under one name: unknown, never guess
                        self.consts[t.id] = text if self.consts.get(t.id, text) == text else None

    def const_str(self, node):
        """A string value for a literal, f-string, + concatenation or a known constant name; None if unresolvable."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, (ast.Name, ast.Attribute)):
            return self.consts.get(_name(node))
        if isinstance(node, ast.JoinedStr):
            parts = [self.const_str(p.value) if isinstance(p, ast.FormattedValue) else _str(p) for p in node.values]
            return None if any(p is None for p in parts) else "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.const_str(node.left), self.const_str(node.right)
            return None if left is None or right is None else left + right
        return None

    def prefix_of(self, call):
        """The prefix= of a router or include_router call: "" when absent, UNKNOWN when it cannot be resolved."""
        node = _kw(call, "prefix")
        if node is None:
            return ""
        value = self.const_str(node)
        return UNKNOWN if value is None else value

    def prefixes(self, dotted):
        parts = dotted.split(".")
        return [".".join(parts[:i]) for i in range(1, len(parts) + 1) if ".".join(parts[:i]) in self.mods]

    def _from_base(self, rel, node):
        if node.level == 0:
            return node.module or ""
        me = self.rel_mod[rel]
        pkg = me if self.is_pkg[rel] else me.rpartition(".")[0]
        for _ in range(node.level - 1):
            pkg = pkg.rpartition(".")[0]
        return ".".join(p for p in (pkg, node.module) if p)

    def _scan_imports(self, rel, tree):
        touched, bind = set(), {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    touched.update(self.prefixes(a.name))
                    if a.asname:
                        if a.name in self.mods:
                            bind[a.asname] = ("mod", a.name)
                    else:
                        first = a.name.split(".")[0]
                        if first in self.mods:
                            bind[first] = ("mod", first)
            elif isinstance(node, ast.ImportFrom):
                base = self._from_base(rel, node)
                touched.update(self.prefixes(base))
                for a in node.names:
                    if a.name == "*":
                        continue
                    full = f"{base}.{a.name}" if base else a.name
                    local = a.asname or a.name
                    if full in self.mods:
                        touched.add(full)
                        bind[local] = ("mod", full)
                    else:
                        bind[local] = ("sym", base, a.name)
            elif isinstance(node, ast.Call) and _name(node.func) in ("import_module", "__import__") and node.args:
                lit = _str(node.args[0])
                if lit:
                    touched.update(self.prefixes(lit))
        self.touched[rel] = touched
        self.bind[rel] = bind

    def _scan_routers(self, rel, tree):
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.Call) and _name(value.func) in ROUTER_CTORS:
                for t in targets:
                    if isinstance(t, ast.Name):
                        kind = _name(value.func)
                        prefix = self.prefix_of(value) if kind == "APIRouter" else ""
                        self.routers[(self.rel_mod[rel], t.id)] = {
                            "kind": kind, "prefix": prefix or "", "rel": rel, "line": node.lineno, "name": t.id,
                            "src_line": node.lineno,
                        }

    def sym(self, mod, name, seen=None):
        seen = seen if seen is not None else set()
        if (mod, name) in self.routers:
            return (mod, name)
        if (mod, name) in seen:
            return None
        seen.add((mod, name))
        rel = self.mods.get(mod)
        b = self.bind.get(rel, {}).get(name) if rel else None
        if b and b[0] == "sym":
            return self.sym(b[1], b[2], seen)
        return None

    def resolve(self, rel, expr):
        parts = _flatten(expr)
        if not parts:
            return None
        b = self.bind[rel].get(parts[0])
        if b is None:
            return self.sym(self.rel_mod[rel], parts[0]) if len(parts) == 1 else None
        if b[0] == "mod":
            m, rest = b[1], parts[1:]
            while rest and f"{m}.{rest[0]}" in self.mods:
                m, rest = f"{m}.{rest[0]}", rest[1:]
            return self.sym(m, rest[0]) if len(rest) == 1 else None
        return self.sym(b[1], b[2]) if len(parts) == 1 else None

    def _scan_includes(self, rel, tree):
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"):
                continue
            recv = self.resolve(rel, node.func.value)
            arg = node.args[0] if node.args else _kw(node, "router")
            prefix = self.prefix_of(node)
            target = self.resolve(rel, arg) if arg is not None else None
            if target is not None:
                self.edges.append({"recv": recv, "target": target, "prefix": prefix, "rel": rel, "line": node.lineno})
                continue
            # Unresolvable argument (loop variable, list of routers, factory): count every router
            # this file can name as mounted, so we under-report rather than raise false alarms.
            seen = set()
            for sub in ast.walk(tree):
                if isinstance(sub, (ast.Name, ast.Attribute)):
                    t = self.resolve(rel, sub)
                    if t is not None and t != recv and t not in seen:
                        seen.add(t)
                        self.edges.append({"recv": recv, "target": t, "prefix": prefix, "rel": rel, "line": node.lineno})

    def mounted(self):
        return {e["target"] for e in self.edges}

    def full_prefixes(self, key, seen=None):
        """Every URL prefix a router is reachable under; empty set when it is never included."""
        seen = seen or set()
        r = self.routers[key]
        if r["kind"] == "FastAPI":
            return {""}
        if key in seen:
            return set()
        seen = seen | {key}
        out = set()
        for e in self.edges:
            if e["target"] != key:
                continue
            bases = {""} if e["recv"] is None else self.full_prefixes(e["recv"], seen)
            out.update(b + e["prefix"] + r["prefix"] for b in bases)
        return out


JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
JS_SKIP = {"node_modules", ".next", "dist", "build", ".git", "coverage", "public", "out", ".turbo", "storybook-static", ".output", ".nuxt", "vendor"}
JS_MAX_BYTES = 1_000_000
BUILTIN_ROUTES = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]
ALL_VERBS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
HTTP = "get|post|put|patch|delete|head|options"
CLIENTS = "axios|http|api|client|apiClient|request|\\$http"
# The two alternatives never match the same character (a backslash only starts the first), so a
# string that is never closed fails in linear time instead of backtracking exponentially.
STR = r"(?P<q>['\"`])(?P<p>(?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)"
FETCH_RE = re.compile(r"\bfetch\s*\(\s*" + STR, re.S)
CLIENT_RE = re.compile(r"\b(?:" + CLIENTS + r")\.(?P<m>" + HTTP + r")\s*(?:<[^>()]*>)?\s*\(\s*" + STR, re.S)
AXIOS_CALL_RE = re.compile(r"\baxios\s*\(\s*" + STR, re.S)
CONST_RE = re.compile(r"(?<![\w$])(?P<n>[A-Za-z_$][\w$]*)\s*(?::\s*[\w<>\[\]| ]+?)?\s*=\s*" + STR)
CONST_NAME_RE = re.compile(r"(?:endpoint|url)s?$", re.I)
ASSET_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")
HTTP_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
README_RE = re.compile(r"\b(?P<m>GET|POST|PUT|PATCH|DELETE)\s+(?P<p>/[^\s`|)\]>,;]*)")


def norm(path, call=False):
    """Canonical path: leading slash, no query, no trailing slash, every parameter as {} (or {*} for a path converter).

    A parameter glued to literal text stays in its segment ({}.json). With call=True a template tail glued
    to the last segment (`/items${qs}`) is dropped: it is a query string, not part of the path.
    """
    path = re.sub(r"\$\{[^}]*\}", "{}", path.strip())
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    if not path.startswith("/"):
        path = "/" + path
    segs = []
    for s in path.split("/"):
        if re.fullmatch(r"\{[^}]*:path\}", s):
            s = "{*}"
        elif re.fullmatch(r"\{[^}]*\}|:[\w-]+|<[^>]+>|\[[^\]]+\]", s):
            s = "{}"
        else:
            s = re.sub(r"\{[^}/]*\}", "{}", s)
        segs.append(s)
    if call and len(segs) > 1 and segs[-1] != "{}" and segs[-1].endswith("{}"):
        segs[-1] = segs[-1][:-2]
    out = "/".join(segs)
    return out.rstrip("/") or "/"


def _regex(route):
    parts = []
    for seg in route.split("/")[1:]:
        if seg == "{*}":
            parts.append(".+")
        else:
            parts.append("[^/]+".join(re.escape(p) for p in seg.split("{}")))
    return re.compile("/" + "/".join(parts) if parts != [""] else "/")


def route_table(ix):
    """[(METHOD, normalised path, rel, line)] for every route on a router that is reachable from an app."""
    out = []
    for rel, tree in ix.trees.items():
        def add(recv, methods, path, line):
            key = ix.resolve(rel, recv)
            if key is None:
                return
            for base in ix.full_prefixes(key):
                # an unresolvable prefix may be empty or any path: keep both readings so we never invent a missing route
                for variant in ([base.replace(UNKNOWN, ""), base.replace(UNKNOWN, "/{x:path}")] if UNKNOWN in base else [base]):
                    for m in methods:
                        out.append((m.upper(), norm(variant + path), rel, line))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                        continue
                    attr = d.func.attr
                    path = _str(d.args[0]) if d.args else _str(_kw(d, "path"))
                    if attr in METHODS:
                        add(d.func.value, [attr], path or "", node.lineno)
                    elif attr == "api_route":
                        add(d.func.value, _methods_kw(d) or ["get"], path or "", node.lineno)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "mount" and node.args:
                mount = ix.const_str(node.args[0])  # app.mount("/static", StaticFiles(...)): everything below it is served
                if mount and mount.startswith("/"):
                    for m in ALL_VERBS:
                        out.append((m, norm(mount), rel, node.lineno))
                        out.append((m, norm(mount.rstrip("/") + "/{x:path}"), rel, node.lineno))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("add_api_route", "add_route"):
                path = _str(node.args[0]) if node.args else _str(_kw(node, "path"))
                add(node.func.value, _methods_kw(node) or ["get"], path or "", node.lineno)
    for p in BUILTIN_ROUTES:  # FastAPI serves these itself
        out.append(("GET", norm(p), "<fastapi>", 0))
    return out


def next_routes(env):
    """Next.js API routes (pages/api/**, app/api/**/route.*) are served by the frontend, not by FastAPI."""
    out = []
    base = os.path.join(env.root, "frontend")
    for d, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if x not in JS_SKIP]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")
            m = re.fullmatch(r"(?:src/)?pages/(api(?:/.+)?)\.(?:ts|tsx|js|jsx)", rel) or re.fullmatch(r"(?:src/)?app/(api(?:/.+)?)/route\.(?:ts|tsx|js|jsx)", rel)
            if not m:
                continue
            path = re.sub(r"/index$", "", m.group(1))
            path = re.sub(r"\[\.\.\.[^\]]+\]", "{x:path}", path)
            for v in ALL_VERBS:
                out.append((v, norm("/" + path), f"frontend/{rel}", 0))
    return out


def _methods_kw(call):
    v = _kw(call, "methods")
    if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
        return [_str(e) for e in v.elts if _str(e)]
    return []


def frontend_calls(env, route_roots=None):
    """[(METHOD, raw path, rel, line)] from fetch/axios calls and endpoint constants in frontend/**."""
    out = []
    consts = []
    base = os.path.join(env.root, "frontend")
    for d, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if x not in JS_SKIP]
        for f in files:
            if not f.endswith(JS_EXTS) or f.endswith((".min.js", ".d.ts")) or ".test." in f or ".spec." in f or d.endswith("__tests__"):
                continue
            full = os.path.join(d, f)
            try:
                if os.path.getsize(full) > JS_MAX_BYTES:  # a bundle, not source
                    continue
            except OSError:
                continue
            rel = os.path.relpath(full, env.root).replace(os.sep, "/")
            try:
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            line = lambda pos: text.count("\n", 0, pos) + 1  # noqa: E731
            spans = []
            for rx in (FETCH_RE, CLIENT_RE, AXIOS_CALL_RE):
                for m in rx.finditer(text):
                    p = m.group("p")
                    if not p.startswith("/") or p.startswith("//"):
                        continue
                    method = (m.groupdict().get("m") or "").upper()
                    if not method:
                        method = _options_method(text, m.end())
                    out.append((method, p, rel, line(m.start())))
                    spans.append((m.start("p"), m.end("p")))
            consts.append((text, rel, spans))
    # An endpoint/url constant counts as a call only when its first segment is one the backend serves or
    # another call already uses: that separates "/api/workflows" from "/login" or "/logo.svg" (client routes, assets).
    roots = {norm(p).split("/")[1] for _, p, _, _ in out}
    roots |= {r for r in (route_roots or ()) if r}
    for text, rel, spans in consts:
        line = lambda pos, text=text: text.count("\n", 0, pos) + 1  # noqa: E731
        for m in CONST_RE.finditer(text):
            p = m.group("p")
            if not CONST_NAME_RE.search(m.group("n")) or not re.match(r"/[A-Za-z0-9_{$-]", p) or any(a <= m.start("p") < b for a, b in spans):
                continue
            if ASSET_EXT_RE.search(p.split("?", 1)[0]) or norm(p).split("/")[1] not in roots:
                continue
            out.append(("GET", p, rel, line(m.start())))
    return out


def _options_method(text, pos):
    """HTTP verb from the first-level `method:` key of the options object right after the URL argument; GET if none."""
    rest = text[pos: pos + 2000]
    head = re.match(r"\s*,\s*\{", rest)
    if not head:
        return "GET"
    depth, top, i = 1, [], head.end()
    while i < len(rest) and depth:
        ch = rest[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif depth == 1:
            top.append(ch)
        i += 1
    mm = re.search(r"(?:^|[,\s])method\s*:\s*['\"`]([A-Za-z]+)['\"`]", "".join(top))
    verb = mm.group(1).upper() if mm else "GET"
    return verb if verb in HTTP_VERBS else "GET"


def readme_calls(env):
    out = []
    path = os.path.join(env.root, "README.md")
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, text in enumerate(fh, 1):
                for m in README_RE.finditer(text):
                    out.append((m.group("m"), m.group("p").rstrip(".:"), "README.md", n))
    except OSError:
        pass
    return out


def run(env):
    ix = Index(env)
    for rel, err in ix.errors:  # a file we could not parse may hold routes: say so rather than guess
        sys.stderr.write(f"api_contract: {rel} not parsed ({err}); routes in it are missing from the table\n")
    table = route_table(ix)
    if not ix.routers or not any(row[2] != "<fastapi>" for row in table):
        sys.stderr.write("api_contract: no FastAPI routes found in the app tree; nothing to compare\n")
        return []
    table += next_routes(env)
    strip = env.opts.get("--strip-prefix") or (env.cfg.get("api_contract") or {}).get("strip_prefix") or ""
    compiled = [(m, p, _regex(p)) for m, p, _, _ in table]
    findings, seen = [], set()
    roots = {p.split("/")[1] for _, p, _, _ in table if p.count("/") >= 1 and not p.startswith("/{")}
    for method, raw, rel, ln in frontend_calls(env, roots) + readme_calls(env):
        shown = norm(raw, call=True)
        path = shown
        if strip and path.startswith(strip.rstrip("/") + "/"):
            path = path[len(strip.rstrip("/")):]
        key = f"contract|{method} {shown}"
        if key in seen:
            continue
        matches = [m for m, p, rx in compiled if rx.fullmatch(path)]
        if method in matches or (method == "HEAD" and "GET" in matches):
            continue
        seen.add(key)
        who = "README lists" if rel == "README.md" else "the frontend calls"
        have = f"; the route table has {path} only for {', '.join(sorted(set(matches)))}" if matches else ""
        findings.append((key, f"{rel}:{ln}: {who} {method} {shown} but no matching route is served{have}; add the route, fix the path, or remove the call"))
    return findings


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    try:
        env = Env(argv)
        findings = run(env)
    except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as "clean"
        sys.stderr.write(f"api_contract: {type(exc).__name__}: {exc}\n")
        return 2
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
