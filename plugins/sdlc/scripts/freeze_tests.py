#!/usr/bin/env python3
"""Freeze the tests /tests wrote so /implement cannot edit them to make them pass.

Why: agents hard-coded test answers or weakened asserts on ambiguous tasks (REC-07).
 `--freeze` records the sha256 of each test file's bytes in
.claude/work/current.json under `frozen_tests` and sets `frozen_at` to HEAD. The edit guard
denies edits to those paths; `--check` (run by verify) fails when any frozen file changed or
disappeared. The conftest.py files above each frozen test, pytest.ini, tox.ini, setup.cfg and the
pyproject [tool.pytest] table are frozen with it (a conftest that skips everything would make a frozen
failing test pass), and `--check` fails when such a file appears that is not frozen; with
`--junit XML` it also fails when a frozen test did not run or was skipped. `--unfreeze` is for humans only (the Bash guard denies it to the agent).

Usage:
  freeze_tests.py [--root DIR] --freeze PATH [PATH ...]
  freeze_tests.py [--root DIR] --unfreeze PATH [PATH ...]
  freeze_tests.py [--root DIR] --check
  freeze_tests.py [--root DIR] --list
Paths are relative to --root. Exit: 0 ok, 1 frozen file changed or missing, 2 misuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402


CONFIG_BASENAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg"}
PYPROJECT_KEY = "pyproject.toml#tool.pytest"


def sha256_file(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def pyproject_pytest_text(root: str) -> str | None:
    """Canonical text of pyproject.toml's [tool.pytest*] tables, or None when there is none."""
    try:
        import tomllib

        with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
            table = (tomllib.load(fh).get("tool") or {}).get("pytest")
    except (OSError, ValueError, ImportError):
        return None
    return json.dumps(table, sort_keys=True) if table is not None else None


def sha_of(root: str, rel: str) -> str | None:
    """sha256 of a file, or of the [tool.pytest] table for the pseudo-path pyproject.toml#tool.pytest."""
    if rel == PYPROJECT_KEY:
        text = pyproject_pytest_text(root)
        return hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    return sha256_file(os.path.join(root, rel))


def config_files_for(root: str, rel: str) -> list[str]:
    """Test-runner config that can change what a Python test at `rel` does: every conftest.py from its
    directory up to the root, pytest.ini / tox.ini / setup.cfg on that path, and the [tool.pytest] table.
    An unfrozen conftest that skips everything makes a frozen failing test pass, so these are pinned too."""
    if not rel.endswith(".py") or os.path.basename(rel) in CONFIG_BASENAMES:
        return []
    out: list[str] = []
    parts = rel.split("/")[:-1]
    for depth in range(len(parts), -1, -1):
        d = "/".join(parts[:depth])
        for name in sorted(CONFIG_BASENAMES):
            cand = f"{d}/{name}" if d else name
            if os.path.isfile(os.path.join(root, cand)):
                out.append(cand)
    if pyproject_pytest_text(root) is not None:
        out.append(PYPROJECT_KEY)
    return out


def junit_problems(root: str, frozen: dict, junit_path: str) -> list[str]:
    """Frozen test files that did not run or were skipped, from a pytest --junitxml report."""
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(junit_path)
    except (OSError, ET.ParseError):
        return []  # no usable report (a non-pytest command): the hash and conftest checks still apply
    ran: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for case in tree.iter("testcase"):
        cls = (case.get("classname") or "").replace(".", "/")
        for rel in frozen:
            mod = rel[:-3] if rel.endswith(".py") else None
            if mod and (cls == mod or cls.startswith(mod + "/")):
                ran[rel] = ran.get(rel, 0) + 1
                if case.find("skipped") is not None:
                    skipped[rel] = skipped.get(rel, 0) + 1
    out = []
    for rel in sorted(frozen):
        if not rel.endswith(".py") or os.path.basename(rel) in CONFIG_BASENAMES:
            continue
        if not ran.get(rel):
            out.append(f"{rel}: frozen test did not run (no testcase in the test report; deselected or collection hidden)")
        elif skipped.get(rel):
            out.append(f"{rel}: {skipped[rel]} frozen test(s) were SKIPPED; a skip hides a failure (check conftest.py and markers)")
    return out


def norm(root: str, p: str) -> str:
    full = os.path.abspath(p if os.path.isabs(p) else os.path.join(root, p))
    return os.path.relpath(full, root).replace(os.sep, "/")


def read_contract(root: str) -> dict | None:
    path = os.path.join(root, cl.CONTRACT_REL)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None


def write_contract(root: str, data: dict) -> None:
    path = os.path.join(root, cl.CONTRACT_REL)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def check(root: str, junit: str | None = None) -> list[str]:
    """Return problem lines; empty when nothing is frozen or all frozen files match."""
    try:
        contract = read_contract(root)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"current.json unreadable: {exc}"]
    frozen = (contract or {}).get("frozen_tests") or {}
    problems = []
    for rel, want in sorted(frozen.items()):
        got = sha_of(root, rel)
        if got is None:
            problems.append(f"{rel}: frozen test is missing")
        elif got != want:
            problems.append(f"{rel}: frozen test was changed")
    for rel in sorted(frozen):
        for cfg_rel in config_files_for(root, rel):
            if cfg_rel not in frozen:
                problems.append(f"{cfg_rel}: test-runner config appeared or changed scope after the freeze, so it can hide a failing frozen test; a human reviews it and refreezes")
    if junit:
        problems += junit_problems(root, frozen, junit)
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="freeze_tests.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=None)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", nargs="+", metavar="PATH")
    mode.add_argument("--unfreeze", nargs="+", metavar="PATH")
    mode.add_argument("--check", action="store_true")
    ap.add_argument("--junit", default=None, metavar="XML", help="with --check: a pytest --junitxml report; frozen tests that did not run or were skipped fail")
    mode.add_argument("--list", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    root = cl.project_root(args.root)
    cfg = cl.load_config(root)

    try:
        contract = read_contract(root)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"freeze_tests: {cl.CONTRACT_REL} unreadable: {exc}", file=sys.stderr)
        return 2

    if args.check:
        problems = check(root, args.junit)
        if problems:
            print("FROZEN TESTS CHANGED:")
            for line in problems:
                print(f"  {line}")
            print("If a test is wrong, stop and ask a human to run: python3 -I scripts/freeze_tests.py --unfreeze <path>")
            return 1
        n = len((contract or {}).get("frozen_tests") or {})
        print(f"frozen tests ok ({n} file(s))")
        return 0

    if args.list:
        for rel, digest in sorted(((contract or {}).get("frozen_tests") or {}).items()):
            print(f"{digest[:12]}  {rel}")
        return 0

    if contract is None:
        print(f"freeze_tests: no {cl.CONTRACT_REL}; run /ticket first", file=sys.stderr)
        return 2
    frozen = dict(contract.get("frozen_tests") or {})

    if args.unfreeze:
        for p in args.unfreeze:
            rel = norm(root, p)
            if frozen.pop(rel, None) is None:
                print(f"not frozen: {rel}")
            else:
                print(f"unfrozen: {rel}")
        contract["frozen_tests"] = frozen
        write_contract(root, contract)
        return 0

    rels = [norm(root, p) for p in args.freeze]
    for rel in rels:
        if rel.startswith("..") or not cl.is_test_path(cfg, rel):
            print(f"freeze_tests: refusing to freeze {rel}: not a test path", file=sys.stderr)
            return 2
        if not os.path.isfile(os.path.join(root, rel)):
            print(f"freeze_tests: {rel} does not exist", file=sys.stderr)
            return 2
    for rel in rels:
        frozen[rel] = sha_of(root, rel)
        print(f"frozen: {rel} {frozen[rel][:12]}")
    for rel in list(rels):
        for cfg_rel in config_files_for(root, rel):
            if cfg_rel not in frozen:
                frozen[cfg_rel] = sha_of(root, cfg_rel)
                print(f"frozen (test config): {cfg_rel} {frozen[cfg_rel][:12]}")
    contract["frozen_tests"] = frozen
    head = cl.git(root, "rev-parse", "HEAD").strip()
    if head:
        contract["frozen_at"] = head
    write_contract(root, contract)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
