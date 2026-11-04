#!/usr/bin/env python3
"""Python: no module-level mutable state on a request path.

Flags a module-level name bound to an empty dict/list/set literal, a comprehension, or a call to
dict/list/set/defaultdict/deque/OrderedDict/Counter, and any `global` statement. Skips dunders and
UPPER_CASE names bound to a non-empty literal (tables filled once at import), a non-empty set/dict/list
that nothing in the module ever mutates (a constant lookup table; consider a frozenset), and any line
ending in `# state: ok <reason>`.

Output: "state|file|name<TAB>file:line: message". A file that cannot be read or parsed is state|file|syntax.
Exit 0 none, 1 findings, 2 misuse/crash.
Usage: module_state.py --root DIR [--config PATH]
"""

from __future__ import annotations

import ast
import os
import sys
import tokenize

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

MUTABLE_CTORS = {"dict", "list", "set", "defaultdict", "deque", "OrderedDict", "Counter"}
PARSE_ERRORS = (SyntaxError, ValueError, OSError, RecursionError, MemoryError)  # UnicodeDecodeError is a ValueError
KNOWN_OPTS = ("--root", "--config")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_mutable(value):
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    return isinstance(value, ast.Call) and _name(value.func) in MUTABLE_CTORS


def _is_empty_literal(value):
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, (ast.List, ast.Set)):
        return not value.elts
    return False


def _looks_like_constant(name, value):
    if name.startswith("__") and name.endswith("__"):
        return True
    bare = name.lstrip("_")
    return bare.isupper() and not isinstance(value, ast.Call) and not _is_empty_literal(value)


MUTATORS = {"add", "append", "extend", "insert", "remove", "discard", "pop", "popitem", "clear", "update", "setdefault",
            "sort", "reverse", "appendleft", "extendleft", "popleft", "rotate", "difference_update",
            "intersection_update", "symmetric_difference_update", "__setitem__", "__delitem__"}


def _is_filled(value):
    """A non-empty literal, comprehension, or constructor call with arguments: a table, not an empty registry."""
    if isinstance(value, ast.Call):
        return bool(value.args or value.keywords)
    return not _is_empty_literal(value)


def _mutated_names(tree):
    """Names the module changes in place or rebinds after the module-level binding: .add()/.append()/...
    calls, item or attribute stores, deletes, augmented assignment, `global` rebinding."""
    out = set()

    def root_name(node):
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
            n = root_name(node.func.value)
            if n:
                out.add(n)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for t in targets:
                if isinstance(t, (ast.Subscript, ast.Attribute)) or isinstance(node, ast.AugAssign):
                    n = root_name(t)
                    if n:
                        out.add(n)
        elif isinstance(node, ast.Global):
            out.update(node.names)
    return out


def check_file(path, rel):
    with tokenize.open(path) as fh:  # honours a BOM and a PEP 263 coding line
        src = fh.read()
    lines = src.split("\n")  # not splitlines(): it also splits on \f and U+2028, which ast line numbers do not
    tree = ast.parse(src, filename=rel)
    out = []
    mutated = _mutated_names(tree)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_mutable(value) or "state: ok" in lines[node.lineno - 1]:
            continue
        kind = f"{_name(value.func)}()" if isinstance(value, ast.Call) else type(value).__name__.lower()
        for t in targets:
            if isinstance(t, ast.Name) and not _looks_like_constant(t.id, value) and not (_is_filled(value) and t.id not in mutated):
                out.append((f"state|{rel}|{t.id}", f"{rel}:{node.lineno}: module-level mutable {kind} '{t.id}' is per-process state; put it behind a store (Redis/Postgres, with a fake for tests) or end the line with '# state: ok <reason>'"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                out.append((f"state|{rel}|global:{name}", f"{rel}:{node.lineno}: 'global {name}' mutates module state at request time"))
    return out


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
    findings = []
    scanned = 0
    for rel in cl.walk_files(root, cfg["paths"].get("app", []), [".py"]):
        full = os.path.join(root, rel)
        if cl.is_test_path(cfg, rel) or not (os.path.islink(full) or os.path.isfile(full)):
            continue
        scanned += 1
        try:
            findings.extend(check_file(full, rel))
        except PARSE_ERRORS as exc:
            findings.append((f"state|{rel}|syntax", f"{rel}: cannot parse: {type(exc).__name__}: {exc}"))
    if not scanned:
        sys.stderr.write(f"module_state: no files to scan under {root} (check paths.app in .claude/sdlc.json); a clean result here means nothing\n")
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
        sys.stderr.write(f"module_state: {type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
