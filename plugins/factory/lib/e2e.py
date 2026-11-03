"""The app an end-to-end suite drives, started by the gate itself from the `e2e` block of .claude/sdlc.json.

Why: Playwright's `webServer` starts the project's dev server (`next dev` and the like), and a dev server with a file
watcher dies inside Claude Code's sandbox (macOS: "Turbopack: unable to start FSEvent stream"), so an e2e spec could
neither fail first in the tests step nor pass in the gate; the console's first e2e contract stopped there with an
escalation. The built app has no watcher: `next build` then `next start` run inside the sandbox (probed on Next 16.4
with Playwright 1.64: the build took 8 s, the server answered in 2 s, Chromium launched), and Playwright's own
`reuseExistingServer: true` then finds the url taken and starts no dev server of its own.

The block (init writes it for a Node repo that has a Playwright config; a human edits it):

  "e2e": {"command": "pnpm test:e2e", "build": "pnpm build", "serve": "pnpm start", "url": "http://localhost:3100",
          "ready_timeout": 120, "build_timeout": 600, "timeout": 900}

`command` runs the suite (the caller narrows it to files and asks for `--reporter=list,json`, with the JSON report in
PLAYWRIGHT_JSON_OUTPUT_FILE). `build` runs once before `serve` (optional). `serve` is the long-running command that
listens at `url`; it runs in its own process group and is stopped with it afterwards. `url` is where the suite expects
the app; any HTTP answer counts as listening. The timeouts are seconds. Without `url` and `serve` nothing is managed and
Playwright's own webServer applies (its failure is then read from the output as a wrong reason).

Ownership: when something already listens at `url` it is reused only when the listening process runs from this project
(its working directory is the project root or below), the way a developer's own dev server is. A port held by another
checkout fails the step with its pid and directory, because a suite run against someone else's server grades the wrong
code; a holder whose directory cannot be read is treated the same (fail closed). Standard library only; imported by
scripts/tests_failing_first.py and scripts/verify.py.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_TIMEOUTS = {"ready_timeout": 120, "build_timeout": 600, "timeout": 900}
KEYS = ("command", "build", "serve", "url", *DEFAULT_TIMEOUTS)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


class E2EError(RuntimeError):
    """The app could not be made to listen, or the block is unusable; the message says what to do."""


def problems(block) -> list[str]:
    """Why an `e2e` block cannot be used (sdlc.py reports these as config problems); empty when it can."""
    if not isinstance(block, dict):
        return ["e2e must be an object (command, build, serve, url, ready_timeout, build_timeout, timeout)"]
    out = []
    for key in ("command", "build", "serve", "url"):
        if key in block and not isinstance(block[key], str):
            out.append(f"e2e.{key} must be a string")
    for key in DEFAULT_TIMEOUTS:
        if key in block and not (isinstance(block[key], int) and not isinstance(block[key], bool) and block[key] > 0):
            out.append(f"e2e.{key} must be a positive number of seconds")
    url = block.get("url")
    if isinstance(url, str) and url.strip():
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            out.append("e2e.url must be an http(s) URL such as http://localhost:3100")
    if isinstance(block.get("serve"), str) and block["serve"].strip() and not (isinstance(url, str) and url.strip()):
        out.append("e2e.serve needs e2e.url (where the served app answers)")
    return out


def settings(cfg: dict) -> dict | None:
    """The normalised block, or None when the project runs no end-to-end suite (`commands.e2e` alone names the command)."""
    raw_block, raw_commands = cfg.get("e2e"), cfg.get("commands")
    block: dict = raw_block if isinstance(raw_block, dict) else {}
    commands: dict = raw_commands if isinstance(raw_commands, dict) else {}
    command = str(block.get("command") or commands.get("e2e") or "").strip()
    if not command:
        return None
    out: dict = {"command": command, "build": str(block.get("build") or "").strip(), "serve": str(block.get("serve") or "").strip(),
                 "url": str(block.get("url") or "").strip()}
    for key, default in DEFAULT_TIMEOUTS.items():
        value = block.get(key)
        out[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else default
    return out


def port_of(url: str) -> int:
    parts = urlsplit(url)
    return parts.port or (443 if parts.scheme == "https" else 80)


def listening(url: str, timeout: float = 2.0) -> bool:
    """True when something answers HTTP at `url` (any status, a 500 included); False when nothing accepts the connection."""
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="GET", headers={"User-Agent": "lathe-e2e-gate"}), timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 or proc.stdout else ""


def holder(port: int) -> tuple[int, str | None] | None:
    """(pid, working directory) of the process listening on a local TCP port, None when it cannot be found. lsof serves
    macOS and Linux alike; on Linux /proc gives the directory without it."""
    pids = [int(p) for p in _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split() if p.strip().isdigit()]
    if not pids:
        return None
    pid = pids[0]
    cwd: str | None = None
    proc_cwd = f"/proc/{pid}/cwd"
    if os.path.exists(proc_cwd):
        try:
            cwd = os.readlink(proc_cwd)
        except OSError:
            cwd = None
    if cwd is None:
        for line in _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines():
            if line.startswith("n") and len(line) > 1:
                cwd = line[1:]
                break
    return pid, cwd


def owned(cwd: str | None, root: str) -> bool:
    """True when `cwd` is the project root or below it (symlinks resolved on both sides)."""
    if not cwd:
        return False
    top = os.path.realpath(root)
    here = os.path.realpath(cwd)
    return here == top or here.startswith(top + os.sep)


def _tail(text: str, n: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def _read_tail(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return _tail(fh.read())
    except OSError:
        return ""


def _kill_group(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


class Lease:
    """A running app for the duration of one suite: ours (stop it afterwards) or a developer's (leave it alone)."""

    def __init__(self, url: str, reused: bool, pid: int | None, proc: subprocess.Popen | None = None,
                 log: str | None = None, built: bool = False, log_handle=None) -> None:
        self.url = url
        self.reused = reused
        self.pid = pid
        self.proc = proc
        self.log = log
        self.built = built
        self._log_handle = log_handle

    @property
    def server(self) -> str:
        if not self.url:
            return "none"
        return "reused" if self.reused else "started"

    def stop(self) -> None:
        if self.proc is not None:
            _kill_group(self.proc)
            self.proc = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        if self.log and os.path.exists(self.log):
            try:
                os.remove(self.log)
            except OSError:
                pass


def _env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONSAFEPATH", None)  # the project's commands run as configured, not under -I
    env.setdefault("CI", "1")
    return env


def start(root: str, e2e: dict, say=lambda line: None) -> Lease:
    """Make the app listen at e2e.url: reuse this checkout's own running server, else build and serve. `say` gets one
    line per step. Raises E2EError with the reason and what to do when it cannot."""
    url = e2e.get("url") or ""
    if not url:
        return Lease(url="", reused=True, pid=None)
    if listening(url):
        found = holder(port_of(url))
        if found is None:
            raise E2EError(f"{url} answers but the process listening there cannot be identified (lsof found none); stop it, "
                           "or point e2e.url at a free port, and rerun")
        pid, cwd = found
        if owned(cwd, root):
            say(f"e2e server: reusing pid {pid} already listening at {url} (started from this checkout)")
            return Lease(url, True, pid)
        raise E2EError(f"{url} is held by pid {pid} running from {cwd or 'a directory that cannot be read'}, not this checkout: "
                       "a suite run against another checkout's server grades the wrong code. Stop it, or point e2e.url at a "
                       "free port, and rerun")
    if not e2e.get("serve"):
        raise E2EError(f"nothing listens at {url} and e2e.serve is not set in .claude/sdlc.json: set e2e.build and e2e.serve "
                       "(the gate builds once and serves the built app itself; a dev server with a file watcher does not run "
                       f"inside the sandbox), or start the app at {url} from this checkout and rerun")
    env = _env()
    if e2e.get("build"):
        say(f"e2e build: {e2e['build']}")
        try:
            proc = subprocess.run(e2e["build"], shell=True, cwd=root, capture_output=True, text=True, env=env,
                                  timeout=e2e["build_timeout"])
        except subprocess.TimeoutExpired:
            raise E2EError(f"e2e build ran longer than {e2e['build_timeout']} s: {e2e['build']}") from None
        if proc.returncode != 0:
            raise E2EError(f"e2e build failed (exit {proc.returncode}): {e2e['build']}\n{_tail(proc.stdout + proc.stderr)}")
    log = tempfile.NamedTemporaryFile("w+", prefix="e2e-server-", suffix=".log", delete=False, encoding="utf-8")
    say(f"e2e serve: {e2e['serve']} -> {url}")
    try:
        server = subprocess.Popen(e2e["serve"], shell=True, cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  env=env, start_new_session=True)
    except OSError as exc:
        log.close()
        raise E2EError(f"e2e serve could not start: {exc}") from None
    lease = Lease(url, False, server.pid, server, log.name, built=bool(e2e.get("build")), log_handle=log)
    deadline = time.monotonic() + e2e["ready_timeout"]
    while time.monotonic() < deadline:
        if listening(url, timeout=1.0):
            say(f"e2e server: ready at {url} (pid {server.pid})")
            return lease
        if server.poll() is not None:
            tail = _read_tail(log.name)
            lease.stop()
            raise E2EError(f"e2e serve exited with {server.returncode} before {url} answered: {e2e['serve']}\n{tail}")
        time.sleep(0.5)
    tail = _read_tail(log.name)
    lease.stop()
    raise E2EError(f"e2e serve did not answer at {url} within {e2e['ready_timeout']} s: {e2e['serve']}\n{tail}")


def with_args(command: str, args: list[str]) -> str:
    """`command` with `args` appended for the suite runner; npm needs `--` before arguments meant for the script."""
    quoted = " ".join(shlex.quote(a) for a in args)
    if not quoted:
        return command
    if re.search(r"\bnpm\s+(?:run\s+)?\S+", command) and " -- " not in f" {command} ":
        return f"{command} -- {quoted}"
    return f"{command} {quoted}"


def run_suite(root: str, e2e: dict, files: list[str], timeout: int | None = None) -> tuple[subprocess.CompletedProcess, dict | None, str, str]:
    """Run the suite (narrowed to `files` when given) with `--reporter=list,json`, the JSON report written to a file.
    Returns (process, the report or None, the command, the report path). NO_COLOR is dropped: node warns when both it
    and FORCE_COLOR are set, and Playwright honours FORCE_COLOR=0."""
    import json

    report_dir = tempfile.mkdtemp(prefix="e2e-report-")
    report_path = os.path.join(report_dir, "report.json")
    command = with_args(e2e["command"], ["--reporter=list,json", *files])
    env = _env()
    env.pop("NO_COLOR", None)
    env.update(CI="1", FORCE_COLOR="0", PLAYWRIGHT_JSON_OUTPUT_FILE=report_path)
    try:
        proc = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, env=env, timeout=timeout or e2e["timeout"])
    except subprocess.TimeoutExpired:
        raise E2EError(f"the e2e command ran longer than {timeout or e2e['timeout']} s: {command}") from None
    report = None
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, ValueError):
            report = None
    return proc, report, command, report_path


def counts(parsed: dict) -> dict:
    """{"passed", "failed", "skipped"} over a parsed run (lib/test_reasons.py's shape)."""
    return {"passed": sum(parsed.get("passed", {}).values()), "failed": len(parsed.get("results") or []),
            "skipped": sum((parsed.get("skipped") or {}).values())}


if __name__ == "__main__":  # a human's quick check: python3 -I lib/e2e.py [--root DIR]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sdlc as cl  # noqa: E402

    root = cl.project_root(sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else None)
    block = settings(cl.load_config(root))
    if block is None:
        print("no e2e block in .claude/sdlc.json")
        sys.exit(0)
    lease = start(root, block, say=print)
    print(f"server: {lease.server} pid={lease.pid} url={lease.url}")
    lease.stop()
