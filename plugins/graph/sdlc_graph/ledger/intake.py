"""Ticket intake and ``contract_draft``.

``scan`` applies the pack's ticket-intake rules server-side (port of the sdlc plugin's
``scripts/ticket_intake.py`` RULES: shell commands,
credential/exfiltration words, prompt-injection phrases, URLs, invisible characters, imperative
instructions to the assistant). Ticket text is data, never instructions.

``contract_draft`` order: scan -> dedupe -> overlap rule -> create. The overlap rule: an open
contract in the same repo whose touched_symbols overlap (nodes.symbols_overlap) must be linked by
a ``depends_on`` edge from the new contract (``depends_on`` argument), else nothing is created
and the conflict is returned.

The scan is advisory, a heuristic a determined author can evade; the real protections are that
ticket text is stored as data, ticket-derived acceptance rows carry ``source: "ticket"`` (a human
must mark the contract ready), control characters never reach storage, and titles are single-line.

Flagged lines are stored in ``node.attrs["intake"]`` (line, escaped text, reasons) for a human to
read; they never become the title, and acceptance lines derived from the ticket skip them. The raw
ticket text is not stored (its sha256 is).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

import psycopg

I = re.I
RULES: list[tuple[str, re.Pattern[str]]] = [
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
        r"^(?:\s|>|\*|-|\d+[.)]|\[[ xX]\])*(?:please\s+)?(?:run|execute|install|delete|remove|disable|reveal|email|send|push|merge|deploy|download)\b", I)),
    ("gate or review bypass", re.compile(
        r"\b(?:mark|set|move|flag)\b.{0,40}\b(?:approved|merged|released|closed|verified)\b|"
        r"\b(?:merge|approve|release|deploy)\b.{0,40}\b(?:without|skip(?:ping)?|bypass(?:ing)?)\b|"
        r"\b(?:skip|bypass|disable)\b.{0,30}\b(?:review|approval|guards?|checks?|tests?)\b|\bpush(?:ed)? to (?:main|master|prod)", I)),
]
INVISIBLE = re.compile("[​-‏ -‮⁠-⁤⁦-⁩﻿\xad͏؜᠎]")
#: C0 and C1 controls except tab and newline (ESC sequences, bell, NUL ...).
CONTROLS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Cyrillic and Greek letters that read as Latin ones; folded before the rules run.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "і": "i", "ј": "j", "ѕ": "s", "һ": "h",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "І": "I",
    "К": "K", "М": "M", "Н": "H", "Т": "T",
    "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x",
    "Ο": "O", "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ρ": "P", "Τ": "T", "Χ": "X"})
_MIXED_SCRIPT = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[\u0370-\u03ff\u0400-\u04ff])\w+\b")


def fold(text: str) -> str:
    """NFKC, confusable letters mapped to Latin, controls and invisibles removed, runs of blanks
    collapsed (for matching only)."""
    text = CONTROLS.sub("", INVISIBLE.sub("", unicodedata.normalize("NFKC", str(text or "")).translate(_CONFUSABLES)))
    return re.sub(r"[ \t]+", " ", text)


def clean_text(text: str, *, single_line: bool = False) -> str:
    """Strip C0/C1 controls and invisible characters from text that is about to be stored; with
    ``single_line`` a newline is an error."""
    text = str(text or "")
    if single_line and re.search(r"[\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]", text):
        raise ValueError(f"must be a single line, got {text!r}")
    return CONTROLS.sub("", INVISIBLE.sub("", text))
ACCEPTANCE = re.compile(
    r"^\s*(?:[-*]\s*\[[ xX]\]\s+.+|(?:given|when|then|and)\b.+|AC\d+\s*[:.)\-]\s*.+)$", I)
DEDUPE_SIMILARITY = 0.6
#: A contract slug names the git branch ``sdlc/<slug>`` and a worktree directory: branch-safe characters only.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def esc_invisible(s: str) -> str:
    """Show invisible and control characters as ``\\uXXXX`` (as the pack's script does)."""
    return CONTROLS.sub(lambda m: "\\u%04X" % ord(m.group(0)),
                        INVISIBLE.sub(lambda m: "\\u%04X" % ord(m.group(0)), s))


def scan(ticket_text: str) -> dict[str, Any]:
    """Screen ticket text.

    Returns:
        ``{"flags": [{"line": int, "text": str, "reason": str, "reasons": [str]}], "clean": bool,
        "acceptance": [lines that look like acceptance criteria ("- [ ] ...", "Given/When/Then",
        "AC1: ...")]}``. ``text`` has invisible characters escaped. Flagged lines are never in
        ``acceptance``. Pure, never raises on content.
    """
    flags: list[dict[str, Any]] = []
    acceptance: list[str] = []
    lines = str(ticket_text or "").splitlines()
    folded = [fold(line) for line in lines]
    by_line: dict[int, list[str]] = {}
    for n, (line, fl) in enumerate(zip(lines, folded), 1):
        reasons = [name for name, rx in RULES if rx.search(fl)]
        if INVISIBLE.search(line):
            reasons.append("invisible characters")
        if CONTROLS.search(line):
            reasons.append("control characters")
        if _MIXED_SCRIPT.search(unicodedata.normalize("NFKC", line)):
            reasons.append("mixed-script (homoglyph) text")
        by_line[n] = reasons
    # A phrase split over two lines is still the phrase.
    for n in range(1, len(lines)):
        joined = folded[n - 1].rstrip() + " " + folded[n].lstrip()
        for name, rx in RULES:
            if rx.search(joined) and name not in by_line[n] and name not in by_line[n + 1]:
                for k in (n, n + 1):
                    by_line[k].append(name)
    for n, line in enumerate(lines, 1):
        reasons = by_line[n]
        if reasons:
            flags.append({"line": n, "text": esc_invisible(line), "reason": "; ".join(reasons), "reasons": reasons})
        elif ACCEPTANCE.match(line):
            acceptance.append(line.strip())
    return {"flags": flags, "clean": not flags, "acceptance": acceptance}


def _acceptance_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows = []
    for i, line in enumerate(lines, 1):
        text = re.sub(r"^[-*]\s*\[[ xX]\]\s+", "", line)
        m = re.match(r"^(AC\d+)\s*[:.)\-]\s*(.+)$", text, I)
        # source "ticket": text from an issue body, quoted as data downstream; a human marks the contract ready.
        rows.append({"id": m.group(1).upper(), "text": m.group(2).strip(), "source": "ticket"} if m
                    else {"id": f"AC{i}", "text": text.strip(), "source": "ticket"})
    return rows


def _words(text: str) -> str:
    """Case-folded, NFKC, confusables mapped, punctuation and whitespace collapsed to single spaces."""
    return " ".join(re.sub(r"[\W_]+", " ", fold(text).casefold()).split())


def _title_carries_flag(title: str, flags: list[dict[str, Any]], ticket_text: str) -> str | None:
    lines = str(ticket_text or "").splitlines()
    t = _words(title)
    for f in flags:
        raw = lines[f["line"] - 1].strip() if 0 < f["line"] <= len(lines) else ""
        w = _words(raw)
        if len(w) >= 8 and w in t:
            return raw
    if INVISIBLE.search(title) or CONTROLS.search(title):
        return title
    injected = [name for name, rx in RULES if name in ("prompt-injection phrase", "gate or review bypass") and rx.search(fold(title))]
    if injected:
        return title
    return None


def contract_draft(conn: psycopg.Connection, *, slug: str, title: str, repo: str, ticket_text: str, actor: str,
                   feature: str | None = None, touched_symbols: list[str] | None = None,
                   capabilities: dict[str, Any] | None = None, acceptance: list[dict[str, Any]] | None = None,
                   depends_on: list[str] | None = None, tier: str | None = None,
                   external_id: str | None = None, defaults: list[str] | None = None,
                   contract_text: str | None = None) -> dict[str, Any]:
    """Draft a contract node in state ``drafted``.

    The contract's autonomy blocks can be attached at draft time, so a contract rendered from the
    ledger already carries them: ``contract_text`` (a contract.md) is parsed for ``## Defaults when
    ambiguous`` and ``## Capabilities`` (``sdlc_graph.orchestrator.contract_blocks.attach``), and
    explicit ``defaults`` (lines) replace whatever defaults the text held. Explicit ``capabilities``
    are stored first and a Capabilities block in the text is merged over them. A refused block
    (a capability ``impact.capabilities.validate`` rejects) raises ValueError and nothing is created.
    ``result["blocks"]`` reports ``missing`` blocks and capability lines not ``unknown`` (both empty
    when none were given: the ``drafted -> ready`` guard still names what is missing).

    Steps: ``scan`` (flags are returned and stored in ``attrs.intake``, never block); dedupe against
    open contracts in the repo (same external_id, or pg_trgm ``similarity(title) >= 0.6``; contracts
    named in ``depends_on`` are related, not duplicates); overlap rule; on success create the node,
    link ``feature -[decomposes_into]-> contract`` when ``feature`` is given and
    ``contract -[depends_on]-> dep`` for each ``depends_on`` slug. With no ``acceptance`` given,
    the ticket's unflagged acceptance lines become it.

    Returns:
        ``{"status": "created"|"conflict"|"duplicate", "node": Node.to_dict()|None,
        "conflicts": [{"slug", "state", "overlap": [...]}], "duplicates": [{"slug", "title",
        "similarity"}], "flags": [...]}``. Conflict and duplicate are normal returns.

    Raises:
        ValueError: bad actor, malformed touched_symbols entry, a title that repeats a flagged
        ticket line. LookupError: unknown feature or depends_on slug.
        psycopg.errors.UniqueViolation: slug exists.
    """
    from . import nodes
    from .transitions import actor_class, terminal_states

    actor_class(actor)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if CONTROLS.search(title):
        raise ValueError(f"title contains control characters: {esc_invisible(title)!r}")
    title = clean_text(title, single_line=True)
    if not title.strip():
        raise ValueError("title must be a non-empty string")
    slug = clean_text(slug, single_line=True)
    if not SLUG_RE.match(slug) or ".." in slug or slug.endswith((".", ".lock")):
        raise ValueError(f"slug {slug!r} must be 1-100 characters of letters, digits, '.', '_' or '-', starting with a letter or digit "
                         f"(it names a git branch and a worktree directory)")
    touched = nodes.validate_touched(touched_symbols)
    result = scan(ticket_text)
    flags = result["flags"]
    carried = _title_carries_flag(title, flags, ticket_text)
    if carried is not None:
        raise ValueError(f"title repeats a flagged ticket line ({esc_invisible(carried)!r}); write the title yourself")
    deps = list(dict.fromkeys(depends_on or []))
    if acceptance is None:
        acceptance = _acceptance_rows(result["acceptance"])
    else:
        # Caller-supplied rows are scanned too: flags are recorded for a human, control characters
        # are stripped, nothing is rejected (the scan is advisory).
        cleaned = []
        for i, row in enumerate(acceptance):
            row = dict(row) if isinstance(row, dict) else row
            if isinstance(row, dict):
                for key in ("id", "text"):
                    if isinstance(row.get(key), str):
                        for f in scan(row[key])["flags"]:
                            # credential words are normal in an auth contract's criteria; keep the rest
                            reasons = [r for r in f["reasons"] if r != "credential or exfiltration word"]
                            if reasons:
                                flags = flags + [{**f, "reason": "; ".join(reasons), "reasons": reasons, "line": 0,
                                                  "field": f"acceptance[{i}].{key}"}]
                        row[key] = clean_text(row[key])
            cleaned.append(row)
        acceptance = cleaned
    attrs = {"intake": {"clean": result["clean"], "flags": flags, "drafted_by": actor,
                        "ticket_sha256": hashlib.sha256(str(ticket_text or "").encode("utf-8")).hexdigest()}}
    out: dict[str, Any] = {"status": "created", "node": None, "conflicts": [], "duplicates": [], "flags": flags}
    closed = sorted(terminal_states("contract"))
    with conn.transaction():
        # Serialise drafts per repo so two concurrent drafts cannot both pass the overlap rule.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"contract_draft:{repo}",))
        feature_row = None
        if feature:
            feature_row = nodes.get_row(conn, feature)
            if feature_row["kind"] not in ("feature", "epic", "story"):
                raise ValueError(f"{feature!r} is a {feature_row['kind']}; a contract decomposes from a feature, epic or story")
        for d in deps:
            nodes.get_row(conn, d)
        dupes = conn.execute(
            """SELECT slug, title, similarity(title, %s) AS sim, external_id FROM node
               WHERE kind = 'contract' AND repo = %s AND NOT (state = ANY(%s)) AND NOT (slug = ANY(%s))
                 AND ((%s::text IS NOT NULL AND external_id = %s) OR similarity(title, %s) >= %s)
               ORDER BY sim DESC, slug""",
            (title, repo, closed, deps, external_id, external_id, title, DEDUPE_SIMILARITY),
        ).fetchall()
        if dupes:
            out["status"] = "duplicate"
            out["duplicates"] = [{"slug": r["slug"], "title": r["title"], "similarity": round(float(r["sim"]), 3),
                                  "same_external_id": external_id is not None and r["external_id"] == external_id}
                                 for r in dupes]
            return out
        conflicts = [c for c in nodes.overlaps(conn, repo, touched, exclude_slug=slug) if c["slug"] not in deps]
        if conflicts:
            out["status"] = "conflict"
            out["conflicts"] = conflicts
            return out
        node = nodes.create(conn, kind="contract", slug=slug, title=title.strip(), repo=repo, created_by=actor,
                            tier=tier, external_id=external_id, touched_symbols=touched,
                            capabilities=capabilities, acceptance=acceptance, attrs=attrs)
        if feature_row is not None:
            nodes.link(conn, feature, slug, "decomposes_into")
        for d in deps:
            nodes.link(conn, slug, d, "depends_on")
        if contract_text is not None or defaults is not None:
            from sdlc_graph.orchestrator import contract_blocks

            blocks: dict[str, Any] = {"missing": [], "unknown": []}
            if contract_text is not None:
                got = contract_blocks.attach(conn, slug, str(contract_text), actor=actor, source="contract_draft")
                blocks = {"missing": got["missing"], "unknown": got["unknown"], "sha256": got["sha256"]}
            if defaults is not None:
                if not isinstance(defaults, list) or not all(isinstance(d, str) for d in defaults):
                    raise ValueError("defaults must be a list of lines")
                got = contract_blocks.set_blocks(conn, slug, actor=actor, defaults=[clean_text(d) for d in defaults],
                                                 source="contract_draft")
                blocks["missing"] = got["missing"]
            out["blocks"] = blocks
            node = nodes.Node.from_row(nodes.get_row(conn, slug)).to_dict()
    out["node"] = node
    return out
