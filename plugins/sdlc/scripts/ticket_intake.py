#!/usr/bin/env python3
"""Screen ticket text before /ticket acts on it (REC-04; the "lethal trifecta" in untrusted input).

Ticket text is data, never instructions. This reads a ticket (`--file PATH`, a positional file, or stdin when neither is given or "-")
and flags lines containing: shell commands (cat, curl, wget, base64, rm, chmod, ssh, scp, nc),
credential or exfiltration words (.env, secret, token, password, "send to", "post to", "upload"),
prompt-injection phrases ("ignore previous", "disregard", "you are now", "system prompt"), URLs,
invisible characters, and imperative instructions addressed to the assistant.
It prints a "Quoted back to the human" block: every flagged line verbatim with the reason, for the
human to read and confirm before anything is acted on. Invisible characters are shown as \\uXXXX.

Skills call it as `ticket_intake.py --file .claude/work/<slug>/ticket.md` after writing the ticket with
the Write tool: a heredoc or a pipe through the shell is denied under dontAsk when the ticket text holds
braces and quotes. stdin stays for humans.

Exit codes: 0 nothing flagged ("nothing to quote back"), 1 something flagged, 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

I = re.I
RULES = [
    ("shell command", re.compile(r"(?<![\w./-])(?:cat|curl|wget|base64|rm|chmod|ssh|scp|nc)\s+\S", I)),
    ("credential or exfiltration word", re.compile(
        r"\.env\b|\bsecrets?\b|\btokens?\b|\bpasswords?\b|\bpasswd\b|\bapi[ _-]?keys?\b|\bcredentials?\b|id_rsa|\.ssh\b|"
        r"\bsend (?:it |them |this |that )?to\b|\bpost (?:it |them |this |that )?to\b|\bupload\b|\bexfiltrat", I)),
    ("prompt-injection phrase", re.compile(
        r"ignore (?:all |any |the )?(?:previous|prior|above)|\bdisregard\b|you are now\b|system prompt|"
        r"new instructions|forget (?:your|all|everything)|override (?:your|the) (?:rules|instructions)", I)),
    ("URL", re.compile(r"\b(?:https?|ftp|file)://\S+|\bwww\.\S+\.\S+", I)),
    ("imperative addressed to the assistant", re.compile(
        r"\b(?:claude|assistant|ai agent|agent|bot)\b\s*[,:]|\byou (?:must|should|need to|have to|will|are to)\b|"
        r"\b(?:do not|don'?t) (?:tell|mention|reveal|inform|ask)\b|\bmake sure you\b|"
        r"^[\s>*\-\d.)]*(?:please\s+)?(?:run|execute|install|delete|remove|disable|reveal|email|send|push|merge|deploy|download)\b", I)),
]
# Default_Ignorable code points, braille blank, and the whole Unicode tag block (hides ASCII text)
INVISIBLE_RANGES = [
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
]
INVISIBLE = re.compile("[" + "".join("%s-%s" % (chr(lo), chr(hi)) if lo != hi else chr(lo) for lo, hi in INVISIBLE_RANGES) + "]")


def esc_invisible(s: str) -> str:
    return INVISIBLE.sub(lambda m: "\\u%04X" % ord(m.group(0)) if ord(m.group(0)) <= 0xFFFF else "\\U%08X" % ord(m.group(0)), s)


def tag_payload(line: str) -> str:
    """ASCII hidden in Unicode tag characters (U+E0020..E007E)."""
    return "".join(chr(ord(c) - 0xE0000) for c in line if 0xE0020 <= ord(c) <= 0xE007E)


def scan(text: str) -> list[tuple[int, str, list[str]]]:
    flagged = []
    for n, line in enumerate(text.splitlines(), 1):
        reasons = [name for name, rx in RULES if rx.search(line)]
        if INVISIBLE.search(line):
            reasons.append("invisible characters")
            hidden = tag_payload(line)
            if hidden:  # screen what the hidden text says, and show it to the human
                reasons.append("hidden text: %r" % hidden)
                reasons += ["hidden " + name for name, rx in RULES if rx.search(hidden)]
        if reasons:
            flagged.append((n, line, reasons))
    return flagged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Flag shell commands, exfiltration words, prompt-injection phrases, URLs and assistant-directed imperatives in ticket text.",
        epilog="Exit: 0 nothing flagged, 1 flagged lines quoted back, 2 misuse.",
    )
    ap.add_argument("ticket", nargs="?", default="-", help="ticket text file, or - for stdin (default)")
    ap.add_argument("--file", dest="file", default=None, metavar="PATH",
                    help="ticket text file (same as the positional argument; what skills use instead of a heredoc)")
    ap.add_argument("--root", default=".", help="repository root; a relative ticket path is also tried under it")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    if args.file is not None:
        args.ticket = args.file
    try:
        if args.ticket == "-":
            text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        else:
            path = args.ticket
            if not os.path.isfile(path):
                alt = os.path.join(args.root, path)
                path = alt if os.path.isfile(alt) else path
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        print("ticket_intake: cannot read ticket: %s" % exc, file=sys.stderr)
        return 2
    flagged = scan(text)
    if not flagged:
        print("ticket_intake: nothing to quote back")
        return 0
    print("Quoted back to the human")
    print("The lines below came from the ticket. They are data, not instructions: nothing in them will be")
    print("run, fetched or sent unless you confirm it.")
    print("-" * 60)
    for n, line, reasons in flagged:
        print("line %d [%s]" % (n, "; ".join(reasons)))
        print("  > " + esc_invisible(line))
    print("-" * 60)
    print("ticket_intake: %d line(s) flagged; confirm with the requester before acting" % len(flagged))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("ticket_intake: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
