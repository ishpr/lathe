#!/usr/bin/env python3
"""Python / FastAPI: every route carries an auth dependency unless the public-routes file says it is public.

Protection counts when any of these holds, where <auth> is a name in config auth.dependency_names:
  - the APIRouter was built with dependencies=[Depends(<auth>)] (preferred), or was mounted with
    include_router(router, dependencies=[Depends(<auth>)]), or is included by a router that was
  - the route decorator has dependencies=[Depends(<auth>)] / [Security(<auth>)]
  - a parameter defaults to Depends(<auth>) / Security(<auth>), or is annotated
    Annotated[User, Depends(<auth>)], or with a module-level alias of such an annotation (CurrentUser)
A file that cannot be read or parsed is reported as route-auth|syntax <file>, never skipped silently.

Output: "route-auth|METHOD PATH<TAB>file:line: message". Exit 0 none, 1 findings, 2 misuse/crash.
Usage: route_auth.py --root DIR [--config PATH]
"""

from __future__ import annotations

import ast
import os
import sys
import tokenize

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
ROUTER_CTORS = {"APIRouter", "FastAPI"}
AUTH_WRAPPERS = {"Depends", "Security"}
PARSE_ERRORS = (SyntaxError, ValueError, OSError, RecursionError, MemoryError)
KNOWN_OPTS = ("--root", "--config")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _no_auth(route, fn, auth_names):
    return f"{route} ({fn}) has no auth dependency ({', '.join(sorted(auth_names)) or 'none configured'}) and is not in the public-routes file"

def _modname(rel):
    base = rel.rsplit("/", 1)[-1][:-3]
    return rel.rsplit("/", 2)[-2] if base == "__init__" and "/" in rel else base


def _is_auth_dep(node, auth_names):
    """Depends(<auth>) or Security(<auth>, scopes=...)."""
    if not (isinstance(node, ast.Call) and _name(node.func) in AUTH_WRAPPERS):
        return False
    first = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "dependency"), None)
    return first is not None and _name(first) in auth_names


def _deps_protect(call, auth_names):
    for kw in call.keywords:
        if kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
            if any(_is_auth_dep(e, auth_names) for e in kw.value.elts):
                return True
    return False


def _ann_has_auth(ann, auth_names, aliases):
    """Annotated[User, Depends(get_current_user)] or an alias of it (CurrentUser)."""
    if ann is None:
        return False
    for n in ast.walk(ann):
        if _is_auth_dep(n, auth_names) or (isinstance(n, (ast.Name, ast.Attribute)) and _name(n) in aliases):
            return True
    return False


def _assigns(body):
    for node in body:
        if isinstance(node, ast.Assign):
            yield node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            yield [node.target], node.value
        elif type(node).__name__ == "TypeAlias":  # `type CurrentUser = Annotated[...]` (3.12+)
            yield [node.name], node.value


def collect_aliases(trees, auth_names):
    """Module-level names bound to an Annotated[..., Depends(<auth>)] type, in any file (CurrentUser in deps.py)."""
    aliases = set()
    changed = True
    while changed:
        changed = False
        for tree in trees:
            for targets, value in _assigns(tree.body):
                if _ann_has_auth(value, auth_names, aliases):
                    for t in targets:
                        if isinstance(t, ast.Name) and t.id not in aliases:
                            aliases.add(t.id)
                            changed = True
    return aliases


def _bindings(tree):
    bind = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[-1]
            for a in node.names:
                bind[a.asname or a.name] = (mod, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    parts = a.name.split(".")
                    bind[a.asname] = (".".join(parts[:-1]), parts[-1])
    return bind


def _router_key(rel, bind, expr):
    """(module, variable) for `router`, `items.router` or an imported alias; None when it cannot be named."""
    if isinstance(expr, ast.Name):
        return bind.get(expr.id) or (_modname(rel), expr.id)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        b = bind.get(expr.value.id)
        return (b[1] if b else expr.value.id, expr.attr)
    return None


def collect_protected_includes(trees, auth_names):
    """Routers mounted with include_router(..., dependencies=[Depends(<auth>)]), plus everything those routers include."""
    edges = []
    for rel, tree in trees.items():
        bind = _bindings(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"):
                continue
            arg = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "router"), None)
            target = _router_key(rel, bind, arg) if arg is not None else None
            if target:
                edges.append((_router_key(rel, bind, node.func.value), target, _deps_protect(node, auth_names)))
    protected = {t for _, t, p in edges if p}
    changed = True
    while changed:
        changed = False
        for recv, target, _ in edges:
            if recv in protected and target not in protected:
                protected.add(target)
                changed = True
    return protected


def _str_kw(call, key):
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return ""


def _args_have_auth(node, auth_names, aliases):
    a = node.args
    defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
    if any(_is_auth_dep(d, auth_names) for d in defaults):
        return True
    return any(_ann_has_auth(arg.annotation, auth_names, aliases) for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))


def check_file(tree, rel, allowed, auth_names, aliases, included):
    routers = {}
    for targets, value in _assigns(tree.body):
        if isinstance(value, ast.Call) and _name(value.func) in ROUTER_CTORS:
            for t in targets:
                if isinstance(t, ast.Name):
                    routers[t.id] = (_str_kw(value, "prefix"), _deps_protect(value, auth_names) or (_modname(rel), t.id) in included)
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method, rname = dec.func.attr, _name(dec.func.value)
            if method not in METHODS or rname not in routers:
                continue
            prefix, router_protected = routers[rname]
            sub = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str) else ""
            route = f"{method.upper()} {prefix}{sub}"
            protected = router_protected or _deps_protect(dec, auth_names) or _args_have_auth(node, auth_names, aliases)
            if protected or route in allowed:
                continue
            out.append((f"route-auth|{route}", f"{rel}:{node.lineno}: {_no_auth(route, node.name, auth_names)}"))
    return out


def run_files(files, read, allowed, auth_names):
    """files: [(rel, path)]. A file that cannot be read or parsed is a finding, never a crash."""
    trees, findings = {}, []
    for rel, path in files:
        try:
            trees[rel] = ast.parse(read(path), filename=rel)
        except PARSE_ERRORS as exc:
            findings.append((f"route-auth|syntax {rel}", f"{rel}: cannot parse: {type(exc).__name__}: {exc}"))
    aliases = collect_aliases(trees.values(), auth_names)
    included = collect_protected_includes(trees, auth_names)
    for rel, tree in trees.items():
        try:
            findings.extend(check_file(tree, rel, allowed, auth_names, aliases, included))
        except PARSE_ERRORS as exc:
            findings.append((f"route-auth|syntax {rel}", f"{rel}: cannot analyse: {type(exc).__name__}: {exc}"))
    return findings


def _read(path):
    with tokenize.open(path) as fh:  # honours a BOM and a PEP 263 coding line
        return fh.read()


def load_allowlist(path):
    allowed = {}
    if not os.path.exists(path):
        return allowed
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) >= 2:
                allowed[f"{parts[0].upper()} {parts[1]}"] = parts[2] if len(parts) > 2 else ""
    return allowed


def _root_arg(argv):
    root, i = None, 0
    while i < len(argv):
        if argv[i] in KNOWN_OPTS:
            if i + 1 >= len(argv):
                raise ValueError(f"{argv[i]} needs a value")
            if argv[i] == "--root":
                root = argv[i + 1]
            i += 2
        elif argv[i].startswith("-"):
            raise ValueError(f"unknown option {argv[i]} (options: {', '.join(KNOWN_OPTS)}, --help)")
        else:
            i += 1
    return root


def run(argv):
    hint = _root_arg(argv)
    root = os.path.abspath(hint) if hint else cl.project_root(None)  # an explicit --root wins over CLAUDE_PROJECT_DIR
    if not os.path.isdir(root):
        raise ValueError(f"--root {root} is not a directory")
    cfg = cl.load_config(root)
    auth_names = set(cfg.get("auth", {}).get("dependency_names") or [])
    allowed = load_allowlist(os.path.join(root, cfg.get("auth", {}).get("public_routes_file") or ".claude/public_routes.txt"))
    files = [
        (rel, os.path.join(root, rel))
        for rel in cl.walk_files(root, cfg["paths"].get("app", []), [".py"])
        if not cl.is_test_path(cfg, rel) and (os.path.islink(os.path.join(root, rel)) or os.path.isfile(os.path.join(root, rel)))
    ]
    if not files:
        sys.stderr.write(f"route_auth: no files to scan under {root} (check paths.app in .claude/sdlc.json); a clean result here means nothing\n")
    findings = run_files(files, _read, allowed, auth_names)
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as "clean"
        sys.stderr.write(f"route_auth: {type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
