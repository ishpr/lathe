"""A fake step executor for tests and dry runs: speaks the step-executor protocol, never calls a model.

Use it with ``ORCHESTRATOR_EXECUTOR="<python> -m sdlc_graph.orchestrator.fake_executor"``. It reads
``.claude/work/current.json`` in the worktree (as a real session would), does the step's work with
plain file writes and git commits, records evidence in the ledger the way the pack's skills do
(``DATABASE_URL``/``SDLC_SCHEMA`` from the environment), and writes the result file last.

Behaviour per step comes from ``ORCH_FAKE_PLAN``: a JSON file mapping ``"<slug>:<step>:<attempt>"``,
``"<slug>:<step>"``, ``"<step>"`` or ``"*"`` (first match wins) to a mode:

    ok            do the step and record its evidence (default)
    nothing       write nothing, record nothing, exit 0
    drift         like ok, plus commit a file outside allowed_paths
    frozen_edit   like ok, plus change a frozen test file (implement/verify)
    ratchet       like ok, plus ratchet evidence with new = 2
    hang          sleep until killed, writing nothing
    crash         exit 3 without a result file
    crash_dirty   leave an uncommitted file inside allowed_paths, then exit 3 without a result file
    A mode may combine behaviours with ``+`` (``drift+self_transition``).
    self_transition  like ok, then move the ledger state itself as ``agent:claude``
    ask           write .claude/work/<slug>/questions.md and stop, recording nothing
    lie_tests     (tests) write only a dummy test file and record evidence that names the real acceptance tests
    lie_pr        (ship) record a pr_url that is not a pull request of this repository, and open none
    plant         like ok, plus gitignored files outside allowed_paths, a stray file in .claude/work and a
                  symlink that leaves the worktree
    hold_drift    wait for ``$ORCH_FAKE_PLAN.release`` (writing ``.started`` first), then behave like drift
    assume        like ok, plus write .claude/work/<slug>/assumptions.md: the text of ``$ORCH_FAKE_PLAN.assumptions.md``
                  when that file exists, else one well-formed assumption
    denied        stream a ``system``/``permission_denied`` event and a result whose ``permission_denials`` names a
                  Bash command (``$FAKE_DENIED_COMMAND``, default ``uv run pytest -q``), record nothing, exit 0
                  (a session under dontAsk that could not do its step)

The executor refuses (exit 2) unless it is given ``--permission-mode dontAsk``, ``--permission-prompts none`` and
a non-empty ``--allowed-tools-file``, as the real step executor does; its first log line records all three.

``FAKE_COST`` is the ``total_cost_usd`` the result file reports (default 0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

GIT_ID = ["-c", "user.name=Fake Executor", "-c", "user.email=fake@sdlc-graph.invalid", "-c", "commit.gpgsign=false"]


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="fake_executor")
    for flag in ("--worktree", "--command", "--prompt-file", "--settings", "--result-file", "--log-file",
                 "--session-id", "--model", "--max-turns", "--budget", "--reviewer", "--allowed-tools-file",
                 "--permission-mode", "--permission-prompts"):
        p.add_argument(flag, required=flag in ("--worktree", "--command", "--result-file", "--log-file"))
    p.add_argument("--plugin-dir", action="append", default=[])
    return p.parse_args(argv)


def _has_origin(wt: Path) -> bool:
    return bool(subprocess.run(["git", "-C", str(wt), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip())


def mode_for(slug: str, step: str, attempt: int) -> str:
    path = os.environ.get("ORCH_FAKE_PLAN")
    plan: dict[str, str] = {}
    if path and Path(path).is_file():
        plan = json.loads(Path(path).read_text())
    for key in (f"{slug}:{step}:{attempt}", f"{slug}:{step}", step, "*"):
        if key in plan:
            return plan[key]
    return "ok"


def git(wt: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(wt), *GIT_ID, *args], check=True, capture_output=True, text=True).stdout


def commit(wt: Path, files: list[str], message: str) -> str:
    git(wt, "add", "--", *files)
    git(wt, "commit", "-q", "-m", message)
    return git(wt, "rev-parse", "HEAD").strip()


def _glob_dir(pattern: str) -> str:
    """The literal directory prefix of an allowed-path glob (``app/ratelimit/**`` -> ``app/ratelimit``)."""
    p = pattern.removeprefix("./")
    lit = re.split(r"[*?\[]", p, maxsplit=1)[0]
    if lit != p:
        return lit.rstrip("/")
    return p.rstrip("/") if p.endswith("/") else str(Path(p).parent) if "." in Path(p).name else p


def _is_tests(p: str) -> bool:
    return p.removeprefix("./").startswith("tests") or "/tests" in p


def do_tests(wt: Path, cur: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    acceptance = cur.get("acceptance") or []
    by_file: dict[str, list[str]] = {}
    for row in acceptance:
        if row.get("test") and "::" in row["test"]:
            f, name = row["test"].split("::", 1)
            by_file.setdefault(f, []).append(name)
    if not by_file:
        d = next((_glob_dir(p) for p in cur["allowed_paths"] if _is_tests(p)), "tests")
        by_file[f"{d}/test_{cur['slug'].replace('-', '_')}.py"] = ["test_behaviour_missing"]
    written = []
    for f, names in by_file.items():
        path = wt / f
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"def {n}():\n    assert False, 'behaviour not implemented yet'\n\n\n" for n in names)
        path.write_text(f'"""Tests for {cur["slug"]} (fake executor)."""\n\n\n' + body)
        written.append(f)
    tests = [f"{f}::{n}" for f, names in by_file.items() for n in names]
    files = {f: hashlib.sha256((wt / f).read_bytes()).hexdigest() for f in written}
    return tests, files


def do_implement(wt: Path, cur: dict[str, Any]) -> str:
    d = next((_glob_dir(p) for p in cur["allowed_paths"] if not _is_tests(p)), "app")
    rel = f"{d}/{cur['slug'].replace('-', '_')}.py" if d else f"{cur['slug'].replace('-', '_')}.py"
    path = wt / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(path.read_text().splitlines()) if path.exists() else 0
    with path.open("a") as fh:
        fh.write(f"# implementation of {cur['slug']} (fake executor), revision {n + 1}\nVALUE_{n + 1} = {n + 1}\n")
    return rel


def main(argv: list[str] | None = None) -> int:
    a = _args(argv)
    wt = Path(a.worktree)
    t0 = time.time()
    step = os.environ.get("ORCH_STEP") or a.command.rsplit(":", 1)[-1]
    attempt = int(os.environ.get("ORCH_ATTEMPT") or 1)
    cur = json.loads((wt / ".claude" / "work" / "current.json").read_text())
    slug = cur["slug"]
    mode = mode_for(slug, step, attempt)
    flags = set(mode.split("+"))     # "drift+self_transition": several behaviours at once
    mode = mode.split("+")[0]
    tools = Path(a.allowed_tools_file).read_text().splitlines() if a.allowed_tools_file and Path(a.allowed_tools_file).is_file() else []
    if a.permission_mode != "dontAsk" or a.permission_prompts != "none" or not tools:
        print(f"fake_executor: refused: permission mode {a.permission_mode!r}, prompts {a.permission_prompts!r}, "
              f"{len(tools)} allowed tool(s)", file=sys.stderr)
        return 2
    log = Path(a.log_file)
    log.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": a.session_id, "fake": True,
                               "step": step, "mode": mode, "permissionMode": a.permission_mode,
                               "permission_prompts": a.permission_prompts, "tools": tools}) + "\n")
    if mode == "denied":
        cmd = os.environ.get("FAKE_DENIED_COMMAND") or "uv run pytest -q"
        use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_fake1", "name": "Bash",
                                                              "input": {"command": cmd}}]}}
        denied = {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_fake1",
                  "tool_input": {"command": cmd}}
        res = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.0,
               "result": f"could not run {cmd}", "permission_denials": [{"tool_name": "Bash", "tool_use_id": "toolu_fake1",
                                                                       "tool_input": {"command": cmd}}]}
        with log.open("a") as fh:
            for ev in (use, denied, res):
                fh.write(json.dumps(ev) + "\n")
        Path(a.result_file).write_text(json.dumps({
            "slug": slug, "exit_code": 0, "duration_seconds": int(time.time() - t0), "last_assistant_message": res["result"],
            "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.0, "session_id": a.session_id,
            "permission_denials": res["permission_denials"], "fake_mode": mode}, indent=2) + "\n")
        return 0
    if mode == "crash":
        return 3
    if mode == "crash_dirty":
        d = next((_glob_dir(p) for p in cur["allowed_paths"] if not _is_tests(p)), "app")
        (wt / d).mkdir(parents=True, exist_ok=True)
        (wt / d / "half_done.py").write_text("# half-written by a session that crashed\n")
        return 3
    if mode == "hang":
        while True:
            time.sleep(1)
    if mode == "ask":
        q = wt / ".claude" / "work" / slug / "questions.md"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text("Should the limit be per API key or per account?\n")
        mode = "nothing"
    if mode == "hold_drift":
        plan = os.environ["ORCH_FAKE_PLAN"]
        Path(plan + ".started").write_text("1")
        while not Path(plan + ".release").exists():
            time.sleep(0.05)
        mode = "drift"

    from sdlc_graph import db
    from sdlc_graph.ledger import evidence

    message = f"fake {step} ({mode})"
    if mode != "nothing":
        with db.connect() as conn:
            if step == "tests" and mode == "lie_tests":
                d = next((_glob_dir(p) for p in cur["allowed_paths"] if _is_tests(p)), "tests")
                dummy = f"{d}/dummy_placeholder.py"
                (wt / d).mkdir(parents=True, exist_ok=True)
                (wt / dummy).write_text("# nothing here\n")
                sha = commit(wt, [dummy], f"test({slug}): placeholder")
                ids = [a["test"] for a in cur.get("acceptance") or [] if a.get("test")]
                files = {dummy: hashlib.sha256((wt / dummy).read_bytes()).hexdigest()}
                evidence.add(conn, slug, "tests_failing_first", {"tests": ids, "commit": sha})
                evidence.add(conn, slug, "tests_frozen", {"files": files, "commit": sha})
                message = "claimed failing tests"
            elif step == "tests":
                tests, files = do_tests(wt, cur)
                sha = commit(wt, list(files), f"test({slug}): failing tests for the acceptance criteria")
                evidence.add(conn, slug, "tests_failing_first", {"tests": tests, "commit": sha})
                evidence.add(conn, slug, "tests_frozen", {"files": files, "commit": sha})
                cur.update(frozen_tests=files, frozen_at=sha)
                (wt / ".claude" / "work" / "current.json").write_text(json.dumps(cur, indent=2) + "\n")
                message = f"wrote and froze {len(tests)} failing tests at {sha[:12]}"
            elif step == "implement":
                rel = do_implement(wt, cur)   # left uncommitted: the pack commits app code at /ship
                message = f"implemented in {rel} (uncommitted until ship)"
            elif step == "verify":
                n = max(len(cur.get("acceptance") or []), 1)
                evidence.add(conn, slug, "verify_stamp", {"frozen_ok": True, "acceptance": {"passed": n, "failed": 0},
                                                          "ratchet": {"new": 0, "known": 0}, "db_backend": "sqlite",
                                                          "tool_versions": {"pytest": "fake"}})
                message = f"verified: acceptance {n}/{n}"
            elif step == "ship":
                git(wt, "add", "-A")
                git(wt, "reset", "-q", "--", ".claude/work")
                if git(wt, "diff", "--cached", "--name-only").strip():
                    git(wt, "commit", "-q", "-m", f"feat({slug}): ship")
                if mode == "lie_pr":
                    url = "https://example.com/this-is-not-a-pull-request"
                else:
                    origin = git(wt, "remote", "get-url", "origin").strip() if _has_origin(wt) else ""
                    m = re.search(r"[:/]+([^/:]+/[^/]+?)(?:\.git)?$", origin)
                    url = f"https://github.com/{m.group(1) if m else 'example/' + wt.name}/pull/{os.environ.get('ORCH_RUN_ID', '1')}"
                    (wt / git(wt, "rev-parse", "--git-dir").strip() / "fake-pr").write_text(os.environ.get("ORCH_RUN_ID", "1"))
                evidence.add(conn, slug, "pr_url", {"url": url})
                message = f"PR {url}"
            if "plant" in flags:
                for rel, body in ((".env", "SECRET=1\n"), ("dist/payload.js", "evil()\n"), (".claude/work/anything.sh", "#!/bin/sh\n")):
                    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                    (wt / rel).write_text(body)
                d = next((_glob_dir(p) for p in cur["allowed_paths"] if not _is_tests(p)), "app")
                link = wt / d / "escape"
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink():
                    link.symlink_to(os.environ.get("FAKE_ESCAPE_DIR") or "/tmp")
                message += "; planted ignored files and a symlink"
            if mode == "assume" or "assume" in flags:
                src = Path(os.environ["ORCH_FAKE_PLAN"] + ".assumptions.md") if os.environ.get("ORCH_FAKE_PLAN") else None
                text = src.read_text() if src and src.is_file() else (
                    "- default_applied: followed the nearest existing pattern\n"
                    f"  where: {cur['allowed_paths'][0] if cur.get('allowed_paths') else 'app'}\n"
                    "  why: the contract does not say how the module is laid out\n"
                    "  decision: none\n")
                q = wt / ".claude" / "work" / slug / "assumptions.md"
                q.parent.mkdir(parents=True, exist_ok=True)
                q.write_text(text)
                message += "; recorded assumptions"
            if "self_transition" in flags:
                from sdlc_graph.ledger import transitions

                target = {"tests": "tests_frozen", "implement": "implementing", "verify": "verified", "ship": "in_review"}[step]
                transitions.transition(conn, slug, target, "agent:claude", reason="the session moved it")
                message += "; moved the ledger state itself"
            if mode == "drift" or "drift" in flags:
                out = wt / "drift" / f"outside-{step}-{attempt}.txt"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("a change outside allowed_paths\n")
                commit(wt, [str(out.relative_to(wt))], f"chore({slug}): out of scope")
                message += "; also changed drift/ (outside allowed_paths)"
            if mode == "frozen_edit" or "frozen_edit" in flags:
                for f in (cur.get("frozen_tests") or {}):
                    p = wt / f
                    p.write_text(p.read_text() + "\n# edited by the fake executor\n")
                    commit(wt, [f], f"test({slug}): weaken a frozen test")
                    break
                message += "; also edited a frozen test"
            if mode == "ratchet" or "ratchet" in flags:
                evidence.add(conn, slug, "ratchet", {"new": 2, "known": 0})
                message += "; ratchet new=2"

    with log.open("a") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": message}]}}) + "\n")
        fh.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                             "total_cost_usd": 0.0, "result": message}) + "\n")
    Path(a.result_file).write_text(json.dumps({
        "slug": slug, "exit_code": 0, "duration_seconds": int(time.time() - t0), "last_assistant_message": message,
        "subtype": "success", "is_error": False, "num_turns": 1, "total_cost_usd": float(os.environ.get("FAKE_COST") or 0.0), "session_id": a.session_id,
        "fake_mode": mode}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
