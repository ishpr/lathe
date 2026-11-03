#!/usr/bin/env python3
"""Node / TypeScript (jest, vitest, mocha): a test asserts behaviour, not existence.

Heuristic, by text: each `it(...)` / `test(...)` block is brace-matched; a block with no
`expect(` / `assert` / `.should` is no-assert; a block whose only expectations are
`toBeDefined()` / `toBeTruthy()` on a bare identifier or `expect(true).toBe(true)` is an
existence-assert. A file with tests and no `toThrow`, `rejects`, `.not.`, 4xx/5xx or error/fail
is no-negative-case.

Output: "tests|file|test|code<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

BLOCK_RE = re.compile(r"""(?m)^\s*(?:it|test)(?:\.(?:only|skip|each\([^)]*\)))?\s*\(\s*(['"`])((?:(?!\1).)*)\1""")
EXPECT_RE = re.compile(r"\bexpect\s*\(|\bassert\b|\.should\b|\bexpectTypeOf\b")
EXIST_RE = re.compile(r"expect\s*\(\s*(?:true|[A-Za-z_$][\w$]*)\s*\)\s*\.(?:toBeDefined|toBeTruthy|toBe\s*\(\s*true)\s*\(")
NEGATIVE_RE = re.compile(r"toThrow|rejects|\.not\.|\b4\d\d\b|\b5\d\d\b|\berror\b|\bfail", re.I)


CALLBACK_RE = re.compile(r"=>|\bfunction\b")


def body_start(text: str, start: int) -> int:
    """Index of the `{` that opens the test's callback body. The first `{` after the name is not always it: a
    Playwright test takes its fixtures by destructuring, `test("x", async ({ page }) => { ... })`, and the console's
    e2e specs were reported as twelve tests with no expect because `{ page }` was read as the body. The body brace
    follows the arrow (its parameters precede `=>`), or a `function`'s parameter list."""
    first = text.find("{", start)
    m = CALLBACK_RE.search(text, start)
    if m is None or (first >= 0 and first < m.start() and text.find(")", first) > m.start()):
        return first
    if m.group(0) == "=>":
        return text.find("{", m.end())
    paren = text.find("(", m.end())
    if paren < 0 or (first >= 0 and first < paren):
        return first
    depth = 0
    for j in range(paren, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text.find("{", j)
    return first


def block_end(text: str, start: int) -> int:
    i = body_start(text, start)
    if i < 0:
        return len(text)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def check_file(path, rel):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    out = []
    tests = list(BLOCK_RE.finditer(text))
    for m in tests:
        name = m.group(2)[:60]
        body = text[m.end():block_end(text, m.end())]
        line = text.count("\n", 0, m.start()) + 1
        expects = EXPECT_RE.findall(body)
        if not expects:
            out.append((f"tests|{rel}|{name}|no-assert", f"{rel}:{line}: '{name}' has no expect/assert"))
            continue
        exist = EXIST_RE.findall(body)
        if exist and len(exist) == len(re.findall(r"\bexpect\s*\(", body)):
            out.append((f"tests|{rel}|{name}|existence-assert", f"{rel}:{line}: '{name}' only checks that something is defined or truthy, not what it does"))
    if tests and not NEGATIVE_RE.search(text):
        out.append((f"tests|{rel}|-|no-negative-case", f"{rel}:1: {len(tests)} tests and no negative case (no toThrow/rejects/.not./4xx/5xx/error)"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, ["**"], cfg.get("extensions") or [".ts", ".tsx", ".js", ".jsx"]):
        if cl.is_test_path(cfg, rel):
            findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
