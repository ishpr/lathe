#!/usr/bin/env python3
"""Python: routers nobody mounts and modules nobody imports.

Why this exists (REC-34): billing, the scheduler, the throttle and other code
"are all present and unwired". Two checks, ast only (the project is never imported):
  <router name>   an APIRouter() assigned at module level that is never passed to include_router anywhere
                  in the app tree (aliases and package re-exports are followed)
  <module>        a module under the app roots that is not reached by imports (direct, relative, function-level,
                  importlib literals, parent packages) from the entry modules (config entry_modules,
                  default app/main.py). __init__.py, alembic, tests and conftest are not reported.
A router line, or a module's first line, ending in `# dead: ok <reason>` is skipped.

Output: "dead|<file>|<router name or dotted module><TAB>file:line: message". Exit 0 none, 1 findings, 2 misuse/crash.
Usage: dead_code.py --root DIR [--config PATH]
"""

from __future__ import annotations

import ast
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
        for rel, tree in self.trees.items():
            self._scan_imports(rel, tree)
            self._scan_routers(rel, tree)
        for rel, tree in self.trees.items():
            self._scan_includes(rel, tree)

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
                        prefix = _str(_kw(value, "prefix")) if kind == "APIRouter" else ""
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
            prefix = _str(_kw(node, "prefix")) or ""
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


def _suppressed(env, rel, line):
    try:
        lines = env.read(rel).split("\n")  # not splitlines(): it also splits on \f and U+2028, which ast line numbers do not
    except PARSE_ERRORS:
        return False
    return 0 < line <= len(lines) and "dead: ok" in lines[line - 1]


def run(env):
    ix = Index(env)
    findings = [(f"dead|{rel}|syntax", f"{rel}: cannot parse: {err}") for rel, err in ix.errors]
    mounted = ix.mounted()
    for key, r in sorted(ix.routers.items(), key=lambda kv: (kv[1]["rel"], kv[1]["line"])):
        if r["kind"] != "APIRouter" or key in mounted or _suppressed(env, r["rel"], r["line"]):
            continue
        findings.append((f"dead|{r['rel']}|{r['name']}", f"{r['rel']}:{r['line']}: APIRouter '{r['name']}' is never passed to include_router anywhere in the app tree, so its routes are not served; mount it in the app or delete it (end the line with '# dead: ok <reason>' if it is mounted dynamically)"))

    entry_cfg = env.setting("entry_modules", ["app/main.py"])
    entries = [ix.rel_mod[e.replace("\\", "/")] for e in entry_cfg if e.replace("\\", "/") in ix.rel_mod]
    if not entries:
        sys.stderr.write("dead_code: no entry module found (config entry_modules); module reachability skipped\n")
        return findings
    seen, queue = set(), list(entries)
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        seen.add(mod)
        rel = ix.mods.get(mod)
        if not rel:
            continue
        queue.extend(dep for dep in ix.touched.get(rel, ()) if dep not in seen)
        parent = mod.rpartition(".")[0]
        while parent:  # importing a.b.c runs a/__init__ and a/b/__init__
            if parent in ix.mods and parent not in seen:
                queue.append(parent)
            parent = parent.rpartition(".")[0]
    for rel, mod in sorted(ix.rel_mod.items()):
        base = rel.rsplit("/", 1)[-1]
        if mod in seen or base in ("__init__.py", "conftest.py", "__main__.py") or rel.startswith("alembic/") or "/alembic/" in rel:
            continue
        if _suppressed(env, rel, 1):
            continue
        findings.append((f"dead|{rel}|{mod}", f"{rel}:1: module '{mod}' is never imported, directly or through a package __init__, starting from {', '.join(entry_cfg)}; wire it in or delete it"))
    return findings


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    try:
        env = Env(argv)
        findings = run(env)
    except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as "clean"
        sys.stderr.write(f"dead_code: {type(exc).__name__}: {exc}\n")
        return 2
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
