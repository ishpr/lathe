#!/usr/bin/env python3
"""PreModelSwitch / PostModelSwitch hook: log every model change in the session.

Stdlib only (``python3 -I hooks/record_model_switch.py``). The input carries ``from_model`` and
``to_model``; one JSON line goes to ``<project>/.claude/work/model_switches.jsonl`` with the event
name, both models, the session id and a UTC timestamp. Logging only (docs/DESIGN.md section 4):
it never blocks and never fails the session, so any error (bad stdin, unwritable directory) exits 0
with one stderr line.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict):
            raise ValueError("hook input is not a JSON object")
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": data.get("hook_event_name"),
            "from_model": data.get("from_model"),
            "to_model": data.get("to_model"),
            "session_id": data.get("session_id"),
        }
        path = root / ".claude" / "work" / "model_switches.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 - logging must never fail the session
        print(f"record_model_switch: not logged: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
