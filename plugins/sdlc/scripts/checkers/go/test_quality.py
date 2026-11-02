#!/usr/bin/env python3
"""Go: a test asserts behaviour, not existence.

Heuristic, by text: each `func TestX(t *testing.T)` is brace-matched; a body with no
t.Error/t.Errorf/t.Fatal/t.Fatalf/t.Fail/assert./require./is. is no-assert. A file with tests and
no wantErr/expectErr/ErrorIs/EqualError/Panics/`err == nil {` is no-negative-case.

Output: "tests|file|test|code<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

FUNC_RE = re.compile(r"(?m)^func (Test\w+)\s*\(\s*\w+\s+\*testing\.(?:T|B)\s*\)")
ASSERT_RE = re.compile(r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b|\b(?:assert|require|is)\.\w+\(")
NEGATIVE_RE = re.compile(r"wantErr|expectErr|ErrorIs|EqualError|Panics|err\s*==\s*nil\s*\{|NotNil\(.*err|Error\(t,\s*err", re.I)


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
    tests = list(FUNC_RE.finditer(text))
    for m in tests:
        body = text[m.end():block_end(text, m.end())]
        line = text.count("\n", 0, m.start()) + 1
        if not ASSERT_RE.search(body):
            out.append((f"tests|{rel}|{m.group(1)}|no-assert", f"{rel}:{line}: {m.group(1)} reports no failure (no t.Error/t.Fatal/assert/require)"))
    if tests and not NEGATIVE_RE.search(text):
        out.append((f"tests|{rel}|-|no-negative-case", f"{rel}:1: {len(tests)} tests and no negative case (no wantErr/ErrorIs/Panics/error path)"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, ["**"], [".go"]):
        if rel.endswith("_test.go"):
            findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
