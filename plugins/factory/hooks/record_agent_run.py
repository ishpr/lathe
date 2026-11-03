#!/usr/bin/env python3
"""PostToolUse hook for the ``Agent`` tool: write what actually ran into the ledger ``run`` row.

Stdlib only (``python3 -I hooks/record_agent_run.py``). Reads ``tool_response`` fields
``resolvedModel``, ``modelsUsed``, ``totalTokens``, ``totalDurationMs``, ``totalToolUseCount``
(the Claude Code hooks documentation) and calls the CLI: ``sdlc-graph run start`` then ``run finish <id>`` (the
PreToolUse hook does not write rows, so there is never an id to reuse). The requested model, stage
and node come from the note the guard left in ``.claude/work/spawns.json``; without it the hook
uses the spawn's tag/env/current.json and ``tool_input.model``.

Never blocks: whatever goes wrong (ledger down, CLI missing, bad input) it prints a warning on
stderr and exits 0.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("sdlc_guard_agent_spawn", Path(__file__).resolve().parent / "guard_agent_spawn.py")
guard = importlib.util.module_from_spec(_spec)
sys.modules["sdlc_guard_agent_spawn"] = guard
_spec.loader.exec_module(guard)  # type: ignore[union-attr]


def usage_from(hook_input: dict[str, Any]) -> dict[str, Any]:
    """Map the PostToolUse payload to ``run finish --usage`` keys (absent values are omitted).
    ``totalTokens`` has no in/out split, so it is stored as ``total_tokens`` (lands in run.meta)."""
    resp = hook_input.get("tool_response")
    if not isinstance(resp, dict):
        return {}
    out: dict[str, Any] = {}
    if resp.get("resolvedModel"):
        out["resolved_model"] = resp["resolvedModel"]
    if resp.get("modelsUsed"):
        out["models_used"] = resp["modelsUsed"]
    if resp.get("totalTokens") is not None:
        out["total_tokens"] = resp["totalTokens"]
    if resp.get("totalDurationMs") is not None:
        out["total_duration_ms"] = resp["totalDurationMs"]
    if resp.get("totalToolUseCount") is not None:
        out["tool_use_count"] = resp["totalToolUseCount"]
    return out


def _outcome(hook_input: dict[str, Any]) -> str:
    resp = hook_input.get("tool_response")
    status = str(resp.get("status", "")).lower() if isinstance(resp, dict) else ""
    return "error" if status in {"error", "failed", "failure"} else "ok"


def main(argv: list[str] | None = None) -> int:
    try:
        hook_input = json.loads(sys.stdin.read())
        if not isinstance(hook_input, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        print(f"sdlc record: unreadable hook input, nothing recorded: {exc}", file=sys.stderr)
        return 0
    try:
        if hook_input.get("tool_name", "Agent") != "Agent":
            return 0
        tool_input = hook_input.get("tool_input") or {}
        work = guard.work_dir(hook_input)
        tid = hook_input.get("tool_use_id")
        spawns = guard._read_json(work / "spawns.json")
        note = spawns.get(tid) if isinstance(spawns, dict) and tid else None
        if not isinstance(note, dict):
            stage, node = guard.parse_tag(tool_input, work)
            note = {"stage": stage, "node": node, "requested_model": tool_input.get("model"),
                    "agent_type": tool_input.get("subagent_type")}
        requested = note.get("requested_model") or tool_input.get("model") or "unspecified"

        if note.get("run_id") is not None:  # the guard reserved the planner slot: just close that row
            usage = usage_from(hook_input)
            if note.get("rewritten"):
                usage["rewritten_to"] = note.get("model")
                usage["requested_model"] = note.get("requested_model")
            code, _data, err = guard.run_cli(["run", "finish", str(note["run_id"]), "--outcome",
                                              _outcome(hook_input), "--usage", json.dumps(usage)])
            if code != 0:
                print(f"sdlc record: reserved run {note['run_id']} finish failed ({err or f'exit {code}'})", file=sys.stderr)
            return 0

        # --record-only: the spawn already ran, so record it even when the epic's Fable budget is
        # spent (the PreToolUse guard is where a planner spawn is refused).
        args = ["run", "start", "--record-only", "--model", str(requested)]
        if note.get("agent_type"):
            args += ["--agent-type", str(note["agent_type"])]
        if note.get("stage"):
            args += ["--stage", str(note["stage"])]
        if note.get("node"):
            args += ["--node", str(note["node"])]
        if tid:
            args += ["--tool-use-id", str(tid)]
        code, data, err = guard.run_cli(args)
        if code != 0 or not isinstance(data, dict) or "id" not in data:
            print(f"sdlc record: ledger unavailable, run not recorded ({err or data or f'exit {code}'})", file=sys.stderr)
            return 0
        usage = usage_from(hook_input)
        if note.get("rewritten"):
            usage["rewritten_to"] = note.get("model")
        code, _data, err = guard.run_cli(["run", "finish", str(data["id"]), "--outcome", _outcome(hook_input),
                                          "--usage", json.dumps(usage)])
        if code != 0:
            print(f"sdlc record: run {data['id']} started but finish failed ({err or f'exit {code}'})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - the hook must never fail the tool call
        print(f"sdlc record: unexpected error, run not recorded: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
