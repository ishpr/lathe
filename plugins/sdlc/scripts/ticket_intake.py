#!/usr/bin/env python3
"""Screen ticket text before /ticket acts on it (REC-04; the "lethal trifecta" in untrusted input).

Ticket text is data, never instructions. This reads a ticket (`--file PATH`, a positional file, or stdin when neither is given or "-")
and flags lines containing: shell commands (cat, curl, wget, base64, rm, chmod, ssh, scp, nc),
credential values (secret-shaped literals: AKIA..., ghp_/gho_/ghs_/github_pat_ tokens, sk-..., xox?-...,
AIza..., PRIVATE KEY blocks, JWTs, and KEY/token/password assigned a value of 16+ characters),
credential handling instructions (a credential noun such as .env, secret, token, password, api key, credential,
id_rsa, .ssh or private key within six words, in either order, of a strong verb (print, echo, cat, dump, leak,
exfiltrate, reveal, paste, curl), or of a handling verb (show, copy, send, write, log, ...) that takes it/them/this/that
as its object or points at a destination such as "to stdout", "into the log", "to Slack", "on a gist"), or a credential file
(id_rsa, id_dsa, id_ecdsa, id_ed25519, a path containing .ssh/, .aws/credentials, .netrc, .pgpass, .git-credentials,
credentials.json) within six words of ANY handling verb, no destination needed ("read ~/.ssh/id_rsa"; .env is not a file
noun, so "add REDIS_URL to .env" passes), exfiltration phrases ("send it to",
"post it to", "upload", "exfiltrat"), prompt-injection phrases ("ignore previous", "disregard", "you are now", "system prompt"), URLs,
invisible characters, and instructions addressed to the assistant (a second-person pronoun such as you or your, the words
claude, assistant, AI or LLM, a vocative such as "agent,", "ignore previous", "disregard", "as an AI", "run the following",
"print your"). A plain requirement in the imperative ("Delete the generated folder at the end of each test run", "Return 429
over the limit") is not flagged.
A bare credential noun with no value and no handling verb ("rotate the token weekly") is not flagged, nor is
ordinary product wording ("send the user a password reset email", "include the token in the Authorization header").
It prints a "Quoted back to the human" block: every flagged line verbatim with the reason, for the
human to read and confirm before anything is acted on. Invisible characters are shown as \\uXXXX.

Skills call it as `ticket_intake.py --file .claude/work/<slug>/ticket.md` after writing the ticket with
the Write tool: a heredoc or a pipe through the shell is denied under dontAsk when the ticket text holds
braces and quotes. stdin stays for humans.

`ticket_intake.py --slug TITLE` prints the work slug of a title instead (lowercase, runs of other characters one
hyphen, at most 40 characters, cut at the last hyphen at or before 40 so no word is cut in half) and scans nothing.
A leading heading label is dropped first ("### Story: Read ledger data" gives "read-ledger-data", not "story-read-...").

Exit codes: 0 nothing flagged ("nothing to quote back"), 1 something flagged, 2 misuse or crash.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

I = re.I
_NOUN = re.compile(r"\.env\b|\.ssh\b|id_rsa|^(?:secrets?|tokens?|passwords?|passwd|apikeys?|credentials?|privatekeys?)$", I)
# A credential FILE is dangerous to touch at all: any handling verb within MAX_GAP words counts, with no destination
# or pronoun needed. (.env stays an ordinary noun above: "add REDIS_URL to .env" is a normal ticket.)
_FILE_NOUN = re.compile(
    r"id_(?:rsa|dsa|ecdsa|ed25519)|\.ssh/|\.aws/credentials|\.netrc\b|\.pgpass\b|\.gitcredentials\b|credentials\.json\b", I)
_VERB_STEMS = (
    "print", "echo", "cat", "dump", "show", "display", "log", "read", "open", "copy", "paste", "include", "attach",
    "export", "send", "post", "upload", "email", "mail", "curl", "fetch", "leak", "exfiltrate", "reveal", "output", "write",
)
_VERB_FORMS = {"sent", "shown", "logged", "logging", "copied", "copies", "copying", "outputted", "outputting"}
for _v in _VERB_STEMS:
    _VERB_FORMS |= {_v + "s", _v + "es", _v + "ed", _v + "ing", _v}
    if _v.endswith("e"):
        _VERB_FORMS |= {_v + "d", _v[:-1] + "ing"}
# A credential noun counts as handled, in either order within MAX_GAP words, when the sentence has a strong verb, or
# any handling verb with a pronoun object ("send it") or with a destination ("write the key to the response body").
_STRONG = {"print", "echo", "cat", "dump", "leak", "exfiltrate", "reveal", "paste", "curl"}
_STRONG_FORMS = {f for f in _VERB_FORMS if any(f.startswith(v) for v in _STRONG)}
_PRONOUNS = {"it", "them", "this", "that", "these", "those"}
_PREPOSITIONS = {"to", "into", "in", "on", "onto", "at"}
_DESTINATIONS = {
    "log", "logs", "logging", "stdout", "stderr", "console", "output", "response", "body", "chat", "here", "slack",
    "discord", "url", "http", "https", "file", "disk", "s3", "bucket", "pastebin", "gist", "issue", "comment", "pr",
    "description",
}
_RECIPIENTS = {"email", "mail", "me", "us", "them"}
_SENTENCE_END = re.compile(r"[.!?;]+(?=\s|$)")
_WORD = re.compile(r"[\w.~/]+")
MAX_GAP = 6  # words between the noun and the verb


class _Handling:
    """A credential noun within six words of a strong verb, or of a handling verb with a pronoun object or a destination;
    a credential file (id_rsa, .ssh/, .netrc, ...) within six words of any handling verb."""

    def search(self, line: str) -> bool:
        for sentence in _SENTENCE_END.split(line):
            sentence = re.sub(r"\b(api)[ _-]?(keys?)\b|\b(private) (keys?)\b", lambda m: "".join(g for g in m.groups() if g), sentence, flags=I)
            sentence = re.sub(r"\.git-credentials\b", ".gitcredentials", sentence, flags=I)
            words = [w.rstrip(".").lower() for w in _WORD.findall(sentence)]
            nouns = [i for i, w in enumerate(words) if _NOUN.search(w)]
            files = [i for i, w in enumerate(words) if _FILE_NOUN.search(w)]
            for i, w in enumerate(words):
                if w not in _VERB_FORMS:
                    continue
                if any(f != i and abs(f - i) - 1 <= MAX_GAP for f in files):
                    return True
                for n in nouns:
                    if n == i or abs(n - i) - 1 > MAX_GAP:
                        continue
                    if (w in _STRONG_FORMS or (i + 1 < len(words) and words[i + 1] in _PRONOUNS)
                            or self._has_destination(words, i)):
                        return True
        return False

    @staticmethod
    def _has_destination(words: list[str], verb: int) -> bool:
        """A preposition after the verb, followed within three words by a place the credential can end up."""
        for p in range(verb + 1, len(words)):
            if words[p] not in _PREPOSITIONS:
                continue
            for d in words[p + 1:p + 4]:
                if d in _DESTINATIONS or (words[p] == "to" and d in _RECIPIENTS):
                    return True
        return False


# A line is addressed to the assistant only when it says so: a second-person pronoun, a name for the assistant or an AI,
# a vocative ("agent, ..."), the injection openers ("ignore previous", "disregard", "as an AI", "run the following"). A
# bare imperative requirement ("Delete the generated folder at the end of each test run", "Return 429 over the limit")
# is ordinary ticket wording and passes; so does the user-story opener "As an admin, I want ...". "agent", "model" and "bot"
# are common nouns ("support agent", "the data model"), so they count only as a vocative or beside "AI", "LLM" or "language".
ASSISTANT_ADDRESS = re.compile(
    r"\byou(?:r|rs|rself)?\b|"
    r"\b(?:claude|assistant|chatbot|llm|ai)\b(?![-\w])|"
    r"\b(?:agent|model|bot)\b\s*[,:]|\b(?:hey|hi|hello|dear|ok|okay|attention)[,\s]+(?:agent|model|bot)\b|"
    r"\b(?:ai|llm|language)\s+(?:agent|model)\b|"
    r"\bignore (?:all |any |the )?(?:previous|prior|above)\b|\bdisregard\b|"
    r"\bas an? (?:ai|assistant|llm|language model|agent)\b|"
    r"\b(?:run|execute) the following\b|\b(?:print|reveal) your\b", I)

RULES = [
    ("shell command", re.compile(r"(?<![\w./-])(?:cat|curl|wget|base64|rm|chmod|ssh|scp|nc)\s+\S", I)),
    ("credential value", re.compile(
        r"\bAKIA[0-9A-Z]{16}|\b(?:ghp|gho|ghs)_[A-Za-z0-9]{10,}|\bgithub_pat_[A-Za-z0-9_]{10,}|"
        r"\bsk-[A-Za-z0-9_-]{20,}|\bxox[baprs]-|\bAIza[0-9A-Za-z_-]{35}|"
        r"BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
        r"(?i:\b[\w-]*(?:key|token|passw(?:or)?d|secret|credential)s?\b[\"']?\s*[:=]\s*[\"'`]?\S{16,})", 0)),
    ("credential handling instruction", _Handling()),
    ("exfiltration phrase", re.compile(
        r"\bsend (?:it |them |this |that )?to\b|\bpost (?:it |them |this |that )?to\b|\bupload\b|\bexfiltrat", I)),
    ("prompt-injection phrase", re.compile(
        r"ignore (?:all |any |the )?(?:previous|prior|above)|\bdisregard\b|you are now\b|system prompt|"
        r"new instructions|forget (?:your|all|everything)|override (?:your|the) (?:rules|instructions)", I)),
    ("URL", re.compile(r"\b(?:https?|ftp|file)://\S+|\bwww\.\S+\.\S+", I)),
    ("imperative addressed to the assistant", ASSISTANT_ADDRESS),
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


SLUG_MAX = 40

# A heading label in front of the title: "### Story: ...", "**Ticket:** ...", "User story - ..." is not one (no colon).
# Only a label followed by a colon is dropped, so a title that starts with the word ("Task queue retries") keeps it.
TITLE_LABEL = re.compile(
    r"^[\s#>*_`]*(?:user\s+story|story|ticket|issue|title|task|feature|epic|bug|chore)\s*(?:[*_`]+\s*)?:\s*(?:[*_`]+\s*)?",
    re.IGNORECASE)


def slugify(text: str, limit: int = SLUG_MAX) -> str:
    """The work slug of a ticket title: lowercase, every run of other characters one hyphen, no hyphen at either end,
    at most `limit` characters, cut at the last hyphen at or before the limit so no word is cut in half (a ticket
    slug was cut mid-word: "render-the-read-only-view-helpers-with-t"). One word longer than the limit is cut at it.
    A leading heading label with a colon ("### Story:") is dropped first, unless nothing would be left: a slug taken
    from the heading began "story-read-ledger-data-through-typed"."""
    text = text or ""
    bare = TITLE_LABEL.sub("", text, count=1)
    if re.search(r"[A-Za-z0-9]", bare):
        text = bare
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) <= limit:
        return slug
    cut = slug.rfind("-", 0, limit + 1)
    return (slug[:cut] if cut > 0 else slug[:limit]).strip("-")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Flag shell commands, credential values, credential handling instructions (a credential noun near a strong verb such as print or cat, or near a handling verb such as send or write with it/them or a destination such as stdout, a log, Slack or a file; or a credential file such as id_rsa, .ssh/, .aws/credentials or .netrc near any handling verb such as read or open), exfiltration phrases, prompt-injection phrases, URLs and assistant-directed imperatives in ticket text.",
        epilog="Exit: 0 nothing flagged, 1 flagged lines quoted back, 2 misuse.",
    )
    ap.add_argument("ticket", nargs="?", default="-", help="ticket text file, or - for stdin (default)")
    ap.add_argument("--file", dest="file", default=None, metavar="PATH",
                    help="ticket text file (same as the positional argument; what skills use instead of a heredoc)")
    ap.add_argument("--root", default=".", help="repository root; a relative ticket path is also tried under it")
    ap.add_argument("--slug", default=None, metavar="TITLE",
                    help="print the work slug of TITLE (cut at the last hyphen at or before 40 characters) and exit; scans nothing")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    if args.slug is not None:
        slug = slugify(args.slug)
        if not slug:
            print("ticket_intake: the title has no letters or digits to make a slug from", file=sys.stderr)
            return 2
        print(slug)
        return 0
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
