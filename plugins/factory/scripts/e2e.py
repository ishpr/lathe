#!/usr/bin/env python3
"""Run the end-to-end suite where a browser can launch, and record the run for the gate.

Why: Claude Code's sandbox denies the Mach bootstrap check-in Chromium needs on macOS (`bootstrap_check_in
org.chromium.Chromium.MachPortRendezvousServer: Permission denied`), so a browser never starts inside the sandboxed
gate scripts; and a dev server with a file watcher (`next dev`) dies there too. This script is the one place the suite
runs: a skill runs it as its own Bash command, which the human lets out of the sandbox (`sandbox.excludedCommands` in
.claude/settings.json, written by /factory:init next to the `e2e` block; the orchestrator compiles the same exclusion into
a step's settings when the contract's commands name the e2e command). Nothing else about the gate changes: the recorder
and verify read what this script recorded and refuse a run the tree has moved past.

What it does (lib/e2e.py): makes the app listen at e2e.url (reusing this checkout's own running server, else e2e.build
once and e2e.serve), runs e2e.command narrowed to the given spec files with --reporter=list,json, stops the server it
started, and writes .claude/work/e2e/: report.json (Playwright's json report), output.txt (the list reporter's text)
and run.json {command, files, started_at, finished_at, exit, server, url, counts, fingerprint}. The fingerprint is
scripts/worktree_fingerprint.py's value for the tree the suite ran against; tests_failing_first.py and verify.py compare
it with the tree they see and refuse a stale run. It prints each failing test with its reason and the summary line.

Usage: e2e.py run [--root DIR] [FILE ...]        FILE narrows the suite to those spec files (verify needs the whole suite)
Exit: the suite's exit code (0 green, 1 a failure); 2 when the app could not be made to listen or there is no e2e block.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import e2e  # noqa: E402
import sdlc as cl  # noqa: E402
import test_reasons as tr  # noqa: E402


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] != "run":
        print(f"e2e: unknown command {argv[0]!r}; usage: e2e.py run [--root DIR] [FILE ...]", file=sys.stderr)
        return 2
    args = argv[1:]
    root_hint = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print("e2e: --root needs a directory", file=sys.stderr)
            return 2
        root_hint = args[i + 1]
        args = args[:i] + args[i + 2:]
    root = cl.project_root(root_hint)
    cfg = cl.load_config(root)
    if not cfg["_configured"]:
        print("e2e: this repo is not initialised; run /factory:init first", file=sys.stderr)
        return 2
    block = e2e.settings(cfg)
    if block is None:
        print("e2e: .claude/sdlc.json has no e2e block (command, build, serve, url); nothing to run", file=sys.stderr)
        return 2
    files = []
    for f in args:
        full = os.path.abspath(f if os.path.isabs(f) else os.path.join(root, f))
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        if rel.startswith("..") or not os.path.isfile(full):
            print(f"e2e: {f} is not a file inside the project", file=sys.stderr)
            return 2
        files.append(rel)

    paths = e2e.run_paths(root)
    shutil.rmtree(paths["dir"], ignore_errors=True)
    os.makedirs(paths["dir"], exist_ok=True)
    started = now()
    before = e2e.fingerprint(root)
    try:
        lease = e2e.start(root, block, say=print)
    except e2e.E2EError as exc:
        print(f"e2e: {exc}", file=sys.stderr)
        return 2
    try:
        proc, report, command, report_path = e2e.run_suite(root, block, files)
    except e2e.E2EError as exc:
        print(f"e2e: {exc}", file=sys.stderr)
        return 2
    finally:
        lease.stop()
    output = proc.stdout + "\n" + proc.stderr
    parsed = tr.parse_playwright(output, report, root)
    n = e2e.counts(parsed)
    with open(paths["output"], "w", encoding="utf-8") as fh:
        fh.write(output)
    if report is not None:
        with open(paths["report"], "w", encoding="utf-8") as fh:
            json.dump(report, fh)
    shutil.rmtree(os.path.dirname(report_path), ignore_errors=True)
    after = e2e.fingerprint(root)
    run = {"command": command, "files": files, "started_at": started, "finished_at": now(), "exit": proc.returncode,
           "server": lease.server, "url": block["url"], "counts": n, "fingerprint": after, "report": report is not None}
    with open(paths["run"], "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=2)
        fh.write("\n")
    print(f"e2e run: {command}")
    for row in parsed["results"]:
        print(f"  {row['reason']:<16} {row['test']}")
    for err in parsed.get("errors") or []:
        print(f"  run error: {err['detail']}")
    if proc.returncode != 0 and not parsed["results"] and not parsed.get("errors"):
        print("\n".join(output.strip().splitlines()[-8:]))
    print(f"e2e: {n['passed']} passed, {n['failed']} failed, {n['skipped']} skipped; server {lease.server} at {block['url'] or '(Playwright webServer)'}")
    if before != after:
        print("NOTE: the tree changed while the suite ran; the recorded fingerprint is the tree after the run")
    print(f"recorded: {os.path.relpath(paths['run'], root)} (report: {'yes' if report is not None else 'no json report'})")
    return proc.returncode if proc.returncode in (0, 1) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
