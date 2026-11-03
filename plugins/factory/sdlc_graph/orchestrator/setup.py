"""Worktree setup: install a repository's dependencies in a contract's fresh worktree before a session runs.

The orchestrator makes a git worktree per contract, so a node, python or other project has no
``node_modules`` or ``.venv`` there and every test command would fail until they are installed. The
repository declares the install in ``.claude/sdlc.json``::

    {"commands": {"setup": "pnpm install --frozen-lockfile"}}

``ensure`` is called by ``dispatch`` before every step, which also covers a worktree the loop has just reset
(a reset leaves ignored directories alone, but the commit it returns to may carry another lockfile):

* the key missing or empty: nothing runs, nothing is recorded;
* ``.claude/work/orchestrator/setup.ok`` in the worktree holds the lockfile's sha256 and the command of the last
  successful setup: the same lockfile and command are not set up again;
* otherwise the command runs as a plain subprocess (no Claude session, no shell: the line is split like a shell
  would; a chain needs a script) in the worktree, with the engineer's ``PATH`` as ``config.session_path`` cleans it
  and the same allowlisted environment a session gets, a ``Config.setup_timeout`` (20 minutes) limit and stdout and
  stderr appended to ``<log root>/<slug>/setup.log``;
* a run row of step ``setup`` (executor ``local``, no model) records the exit code and the duration;
* a failure (non-zero exit, timeout, command not found) ends the run with outcome ``setup_failed`` and opens a P1
  decision quoting the last 20 log lines; the contract is not dispatched until a human answers ``retry`` or ``skip``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psycopg

from . import store
from .config import Config, clean_env

OUTCOME_FAILED = "setup_failed"
MARKER = Path(".claude") / "work" / "orchestrator" / "setup.ok"
#: Lockfiles, first match wins. The hash of the file in the worktree decides whether setup runs again.
LOCKFILES = ("pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb", "package-lock.json", "npm-shrinkwrap.json",
             "uv.lock", "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock")
TAIL_LINES = 20


def setup_command(repo: Path, wt: Path | None = None) -> str | None:
    """``commands.setup`` from the repository's ``.claude/sdlc.json`` (the source checkout, which a session cannot
    edit; the worktree's copy only when the checkout has none), or None when the key is missing, not a string, or
    blank."""
    for root in (repo, wt):
        if root is None:
            continue
        try:
            data = json.loads((Path(root) / ".claude" / "sdlc.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cmds = data.get("commands") if isinstance(data, dict) else None
        if isinstance(cmds, dict):
            cmd = cmds.get("setup")
            return cmd.strip() if isinstance(cmd, str) and cmd.strip() else None
    return None


def lockfile_state(wt: Path) -> tuple[str | None, str]:
    """``(lockfile name, sha256)`` of the first lockfile in the worktree; ``(None, "")`` when it has none."""
    for name in LOCKFILES:
        p = Path(wt) / name
        if p.is_file():
            return name, hashlib.sha256(p.read_bytes()).hexdigest()
    return None, ""


def is_done(wt: Path, command: str) -> bool:
    """The marker records a successful setup of this command on the lockfile the worktree has now."""
    try:
        marker = json.loads((Path(wt) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    name, digest = lockfile_state(wt)
    return (isinstance(marker, dict) and marker.get("command") == command and marker.get("lockfile") == name
            and marker.get("sha256") == digest)


def _write_marker(wt: Path, command: str) -> None:
    name, digest = lockfile_state(wt)
    path = Path(wt) / MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"command": command, "lockfile": name, "sha256": digest}, indent=2) + "\n")


def _tail(path: Path, n: int = TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _run(command: str, wt: Path, log: Path, timeout: float) -> tuple[int, str | None]:
    """Run ``command`` in ``wt`` writing to ``log``. Returns ``(exit code, problem)``; a timeout kills the process group.

    No shell is involved: the command is split into words. ``&&`` is the one operator understood, so a setup can
    chain steps (``pnpm install --frozen-lockfile && pnpm exec next typegen``) and stops at the first that fails; the
    whole chain shares the timeout. The console's setup needed a second step and a package script in the worktree
    was not there until main was merged in."""
    steps = [s.strip() for s in command.split("&&")]
    if not command.strip() or not all(steps):
        return 2, "empty setup command"
    argvs = []
    for step in steps:
        try:
            argv = shlex.split(step)
        except ValueError as exc:
            return 2, f"cannot parse the setup command: {exc}"
        if not argv:
            return 2, "empty setup command"
        argvs.append(argv)
    env = clean_env(worktree=wt)
    deadline = time.monotonic() + timeout
    with open(log, "ab") as out:
        for argv, step in zip(argvs, steps):
            out.write(f"$ {step}\n".encode())
            out.flush()
            try:
                proc = subprocess.Popen(argv, cwd=wt, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as exc:
                out.write(f"could not start {argv[0]}: {exc}\n".encode())
                return 127, f"could not start {argv[0]}: {exc}"
            try:
                code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait()
                out.write(f"\nsetup timed out after {timeout:g} seconds\n".encode())
                return 124, f"timed out after {timeout:g} seconds"
            if code != 0:
                return code, None
        return 0, None


def ensure(conn: psycopg.Connection, cfg: Config, slug: str, wt: Path, *, feature: str | None = None,
           attempt: int = 1) -> dict[str, Any] | None:
    """Make sure the worktree has its dependencies. Returns None when nothing had to run or setup succeeded
    (the successful run row is still recorded), or the finished ``setup_failed`` run row after opening the P1
    decision. Never raises for a failing command."""
    command = setup_command(cfg.repo, wt)
    if not command or is_done(wt, command):
        return None
    logdir = Path(cfg.log_root) / slug
    logdir.mkdir(parents=True, exist_ok=True)
    log = logdir / "setup.log"
    lock_name, digest = lockfile_state(wt)
    run = store.start_run(conn, slug, step="setup", stage=None, model="none", attempt=attempt, executor="local",
                          meta={"worktree": str(wt), "command": command, "lockfile": lock_name, "lockfile_sha256": digest,
                                "feature": feature, "log": str(log)})
    rid = run["id"]
    t0 = time.monotonic()
    code, problem = _run(command, wt, log, cfg.setup_timeout)
    seconds = round(time.monotonic() - t0, 3)
    ok = code == 0 and problem is None
    meta = {"exit_code": code, "duration_seconds": seconds, "log": str(log)}
    if ok:
        _write_marker(wt, command)
        store.finish_run(conn, rid, "ok", usage={"total_duration_ms": int(seconds * 1000)}, meta=meta,
                         summary=f"setup: {command} exited 0 in {seconds:g}s")
    if ok:
        return None
    tail = _tail(log)
    summary = f"setup failed: {command} {problem or f'exited {code}'}"
    done = store.finish_run(conn, rid, OUTCOME_FAILED, usage={"total_duration_ms": int(seconds * 1000)}, meta=meta,
                            summary=summary + ("\n" + tail if tail else ""))
    store.escalate(conn, slug, key=f"{slug}:setup:failed:{rid}", severity="P1",
                   question=(f"Setting up the worktree {wt} failed (`{command}` {problem or f'exited {code}'}, run #{rid}). "
                             f"Last {TAIL_LINES} lines of {log}:\n{tail}\n"
                             f"Fix the cause (network, registry access, the lockfile), then answer retry; or skip the contract."),
                   meta={"step": "setup", "kind": OUTCOME_FAILED, "run_id": rid, "exit_code": code, "worktree": str(wt)})
    return done
