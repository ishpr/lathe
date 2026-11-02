#!/usr/bin/env python3
"""Stop: app code changed without a test change gets one nudge back, never a loop.

Active when the project config and the active contract both say require_tests. Claude Code sets
stop_hook_active on the continuation; a marker file covers a version that does not send it.
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402


def main() -> int:
    data = cl.read_hook_input()
    if data is None:
        print("stop-check: hook input unreadable; test nudge skipped.")
        return 0
    root = cl.project_root(data.get("cwd"))
    marker = os.path.join(root, ".claude", "work", ".stop_nudged")
    if data.get("stop_hook_active") or os.path.exists(marker):
        try:
            os.remove(marker)
        except OSError:
            pass
        return 0
    cfg = cl.load_config(root)
    contract = cl.load_contract(root)
    if not contract or not cfg.get("require_tests") or not contract.get("require_tests", True):
        return 0
    changed = cl.changed_paths(root)
    app = [p for p in changed if cl.is_app_path(cfg, p)]
    tests = [p for p in changed if cl.is_test_path(cfg, p)]
    if app and not tests:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w", encoding="utf-8").close()
        sys.stderr.write(
            "app code changed (" + ", ".join(app[:5]) + ") and no test changed. The contract requires a test for every "
            "behaviour change: add or update the test that pins this, or write under Decisions in contract.md why none "
            "is needed. Then stop.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
