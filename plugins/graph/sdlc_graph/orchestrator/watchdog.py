"""The watchdog patrol (Gas Town's Witness): dead processes, stalls and finished sessions.

Each tick, for every open orchestrated run:

* process gone -> the session ended: run the gates (no result file = ``crashed``) and finish the run;
* alive but no progress (new evidence or transition on the node, or new output in the session's
  log) for ``stall_minutes`` -> mark the run warned; still no progress one more window later ->
  stop the session's process group and finish the run as ``stalled``. Thresholds are generous on
  purpose: a false "stuck" is worse than a slow one, and nothing is killed without one retry window.

The watchdog observes; it never gates completion. Completion is the gates' job (:mod:`.gates`).
A failed run is retried by the loop with a fresh session; the third failure on one step becomes a P1
decision. Worktrees are never deleted here.
"""

from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psycopg

from . import gates, store
from .config import Config
from .dispatch import CHILDREN, process_identity


def is_executor(pid: int | None, run: dict[str, Any] | None) -> bool:
    """Whether ``pid`` is still the executor this run started, not an unrelated process that was given
    the number after a restart or reboot. Checked against the start time recorded at dispatch and the
    run's ``--session-id`` on the command line. Our own children need no check."""
    if not pid:
        return False
    if pid in CHILDREN or run is None:
        return True
    ident = process_identity(int(pid))
    if ident is None:
        return False
    started, command = ident
    meta = run.get("meta") or {}
    if meta.get("pid_started") and meta["pid_started"] != started:
        return False
    if run.get("session_id") and str(run["session_id"]) not in command:
        return False
    return True


def alive(pid: int | None, run: dict[str, Any] | None = None) -> bool:
    """Whether the executor process still runs (reaps it when it is our child). With ``run`` the process
    must also be that run's executor (:func:`is_executor`); a recycled pid counts as dead."""
    if not pid:
        return False
    proc = CHILDREN.get(pid)
    if proc is not None:
        if proc.poll() is None:
            return True
        CHILDREN.pop(pid, None)
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return is_executor(pid, run)
    return is_executor(pid, run)


def stop(pid: int, grace: float = 5.0, run: dict[str, Any] | None = None) -> None:
    """SIGTERM the executor's process group, SIGKILL after ``grace`` seconds. With ``run`` nothing is
    signalled unless ``pid`` still passes the identity check."""
    if run is not None and not is_executor(pid, run):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not alive(pid):
                return
            time.sleep(0.05)


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def last_progress(conn: psycopg.Connection, run: dict[str, Any]) -> dt.datetime:
    started = _aware(dt.datetime.fromisoformat(run["started_at"]))
    marks = [started]
    p = store.progress_at(conn, run["slug"])
    if p is not None:
        marks.append(_aware(p))
    for key in ("log_path",):
        path = run.get(key)
        if path and Path(path).exists():
            marks.append(dt.datetime.fromtimestamp(Path(path).stat().st_mtime, dt.timezone.utc))
    return max(marks)


def finish(conn: psycopg.Connection, run: dict[str, Any], verdict: gates.Verdict) -> dict[str, Any]:
    result = verdict.result or {}
    usage = {"turns": result.get("num_turns"), "cost_usd": result.get("total_cost_usd")}
    return store.finish_run(conn, run["id"], verdict.outcome, usage=usage,
                            summary=verdict.summary(run["step"], int(run.get("attempt") or 1)),
                            last_message=result.get("last_assistant_message") or None,
                            meta={"reasons": verdict.reasons, "reset_from": verdict.reset_from,
                                  "exit_code": result.get("exit_code"), "subtype": result.get("subtype"),
                                  "conformance": verdict.conformance, "evidence_ids": verdict.evidence_ids,
                                  "denials": verdict.denials,
                                  "assumptions": len((verdict.assumptions or {}).get("assumptions") or [])})


def patrol(conn: psycopg.Connection, cfg: Config, run: dict[str, Any]) -> dict[str, Any]:
    """Check one open run. Returns ``{"status": running|warned|finished|stalled, "run", "verdict"?}``."""
    pid = run.get("pid")
    if not alive(pid, run):
        verdict = gates.evaluate(conn, cfg, run, crashed=gates.read_result(run) is None)
        return {"status": "finished", "run": finish(conn, run, verdict), "verdict": verdict}

    now = _aware(store.db_now(conn))
    window = dt.timedelta(minutes=cfg.stall_minutes)
    progress = last_progress(conn, run)
    meta = run.get("meta") or {}
    warned_at = meta.get("stall_warned_at")
    if now - progress <= window:
        if warned_at:
            store.update_run(conn, run["id"], meta={"stall_warned_at": None, "stall_cleared_at": now.isoformat()})
        return {"status": "running", "run": run}
    if not warned_at:
        store.update_run(conn, run["id"], meta={"stall_warned_at": now.isoformat()})
        return {"status": "warned", "run": run}
    warned = _aware(dt.datetime.fromisoformat(warned_at))
    if now - warned <= window or progress > warned:
        return {"status": "warned", "run": run}
    stop(int(pid), run=run)
    idle = int((now - progress).total_seconds())
    verdict = gates.Verdict("stalled", [f"no evidence, transition or log output for {idle}s (two {cfg.stall_minutes}-minute windows); session stopped"],
                            result=gates.read_result(run))
    return {"status": "stalled", "run": finish(conn, run, verdict), "verdict": verdict}
