#!/usr/bin/env python3
"""PostToolUse hook for the ``Agent`` tool: write what actually ran into the ledger ``run`` row, then
append the hand-off to the work directory's ``handoffs.md``.

Stdlib only (``python3 -I hooks/record_agent_run.py``). Reads ``tool_response`` fields
``resolvedModel``, ``modelsUsed``, ``totalTokens``, ``totalDurationMs``, ``totalToolUseCount``
(the Claude Code hooks documentation) and calls the CLI: ``sdlc-graph run start`` then ``run finish <id>`` (the
PreToolUse hook does not write rows, so there is never an id to reuse). The requested model, stage
and node come from the note the guard left in ``.claude/work/spawns.json``; without it the hook
uses the spawn's tag/env/current.json and ``tool_input.model``.

Hand-off log. Once the ledger work is done (or the ledger turned out to be unavailable) the hook appends one
row to a Markdown table, so the evidence trail names who did what: the time (UTC, to the second),
``tool_input.subagent_type`` (``general-purpose`` when unset), ``tool_input.description`` (the brief's title:
pipes become ``/``, cut to 80 characters), the outcome, ``totalDurationMs`` as seconds with one decimal and
``totalToolUseCount`` (both empty when absent). The file is ``.claude/work/<slug>/handoffs.md`` when
``.claude/work/current.json`` holds a ``slug`` matching ``^[a-z0-9][a-z0-9-]{0,40}$`` and ``.claude/work/<slug>``
is a directory that is not a symlink, else ``.claude/work/handoffs.md`` (``.claude/work/`` is created when
needed). A new file starts with ``# Hand-offs`` and the table header. The append runs under an exclusive
``flock`` (parallel hand-offs finish together, so one header and every row survive) and never opens the log
through a symlink.

Guard blocks. The guard hooks log every refusal to ``.claude/work/blocks.jsonl``. When the spawn guard's note has
the hand-off's start (``at``), the records logged since then (``ts``, ``hook``, ``rule``, a detail cut to 80
characters; at most :data:`GUARD_BLOCKS_LIMIT`) go on the run as ``guard_blocks`` (in ``run.meta``), which
``metrics.snapshot`` counts as guard trips. Hand-offs that run in parallel share the window, so a block can land on
two runs; the metric counts the same rule and detail on one node once.

Never blocks: whatever goes wrong (ledger down, CLI missing, bad input, a log that cannot be written) it prints
one warning line on stderr and exits 0.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("sdlc_guard_agent_spawn", Path(__file__).resolve().parent / "guard_agent_spawn.py")
guard = importlib.util.module_from_spec(_spec)
sys.modules["sdlc_guard_agent_spawn"] = guard
_spec.loader.exec_module(guard)  # type: ignore[union-attr]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # the slug rule scripts/workfile.py applies
HANDOFF_FILE = "handoffs.md"
HANDOFF_HEADER = (
    "# Hand-offs\n"
    "\n"
    "| when (UTC) | agent | hand-off | outcome | duration | tool uses |\n"
    "|---|---|---|---|---|---|\n"
)
CELL_LIMIT = 80
GUARD_BLOCKS_LIMIT = 50
BLOCKS_FILE = "blocks.jsonl"


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


def guard_blocks(work: Path, since: Any) -> list[dict[str, Any]]:
    """The guard hooks' block records (``<work>/blocks.jsonl``) logged at or after ``since`` (an ISO time with a Z
    or an offset), oldest first: ``{"ts", "hook", "rule", "detail"}``. Empty when ``since`` is missing or unreadable,
    the log is absent or a symlink; a line that is not a record is skipped, the ``crash`` rule (a guard that failed,
    not a refusal) too."""
    if not isinstance(since, str) or not since:
        return []
    try:
        t0 = datetime.fromisoformat(since.replace("Z", "+00:00"))
        path = work / BLOCKS_FILE
        if path.is_symlink() or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (ValueError, OSError):
        return []
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
            at = datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= t0 and rec.get("rule") != "crash":
            out.append({"ts": rec["ts"], "hook": str(rec.get("hook") or ""), "rule": str(rec.get("rule") or ""),
                        "detail": str(rec.get("detail") or "")[:80]})
    return out[-GUARD_BLOCKS_LIMIT:]


def _outcome(hook_input: dict[str, Any]) -> str:
    resp = hook_input.get("tool_response")
    status = str(resp.get("status", "")).lower() if isinstance(resp, dict) else ""
    return "error" if status in {"error", "failed", "failure"} else "ok"


# --------------------------------------------------------------------------- hand-off log


def _cell(value: Any) -> str:
    """One table cell from free text: a pipe becomes ``/``, every run of whitespace (a newline too) one space,
    other control characters are dropped and the text is cut to :data:`CELL_LIMIT` characters. Anything but a
    string is empty."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value.replace("|", "/") if ch.isprintable() or ch.isspace())
    return " ".join(text.split())[:CELL_LIMIT].rstrip()


def _number(value: Any) -> float | None:
    """A finite, non-negative number from a JSON value (a numeric string counts); None for anything else."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _seconds(millis: Any) -> str:
    number = _number(millis)
    return "" if number is None else f"{number / 1000:.1f}"


def _count(value: Any) -> str:
    number = _number(value)
    return str(int(number)) if number is not None and number.is_integer() else ""


def handoff_row(hook_input: dict[str, Any]) -> str:
    """The table row for this Agent call (newline included)."""
    tool_input = hook_input.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    resp = hook_input.get("tool_response")
    resp = resp if isinstance(resp, dict) else {}
    cells = [
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        _cell(tool_input.get("subagent_type")) or "general-purpose",
        _cell(tool_input.get("description")),
        _outcome(hook_input),
        _seconds(resp.get("totalDurationMs")),
        _count(resp.get("totalToolUseCount")),
    ]
    return "| " + " | ".join(cells) + " |\n"


def handoff_path(work: Path) -> Path:
    """``<work>/<slug>/handoffs.md`` when ``current.json`` names a plain slug whose directory exists (a symlinked
    directory is skipped, never followed), else ``<work>/handoffs.md``."""
    current = guard._read_json(work / "current.json")
    slug = current.get("slug") if isinstance(current, dict) else None
    if isinstance(slug, str) and SLUG_RE.fullmatch(slug):
        folder = work / slug
        if folder.is_dir() and not folder.is_symlink():
            return folder / HANDOFF_FILE
    return work / HANDOFF_FILE


def append_handoff(path: Path, row: str) -> None:
    """Append ``row`` to ``path``, behind the title and table header when the file is new or empty. The whole
    append (header check included) holds an exclusive ``flock``, a last line that lost its newline is finished
    first, and the file is never opened through a symlink. Raises OSError when it cannot be written."""
    if path.is_symlink():
        raise OSError(f"{path} is a symbolic link, not following it")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # released when the descriptor closes
        size = os.fstat(fd).st_size
        if size == 0:
            prefix = HANDOFF_HEADER
        else:
            prefix = "" if os.pread(fd, 1, size - 1) == b"\n" else "\n"
        data = memoryview((prefix + row).encode("utf-8"))
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- the hook


def _record_run(hook_input: dict[str, Any]) -> None:
    """The ledger half: ``run start`` then ``run finish`` (or just ``run finish`` for a slot the guard reserved)."""
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

    blocks = guard_blocks(work, note.get("at"))
    if note.get("run_id") is not None:  # the guard reserved the planner slot: just close that row
        usage = usage_from(hook_input)
        if blocks:
            usage["guard_blocks"] = blocks
        code, _data, err = guard.run_cli(["run", "finish", str(note["run_id"]), "--outcome",
                                          _outcome(hook_input), "--usage", json.dumps(usage)])
        if code != 0:
            print(f"sdlc record: reserved run {note['run_id']} finish failed ({err or f'exit {code}'})", file=sys.stderr)
        return

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
    if note.get("default") is not None:
        args += ["--meta", json.dumps({"default": note.get("default"), "override": bool(note.get("override"))})]
    code, data, err = guard.run_cli(args)
    if code != 0 or not isinstance(data, dict) or "id" not in data:
        print(f"sdlc record: ledger unavailable, run not recorded ({err or data or f'exit {code}'})", file=sys.stderr)
        return
    usage = usage_from(hook_input)
    if blocks:
        usage["guard_blocks"] = blocks
    code, _data, err = guard.run_cli(["run", "finish", str(data["id"]), "--outcome", _outcome(hook_input),
                                      "--usage", json.dumps(usage)])
    if code != 0:
        print(f"sdlc record: run {data['id']} started but finish failed ({err or f'exit {code}'})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    try:
        hook_input = json.loads(sys.stdin.read())
        if not isinstance(hook_input, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        print(f"sdlc record: unreadable hook input, nothing recorded: {exc}", file=sys.stderr)
        return 0
    if hook_input.get("tool_name", "Agent") != "Agent":
        return 0
    try:
        _record_run(hook_input)
    except Exception as exc:  # noqa: BLE001 - the hook must never fail the tool call
        print(f"sdlc record: unexpected error, run not recorded: {exc}", file=sys.stderr)
    try:  # whatever the ledger said, the hand-off is still logged
        append_handoff(handoff_path(guard.work_dir(hook_input)), handoff_row(hook_input))
    except Exception as exc:  # noqa: BLE001 - a log that cannot be written must not fail the tool call either
        print(f"sdlc record: hand-off not logged: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
