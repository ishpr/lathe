"""``sdlc-graph prime``: what the SessionStart hook prints (the propulsion principle).

When the project has ``.claude/work/current.json``, print the contract, its ledger state, the one
next step and the previous run's summary, so the session's first action is that step. With no
current.json it prints nothing. It never fails the session: every error becomes a line of text and
the exit code is always 0.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _clean(s: Any, n: int = 300) -> str:
    """One line, printable characters only: the file and the ledger are data, not instructions."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(s))
    return text if len(text) <= n else text[: n - 3] + "..."


def prime_data(project_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    cur_path = root / ".claude" / "work" / "current.json"
    if not cur_path.is_file():
        return {"active": False}
    try:
        cur = json.loads(cur_path.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return {"active": True, "error": f"current.json unreadable: {exc}"}
    out: dict[str, Any] = {"active": True, "current": {k: cur.get(k) for k in
                           ("slug", "ticket", "tier", "step", "command", "allowed_paths", "frozen_at", "reviewer")
                           if cur.get(k) not in (None, "", [])}}
    slug = cur.get("node") or cur.get("slug")
    if not slug:
        return out
    try:
        from sdlc_graph import db
        from sdlc_graph.ledger import workflow

        from . import store

        with db.connect() as conn:
            wf = workflow.next_step(conn, str(slug))
            out["ledger"] = {"state": wf["state"], "next_state": wf["next_state"], "command": wf["command"],
                             "stage": wf["stage"], "model": wf["model"], "missing": wf["missing"],
                             "human_only": wf["human_only"],
                             "open_decisions": [{"id": d["id"], "question": d["question"]} for d in wf["open_decisions"]]}
            last = store.last_run(conn, str(slug))
            if last:
                out["previous_run"] = {k: last.get(k) for k in ("id", "step", "attempt", "outcome", "summary", "last_assistant_message")}
    except Exception as exc:  # noqa: BLE001 - prime never fails the session
        out["ledger_error"] = type(exc).__name__   # never the message: libpq echoes a malformed URL, password included
    return out


def render(data: dict[str, Any]) -> str:
    if not data.get("active"):
        return ""
    if data.get("error"):
        return f"[graph] {_clean(data['error'])}"
    cur = data.get("current", {})
    lines = [f"[graph] Active contract: {_clean(cur.get('slug', '?'))}" +
             (f" ({_clean(cur['ticket'], 120)})" if cur.get("ticket") else "")]
    if cur.get("tier"):
        lines.append(f"  tier: {_clean(cur['tier'])}")
    if cur.get("allowed_paths"):
        lines.append(f"  allowed paths: {_clean(', '.join(map(str, cur['allowed_paths'])), 400)}")
    led = data.get("ledger")
    if led:
        lines.append(f"  ledger state: {led['state']}" + (f" -> {led['next_state']}" if led.get("next_state") else " (done)"))
        if led.get("open_decisions"):
            lines.append("  open decisions (a human answers them): " +
                         "; ".join(f"#{d['id']} {_clean(d['question'], 120)}" for d in led["open_decisions"]))
        if cur.get("command"):
            lines.append(f"  this session's step: {_clean(cur['command'])} (from the orchestrator)")
        elif led.get("command") and not led.get("human_only"):
            lines.append(f"  next step: {led['command']} (stage {led['stage']}, model {led['model']})")
        elif led.get("human_only"):
            lines.append(f"  next step needs a human or CI ({led['next_state']}); missing: {', '.join(led['missing']) or 'nothing'}")
    elif data.get("ledger_error"):
        lines.append(f"  ledger unreachable ({_clean(data['ledger_error'], 160)}); from current.json only")
        if cur.get("command"):
            lines.append(f"  this session's step: {_clean(cur['command'])}")
    prev = data.get("previous_run")
    if prev:
        lines.append(f"  previous run #{prev['id']} ({prev.get('step')} attempt {prev.get('attempt')}): {prev.get('outcome')}")
        if prev.get("summary"):
            lines.append(f"    {_clean(prev['summary'], 400)}")
        if prev.get("last_assistant_message"):
            lines.append(f"    its last message: {_clean(prev['last_assistant_message'], 400)}")
    lines.append("  Start with that step. Record evidence through the ledger tools; the orchestrator moves the state.")
    return "\n".join(lines)


def prime(project_dir: str | Path | None = None) -> str:
    try:
        return render(prime_data(project_dir))
    except Exception as exc:  # noqa: BLE001
        return f"[graph] prime failed: {type(exc).__name__}"
