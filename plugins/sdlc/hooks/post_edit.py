#!/usr/bin/env python3
"""PostToolUse for Edit / Write / MultiEdit on files of the project's stack.

1. The stack's lint-fix and format commands on the file (`{file}` in .claude/sdlc.json).
2. The ratchet for this file only (checkers + lint rules vs .claude/baseline.json). New
   violations → exit 2: the edit already happened; the model gets the list as feedback.

A missing tool or a crashing command is reported on stdout and never blocks: a hook that stops
every edit gets disabled, and a disabled check is the pitfall this plugin exists to fix.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402


def main() -> int:
    data = cl.read_hook_input()
    if data is None or not isinstance(data.get("tool_input") or {}, dict):
        print("post-edit: hook input unreadable; lint and ratchet skipped for this edit.")
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not os.path.exists(path):
        return 0
    root = cl.project_root(data.get("cwd"))
    cfg = cl.load_config(root)
    rel = cl.rel_to(root, path)
    if rel.startswith("..") or not cfg["_configured"] or not cl.has_stack_extension(cfg, rel):
        return 0

    for key in ("lint_fix", "format"):
        command = (cfg.get("commands") or {}).get(key) or ""
        if "{file}" not in command:
            continue
        proc = subprocess.run(command.replace("{file}", shlex.quote(path)), shell=True, cwd=root, capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            print(f"post-edit: {key} command failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[-200:]}")

    proc = subprocess.run(
        [sys.executable, "-I", os.path.join(cl.plugin_root(), "scripts", "ratchet.py"), "--root", root, "--fast", "--files", rel],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode == 1:
        sys.stderr.write(f"New violations in {rel} (vs .claude/baseline.json):\n{proc.stdout}Fix them before moving on; the rules are in .claude/rules/ and CLAUDE.md.\n")
        return 2
    if proc.returncode > 1:
        print(f"post-edit: ratchet could not run: {proc.stderr.strip()[-300:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
