#!/usr/bin/env python3
"""Rust: no process-global mutable state on a request path.

Heuristic, by text: `static mut NAME`, a `static NAME: ...Mutex|RwLock|RefCell|OnceCell|OnceLock|Lazy<...>`,
and `lazy_static!` / `static ref NAME: Mutex|RwLock<...>`. A line ending in `// state: ok <reason>`
is skipped (use it for config loaded once and never mutated).

Output: "state|file|name<TAB>file:line: message". Exit 1 if any.
"""

from __future__ import annotations

import os
import re
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402

STATIC_MUT_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?static\s+mut\s+(\w+)")
STATIC_CELL_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?static\s+(?:ref\s+)?(\w+)\s*:\s*[^=]*\b(?:Mutex|RwLock|RefCell|OnceCell|OnceLock|Lazy|AtomicU\d+|AtomicUsize|AtomicBool|DashMap)\b")


def check_file(path, rel):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        lines = fh.read().splitlines()
    out = []
    for i, line in enumerate(lines, 1):
        if "state: ok" in line:
            continue
        m = STATIC_MUT_RE.match(line) or STATIC_CELL_RE.match(line)
        if m:
            out.append((f"state|{rel}|{m.group(1)}", f"{rel}:{i}: process-global mutable '{m.group(1)}' is per-process state; keep it in a store with a fake for tests, or end the line with '// state: ok <reason>'"))
    return out


def main(argv):
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv else None)
    cfg = cl.load_config(root)
    findings = []
    for rel in cl.walk_files(root, cfg["paths"].get("app", ["src/**"]), [".rs"]):
        findings.extend(check_file(os.path.join(root, rel), rel))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
