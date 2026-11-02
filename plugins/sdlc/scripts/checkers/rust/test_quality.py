#!/usr/bin/env python3
"""Rust: a test asserts behaviour, not existence.

Heuristic, by text: each `#[test]` (or `#[tokio::test]`) fn is brace-matched; a body with no
assert!/assert_eq!/assert_ne!/debug_assert/matches!/unwrap_err/expect_err and no #[should_panic]
is no-assert. A file with tests and no should_panic/is_err/unwrap_err/Err( is no-negative-case.

Output: "tests|file|test|code<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

TEST_RE = re.compile(r"#\[(?:\w+::)?test[^\]]*\]((?:\s*#\[[^\]]*\])*)\s*(?:async\s+)?fn\s+(\w+)")
ASSERT_RE = re.compile(r"\bassert(?:_eq|_ne)?!|\bdebug_assert|\bmatches!|unwrap_err\(|expect_err\(|should_panic")
NEGATIVE_RE = re.compile(r"should_panic|is_err\(\)|unwrap_err|expect_err|\bErr\(")


def block_end(text: str, start: int) -> int:
    i = text.find("{", start)
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
    tests = list(TEST_RE.finditer(text))
    for m in tests:
        attrs, name = m.group(1) or "", m.group(2)
        body = text[m.end():block_end(text, m.end())]
        line = text.count("\n", 0, m.start()) + 1
        if not ASSERT_RE.search(body) and "should_panic" not in attrs:
            out.append((f"tests|{rel}|{name}|no-assert", f"{rel}:{line}: {name} asserts nothing"))
    if tests and not NEGATIVE_RE.search(text):
        out.append((f"tests|{rel}|-|no-negative-case", f"{rel}:1: {len(tests)} tests and no negative case (no should_panic/is_err/Err)"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    findings = []
    for rel in cl.walk_files(root, ["**"], [".rs"]):
        findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
