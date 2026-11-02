#!/usr/bin/env python3
"""Does the commit message describe the diff? The mechanical half of the check; the reviewer
agent reads the message against the diff for the rest.

Rules (each a key "commit|<rule>"):
  subject-empty, subject-too-long (>72), subject-vague ("fix", "update", "wip"...),
  claims-tests (message claims tests; no test file in the diff), claims-path:<p> (names a file not
  in the diff), body-missing (four or more files and no body),
  signed-off-by-agent (REC-64: a Signed-off-by trailer on a message that also carries a
  "Co-Authored-By: Claude" trailer; the DCO sign-off is a human's statement, an agent must not add it).

Usage: check_commit_message.py [--message MSG | --text-file PATH] [--root DIR] [--files f1 f2 ...]
(message on stdin otherwise; --text-file checks a PR title + body in CI)
"""

from __future__ import annotations

import re
import sys

VAGUE = {
    "wip", "fix", "fixes", "fixed", "fixing", "update", "updates", "updated", "change", "changes", "misc",
    "stuff", "tweak", "tweaks", "cleanup", "clean", "up", "more", "things", "various", "minor", "small",
    "some", "the", "a", "and", "of", "to", "in",
}
TYPES = {"feat", "fix", "test", "tests", "chore", "docs", "refactor", "perf", "build", "ci", "style", "revert"}
PATH_RE = re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|md|yml|yaml|toml|json|sh|txt|ini|cfg|sql)\b")
TEST_CLAIM_RE = re.compile(
    r"\b(?:add|adds|added|adding|update|updates|updated|cover|covers|covered|write|writes|wrote|extend|extends|"
    r"extended|include|includes|included|with|fix|fixes|fixed)\b[^.\n]{0,40}\btests?\b"
    r"|\btests?\b[^.\n]{0,40}\b(?:added|updated|pass|passes|passing|green|cover|covers)\b",
    re.I,
)


SIGNED_OFF_RE = re.compile(r"^\s*Signed-off-by:", re.I | re.M)
AGENT_RE = re.compile(r"^\s*Co-Authored-By:\s*Claude|Generated with \[?Claude Code", re.I | re.M)


def is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return bool(
        re.search(r"(^|/)(tests?|__tests__|spec)/", path)
        or base.startswith(("test_", "conftest"))
        or base.endswith(("_test.py", "_test.go", "_test.rs"))
        or ".test." in base
        or ".spec." in base
    )


def check(message: str, files: list[str]) -> list[tuple[str, str]]:
    lines = [ln.rstrip() for ln in message.strip().splitlines() if not ln.startswith("#")]
    subject = lines[0].strip() if lines else ""
    body = "\n".join(lines[1:]).strip()
    out: list[tuple[str, str]] = []
    if not subject:
        out.append(("commit|subject-empty", "subject is empty"))
    if len(subject) > 72:
        out.append(("commit|subject-too-long", f"subject is {len(subject)} characters; keep it under 72"))
    words = re.findall(r"[a-z]+", subject.lower())
    content = [w for w in words if w not in TYPES]
    if subject and (not content or all(w in VAGUE for w in content)):
        out.append(("commit|subject-vague", f"subject '{subject}' says nothing about the change"))
    if files and TEST_CLAIM_RE.search(message) and not any(is_test_file(f) for f in files):
        out.append(("commit|claims-tests", "message claims tests but no test file is in the diff"))
    for tok in sorted(set(PATH_RE.findall(message))):
        if files and not any(f == tok or f.endswith("/" + tok) or tok.endswith("/" + f) for f in files):
            out.append((f"commit|claims-path:{tok}", f"message names {tok}, which is not in the diff"))
    if SIGNED_OFF_RE.search(message) and AGENT_RE.search(message):
        out.append(("commit|signed-off-by-agent", "Signed-off-by on an agent commit; remove it (the DCO sign-off is a human's statement)"))
    if len(files) >= 4 and not body:
        out.append(("commit|body-missing", f"{len(files)} files changed and no body explaining why"))
    return out


def main(argv: list[str]) -> int:
    """Exit 0 clean, 1 findings, 2 misuse (missing option value, unreadable --text-file)."""
    message: str | None = None
    files: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(
                "usage: check_commit_message.py [--message MSG | --text-file PATH] [--root DIR] [--files f1 f2 ...]\n"
                "  --text-file  check the text in PATH as a message (CI: a PR title + body)\n"
                "  message on stdin when neither --message nor --text-file is given.\n"
                "  exit 0 ok, 1 findings (key<TAB>message per line), 2 misuse"
            )
            return 0
        if a in ("--message", "--text-file", "--root"):
            if i + 1 >= len(argv):
                sys.stderr.write(f"check_commit_message: {a} needs a value\n")
                return 2
            val = argv[i + 1]
            if a == "--message":
                message = val
            elif a == "--text-file":
                try:
                    with open(val, encoding="utf-8") as fh:
                        message = fh.read()
                except OSError as exc:
                    sys.stderr.write(f"check_commit_message: cannot read {val}: {exc}\n")
                    return 2
            i += 2
            continue
        if a == "--files":
            files = [x for x in argv[i + 1:] if x]
            break
        i += 1
    if message is None:
        message = sys.stdin.read()
    out = check(message, files)
    for key, msg in out:
        print(f"{key}\t{msg}")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
