#!/usr/bin/env python3
"""Move a file the agent staged outside `.claude/` into `.claude/work/` (first live headless run, finding 3; REC-52).

Why: under `claude -p --permission-mode dontAsk` Claude Code refuses the Write and Edit tools for
anything under `.claude/`, whatever allow rules are given (probed on 2.1.316 in a bare repo and in a
pack fixture: `Write`, `Write(.claude/work/**)`, `Write(.claude/**)`, `Write(/.claude/work/**)`,
`Write(//abs/.claude/work/**)`, `Write(**)` and a PreToolUse hook answering "allow" all end in
"Permission to use Write has been denied because Claude Code is running in don't ask mode"; the same
Write to `docs/` succeeds). Skills therefore write the text with the Write tool to
`.sdlc-stage/<same path below .claude/work>` and run this script, which moves it into place:

    python3 -I scripts/workfile.py .claude/work/<slug>/ticket.md     # moves .sdlc-stage/<slug>/ticket.md

No heredoc, no ticket text on a command line, nothing piped through the shell.

This script is a write path the edit guard cannot see, so it applies the guard's own rules itself:
  - the destination is `.claude/work/current.json` or `.claude/work/<slug>/<name>`; slug matches
    ^[a-z0-9][a-z0-9-]{0,40}$ (the rule /ship uses before it deletes a directory); name is a plain
    .md/.json/.txt file name; `last_verify.json` (the verify stamp) is never written here;
  - the source is a regular file (no symlink) of at most 256 KiB, valid UTF-8, with no secret-looking
    string (the guard_edit patterns);
  - no symlink anywhere on the destination path;
  - an existing `current.json` is replaced only by a JSON object that keeps tier, slug and frozen_at,
    keeps require_tests on, only narrows allowed_paths and keeps every frozen_tests entry (what
    guard_edit's check_contract_edit enforces for the Edit tool; creating the first one is not checked,
    as there). Nothing outside .claude/work is reachable through here.
The staged file is removed after a successful move, and the empty staging directories with it.

`--discard DEST` removes the staged file for DEST instead of moving it (a PR body that `gh pr create
--body-file` has read; nothing is written below .claude/work).

`--clear SLUG` is /ship's step 2 (review finding: ship ran `rm -f current.json` and `rm -rf .claude/work/<slug>`,
which no allow rule covers, so every ship was denied under dontAsk). It removes `.claude/work/current.json`
and the `.claude/work/<slug>` directory, and nothing else, after the same checks the guard applies to an rm of
the contract: SLUG matches the slug pattern and `.claude/work/<slug>` is a real directory (no symlink)
directly under `.claude/work`; `last_verify.json` exists, says ok, and its fingerprint equals
`worktree_fingerprint.py` for the tree now (the stamp must still hold, as for the guard's stamped rm);
`last_verify.json` itself is left alone. A missing current.json or slug directory is fine (already clear).

Usage: workfile.py [--root DIR] [--stage DIR] [--discard] DEST
       workfile.py [--root DIR] --clear SLUG
Exit: 0 moved or cleared, 1 refused (content or contract rule), 2 misuse or crash.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys

STAGE_DEFAULT = ".sdlc-stage"
WORK = ".claude/work"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(?:md|json|txt)$")
NEVER = {"last_verify.json"}
TOP_LEVEL = {"current.json", "security-impact.json", "security-reviewer.json"}  # /security stages these two
MAX_BYTES = 256 * 1024
SECRETS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY", "private key"),
    (r"\bsk_(?:live|test)_[0-9A-Za-z]{16,}", "Stripe secret key"),
    (r"\bxox[abp]-[0-9A-Za-z-]{10,}", "Slack token"),
    (r"\bgh[pousr]_[0-9A-Za-z]{30,}", "GitHub token"),
    (r"\bgithub_pat_[0-9A-Za-z_]{22,}", "GitHub fine-grained token"),
    (r"\bsk-ant-[0-9A-Za-z_-]{20,}", "Anthropic API key"),
    (r"\bsk-proj-[0-9A-Za-z_-]{20,}", "OpenAI API key"),
    (r"\bsk-[0-9A-Za-z]{32,}", "API secret key"),
    (r"\bnpm_[0-9A-Za-z]{30,}", "npm token"),
    (r"\bpypi-[0-9A-Za-z_-]{30,}", "PyPI token"),
    (r"\bAIza[0-9A-Za-z_-]{30,}", "Google API key"),
]
PLACEHOLDER = re.compile(r"(?i)example|replace|placeholder|your[-_ ]|changeme|change-me|dummy|fake|sample|redacted|xxxx|\*{4}|<[^>]+>")


class Refused(Exception):
    pass


def match_one(rel: str, pattern: str) -> bool:
    pattern = pattern.strip().rstrip("/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern or pattern == ".":
        return True
    if fnmatch.fnmatchcase(rel, pattern):
        return True
    if pattern.endswith("/**") and (rel == pattern[:-3] or rel.startswith(pattern[:-3] + "/")):
        return True
    if "*" not in pattern and (rel == pattern or rel.startswith(pattern + "/")):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatchcase(rel, pattern[3:])


def check_dest(dest: str) -> str:
    """Normalised destination below .claude/work, or Refused."""
    if os.path.isabs(dest) or "\\" in dest or "\0" in dest:
        raise Refused("DEST must be a relative path like .claude/work/<slug>/ticket.md")
    parts = [p for p in dest.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise Refused("no '..' in DEST")
    rel = "/".join(parts)
    if not rel.startswith(WORK + "/"):
        raise Refused("DEST must be below %s/" % WORK)
    tail = rel[len(WORK) + 1:].split("/")
    if len(tail) == 1 and tail[0] in TOP_LEVEL:
        return rel
    if len(tail) == 2 and SLUG.match(tail[0]) and NAME.match(tail[1]) and tail[1] not in NEVER:
        return rel
    raise Refused("DEST must be %s/current.json, security-impact.json, security-reviewer.json or %s/<slug>/<name>.md|.json|.txt (slug: lowercase letters, digits, hyphens, "
                  "at most 41 characters; never last_verify.json)" % (WORK, WORK))


def check_secrets(text: str) -> None:
    for pattern, label in SECRETS:
        for m in re.finditer(pattern, text):
            if not PLACEHOLDER.search(m.group(0)):
                raise Refused("the text contains what looks like a %s (%s...); secrets stay out of work files" % (label, m.group(0)[:12]))


def check_rows_kept(path: str, new_text: str) -> None:
    """assumptions.md is append-only across stages: every row already there stays, unchanged."""
    with open(path, encoding="utf-8") as fh:
        old = [ln.rstrip() for ln in fh if ln.startswith(("| A", "- default_applied:"))]
    kept = {ln.rstrip() for ln in new_text.splitlines()}
    lost = [ln for ln in old if ln not in kept]
    if lost:
        raise Refused("this would drop %d assumption row(s) an earlier stage recorded (first: %s); Read the file "
                      "and append your rows after them, numbering on" % (len(lost), lost[0][:80]))


def check_contract(path: str, new_text: str) -> None:
    try:
        after = json.loads(new_text)
    except ValueError:
        raise Refused("current.json must be valid JSON")
    if not isinstance(after, dict):
        raise Refused("current.json must be a JSON object")
    if not os.path.exists(path):
        return  # the first current.json is /ticket's to create; the guard does not check that either
    try:
        with open(path, encoding="utf-8") as fh:
            before = json.load(fh)
    except (OSError, ValueError):
        raise Refused("the existing current.json is unreadable; a human repairs it")
    if not isinstance(before, dict):
        raise Refused("the existing current.json is not a JSON object; a human repairs it")

    def norm(v) -> str:
        return str(v if v is not None else "").strip().lower()

    problems = []
    if norm(after.get("tier")) != norm(before.get("tier")):
        problems.append("tier %r -> %r" % (before.get("tier"), after.get("tier")))
    if "slug" in before and after.get("slug") != before.get("slug"):
        problems.append("slug %r -> %r" % (before.get("slug"), after.get("slug")))
    if before.get("frozen_at") and after.get("frozen_at") != before.get("frozen_at"):
        problems.append("frozen_at changed")
    bp, ap = before.get("allowed_paths") or [], after.get("allowed_paths") or []
    if isinstance(bp, list) and bp:
        if not isinstance(ap, list) or not ap or not all(
                isinstance(p, str) and (p in bp or any(match_one(p, b) for b in bp if isinstance(b, str))) for p in ap):
            problems.append("allowed_paths %s -> %s" % (bp, ap))
    if before.get("require_tests", True) and not after.get("require_tests", True):
        problems.append("require_tests true -> false")
    bf = before.get("frozen_tests") or {}
    af = after.get("frozen_tests") or {}
    if isinstance(bf, dict) and (not isinstance(af, dict) or any(af.get(k) != v for k, v in bf.items())):
        problems.append("frozen_tests entries dropped or changed")
    if problems:
        raise Refused("this would loosen the contract (%s); only a human changes those" % "; ".join(problems))


def no_symlink(root: str, rel: str) -> None:
    cur = root
    for part in rel.split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise Refused("%s is a symlink" % os.path.relpath(cur, root))


def run(root: str, stage: str, dest: str, discard: bool = False) -> str:
    rel = check_dest(dest)
    stage = stage.strip("/") or STAGE_DEFAULT
    if ".." in stage.split("/") or os.path.isabs(stage):
        raise Refused("--stage must be a relative directory inside the project")
    src_rel = stage + "/" + rel[len(WORK) + 1:]
    src = os.path.join(root, src_rel)
    out = os.path.join(root, rel)
    no_symlink(root, src_rel)
    no_symlink(root, rel)
    if discard:
        if os.path.isfile(src):
            os.remove(src)
            tidy(root, stage, src)
        return rel
    if not os.path.isfile(src):
        raise Refused("nothing staged at %s: write the text there with the Write tool first" % src_rel)
    if os.path.getsize(src) > MAX_BYTES:
        raise Refused("%s is larger than %d KiB" % (src_rel, MAX_BYTES // 1024))
    with open(src, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("%s is not valid UTF-8" % src_rel)
    check_secrets(text)
    if rel == WORK + "/current.json":
        check_contract(out, text)
    if os.path.basename(rel) == "assumptions.md" and os.path.isfile(out):
        check_rows_kept(out, text)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp-%d" % os.getpid()
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, out)
    os.remove(src)
    tidy(root, stage, src)
    return rel


def clear(root: str, slug: str) -> list[str]:
    """Remove current.json and the slug's work directory after a green, still-matching verify stamp."""
    if not SLUG.match(slug or ""):
        raise Refused("SLUG must match ^[a-z0-9][a-z0-9-]{0,40}$ (no '/', no '..', not empty); delete nothing and tell the human")
    work = os.path.join(root, WORK)
    no_symlink(root, WORK)
    slug_dir = os.path.join(work, slug)
    if os.path.lexists(slug_dir) and (os.path.islink(slug_dir) or not os.path.isdir(slug_dir)):
        raise Refused("%s/%s is not a plain directory directly under %s; delete nothing and tell the human" % (WORK, slug, WORK))
    cur = os.path.join(work, "current.json")
    if os.path.islink(cur):
        raise Refused("%s/current.json is a symlink" % WORK)
    try:
        with open(os.path.join(work, "last_verify.json"), encoding="utf-8") as fh:
            stamp = json.load(fh)
    except (OSError, ValueError):
        raise Refused("no readable verify stamp: run /verify before /ship clears the work state")
    if not isinstance(stamp, dict) or not stamp.get("ok"):
        raise Refused("the verify stamp is not green: run /verify first")
    fp_script = os.path.join(os.path.dirname(os.path.realpath(__file__)), "worktree_fingerprint.py")
    p = subprocess.run([sys.executable, "-I", fp_script, root], capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip() or p.stdout.strip() != stamp.get("fingerprint"):
        raise Refused("the verify stamp no longer matches the tree: run /verify again")
    removed = []
    if os.path.lexists(cur):
        os.remove(cur)
        removed.append(WORK + "/current.json")
    if os.path.isdir(slug_dir):
        shutil.rmtree(slug_dir)
        removed.append("%s/%s" % (WORK, slug))
    return removed


def tidy(root: str, stage: str, src: str) -> None:
    """Remove the staging directories that are empty now, the staging directory itself last."""
    d = os.path.dirname(src)
    top = os.path.join(root, stage)
    while d.startswith(top):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Move a file staged under %s/ into .claude/work/ (Claude Code refuses Write/Edit under .claude/ in dontAsk mode)." % STAGE_DEFAULT,
        epilog="Exit: 0 moved, 1 refused, 2 misuse.",
    )
    ap.add_argument("dest", nargs="?", help=".claude/work/current.json or .claude/work/<slug>/<name>; the source is %s/<path below .claude/work>" % STAGE_DEFAULT)
    ap.add_argument("--clear", metavar="SLUG", help="remove .claude/work/current.json and .claude/work/SLUG after a green, matching verify stamp (/ship step 2)")
    ap.add_argument("--discard", action="store_true", help="delete the staged file for DEST instead of moving it")
    ap.add_argument("--root", default=".", help="project root (default: the current directory)")
    ap.add_argument("--stage", default=STAGE_DEFAULT, help="staging directory below the root (default %(default)s)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        print("workfile: --root %s is not a directory" % args.root, file=sys.stderr)
        return 2
    if args.clear is not None:
        if args.dest:
            print("workfile: --clear takes a SLUG and no DEST", file=sys.stderr)
            return 2
        try:
            removed = clear(root, args.clear)
        except Refused as exc:
            print("workfile: refused: %s" % exc, file=sys.stderr)
            return 1
        except OSError as exc:
            print("workfile: %s" % exc, file=sys.stderr)
            return 2
        print("workfile: cleared %s" % (", ".join(removed) or "nothing (already clear)"))
        return 0
    if not args.dest:
        print("workfile: DEST is required (or --clear SLUG)", file=sys.stderr)
        return 2
    try:
        rel = run(root, args.stage, args.dest, args.discard)
    except Refused as exc:
        print("workfile: refused: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("workfile: %s" % exc, file=sys.stderr)
        return 2
    print("workfile: %s %s" % ("discarded the staged copy of" if args.discard else "wrote", rel))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("workfile: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
