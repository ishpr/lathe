#!/usr/bin/env python3
"""CI: fail when a PR's .claude/baseline.json grows relative to the base ref (REC-43, REC-116).

Why: the ratchet grades a PR against the baseline in the PR's own tree, so a PR that adds its new
violations to the baseline, or raises a count ("a second F401 in the same file"), would grade
itself clean. This step compares the two baselines and fails when a key is added or any count
grows, unless --allow-growth is passed (CI passes it when the PR carries the `baseline-approved`
label). Shrinking is always fine; that is debt being paid. Reads baseline v2 ("counts") and v1
("keys", each counted as 1).

Usage: baseline_diff.py [<base-ref>] [--base-ref REF] [--root DIR] [--baseline .claude/baseline.json] [--allow-growth]
Exit: 0 did not grow (or growth allowed), 1 grew, 2 misuse or a base ref that does not exist
(a missing REF is an error; only a missing baseline FILE on an existing ref counts as 'first baseline').
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def counts_of(data: dict) -> dict[str, int]:
    if isinstance(data.get("counts"), dict):
        return {str(k): int(v) for k, v in data["counts"].items()}
    return {k: 1 for k in data.get("keys", [])}


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    base_ref = root = None
    rel = ".claude/baseline.json"
    allow = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--base-ref", "--root", "--baseline"):
            if i + 1 >= len(argv):
                print(f"baseline_diff: {a} needs a value", file=sys.stderr)
                return 2
            v = argv[i + 1]
            if a == "--base-ref":
                base_ref = v
            elif a == "--root":
                root = v
            else:
                rel = v
            i += 2
            continue
        if a == "--allow-growth":
            allow = True
        elif a.startswith("--"):
            print(f"baseline_diff: unknown argument {a}", file=sys.stderr)
            return 2
        elif base_ref is None:
            base_ref = a
        i += 1
    if not base_ref:
        print(__doc__)
        return 2
    root = os.path.abspath(root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ref_ok = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"], capture_output=True, text=True)
    if ref_ok.returncode != 0:
        print(f"baseline_diff: base ref {base_ref} does not exist here (fetch it first); refusing to treat a bad ref as 'no baseline'", file=sys.stderr)
        return 2
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            new = counts_of(json.load(fh))
    except (OSError, json.JSONDecodeError):
        print("no baseline in this tree; nothing to compare")
        return 0
    shown = subprocess.run(["git", "-C", root, "show", f"{base_ref}:{rel}"], capture_output=True, text=True)
    if shown.returncode != 0 or not shown.stdout.strip():
        print(f"no baseline on {base_ref}; treating this PR's baseline as the first one")
        return 0
    try:
        old = counts_of(json.loads(shown.stdout))
    except json.JSONDecodeError:
        print(f"baseline on {base_ref} is not valid JSON; treating this PR's baseline as the first one")
        return 0
    added = sorted(k for k in new if k not in old)
    grown = sorted(k for k in new if k in old and new[k] > old[k])
    dropped = [k for k in old if k not in new]
    lowered = [k for k in new if k in old and new[k] < old[k]]
    print(f"baseline: {len(old)} key(s) / {sum(old.values())} on {base_ref} -> {len(new)} key(s) / {sum(new.values())} here")
    if added or grown:
        verdict = "growth allowed (baseline-approved)" if allow else "a PR may not add to its own baseline; a human adds the baseline-approved label if this is intended"
        print(f"baseline grew relative to {base_ref}: {len(added)} key(s) added, {len(grown)} count(s) raised; {verdict}:")
        for key in added[:40]:
            print(f"  + {key}  (new, {new[key]})")
        for key in grown[:40]:
            print(f"  ^ {key}  ({old[key]} -> {new[key]})")
        if len(added) + len(grown) > 80:
            print(f"  +{len(added) + len(grown) - 80} more")
        return 0 if allow else 1
    print(f"baseline did not grow ({len(dropped)} key(s) dropped, {len(lowered)} count(s) lowered)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
