"""Ticket intake and ``contract_draft``.

``scan`` applies the pack's ticket-intake rules server-side (port of the pack's
``scripts/ticket_intake.py`` RULES: shell commands,
credential values, credential handling instructions, exfiltration phrases, prompt-injection phrases, URLs, invisible characters, imperative
instructions to the assistant). Ticket text is data, never instructions.

``contract_draft`` order: (parse contract.md) -> scan -> dedupe -> overlap rule -> create. ``contract_update``
re-reads a contract.md onto a drafted or ready contract (``ledger.contract_md`` parses both). The overlap rule: an open
contract in the same repo whose touched_symbols overlap (nodes.symbols_overlap) must be linked by
a ``depends_on`` edge from the new contract (``depends_on`` argument), else nothing is created
and the conflict is returned. On ``contract_update`` an edge in either direction counts (the contract's
dependencies and its dependents), since either one orders the two contracts.

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
from psycopg.types.json import Jsonb

I = re.I
# "credential handling instruction" (port of the sdlc script's _Handling): a credential noun in the same sentence
# within MAX_GAP words, in either order, of a strong verb, or of a handling verb with a pronoun object or a
# destination. A bare noun ("API key", "token") is ordinary in an auth ticket and is not flagged on its own.
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


HANDLING = "credential handling instruction"
RULES: list[tuple[str, Any]] = [
    ("shell command", re.compile(r"(?<![\w./-])(?:cat|curl|wget|base64|rm|chmod|ssh|scp|nc)\s+\S", I)),
    ("credential value", re.compile(
        r"\bAKIA[0-9A-Z]{16}|\b(?:ghp|gho|ghs)_[A-Za-z0-9]{10,}|\bgithub_pat_[A-Za-z0-9_]{10,}|"
        r"\bsk-[A-Za-z0-9_-]{20,}|\bxox[baprs]-|\bAIza[0-9A-Za-z_-]{35}|"
        r"BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
        r"(?i:\b[\w-]*(?:key|token|passw(?:or)?d|secret|credential)s?\b[\"']?\s*[:=]\s*[\"'`]?\S{16,})", 0)),
    (HANDLING, _Handling()),
    ("exfiltration phrase", re.compile(
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


def _scan_rows(acceptance: list[Any], flags: list[dict[str, Any]], *, field: str = "acceptance") -> tuple[list[Any], list[dict[str, Any]]]:
    """Caller-supplied (or contract.md) acceptance rows are scanned too: flags are recorded for a human, control
    characters are stripped, nothing is rejected (the scan is advisory)."""
    cleaned = []
    for i, row in enumerate(acceptance):
        row = dict(row) if isinstance(row, dict) else row
        if isinstance(row, dict):
            for key in ("id", "text", "name"):
                if isinstance(row.get(key), str):
                    for f in scan(row[key])["flags"]:
                        # sending or showing a token is normal in an auth contract's criteria; keep the rest
                        reasons = [r for r in f["reasons"] if r != HANDLING]
                        if reasons:
                            flags = flags + [{**f, "reason": "; ".join(reasons), "reasons": reasons, "line": 0,
                                              "field": f"{field}[{i}].{key}"}]
                    row[key] = clean_text(row[key])
        cleaned.append(row)
    return cleaned, flags


def _clean_defaults(defaults: Any) -> list[str]:
    if not isinstance(defaults, list) or not all(isinstance(d, str) for d in defaults):
        raise ValueError("defaults must be a list of lines")
    clean = [clean_text(d).strip() for d in defaults if d.strip()]
    if not clean:
        raise ValueError("defaults must hold at least one non-empty line")
    return clean


def _blocks_missing(*, parsed: dict[str, Any] | None, strict: bool, acceptance: Any, caps: dict[str, Any],
                    attrs: dict[str, Any], tier: str | None) -> list[str]:
    """What the drafted -> ready guards will still ask for, by contract.md block, after the explicit arguments and
    the parsed contract.md are combined (a block the caller passed explicitly is not missing)."""
    from .transitions import acceptance_rows_ok, capabilities_missing, defaults_ok, paths_ok

    out: list[str] = []
    if (parsed is not None or strict) and not acceptance_rows_ok(acceptance):
        out.append("## Acceptance criteria")
    if parsed is not None:
        out += [m for m in parsed["missing"] if m.startswith("## Decisions")]
    if not defaults_ok(attrs):
        out.append("## Defaults when ambiguous")
    absent = capabilities_missing(caps)
    if absent:
        out.append("## Capabilities (" + ", ".join(absent) + ")")
    if (parsed is not None or strict) and not absent and not paths_ok(caps.get("paths")):
        out.append("## Allowed paths")
    if parsed is not None and tier is None:
        out.append("## Risk tier")
    return out


def _index_context(conn: psycopg.Connection, *, repo: str, slug: str, title: str, ticket_text: str, touched: list[str],
                   caps: dict[str, Any], deps: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(semantic duplicates, precedent, rules)`` from the retrieval index (docs/DESIGN.md 2.2), each ``[]`` when
    the index is not built, no embedding provider is set, or the lookup fails (inside a savepoint, so a failure
    cannot abort the caller's transaction). Never gates the draft."""
    from sdlc_graph.index import search

    try:
        with conn.transaction():
            paths = [str(p) for p in (caps.get("paths") or touched)]
            ctx = search.intake_context(conn, repo=repo, title=title, ticket_text=ticket_text, paths=paths or touched,
                                        exclude_slugs=[slug, *deps])
            return ctx["duplicates"], ctx["precedent"], ctx["rules"]
    except Exception:  # noqa: BLE001 - optional enrichment: any failure (no pgvector, a provider fault) degrades to none
        return [], [], []


def _log_entry(actor: str, state: str, source: str, *, defaults: bool, capability_keys: list[str]) -> dict[str, Any]:
    import datetime as dt

    return {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "actor": actor, "state": state,
            "source": source, "defaults": defaults, "capability_keys": sorted(capability_keys)}


def contract_draft(conn: psycopg.Connection, *, slug: str, title: str, repo: str, ticket_text: str, actor: str,
                   feature: str | None = None, touched_symbols: list[str] | None = None,
                   capabilities: dict[str, Any] | None = None, acceptance: list[dict[str, Any]] | None = None,
                   depends_on: list[str] | None = None, tier: str | None = None,
                   external_id: str | None = None, defaults: list[str] | None = None,
                   contract_text: str | None = None, strict: bool = False,
                   allow_duplicate: bool = False) -> dict[str, Any]:
    """Draft a contract node in state ``drafted``.

    ``contract_text`` (the whole contract.md, the sdlc pack's template) is parsed by
    ``ledger.contract_md.parse_contract`` and fills every block the caller did not pass explicitly:
    ``touched_symbols`` from ``## Allowed paths`` (a glob reduces to its directory: ``src/lib/api/**`` ->
    ``src/lib/api/``), ``capabilities`` from ``## Capabilities`` (per key: an explicit key wins; with no Paths line
    the Allowed paths are used), ``defaults`` from ``## Defaults when ambiguous``, ``acceptance`` from ``## Acceptance
    criteria`` (``{id, text, name, source: "contract"}``, scanned like caller rows), ``tier`` from ``## Risk tier``.
    The Decisions table goes to ``attrs["decisions"]`` (the ``decisions_resolved`` guard reads it), the Acceptance
    lines to ``attrs["acceptance_lines"]``, and the text itself to ``node.contract_md``. Capabilities from the text are
    validated by ``impact.capabilities.validate``: a refused one raises ValueError and nothing is created.

    ``result["blocks"]`` (present when ``contract_text``, ``defaults`` or ``strict`` is given) reports ``missing``
    blocks (what the ``drafted -> ready`` guards will still ask for: ``## Acceptance criteria``, ``## Decisions ...``,
    ``## Defaults when ambiguous``, ``## Capabilities (<keys>)``, ``## Allowed paths``, ``## Risk tier``), capability
    lines not understood (``unknown``), ``warnings`` and the text's ``sha256``. With ``strict`` a draft with any
    missing block is refused: status ``refused``, nothing created.

    Steps: ``scan`` (flags are returned and stored in ``attrs.intake``, never block); dedupe against
    open contracts in the repo (same external_id; an open contract with a chunk within
    ``index.search.DEDUPE_DISTANCE`` cosine distance of the title or ticket text, when the index and an embedding
    provider exist; else pg_trgm ``similarity(title) >= 0.6``, which also runs alongside; contracts
    named in ``depends_on`` are related, not duplicates; ``allow_duplicate`` lets the caller draft anyway, the
    candidates then ride along as ``duplicates_overridden``); overlap rule; on success create the node,
    link ``feature -[decomposes_into]-> contract`` when ``feature`` is given and
    ``contract -[depends_on]-> dep`` for each ``depends_on`` slug. With no ``acceptance`` given (and none in the
    contract text), the ticket's unflagged acceptance lines become it.

    Returns:
        ``{"status": "created"|"conflict"|"duplicate"|"refused", "node": Node.to_dict()|None,
        "conflicts": [{"slug", "state", "overlap": [...]}], "duplicates": [{"slug", "title",
        "similarity", "state", "distance" (cosine, None for a title match), "match": "embedding"|"trigram"|
        "external_id"}], "flags": [...], "blocks": {...}, "precedent": [...], "rules": [...]}``. Conflict,
        duplicate and refused are normal returns. ``precedent`` is up to 3 prior contracts that touched the same
        paths (decisions, test names, reviewer findings) and ``rules`` the rule files whose ``paths:`` match the
        allowed paths, ranked against the ticket (both from ``index.search``; ``[]`` when there is nothing, the
        graph index is not built or a lookup fails: they inform the contract, never gate it).

    Raises:
        ValueError: bad actor, malformed touched_symbols entry, a title that repeats a flagged
        ticket line, a capability the validator refuses. LookupError: unknown feature or depends_on slug.
        A node with this slug already exists: status ``duplicate`` with one ``same_slug`` row (use
        ``contract_update`` to change a drafted contract); nothing is written.
    """
    from sdlc_graph.impact import capabilities as caps_mod

    from . import contract_md as cmd
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
    if tier is not None and tier not in nodes.TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(nodes.TIERS)}")
    if capabilities is not None and not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    parsed: dict[str, Any] | None = None
    warnings: list[str] = []
    if contract_text is not None:
        if not isinstance(contract_text, str):
            raise ValueError("contract_md must be the contract.md text")
        contract_text = clean_text(contract_text)
        parsed = cmd.parse_contract(contract_text)
        warnings += parsed["warnings"]
        if touched_symbols is None and parsed["allowed_paths"]:
            touched_symbols, skipped = cmd.touched_from_paths(parsed["allowed_paths"])
            warnings += [f"allowed path {p!r} has no literal directory to record as a touched path" for p in skipped]
        if acceptance is None and parsed["acceptance_criteria"]:
            acceptance = cmd.acceptance_rows(parsed)
        if tier is None:
            tier = parsed["tier"]
        if defaults is None and parsed["defaults"]:
            defaults = parsed["defaults"]
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
        acceptance, flags = _scan_rows(acceptance, flags)
    caps: dict[str, Any] = dict(capabilities or {})
    if parsed is not None:
        caps = {**parsed["capabilities"], **caps}
        caps_mod.validate(caps)
    attrs: dict[str, Any] = {"intake": {"clean": result["clean"], "flags": flags, "drafted_by": actor,
                                        "ticket_sha256": hashlib.sha256(str(ticket_text or "").encode("utf-8")).hexdigest()}}
    if defaults is not None:
        attrs["defaults"] = _clean_defaults(defaults)
    blocks: dict[str, Any] | None = None
    if parsed is not None or defaults is not None or strict:
        digest = hashlib.sha256(contract_text.encode()).hexdigest() if contract_text is not None else None
        blocks = {"missing": _blocks_missing(parsed=parsed, strict=strict, acceptance=acceptance, caps=caps,
                                             attrs=attrs, tier=tier),
                  "unknown": parsed["unknown"] if parsed else [], "warnings": warnings}
        if digest:
            blocks["sha256"] = digest
        attrs["contract_blocks_log"] = [_log_entry(actor, "drafted", "contract_draft" + (f" sha256:{digest[:16]}" if digest else ""),
                                                   defaults=defaults is not None,
                                                   capability_keys=list(caps) if parsed is not None else [])]
    if parsed is not None:
        attrs["decisions"] = parsed["decisions"]
        attrs["acceptance_lines"] = parsed["acceptance_lines"]
        attrs["contract_sha256"] = blocks["sha256"] if blocks else None
    out: dict[str, Any] = {"status": "created", "node": None, "conflicts": [], "duplicates": [], "flags": flags}
    if blocks is not None:
        out["blocks"] = blocks
    if strict and blocks and blocks["missing"]:
        out["status"] = "refused"
        out["error"] = ("the contract is missing block(s) the ready guard needs: " + "; ".join(blocks["missing"])
                        + ". Pass the whole contract.md as contract_md (fix the file first); nothing was created")
        return out
    closed = sorted(terminal_states("contract"))
    semantic, out["precedent"], out["rules"] = _index_context(conn, repo=repo, slug=slug, title=title, ticket_text=ticket_text,
                                                              touched=touched, caps=caps, deps=deps)
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
        same = conn.execute("SELECT slug, title, kind, state, external_id FROM node WHERE slug = %s", (slug,)).fetchone()
        if same is not None:
            # The slug is the node's key: a second draft under it is the same contract (or a name clash with another
            # node), never a new one. Report it like any duplicate instead of letting the insert fail on node_slug_key.
            out["status"] = "duplicate"
            out["duplicates"] = [{"slug": same["slug"], "title": same["title"], "similarity": 1.0,
                                  "same_external_id": external_id is not None and same["external_id"] == external_id,
                                  "same_slug": True, "kind": same["kind"], "state": same["state"]}]
            out["error"] = (f"a {same['kind']} with slug {slug!r} already exists ({same['state']}): "
                            + ("change it with contract_update (whole contract.md), or choose another slug"
                               if same["kind"] == "contract" else "choose another slug"))
            return out
        dupes = conn.execute(
            """SELECT slug, title, state, similarity(title, %s) AS sim, external_id FROM node
               WHERE kind = 'contract' AND repo = %s AND NOT (state = ANY(%s)) AND NOT (slug = ANY(%s))
                 AND ((%s::text IS NOT NULL AND external_id = %s) OR similarity(title, %s) >= %s)
               ORDER BY sim DESC, slug""",
            (title, repo, closed, deps, external_id, external_id, title, DEDUPE_SIMILARITY),
        ).fetchall()
        found = {r["slug"]: {"slug": r["slug"], "title": r["title"], "similarity": round(float(r["sim"]), 3),
                             "same_external_id": external_id is not None and r["external_id"] == external_id,
                             "state": r["state"], "distance": None,
                             "match": "external_id" if external_id is not None and r["external_id"] == external_id
                             else "trigram"} for r in dupes}
        for h in semantic:
            row = found.setdefault(h["slug"], {"slug": h["slug"], "title": h["title"], "same_external_id": False,
                                               "state": h["state"], "match": "embedding"})
            row["distance"] = h["distance"]
            row["similarity"] = max(row.get("similarity", 0.0), round(1 - h["distance"], 3))
        if found:
            rows = sorted(found.values(), key=lambda d: (d["distance"] if d["distance"] is not None else 1.0, -d["similarity"], d["slug"]))
            if allow_duplicate:
                out["duplicates_overridden"] = rows
            else:
                out["status"] = "duplicate"
                out["duplicates"] = rows
                return out
        conflicts = [c for c in nodes.overlaps(conn, repo, touched, exclude_slug=slug) if c["slug"] not in deps]
        if conflicts:
            out["status"] = "conflict"
            out["conflicts"] = conflicts
            return out
        node = nodes.create(conn, kind="contract", slug=slug, title=title.strip(), repo=repo, created_by=actor,
                            tier=tier, external_id=external_id, touched_symbols=touched,
                            capabilities=caps if (capabilities is not None or parsed is not None) else None,
                            acceptance=acceptance, attrs=attrs, contract_md=contract_text)
        if feature_row is not None:
            nodes.link(conn, feature, slug, "decomposes_into")
        for d in deps:
            nodes.link(conn, slug, d, "depends_on")
    out["node"] = node
    return out


#: States in which ``contract_update`` may rewrite a contract's blocks. From ``tests_frozen`` on the allowed paths
#: and the frozen tests are fixed, so the contract is too.
UPDATABLE_STATES: tuple[str, ...] = ("drafted", "ready")
#: Fields a non-human actor may not change once the contract is ``ready`` (a human marked it ready with them).
_APPROVED_FIELDS: tuple[str, ...] = ("touched_symbols", "capabilities", "defaults", "acceptance", "tier")


def contract_update(conn: psycopg.Connection, slug: str, contract_text: str, *, actor: str) -> dict[str, Any]:
    """Re-read a contract.md onto an existing contract node.

    Replaces ``touched_symbols`` (from Allowed paths), ``capabilities``, ``attrs.defaults``, ``acceptance`` (an
    existing row's ``test`` and ``command`` are kept by id), ``tier``, ``attrs.decisions`` (and
    ``attrs.acceptance_lines``) and ``node.contract_md`` with what the text holds; a block the text lacks keeps the
    node's value and is named in ``blocks.missing``. Allowed only while the node is ``drafted`` or ``ready``.

    Who may update: anyone while ``drafted``; once ``ready``, an ``agent:*`` actor may change only the decisions
    and the stored text (the paths, capabilities, defaults, acceptance and tier are what a human marked ready). A
    decision row counts as confirmed only when its Decided by names a human ``@handle`` (never ``agent:*``), the
    rule ``decisions.resolve`` applies; whoever submits the text, the row's handle is what counts, and the actor
    who recorded it is in the evidence row.

    When anything changed, an evidence row of kind ``contract_updated`` is recorded:
    ``{changed: [field...], decisions_confirmed: [n...], actor, sha256, missing}`` (the feed shows it).

    Returns:
        ``{"status": "updated"|"unchanged"|"conflict", "slug", "state", "changed", "decisions_confirmed",
        "evidence_id", "conflicts", "blocks": {"missing", "unknown", "warnings", "sha256"}, "node"}``.

    Raises:
        LookupError: unknown slug. ValueError: not a contract, bad actor, empty text, a refused capability.
        PermissionError: the node is past ``ready`` (tests_frozen on), or an agent changing an approved field of a
        ``ready`` contract.
    """
    from sdlc_graph.impact import capabilities as caps_mod

    from . import contract_md as cmd
    from . import evidence as evidence_mod
    from . import nodes
    from .transitions import actor_class, decision_confirmed

    cls = actor_class(actor)
    if not isinstance(contract_text, str) or not contract_text.strip():
        raise ValueError("contract_md must be the non-empty contract.md text")
    text = clean_text(contract_text)
    parsed = cmd.parse_contract(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    warnings = list(parsed["warnings"])
    with conn.transaction():
        row = conn.execute("SELECT * FROM node WHERE slug = %s FOR UPDATE", (slug,)).fetchone()
        if row is None:
            raise LookupError(f"unknown node slug: {slug!r}")
        if row["kind"] != "contract":
            raise ValueError(f"{slug!r} is a {row['kind']}, not a contract")
        if row["state"] not in UPDATABLE_STATES:
            raise PermissionError(f"{slug!r} is {row['state']}: a contract is updated only while drafted or ready "
                                  "(from tests_frozen on its allowed paths and tests are frozen)")
        attrs = dict(row["attrs"] or {})
        old = {"touched_symbols": list(row["touched_symbols"] or []), "capabilities": dict(row["capabilities"] or {}),
               "defaults": attrs.get("defaults"), "acceptance": list(row["acceptance"] or []), "tier": row["tier"],
               "decisions": attrs.get("decisions"), "acceptance_lines": attrs.get("acceptance_lines"),
               "contract_md": row.get("contract_md")}
        new = dict(old)
        if parsed["allowed_paths"]:
            touched, skipped = cmd.touched_from_paths(parsed["allowed_paths"])
            warnings += [f"allowed path {p!r} has no literal directory to record as a touched path" for p in skipped]
            new["touched_symbols"] = nodes.validate_touched(touched)
        if parsed["capabilities"]:
            caps = dict(parsed["capabilities"])
            for k in ("secrets", "test_command"):  # keys the text does not name stay as they were
                if k not in caps and k in old["capabilities"]:
                    caps[k] = old["capabilities"][k]
            caps_mod.validate(caps)
            new["capabilities"] = caps
        if parsed["defaults"]:
            new["defaults"] = _clean_defaults(parsed["defaults"])
        flags = list((attrs.get("intake") or {}).get("flags") or [])
        if parsed["acceptance_criteria"]:
            by_id = {r.get("id"): r for r in old["acceptance"] if isinstance(r, dict)}
            rows = []
            for r in cmd.acceptance_rows(parsed):
                prev = by_id.get(r["id"]) or {}
                rows.append({**r, **{k: prev[k] for k in ("test", "command", "expect") if k in prev}})
            new_flags: list[dict[str, Any]] = []
            new["acceptance"], new_flags = _scan_rows(rows, [], field="contract_update.acceptance")
            seen = {(f.get("field"), f.get("text")) for f in flags}
            flags += [f for f in new_flags if (f.get("field"), f.get("text")) not in seen]
        if parsed["tier"]:
            new["tier"] = parsed["tier"]
        if not any(m.startswith("## Decisions") for m in parsed["missing"]):
            new["decisions"] = parsed["decisions"]
        if parsed["acceptance_lines"] or "## Acceptance" not in parsed["optional_missing"]:
            new["acceptance_lines"] = parsed["acceptance_lines"]
        new["contract_md"] = text
        changed = [k for k in ("touched_symbols", "capabilities", "defaults", "acceptance", "tier", "decisions",
                               "acceptance_lines", "contract_md") if new[k] != old[k]]
        if row["state"] != "drafted" and cls != "human":
            touched_approved = [k for k in _APPROVED_FIELDS if k in changed]
            if touched_approved:
                raise PermissionError(f"{slug!r} is {row['state']}: only a human (@handle) may change its "
                                      + ", ".join(touched_approved) + f"; {actor} may update its decisions")
        before = {d.get("n"): d for d in old["decisions"] or [] if isinstance(d, dict)}
        confirmed = [d.get("n") for d in new["decisions"] or [] if isinstance(d, dict) and decision_confirmed(d)
                     and not decision_confirmed(before.get(d.get("n")) or {})]
        out: dict[str, Any] = {"status": "unchanged", "slug": slug, "state": row["state"], "changed": changed,
                               "decisions_confirmed": confirmed, "evidence_id": None, "conflicts": [],
                               "blocks": {"missing": parsed["missing"], "unknown": parsed["unknown"],
                                          "warnings": warnings, "sha256": digest}}
        if "touched_symbols" in changed and new["touched_symbols"]:
            # A depends_on edge in either direction orders the two contracts (the dependent is not dispatched
            # until its dependency merges), so it excuses the overlap: the contract's own dependencies and the
            # contracts that depend on it. contract_draft checks only the outgoing edges, since a contract being
            # drafted cannot have dependents yet.
            deps = {r["slug"] for r in conn.execute(
                """SELECT n.slug FROM edge e JOIN node n ON n.id = e.dst WHERE e.src = %s AND e.rel = 'depends_on'
                   UNION SELECT n.slug FROM edge e JOIN node n ON n.id = e.src WHERE e.dst = %s AND e.rel = 'depends_on'""",
                (row["id"], row["id"])).fetchall()}
            conflicts = [c for c in nodes.overlaps(conn, row["repo"], new["touched_symbols"], exclude_slug=slug)
                         if c["slug"] not in deps]
            if conflicts:
                out["status"] = "conflict"
                out["conflicts"] = conflicts
                out["node"] = nodes.Node.from_row(row).to_dict()
                return out
        if changed:
            if new["defaults"] is not None:
                attrs["defaults"] = new["defaults"]
            attrs["decisions"] = new["decisions"] if new["decisions"] is not None else []
            attrs["acceptance_lines"] = new["acceptance_lines"] if new["acceptance_lines"] is not None else []
            attrs["contract_sha256"] = digest
            if flags:
                attrs["intake"] = {**(attrs.get("intake") or {}), "flags": flags,
                                   "clean": bool((attrs.get("intake") or {}).get("clean", True)) and not flags}
            log = list(attrs.get("contract_blocks_log") or [])
            log.append(_log_entry(actor, row["state"], f"contract_update sha256:{digest[:16]}",
                                  defaults="defaults" in changed,
                                  capability_keys=list(new["capabilities"]) if "capabilities" in changed else []))
            attrs["contract_blocks_log"] = log[-20:]
            conn.execute(
                """UPDATE node SET touched_symbols = %s, capabilities = %s, acceptance = %s, tier = %s, attrs = %s,
                          contract_md = %s, updated_at = now() WHERE id = %s""",
                (new["touched_symbols"], Jsonb(new["capabilities"]), Jsonb(new["acceptance"]), new["tier"],
                 Jsonb(attrs), text, row["id"]))
            ev = evidence_mod.add(conn, slug, "contract_updated",
                                  {"changed": changed, "decisions_confirmed": confirmed, "actor": actor, "sha256": digest,
                                   "missing": parsed["missing"]}, source="local")
            out["status"] = "updated"
            out["evidence_id"] = ev["id"]
        out["node"] = nodes.Node.from_row(nodes.get_row(conn, slug)).to_dict()
    return out
