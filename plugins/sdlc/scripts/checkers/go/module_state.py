#!/usr/bin/env python3
"""Go: no package-level mutable state on a request path.

Heuristic, by text: a package-level `var name = map[...]` / `make(` / `[]T{}` / `sync.Map{}` /
`&T{}`, alone or inside a `var ( ... )` block. A line ending in `// state: ok <reason>` is skipped
(use it for registries filled in init() and read-only afterwards). Test files are skipped.

Output: "state|file|name<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

VALUE = r"(?:map\[|make\(|\[\][\w\.\*]+\{|&?sync\.(?:Map|Mutex|RWMutex)\{|&?\w+\{\s*\})"
SINGLE_RE = re.compile(r"^var\s+(\w+)\s*(?:[\w\[\]\*\.]+\s*)?=\s*" + VALUE)
BLOCK_ITEM_RE = re.compile(r"^\s+(\w+)\s*(?:[\w\[\]\*\.]+\s*)?=\s*" + VALUE)


def check_file(path, rel):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        lines = fh.read().splitlines()
    out = []
    in_block = False
    for i, line in enumerate(lines, 1):
        if "state: ok" in line:
            continue
        stripped = line.strip()
        if stripped.startswith("var ("):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        m = SINGLE_RE.match(line) or (BLOCK_ITEM_RE.match(line) if in_block else None)
        if m:
            out.append((f"state|{rel}|{m.group(1)}", f"{rel}:{i}: package-level mutable '{m.group(1)}' is per-process state; keep it in a store with a fake for tests, or end the line with '// state: ok <reason>'"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, cfg["paths"].get("app", ["**/*.go"]), [".go"]):
        if rel.endswith("_test.go"):
            continue
        findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
