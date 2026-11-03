#!/usr/bin/env python3
"""Run the new tests before the code exists and record WHY each one fails, so only a test that fails for the
intended reason is frozen.

Why: a test that "failed first" with an ImportError (the module under test did not exist) hid that the test itself
was broken: it did `import app.auth`, then `from app.main import app`, which rebinds `app`, so
`monkeypatch.setattr(app.auth, ...)` could never run. It was frozen, and the implement step could not make it pass
(review finding; the autonomy rule REC-07 behind freeze_tests.py: a frozen test must be one the change can satisfy).

What it does: runs the stack's test command (`commands.test` in .claude/sdlc.json; never a command from the
arguments) narrowed to the given test files, reads the runner's text output (pytest with `-rA
--continue-on-collection-errors` appended; vitest's default reporter) and classifies every failure with
lib/test_reasons.py as one of not_implemented, assertion, status_mismatch, import_error, attribute_error, name_error,
syntax_error, collection_error, other. A Playwright spec (it imports @playwright/test) is read from the run
`scripts/e2e.py run <specs>` recorded under .claude/work/e2e/ (lib/e2e.py): that script runs the suite as its own
command outside the sandbox, where a browser can launch, against the app it built and served itself; this script
refuses a run the tree has moved past (the fingerprint in run.json) or that did not cover the specs, and exits 2
naming the command to run. The intended reasons there are a failed expectation and an element or page state that
never came; a web server that did not start, a page that could not be reached, a browser that did not launch, a spec
that does not parse or import and a run that found no test are wrong. The stack's own test command never sees an e2e
spec (vitest cannot collect one). import_error, attribute_error, name_error, syntax_error and collection_error
are the WRONG reasons (the ledger's `failing_first_reasons` guard refuses the same five): the test has to be fixed,
or the module under test given a skeleton, and the run repeated. A given file no test ran from counts as
collection_error. A test that already passes is reported (it is not testing the change) but is not a reason.

The record goes to .claude/work/<slug>/failing_first.json (slug from current.json): per test {test, file, reason},
per file its sha256, its verdict (ok or wrong) and the wrong tests; a later run replaces the entries of the files it
ran. freeze_tests.py --freeze refuses a file whose latest verdict is wrong or that changed since it was recorded, and
prints the `tests_failing_first` evidence payload ({"tests": [{test, reason}], "commit"}) after a freeze.

Usage:
  tests_failing_first.py [--root DIR] PATH [PATH ...]
  tests_failing_first.py [--root DIR] --from-output FILE [--runner pytest|vitest|playwright] PATH [PATH ...]
--from-output classifies a saved run instead of running the tests (for a runner the script cannot drive, and tests);
a saved Playwright run may be the list reporter's text or the JSON report.
Exit: 0 every failure has an intended reason, 1 a wrong reason (listed with what to fix), 2 misuse.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import e2e  # noqa: E402
import sdlc as cl  # noqa: E402
import test_reasons as tr  # noqa: E402

RECORD_NAME = "failing_first.json"
TIMEOUT = 900


def sha256_file(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def norm(root: str, p: str) -> str:
    full = os.path.abspath(p if os.path.isabs(p) else os.path.join(root, p))
    return os.path.relpath(full, root).replace(os.sep, "/")


def record_path(root: str, slug: str) -> str:
    return os.path.join(root, ".claude", "work", slug, RECORD_NAME)


def narrowed(command: str, runner: str, files: list[str]) -> str:
    """The configured test command narrowed to `files` (each shell-quoted)."""
    quoted = " ".join(shlex.quote(f) for f in files)
    if runner == "pytest":
        extra = [a for a in ("-rA", "--continue-on-collection-errors") if a not in command.split()]
        return " ".join([command, *extra, quoted])
    # npm needs `--` before arguments meant for the script; pnpm, yarn and bun pass them on
    if re.search(r"\bnpm\s+(?:run\s+)?test\b", command) and " -- " not in f" {command} ":
        return f"{command} -- {quoted}"
    return f"{command} {quoted}"


E2E_HINT = ('add to .claude/sdlc.json: {"e2e": {"command": "pnpm test:e2e", "build": "pnpm build", "serve": "pnpm start", '
            '"url": "http://localhost:3100"}} (the gate then builds once and serves the built app itself; /factory:init '
            'writes the block for a repo with a Playwright config)')


def run_group(root: str, cfg: dict, runner: str, command: str, rels: list[str]) -> tuple[str, str, dict] | int:
    """Run one runner's files: (the command run, its output, the parsed run), or an exit code with the reason printed.
    Playwright specs are not run here (no browser launches inside the sandbox): the run `scripts/e2e.py run` recorded
    is read, when it is fresh and covered the specs."""
    if runner == "playwright":
        block = e2e.settings(cfg)
        if block is None:
            print(f"tests_failing_first: {', '.join(rels)}: a Playwright spec runs through the e2e block, which .claude/sdlc.json "
                  f"does not have; {E2E_HINT}", file=sys.stderr)
            return 2
        run, report = e2e.load_run(root)
        why = e2e.staleness(root, run, rels)
        if why:
            print(f"tests_failing_first: {why}", file=sys.stderr)
            return 2
        try:
            with open(e2e.run_paths(root)["output"], encoding="utf-8", errors="replace") as fh:
                output = fh.read()
        except OSError:
            output = ""
        ran = f"{run.get('command', 'e2e')} (recorded {run.get('finished_at', '?')}, server {run.get('server', '?')})"
        print(f"e2e run read from {e2e.RUN_DIR}/: {ran}")
        return ran, output, tr.parse_playwright(output, report, root)
    if not command:
        print("tests_failing_first: no commands.test in .claude/sdlc.json", file=sys.stderr)
        return 2
    ran = narrowed(command, runner, rels)
    env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0", CI="1")
    env.pop("PYTHONSAFEPATH", None)  # the test command runs as configured, not under -I
    try:
        proc = subprocess.run(ran, shell=True, cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"tests_failing_first: the test command ran longer than {TIMEOUT} s: {ran}", file=sys.stderr)
        return 2
    output = proc.stdout + "\n" + proc.stderr
    return ran, output, tr.parse(output, runner)


def under_test(cfg: dict, contract: dict, rel: str, runner: str, module: str | None) -> bool:
    """True when an import names a module inside the contract's allowed paths that is not a test path."""
    if not module:
        return False
    allowed = [p for p in (contract.get("allowed_paths") or []) if isinstance(p, str)]
    for cand in tr.module_candidates(module, rel, runner):
        if cl.matches(cand, allowed) and not cl.is_test_path(cfg, cand):
            return True
    return False


def load_record(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="tests_failing_first.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--from-output", default=None, metavar="FILE", help="classify this saved runner output instead of running the tests")
    ap.add_argument("--runner", choices=("pytest", "vitest", "playwright"), default=None, help="with --from-output: the runner that wrote it (default: from commands.test)")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="the new test files")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    root = cl.project_root(args.root)
    cfg = cl.load_config(root)
    contract = cl.load_contract(root)
    if not isinstance(contract, dict) or not contract.get("slug"):
        print(f"tests_failing_first: no {cl.CONTRACT_REL} with a slug; run /factory:ticket first", file=sys.stderr)
        return 2
    slug = str(contract["slug"])

    rels = [norm(root, p) for p in args.paths]
    for rel in rels:
        if rel.startswith("..") or not cl.is_test_path(cfg, rel):
            print(f"tests_failing_first: {rel} is not a test path", file=sys.stderr)
            return 2
        if not os.path.isfile(os.path.join(root, rel)):
            print(f"tests_failing_first: {rel} does not exist", file=sys.stderr)
            return 2

    command = ((cfg.get("commands") or {}).get("test") or "").strip()
    stack_runner = args.runner or tr.detect_runner(command)
    groups: dict[str, list[str]] = {}  # runner -> files; a Playwright spec goes through the e2e block, the rest to commands.test
    for rel in rels:
        runner = (args.runner or tr.runner_of_file(os.path.join(root, rel)) or stack_runner) if not args.from_output else stack_runner
        groups.setdefault(runner, []).append(rel)
    if None in groups:
        if not command:
            print("tests_failing_first: no commands.test in .claude/sdlc.json", file=sys.stderr)
            return 2
        if args.from_output:
            print("tests_failing_first: pass --runner pytest|vitest|playwright (commands.test names none of them)", file=sys.stderr)
            return 2
        print(f"tests_failing_first: cannot read the output of {command!r}; supported runners: pytest, vitest, playwright", file=sys.stderr)
        return 2

    runs: list[tuple[str, str, dict]] = []
    if args.from_output:
        try:
            with open(args.from_output, encoding="utf-8", errors="replace") as fh:
                output = fh.read()
        except OSError as exc:
            print(f"tests_failing_first: cannot read {args.from_output}: {exc}", file=sys.stderr)
            return 2
        runner = next(iter(groups))
        runs.append((f"(saved output {os.path.basename(args.from_output)})", output, tr.parse(output, runner, root=root)))
    else:
        for runner, files in groups.items():
            got = run_group(root, cfg, runner, command, files)
            if isinstance(got, int):
                return got
            runs.append(got)
    ran = "; ".join(r[0] for r in runs)
    runner = ",".join(groups)
    parsed = {"results": [], "passed": {}, "skipped": {}, "errors": []}
    for _, _, one in runs:
        parsed["results"] += one["results"]
        for key in ("passed", "skipped"):
            for file, n in (one.get(key) or {}).items():
                parsed[key][norm(root, file)] = parsed[key].get(norm(root, file), 0) + n
        parsed["errors"] += one.get("errors") or []

    by_file: dict[str, list[dict]] = {rel: [] for rel in rels}
    for row in parsed["results"]:
        rel = norm(root, row["file"])
        if rel in by_file:
            row["file"] = rel
            by_file[rel].append(row)
    for rel in rels:
        if not by_file[rel] and not parsed["passed"].get(rel):
            # the run's own errors (the web server, no tests found) explain why nothing from the file ran
            run_error = next((e for e in parsed["errors"] if e.get("detail")), None)
            detail = (f"no test from this file ran: {run_error['detail']}" if run_error
                      else "no test from this file ran (not collected, deselected or the run stopped early)")
            by_file[rel].append({"test": rel, "file": rel, "reason": "collection_error", "detail": detail})

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    path = record_path(root, slug)
    record = load_record(path)
    files = record.get("files") if isinstance(record.get("files"), dict) else {}
    tests = [t for t in (record.get("tests") or []) if isinstance(t, dict) and t.get("file") not in by_file]
    wrong_lines: list[str] = []
    print(f"failing first ({runner}): {ran}")
    for rel in rels:
        wrong_ids = []
        file_runner = next((r for r, files in groups.items() if rel in files), runner)
        for row in by_file[rel]:
            bad = tr.is_wrong(row["reason"])
            entry = {"test": row["test"], "file": rel, "reason": row["reason"], "wrong": bad,
                     "detail": row.get("detail", "").replace(root + os.sep, "")[:300]}
            if row.get("module"):
                entry["module"] = row["module"]
            tests.append(entry)
            print(f"  {row['reason']:<16} {row['test']}")
            if bad:
                wrong_ids.append(row["test"])
                hint = tr.advice(row, under_test(cfg, contract, rel, file_runner, row.get("module")))
                wrong_lines.append(f"  {row['test']}: {row['reason']} ({entry['detail'][:160]}); {hint}")
        n_passed = int(parsed["passed"].get(rel, 0))
        if n_passed:
            print(f"  NOTE {rel}: {n_passed} test(s) already pass; a test that passes before the implementation is not testing the change (fix or delete it)")
        n_skipped = int((parsed.get("skipped") or {}).get(rel, 0))
        if n_skipped:
            print(f"  NOTE {rel}: {n_skipped} test(s) skipped; a skipped test proves nothing and the test rules allow no skip marker (fix or delete it)")
        files[rel] = {"sha256": sha256_file(os.path.join(root, rel)), "verdict": "wrong" if wrong_ids else "ok",
                      "wrong": wrong_ids, "passed": n_passed, "recorded_at": now, "runner": file_runner}
    record = {"slug": slug, "runner": runner, "command": ran, "recorded_at": now, "files": files, "tests": tests}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    payload = [{"test": t["test"], "reason": t["reason"]} for t in tests if t.get("file") in by_file]
    print(f"recorded: {os.path.relpath(path, root)}")
    print("payload tests: " + json.dumps(payload))
    if wrong_lines:
        print("WRONG REASON: these tests fail because the test is broken, not because the behaviour is missing:")
        for line in wrong_lines:
            print(line)
        print("Fix them (or the skeleton) and rerun this script; freeze_tests.py refuses a file whose latest reason is wrong.")
        return 1
    print("every failure has an intended reason (not_implemented, assertion, status_mismatch or other)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
