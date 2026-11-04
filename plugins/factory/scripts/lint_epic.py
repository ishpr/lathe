#!/usr/bin/env python3
"""Lint an epic or feature document written in the /factory:brief shapes.

It checks the mechanical things so the writer can spend attention on judgement: required
sections, a hypothesis (or an obligation), at least one measurable metric and a guardrail,
non-goals, stories with role/need/benefit, Given/When/Then criteria including an unhappy path,
proposed sizes, owners and dates on open decisions, and no TBD/TODO placeholders. It only checks
that a failure word is present in a criterion; whether it is a real failure is still your call.

Usage: python3 lint_epic.py <file.md> [--strict] [--agent]
  --strict   warnings count as failures
  --agent    each story must also carry Allowed paths, Capabilities, Evidence of done, Defaults when
             ambiguous, Risk tier, a '**Decision owner:** @handle' line (the person whose answers settle the
             decisions; the ticket intake rejects a story without one; 'unknown, blocked' is honest but keeps the
             story '**Ready:** blocked on owner'), a Decisions block (the table, or the line 'None.') and a Ready line; a
             story is '**Ready:** yes' only when every decision in its table has a real value (one of its
             Options or its Recommendation, never "see above", "ask product", "none") that a named person
             decided: a decision marked 'assumed' keeps the story blocked (design:
             CONTRACT_GRAPH_DESIGN.md section 14). Defaults carry the standard four and never ask, stop
             or override a decision.
Output: "file:line: [error|warning] message" lines, then a summary. Exit 1 on errors (and on
warnings with --strict), 2 when a file cannot be read, 0 otherwise.
"""

from __future__ import annotations

import re
import sys

EPIC_SECTIONS = {
    "problem": ["problem", "context", "background"],
    "outcome": ["outcome", "hypothesis", "goal ", "goal:", "obligation"],
    "metrics": ["success metric", "metrics", "measures", "key results"],
    "non-goals": ["non-goal", "non goal", "out of scope", "not doing"],
    "features": ["features", "capabilities", "feature list"],
    "dependencies/risks": ["dependenc", "risk"],
    "open decisions": ["open decision", "decisions", "open question"],
}
FEATURE_SECTIONS = {
    "description": ["description", "what the user can do", "summary"],
    "stories": ["stories", "user stories", "slices"],
    "out of scope": ["out of scope", "non-goal", "non goal"],
}
NEGATIVE_WORDS = re.compile(
    r"\b(invalid|fail\w*|error|errors|not |no |missing|expired|denied|unauthori[sz]ed|forbidden|"
    r"timeout|times out|empty|exceed|exceeds|over the limit|wrong|rejected|reject\w*|declin\w*|"
    r"refus\w*|revok\w*|blocked|locked|cannot|can't|unavailable|unreachable|offline|out of date|"
    r"conflict|duplicate|rate.?limit|roll(s|ed)? back)\b|(status|response|http|returns?)\s+[45]\d\d",
    re.I,
)
IMPL_WORDS = re.compile(r"\b(redis|postgres|mysql|mongo|kafka|sql quer(?:y|ies)|cache key|stored in)\b", re.I)
VAGUE_RESULT = re.compile(r"\bthen\b.*\b(correctly|properly|appropriately|as expected|user-friendly|seamlessly|quickly)\b", re.I)
PLACEHOLDER = re.compile(r"\b(TBD|TODO|FIXME|lorem ipsum|xxx)\b|\?\?\?", re.I)
UNKNOWN_OK = re.compile(r"\[unknown\s*[—-]\s*(ask|owner|instrument|measure)", re.I)
GWT = re.compile(r"\bgiven\b.*\bwhen\b.*\bthen\b", re.I | re.S)
SIZE = re.compile(r"\*{0,2}size(?:\s*\(proposed\))?\*{0,2}\s*:", re.I)
AGENT_FIELDS = ("allowed paths", "capabilities", "evidence of done", "defaults when ambiguous", "risk tier")
CAPABILITY_KEYS = ("paths", "commands", "egress", "data")
NO_VALUE = re.compile(r"^(|[-—–?]+|n/?a|tbd|todo|open|pending|unknown|undecided|<.*>|\[.*\])$", re.I)
# A Value that is not an answer ("see above", "to be decided", "ask product", "none", "not yet known", ...). Review finding:
# the narrow list above let all of these through as values.
PLACEHOLDER_VALUE = re.compile(
    r"^(?:[-—–?.\s]*"
    r"|<[^>]*>|\[[^\]]*\]"
    r"|(?:tbd|tbc|todo|n/?a|none|nil|null|open|pending|unknown|unclear|undecided|unsure|later|maybe|whatever|any|either)\b.*"
    r"|(?:see|as\s+per|per)\s+(?:above|below|ticket|recommendation|discussion|thread|slack|notes?)\b.*"
    r"|(?:to\s+be|will\s+be|needs?\s+to\s+be|yet\s+to\s+be)\s+(?:decided|determined|confirmed|defined|agreed|discussed|clarified)\b.*"
    r"|(?:decide|confirm|ask|check|clarify|discuss|figure|revisit|defer)\b.*"
    r"|not\s+(?:yet\s+)?(?:known|decided|sure|defined|confirmed|determined|clear)\b.*"
    r"|no\s+(?:answer|decision)\b.*)$", re.I)
STANDARD_PHRASES = ("smallest change", "nearest existing pattern", "decisions", "reversible option")
BAD_DEFAULT = re.compile(
    r"^\W*(?:ask|stop|escalate|wait|pause|halt|confirm|check\s+with|consult|defer|prompt|query)\b"
    r"|\b(?:ignore|override|disregard|overrule|bypass|contradict|supersede)\b[^.]*\bdecisions?\b"
    r"|\bdecisions?\b[^.]*\b(?:ignored|overridden|disregarded|overruled|bypassed|do\s+not\s+apply|don't\s+apply)\b"
    r"|\b(?:ask|escalate\s+to)\s+(?:the\s+)?(?:human|user|product|owner|reviewer|team|me)\b", re.I)
READY = re.compile(r"\*{0,2}ready\*{0,2}\s*:\*{0,2}\s*(yes|blocked on\s*((?:owner|#?\d+)(?:\s*[,&]\s*(?:owner|#?\d+)|\s+and\s+(?:owner|#?\d+))*))", re.I)
OWNER_LINE = re.compile(r"\*{0,2}decision owner\*{0,2}\s*:\*{0,2}[ \t]*([^\n]*)", re.I)
# A decision owner that is not a person: "unknown, blocked", "tbd", "none", "<handle>", "ask product", ...
NO_OWNER = re.compile(r"^[\s*_`]*(?:|[-—–?.]+|<[^>]*>|\[[^\]]*\]|(?:unknown|unnamed|nobody|none|n/?a|tbd|tbc|todo|open|pending|undecided|unassigned|ask|to be)\b.*)[\s*_`.]*$", re.I)
VERBS = {
    "use", "follow", "prefer", "choose", "pick", "keep", "record", "stop", "apply", "make", "change",
    "add", "write", "touch", "match", "reuse", "avoid", "leave", "log", "report", "treat", "return",
    "fail", "take", "run", "read", "copy", "mirror", "limit", "defer", "escalate", "name", "state",
    "implement", "satisfy", "pass", "fix", "set", "create", "delete", "remove", "widen", "ask",
    "start", "edit", "call", "send", "store", "open", "revert", "roll", "ship", "check", "list",
    # Imperatives a default commonly starts with that the first list missed ('Decode every response with an effect
    # Schema; never cast.' and 'Put the conventions in one file ...' were flagged as vague, review of a headless run).
    "decode", "encode", "cast", "put", "place", "parse", "validate", "verify", "reject", "accept", "allow", "deny",
    "retry", "raise", "throw", "emit", "wrap", "group", "sort", "cache", "hash", "sign", "test", "assert", "require",
    "render", "show", "hide", "load", "save", "import", "export", "compute", "count", "document", "mark", "tag",
    "include", "exclude", "skip", "rename", "move", "split", "merge", "convert", "format", "sanitize", "escape",
    "trim", "normalize", "normalise", "lock", "paginate", "mock", "stub", "measure", "warn", "block", "drop",
    "append", "prepend", "insert", "update", "upsert", "publish", "schedule", "queue", "batch", "expire", "rotate",
    "encrypt", "decrypt", "compress", "serialize", "serialise", "respond", "handle", "catch", "guard", "extend",
    "define", "declare", "expose", "register", "mount", "route", "bind", "redirect", "default", "fall", "wait",
    "close", "print", "trace", "truncate", "pin", "bump", "omit", "prefix",
}


def _verb_forms(word: str) -> set[str]:
    """The word and the stems its common inflections come from (uses, used, using, parses, retries, stopped)."""
    w = word.lower()
    forms = {w}
    for suffix, repl in (("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""),
                         ("ing", "e")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 2:
            stem = w[: len(w) - len(suffix)] + repl
            forms.add(stem)
            if len(stem) >= 3 and stem[-1] == stem[-2]:
                forms.add(stem[:-1])  # stopped -> stop
    return forms


def field_block(joined: str, label: str) -> str | None:
    """Text from the label to the next bold field label, or None when the label is absent. The label may carry a colon
    (``**Label:**``, ``Label:``) or stand alone at the start of a line as a bold line or a heading (``**Label**``,
    ``#### Label``): a block written without the colon is still the block (review finding: it read as missing)."""
    m = re.search(r"\*{0,2}" + re.escape(label) + r"\*{0,2}\s*:\*{0,2}", joined, re.I)
    if not m:
        m = re.search(r"(?:^|\n)[ \t]*(?:#{1,6}[ \t]*)?(?:[-*][ \t]+)?\*{0,2}" + re.escape(label) + r"\*{0,2}[ \t]*(?=\n|$)", joined, re.I)
    if not m:
        return None
    rest = joined[m.end():]
    nxt = re.search(r"\n\s*(?:\*\*[A-Z][^*\n]{2,60}:?\*\*|#{1,6}\s+\S)", rest)
    return rest[: nxt.start()] if nxt else rest


def split_cells(line: str) -> list[str]:
    """Table cells split on unescaped pipes only (``\\|`` is a pipe inside a cell)."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def _norm(v: str) -> str:
    v = re.sub(r"[*_`]", "", v).strip()
    v = re.sub(r"\s*\([^)]*\)\s*$", "", v).strip()
    return re.sub(r"[^a-z0-9]+", " ", v.rstrip(".").lower()).strip()


def value_problem(value: str, options: str, recommendation: str) -> str | None:
    """None when ``value`` is a real answer: one of the row's Options (or its Recommendation), or free text that is not a
    placeholder. Otherwise why not."""
    raw = re.sub(r"[*_`]", "", value).strip()
    if not raw or re.fullmatch(r"<[^>]*>|\[[^\]]*\]|[-—–?.\s]+", raw):
        return "has no value"
    opts = [_norm(re.sub(r"^\W*(?:[A-Za-z]|\d+)[.):]\s+", "", o)) for o in re.split(r"\s*(?:·|;|\||/|,|\bor\b)\s*", options)]
    opts = [o for o in opts if o]
    rec = re.search(r"\*\*(.+?)\*\*", recommendation or "")
    pool = opts + [_norm(rec.group(1)) if rec else _norm((recommendation or "").split(":")[0].split(",")[0])]
    v = _norm(raw)
    if len(opts) >= 2:
        for o in pool:
            if o and (v == o or (len(o) >= 3 and (v.startswith(o) or o in v)) or (len(v) >= 3 and o.startswith(v))):
                return None
    if PLACEHOLDER_VALUE.match(raw.rstrip(".").strip()) or PLACEHOLDER_VALUE.match(re.sub(r"\s*\([^)]*\)\s*$", "", raw).rstrip(".")):
        return f"has the placeholder value {raw!r}, not an answer"
    if len(v) < 2:
        return f"has the too-short value {raw!r}"
    if len(opts) >= 2:
        return f"has the value {raw!r}, which is not one of its Options ({' / '.join(opts)}) or its Recommendation"
    return None


def vague_default(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 4:
        return True
    return not any(_verb_forms(w) & VERBS for w in words)


def check_agent(doc: "Doc", i: int, text: str, body: list[tuple[int, str]], joined: str) -> None:
    for field in AGENT_FIELDS:
        if field not in joined.lower():
            doc.error(i, f"story '{text}' is missing '{field.title()}' (required with --agent)")
    caps = field_block(joined, "Capabilities")
    if caps is not None:
        if not caps.strip():
            doc.error(i, f"story '{text}' has an empty Capabilities block")
        else:
            for key in CAPABILITY_KEYS:
                if not re.search(r"\b" + key + r"\b\s*:", caps, re.I):
                    doc.warn(i, f"story '{text}': Capabilities does not state '{key}' (write 'none' if none)")
    defaults = field_block(joined, "Defaults when ambiguous")
    if defaults is not None:
        bullets = [t.strip() for t in re.split(r"\n\s*[-*]\s+", "\n" + defaults)[1:] if t.strip()]
        if not bullets:
            doc.error(i, f"story '{text}' has an empty Defaults block; pre-fill the standard defaults")
        low = " ".join(bullets).lower()
        for ph in STANDARD_PHRASES:
            if bullets and ph not in low:
                doc.error(i, f"story '{text}': Defaults lack the standard default about '{ph}' (pre-fill the standard four)")
        for b in bullets:
            if BAD_DEFAULT.search(b):
                doc.error(i, f"story '{text}': default {b[:60]!r} asks, stops or overrides a decision; a default settles ambiguity on its own")
            elif vague_default(b):
                doc.warn(i, f"story '{text}': default is vague (needs a verb and an object): {b[:60]!r}")
    # Decision owner: a named person, or an honest "unknown, blocked" that keeps the story blocked.
    owner_line = OWNER_LINE.search(joined)
    owner_missing = owner_line is None
    owner_unknown = owner_line is not None and bool(NO_OWNER.match(owner_line.group(1)))
    if owner_missing:
        doc.error(i, f"story '{text}' names no decision owner (required with --agent; add '**Decision owner:** @handle', "
                     "or '**Decision owner:** unknown, blocked' with '**Ready:** blocked on owner' when nobody can say)")
    # Decisions: the block, its values and the Ready line.
    open_ids: list[str] = []
    assumed_ids: list[str] = []
    dec = field_block(joined, "Decisions a human makes first")
    table = [(i, t) for t in (dec or "").splitlines() if t.strip().startswith("|")]
    if dec is None:
        doc.error(i, f"story '{text}' has no 'Decisions a human makes first' block (required with --agent; write the table, or the line 'None.' "
                     "when nothing needs deciding)")
    elif not table and not re.match(r"^\W*none\.?\W*$", dec.strip().splitlines()[0] if dec.strip() else "", re.I):
        doc.error(i, f"story '{text}': the decisions block is neither a table nor the line 'None.'")
    oq = field_block(joined, "Open questions")
    if oq is not None and oq.strip() and not re.match(r"^\W*none\.?\W*$", oq.strip().splitlines()[0], re.I):
        doc.error(i, f"story '{text}' has open questions; every question is a decision with a value before the story is ready")
    if table:
        header = [c.lower() for c in split_cells(table[0][1])]
        vcol = next((k for k, c in enumerate(header) if c == "value"), None)
        dcol = next((k for k, c in enumerate(header) if "decided by" in c), None)
        ocol = next((k for k, c in enumerate(header) if c == "options"), None)
        rcol = next((k for k, c in enumerate(header) if c == "recommendation"), None)
        if vcol is None:
            doc.error(table[0][0], f"story '{text}': decisions table has no 'Value' column (required with --agent)")
        for n, t in table[1:]:
            if re.match(r"^\|\s*[-:]+", t.strip()):
                continue
            cells = split_cells(t)
            ident = cells[0].lstrip("#") if cells else ""
            if vcol is None:
                continue
            if len(cells) != len(header):
                doc.error(n, f"decision #{ident}: {len(cells)} cells but {len(header)} columns; write a pipe inside a cell as \\|")
                open_ids.append(ident or "?")
                continue
            value = cells[vcol]
            why = value_problem(value, cells[ocol] if ocol is not None else "", cells[rcol] if rcol is not None else "")
            if why:
                open_ids.append(ident or "?")
                if value.strip() and not NO_VALUE.match(value.strip()):
                    doc.error(n, f"decision #{ident} {why}")
            elif dcol is not None and re.search(r"\bassumed\b|unconfirmed", cells[dcol], re.I):
                assumed_ids.append(ident or "?")
                doc.error(n, f"decision #{ident} is assumed, not confirmed by a person; the story stays blocked until its owner confirms it "
                             "(then 'Decided by' names that person)")
            elif dcol is not None and (len(cells) <= dcol or NO_VALUE.match(cells[dcol])):
                doc.warn(n, f"decision #{ident} has a value but no 'Decided by'; say who decided it")
    blocked_ids = open_ids + assumed_ids
    owner_blocked = owner_missing or owner_unknown
    ready = READY.search(joined)
    if not ready:
        doc.error(i, f"story '{text}' has no Ready line ('**Ready:** yes' or '**Ready:** blocked on #<n>')")
    elif ready.group(1).lower() == "yes":
        if blocked_ids:
            doc.error(i, f"story '{text}' is 'Ready: yes' but decision(s) #{', #'.join(blocked_ids)} have no confirmed value")
        if owner_unknown:
            doc.error(i, f"story '{text}' is 'Ready: yes' but its decision owner is {owner_line.group(1).strip()!r}, not a person")
    else:
        if owner_unknown:
            doc.warn(i, f"story '{text}': decision owner is unknown, so the story stays blocked; a named person must take the decisions")
        if not blocked_ids and not owner_blocked:
            doc.warn(i, f"story '{text}' is blocked but every decision has a value; mark it 'Ready: yes'")
        elif blocked_ids:
            named = set(re.findall(r"\d+", ready.group(2) or ""))
            missing = [x for x in blocked_ids if x.lstrip("#") not in named]
            if missing:
                doc.warn(i, f"story '{text}': Ready line does not name open decision(s) #{', #'.join(missing)}")
        if owner_unknown and not re.search(r"\bowner\b", ready.group(2) or "", re.I):
            doc.warn(i, f"story '{text}': Ready line does not name the owner ('blocked on owner')")


class Doc:
    def __init__(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            self.lines = fh.read().splitlines()
        self.path = path
        self.errors: list[tuple[int, str]] = []
        self.warnings: list[tuple[int, str]] = []
        self._headings: list[tuple[int, int, str]] | None = None

    def error(self, line: int, msg: str) -> None:
        self.errors.append((line, msg))

    def warn(self, line: int, msg: str) -> None:
        self.warnings.append((line, msg))

    def headings(self) -> list[tuple[int, int, str]]:
        if self._headings is None:
            out = []
            fenced = False
            for i, line in enumerate(self.lines, 1):
                if line.strip().startswith("```"):
                    fenced = not fenced
                    continue
                if fenced:
                    continue
                m = re.match(r"^(#{1,6})\s+(.*)$", line)
                if m:
                    out.append((i, len(m.group(1)), m.group(2).strip()))
            self._headings = out
        return self._headings

    def section_text(self, start: int, level: int) -> tuple[int, int]:
        end = len(self.lines) + 1
        for i, lvl, _ in self.headings():
            if i > start and lvl <= level:
                end = i
                break
        return start + 1, end

    def find_section(self, keys: list[str], max_level: int = 3) -> tuple[int, int, int] | None:
        for i, lvl, text in self.headings():
            low = text.lower()
            if lvl <= max_level and any(re.match(r"(\d+\.\s*)?" + re.escape(k), low) for k in keys):
                s, e = self.section_text(i, lvl)
                return i, s, e
        return None

    def body(self, s: int, e: int) -> list[tuple[int, str]]:
        return [(i, self.lines[i - 1]) for i in range(s, e) if i - 1 < len(self.lines)]


def items(body: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Bullets joined with their indented continuation lines and with following Given/When/Then/And/But lines."""
    out: list[tuple[int, str]] = []
    for n, t in body:
        stripped = t.strip()
        if not stripped:
            continue
        is_bullet = bool(re.match(r"^(\s*)([-*]|\d+\.)\s+", t))
        continuation = out and (t.startswith((" ", "\t")) or re.match(r"^(and|but|when|then|given)\b", stripped, re.I))
        if is_bullet and not (continuation and re.match(r"^\s+[-*]\s+(and|but|when|then)\b", t, re.I)):
            out.append((n, stripped))
        elif continuation:
            out[-1] = (out[-1][0], out[-1][1] + " " + stripped)
    return out


def check_stories(doc: Doc, agent: bool) -> int:
    count = 0
    for i, lvl, text in doc.headings():
        if not re.match(r"story\s*:", text, re.I):
            continue
        count += 1
        s, e = doc.section_text(i, lvl)
        body = doc.body(s, e)
        joined = "\n".join(t for _, t in body)
        if not re.search(r"\bas an?\b.*\bi want\b", joined, re.I | re.S):
            doc.error(i, f"story '{text}' has no 'As a <role>, I want <capability>' line")
        if not re.search(r"\bso that\b", joined, re.I):
            doc.warn(i, f"story '{text}' states no benefit ('so that ...'); tie it to the metric")
        if not re.search(r"acceptance criteria", joined, re.I):
            doc.error(i, f"story '{text}' has no 'Acceptance criteria' heading or label")
        criteria = [(n, t) for n, t in items(body) if GWT.search(t)]
        if not criteria:
            doc.error(i, f"story '{text}' has no Given/When/Then criterion")
        elif len(criteria) == 1:
            doc.warn(i, f"story '{text}' has one criterion; a second one usually covers the failure or the limit")
        if criteria and not any(NEGATIVE_WORDS.search(t) for _, t in criteria):
            doc.warn(i, f"story '{text}' has no unhappy-path criterion (a failure, edge, limit or error case)")
        for n, t in criteria:
            if IMPL_WORDS.search(t):
                doc.warn(n, "criterion names an implementation detail; describe what the caller observes instead")
            if VAGUE_RESULT.search(t):
                doc.warn(n, "criterion's result is vague ('correctly', 'as expected'); say what is observed")
        if not SIZE.search(joined):
            doc.warn(i, f"story '{text}' has no proposed size")
        if agent:
            check_agent(doc, i, text, body, joined)
    return count


def check_metrics(doc: Doc, sec: tuple[int, int, int] | None) -> None:
    if not sec:
        return
    _, s, e = sec
    rows = [(n, t) for n, t in items(doc.body(s, e))]
    measurable = [t for _, t in rows if re.search(r"\d", t) or re.search(r"\bunknown\b", t, re.I)]
    if not rows:
        doc.error(sec[0], "success metrics section has no metrics")
    elif not measurable:
        doc.error(sec[0], "no metric carries a number or says 'unknown — how it will be obtained'")
    if rows and not any(re.search(r"guardrail", t, re.I) for _, t in rows):
        doc.warn(sec[0], "no guardrail metric (what must not get worse while the others move)")
    for n, t in rows:
        if re.search(r"\b(better|faster|improved|more|less|easier|nicer)\b", t, re.I) and not re.search(r"\d", t):
            doc.warn(n, "metric uses an adjective with no number")


def check_decisions(doc: Doc, sec: tuple[int, int, int] | None) -> None:
    if not sec:
        return
    _, s, e = sec
    table = [(n, t) for n, t in doc.body(s, e) if t.strip().startswith("|")]
    if not table:
        return
    header = [c.strip().lower() for c in table[0][1].strip().strip("|").split("|")]
    owner = next((k for k, c in enumerate(header) if "owner" in c or "decided by" in c), None)
    due = next((k for k, c in enumerate(header) if "due" in c or "by when" in c or "date" in c), None)
    if owner is None:
        doc.warn(sec[0], "open decisions table has no Owner column")
    for n, t in table[1:]:
        if re.match(r"^\|\s*[-:]+", t.strip()):
            continue
        cells = [c.strip() for c in t.strip().strip("|").split("|")]
        if owner is not None and (len(cells) <= owner or not cells[owner]):
            doc.warn(n, "open decision has no owner")
        if due is not None and (len(cells) <= due or not cells[due]):
            doc.warn(n, "open decision has no due date")


def lint(path: str, strict: bool, agent: bool) -> int:
    try:
        doc = Doc(path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"{path}: cannot read: {exc}")
        return 2
    heads = doc.headings()
    title_head = next((h for h in heads if re.match(r"(epic|feature)\s*:", h[2], re.I)), heads[0] if heads else None)
    if not title_head:
        doc.error(1, "the document needs a title: '# Epic: <outcome>' or '# Feature: <capability>'")
        kind = "epic"
    else:
        line, _, title = title_head
        kind = "feature" if re.match(r"feature\s*:", title, re.I) else "epic"
        if not re.match(r"(epic|feature)\s*:", title, re.I):
            doc.warn(line, "title does not start with 'Epic:' or 'Feature:'")
        bare = re.sub(r"^(epic|feature)\s*:\s*", "", title, flags=re.I)
        if len(bare.split()) < 3:
            doc.warn(line, "title is very short; name the outcome in plain words")
        if kind == "epic" and re.search(r"\b(build|implement|migrate to|microservice|refactor|rewrite|api|module)\b", bare, re.I):
            doc.warn(line, "epic title reads as a solution; title the outcome instead")

    required = EPIC_SECTIONS if kind == "epic" else FEATURE_SECTIONS
    found: dict[str, tuple[int, int, int] | None] = {}
    for name, keys in required.items():
        sec = doc.find_section(keys)
        found[name] = sec
        if not sec:
            doc.error(1, f"missing section: {name}")

    if kind == "epic":
        text = "\n".join(doc.lines)
        if not re.search(r"we believe|hypothes|obligation|deadline", text, re.I):
            doc.warn(1, "no hypothesis sentence ('We believe that ... We will know it worked when ...') or stated obligation")
        if not re.search(r"we( will|'ll) know|know it worked|measure|deadline", text, re.I):
            doc.warn(1, "the hypothesis does not say how you will know it worked")
        check_metrics(doc, found.get("metrics"))
        feats = found.get("features")
        if feats:
            _, s, e = feats
            listed = [t for _, t in doc.body(s, e) if re.match(r"^\s*(\d+\.|-|\*)\s+\S", t)]
            if not listed and not any(re.match(r"feature\s*:", t, re.I) for _, _, t in heads):
                doc.error(feats[0], "features section lists no features")
        check_decisions(doc, found.get("open decisions"))

    stories = check_stories(doc, agent)
    if kind == "feature" and stories == 0:
        doc.error(1, "a feature document needs at least one '### Story:'")
    if kind == "epic" and stories == 0:
        doc.warn(1, "no stories yet; slice the first feature before the work is scheduled")

    fenced = False
    for n, t in enumerate(doc.lines, 1):
        if t.strip().startswith("```"):
            fenced = not fenced
        if not fenced and PLACEHOLDER.search(t) and not UNKNOWN_OK.search(t):
            doc.error(n, "placeholder left in the text (TBD/TODO/???); write the fact, mark it '[unknown — ask <who>]', or move it to Open decisions")

    for n, m in sorted(doc.errors):
        print(f"{path}:{n}: [error] {m}")
    for n, m in sorted(doc.warnings):
        print(f"{path}:{n}: [warning] {m}")
    print(f"{path}: {kind} document, {stories} stories, {len(doc.errors)} errors, {len(doc.warnings)} warnings")
    return 1 if doc.errors or (strict and doc.warnings) else 0


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    agent = "--agent" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    return max(lint(f, strict, agent) for f in files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
