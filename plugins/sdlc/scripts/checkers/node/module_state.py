#!/usr/bin/env python3
"""Node / TypeScript: no module-scope mutable state on a request path.

Heuristic, by text, column 0 only: `let x`/`var x` at module scope, or `const x = {}` / `[]` /
`new Map()` / `new Set()` / `new WeakMap()` (an empty container filled at request time). A line
ending in `// state: ok <reason>` is skipped. Test files are skipped.

Output: "state|file|name<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

LET_RE = re.compile(r"^(?:export\s+)?(?:let|var)\s+([A-Za-z_$][\w$]*)")
CONTAINER_RE = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?\s*=\s*(?:\{\s*\}|\[\s*\]|new\s+(?:Map|Set|WeakMap|WeakSet)\s*(?:<[^>]*>)?\s*\(\s*\)|Object\.create\(null\))")


def check_file(path, rel):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        lines = fh.read().splitlines()
    out = []
    for i, line in enumerate(lines, 1):
        if "state: ok" in line:
            continue
        m = CONTAINER_RE.match(line) or LET_RE.match(line)
        if m:
            what = "empty container" if CONTAINER_RE.match(line) else "mutable binding"
            out.append((f"state|{rel}|{m.group(1)}", f"{rel}:{i}: module-scope {what} '{m.group(1)}' is per-process state; keep it in a store (Redis/DB) with a fake for tests, or end the line with '// state: ok <reason>'"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, cfg["paths"].get("app", []), cfg.get("extensions") or [".ts", ".tsx", ".js"]):
        if cl.is_test_path(cfg, rel) or rel.endswith(".d.ts"):
            continue
        findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
