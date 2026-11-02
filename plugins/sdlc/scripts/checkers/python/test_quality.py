#!/usr/bin/env python3
"""Python / pytest: a test asserts behaviour, not existence.

Per test: no-assert, existence-assert (`assert <imported or defined name>`), constant-assert.
Per file: no-negative-case (no pytest.raises, no 4xx/5xx status, no `assert not`).

Output: "tests|file|test|code<TAB>file:line: message". Exit 1 if any.
Usage: test_quality.py --root DIR
"""

from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def check_file(path, rel):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=rel)
    known = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            known |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            known |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(node.name)
    negative = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name(node.func) == "raises":
            negative = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool) and 400 <= node.value <= 599:
            negative = True
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
            negative = True
    tests = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
    out = []
    for fn in tests:
        asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _name(n.func) == "raises"]
        if not asserts and not raises:
            out.append((f"tests|{rel}|{fn.name}|no-assert", f"{rel}:{fn.lineno}: {fn.name} asserts nothing"))
        for a in asserts:
            t = a.test
            if isinstance(t, ast.Constant):
                out.append((f"tests|{rel}|{fn.name}|constant-assert", f"{rel}:{a.lineno}: {fn.name} asserts a constant"))
            elif isinstance(t, ast.Name) and t.id in known:
                out.append((f"tests|{rel}|{fn.name}|existence-assert", f"{rel}:{a.lineno}: {fn.name} asserts that '{t.id}' exists, not what it does"))
            elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in known:
                out.append((f"tests|{rel}|{fn.name}|existence-assert", f"{rel}:{a.lineno}: {fn.name} asserts that '{t.value.id}.{t.attr}' exists, not what it does"))
    if tests and not negative:
        out.append((f"tests|{rel}|-|no-negative-case", f"{rel}:1: {len(tests)} tests and no negative case (no pytest.raises, no 4xx/5xx status, no 'assert not')"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, ["**"], [".py"]):
        base = rel.rsplit("/", 1)[-1]
        if not cl.is_test_path(cfg, rel) or not (base.startswith("test_") or base.endswith("_test.py")):
            continue
        try:
            findings.extend(check_file(os.path.join(root, rel), rel))
        except SyntaxError as exc:
            findings.append((f"tests|{rel}|-|syntax", f"{rel}: cannot parse: {exc}"))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
