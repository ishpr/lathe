"""Parse a contract.md (the sdlc pack's ``templates/contract.template.md``) into the blocks the ledger stores.

:func:`parse_contract` is pure and never raises on content: a section it cannot read is reported in
``missing`` (or ``warnings``), never guessed. The template's sections, by ``## `` heading (case-insensitive,
HTML comments removed first)::

    # Contract: <slug>                 -> title (the heading, without a "Contract:" prefix)
    ## Outcome                         -> outcome (the paragraph)
    ## Acceptance criteria             -> acceptance_criteria: [{id: "AC<n>", name, text, negative}]
                                          (numbered or bulleted items; a leading `backticked` name is the test name)
    ## Acceptance                      -> acceptance_lines: [{kind: "shell"|"mcp", command, expects: [..]}]
                                          ("- $ cmd" or "- mcp: tool args", then "=> ..." lines)
    ## Decisions                       -> decisions: [{n, question, options, recommendation, value, decided_by,
                                          confirmed, handle}] (the table; the line "None." is an empty list)
    ## Defaults when ambiguous         -> defaults: [lines] (numbered and bulleted items; "Per contract:" labels skipped)
    ## Capabilities                    -> capabilities: {paths, commands, egress, data[, secrets, test_command]}
    ## Out of scope                    -> out_of_scope: [items]
    ## Allowed paths                   -> allowed_paths: [paths]
    ## Risk tier                       -> tier: trivial | low | medium | high (the first such word)
    ## Evidence plan                   -> evidence_plan: [items]

Table cells split on unescaped pipes only (``\\|`` inside a cell is a literal pipe, as the pack's
``acceptance.py`` reads it). A decision is ``confirmed`` when its Decided by cell names a human handle
(``@name``, the ledger's ``@handle`` form) and none of the words recommended, unconfirmed or assumed; an empty
cell, ``recommended, unconfirmed`` or ``agent:<alias>`` is not a confirmation. ``handle`` is that ``@name``.

Capability lines are ``- Key: value, value``: backticked items are taken literally (a command may hold a
comma); without backticks the text is split on commas; a value that starts with ``none`` declares an empty
list (``none``, ``none.``, ``none (fixtures only)``). With no ``Paths`` line the Allowed paths are used.

``missing`` names the blocks the ledger needs that the text lacks (``## Outcome``, ``## Acceptance criteria``,
``## Decisions``, ``## Defaults when ambiguous``, ``## Capabilities`` or ``## Capabilities (<keys>)``,
``## Allowed paths``, ``## Risk tier``); ``optional_missing`` the other template sections (``## Acceptance``,
``## Out of scope``, ``## Evidence plan``) when absent.
"""

from __future__ import annotations

import re
from typing import Any

TIERS: tuple[str, ...] = ("trivial", "low", "medium", "high")
REQUIRED: tuple[str, ...] = ("outcome", "acceptance criteria", "decisions", "defaults when ambiguous", "capabilities",
                             "allowed paths", "risk tier")
OPTIONAL: tuple[str, ...] = ("acceptance", "out of scope", "evidence plan")
CAPABILITY_KEYS: tuple[str, ...] = ("paths", "commands", "egress", "data")
_KEY_ALIASES = {"paths": "paths", "path": "paths", "allowed paths": "paths", "allowed_paths": "paths",
                "write paths": "paths", "commands": "commands", "command": "commands", "egress": "egress",
                "network": "egress", "data": "data", "secrets": "secrets", "test_command": "test_command",
                "test command": "test_command"}
_NONE_WORDS = {"", "none", "(none)", "-", "n/a", "nothing", "[]"}
_BULLET = re.compile(r"^(\s*)(?:[-*+]|(\d+)[.)])\s+(.*)$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: A human handle: ``@name`` not glued to a word (an e-mail address is not one) and not ``@x:`` (an actor label).
HANDLE = re.compile(r"(?<![\w.@])@([A-Za-z0-9][A-Za-z0-9_.\-/]*)(?![\w:\-/])")
_UNCONFIRMED = re.compile(r"\b(?:unconfirmed|recommended|assumed)\b", re.I)
_PLACEHOLDER = re.compile(r"^<[^>]*>$")


# --------------------------------------------------------------------------- low-level readers


def sections(text: str) -> tuple[str, dict[str, list[str]]]:
    """``(title, {lower-cased ## heading: body lines})`` with HTML comments removed. The first ``# `` heading
    is the title; ``###`` headings stay inside their section."""
    text = _COMMENT.sub("", str(text or ""))
    title = ""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m1 = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m1 and not title and current is None:
            title = m1.group(1).strip()
            continue
        m = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if m and not line.startswith("###"):
            current = re.sub(r"\s+", " ", m.group(1).strip().strip("*").strip()).lower()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line)
    title = re.sub(r"^contract\s*:\s*", "", title, flags=re.I).strip()
    return title, out


def items(lines: list[str]) -> list[str]:
    """List items of a section body: each bullet or numbered item with its indented continuation lines joined.
    A non-indented line that ends with ':' (``Per contract:``) is a label and is skipped; a body with no list
    items is one item per paragraph. ``none`` items are dropped."""
    out: list[str] = []
    has_bullets = any(_BULLET.match(ln) for ln in lines)
    para: list[str] = []
    prev_blank = True
    for ln in lines:
        if not has_bullets:
            if ln.strip():
                para.append(ln.strip())
            elif para:
                out.append(" ".join(para))
                para = []
            continue
        m = _BULLET.match(ln)
        if m:
            out.append(m.group(3).strip())
        elif not ln.strip():
            prev_blank = True
            continue
        elif ln[:1].isspace() and out:
            out[-1] = f"{out[-1]} {ln.strip()}"
        elif ln.strip().endswith(":"):
            pass
        elif out and not prev_blank:
            out[-1] = f"{out[-1]} {ln.strip()}"
        else:
            out.append(ln.strip())
        prev_blank = False
    if para:
        out.append(" ".join(para))
    return [i for i in out if i and i.strip().lower() not in _NONE_WORDS]


def cells(line: str) -> list[str]:
    """Table cells split on unescaped pipes; ``\\|`` inside a cell is a literal pipe."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def _is_none(text: str) -> bool:
    t = re.sub(r"[`*_]", "", text).strip().lower().rstrip(".")
    return t in _NONE_WORDS or bool(re.match(r"^none\b", t))


def values(text: str) -> list[str]:
    """Backticked items taken literally, else the comma-separated items; a ``none ...`` value is ``[]``."""
    ticks = [t.strip() for t in re.findall(r"`([^`]+)`", text)]
    if ticks:
        return [t for t in ticks if t and t.lower() not in _NONE_WORDS]
    if _is_none(text):
        return []
    return [v.strip().rstrip(".").strip() for v in text.split(",")
            if v.strip().rstrip(".").strip() and not _is_none(v)]


def _plain(text: str) -> str:
    """A cell without markdown emphasis or code ticks."""
    return re.sub(r"\s+", " ", re.sub(r"\*\*|__|`", "", text)).strip()


# --------------------------------------------------------------------------- blocks


def _acceptance_criteria(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, item in enumerate(items(lines), 1):
        rid = f"AC{n}"
        m = re.match(r"^(AC\d+)\s*[:.)\-]\s*(.+)$", item, re.I)
        if m:
            rid, item = m.group(1).upper(), m.group(2).strip()
        negative = bool(re.search(r"\(negative\)", item, re.I))
        item = re.sub(r"\s*\*?\(negative\)\*?\s*\.?\s*$", "", item, flags=re.I).strip()
        name = None
        text = item
        nm = re.match(r"^`([^`]+)`\s*(?:[—–:-]+\s*)?(.*)$", item)
        if nm:
            name = nm.group(1).strip()
            text = nm.group(2).strip() or name
        rows.append({"id": rid, "name": name, "text": text, "negative": negative})
    return rows


def _acceptance_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^[-*]\s*\$\s+(.+)$", s)
        mm = re.match(r"^[-*]\s*mcp:\s*(.+)$", s, re.I)
        if m:
            out.append({"kind": "shell", "command": m.group(1).strip(), "expects": []})
        elif mm:
            out.append({"kind": "mcp", "command": mm.group(1).strip(), "expects": []})
        elif s.startswith("=>") and out:
            out[-1]["expects"].append(s[2:].strip())
    return out


def decision_handle(decided_by: str) -> tuple[bool, str | None]:
    """``(confirmed, handle)`` of a Decided by cell (module docstring)."""
    text = str(decided_by or "")
    m = HANDLE.search(text)
    handle = "@" + m.group(1).rstrip(".-/") if m else None
    return bool(handle) and not _UNCONFIRMED.search(text), handle


def _decision_n(cell: str, index: int) -> Any:
    m = re.match(r"^\s*(?:[Dd]|#)?\s*(\d+)\s*$", _plain(cell))
    if m:
        return int(m.group(1))
    return _plain(cell) or index


def _decisions(lines: list[str], warnings: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``(decisions, problem)``: None and a reason when the block cannot be read."""
    body = [ln for ln in lines if ln.strip()]
    rows = [ln for ln in body if ln.lstrip().startswith("|")]
    if not rows:
        if body and re.match(r"^\s*none\.?\s*$", body[0], re.I):
            return [], None
        bullets = [ln for ln in body if re.match(r"^\s*[-*]\s", ln)]
        if bullets:  # the ledger renders decided rows as "- <question> -> <choice> (<by>)"
            out = []
            for i, ln in enumerate(bullets, 1):
                q, _, rest = re.sub(r"^\s*[-*]\s*", "", ln).partition("->")
                by = re.search(r"\(([^()]*)\)\s*$", rest)
                value = rest[:by.start()].strip() if by else rest.strip()
                confirmed, handle = decision_handle(by.group(1) if by else "")
                out.append({"n": i, "question": q.strip(), "options": [], "recommendation": "", "value": value,
                            "decided_by": by.group(1).strip() if by else "", "confirmed": confirmed, "handle": handle})
            return out, None
        return None, "no table and not 'None.'"
    head = [_plain(c).lower() for c in cells(rows[0])]
    if "value" not in head:
        return None, "no Value column"
    col = {name: head.index(name) for name in ("#", "question", "options", "recommendation", "value", "decided by")
           if name in head}
    out = []
    for i, r in enumerate(rows[1:], 1):
        if re.match(r"^\s*\|?[\s:|-]+\|?\s*$", r):
            continue
        c = cells(r)
        if len(c) != len(head):
            warnings.append(f"Decisions row {c[0] if c else i!r}: {len(c)} cells, the table has {len(head)} columns "
                            "(write a literal pipe inside a cell as \\|)")
            c = (c + [""] * len(head))[:len(head)]

        def get(name: str) -> str:
            return c[col[name]].strip() if name in col else ""

        value = _plain(get("value"))
        if _PLACEHOLDER.match(value):
            value = ""
        decided_by = get("decided by")
        if _PLACEHOLDER.match(_plain(decided_by)):  # the template's "<@handle>" is a blank to fill, not a handle
            decided_by = ""
        confirmed, handle = decision_handle(decided_by)
        options = [_plain(o) for o in re.split(r"\s+·\s+", get("options")) if _plain(o)]
        out.append({"n": _decision_n(get("#"), len(out) + 1), "question": _plain(get("question")),
                    "options": options, "recommendation": _plain(get("recommendation")), "value": value,
                    "decided_by": _plain(decided_by), "confirmed": confirmed, "handle": handle})
    return out, None


def _capabilities(lines: list[str], allowed: list[str] | None, unknown: list[str]) -> dict[str, Any]:
    caps: dict[str, Any] = {}
    for item in items(lines):
        key, sep, rest = item.partition(":")
        k = _KEY_ALIASES.get(_plain(key).lower())
        if not sep or k is None:
            unknown.append(item)
            continue
        vals = values(rest)
        if k == "test_command":
            if vals:
                caps["test_command"] = vals[0]
            continue
        caps[k] = list(dict.fromkeys(caps.get(k, []) + vals))
    if "paths" not in caps and allowed is not None:
        caps["paths"] = list(allowed)
    return caps


def _tier(lines: list[str]) -> str | None:
    text = _plain(" ".join(ln.strip() for ln in lines if ln.strip())).lower()
    m = re.match(r"^\W*(trivial|low|medium|high)\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\btier\W+(trivial|low|medium|high)\b", text)
    return m.group(1) if m else None


def parse_contract(md: str) -> dict[str, Any]:
    """Parse contract.md text (module docstring for the shape). Pure; never raises on content."""
    from .transitions import defaults_problems

    title, secs = sections(md)
    warnings: list[str] = []
    unknown: list[str] = []
    missing: list[str] = []

    def body(name: str) -> list[str] | None:
        return secs.get(name)

    def present(name: str) -> bool:
        lines = body(name)
        return lines is not None and any(ln.strip() for ln in lines)

    outcome = " ".join(ln.strip() for ln in body("outcome") or [] if ln.strip()) or None
    criteria = _acceptance_criteria(body("acceptance criteria") or [])
    acc_lines = _acceptance_lines(body("acceptance") or [])
    decisions, dproblem = (None, None)
    if body("decisions") is not None:
        decisions, dproblem = _decisions(body("decisions") or [], warnings)
    defaults = items(body("defaults when ambiguous") or []) if present("defaults when ambiguous") else []
    allowed = None
    if body("allowed paths") is not None:
        allowed = [v for item in items(body("allowed paths") or []) for v in (values(item) or [])]
    caps = _capabilities(body("capabilities") or [], allowed, unknown) if body("capabilities") is not None else None
    tier = _tier(body("risk tier") or []) if present("risk tier") else None

    if not outcome:
        missing.append("## Outcome")
    if not criteria:
        missing.append("## Acceptance criteria")
    if body("decisions") is None:
        missing.append("## Decisions")
    elif decisions is None:
        missing.append(f"## Decisions ({dproblem})")
    if not defaults:
        missing.append("## Defaults when ambiguous")
    elif defaults_problems({"defaults": defaults}):
        missing.append("## Defaults when ambiguous (" + "; ".join(defaults_problems({"defaults": defaults})) + ")")
    if caps is None:
        missing.append("## Capabilities")
    else:
        absent = [k for k in CAPABILITY_KEYS if not isinstance(caps.get(k), list)]
        if absent:
            missing.append("## Capabilities (" + ", ".join(absent) + ")")
    if not allowed:
        missing.append("## Allowed paths")
    if not present("risk tier"):
        missing.append("## Risk tier")
    elif tier is None:
        missing.append("## Risk tier (no tier: " + ", ".join(TIERS) + ")")
    optional_missing = [f"## {n[:1].upper()}{n[1:]}" for n in OPTIONAL if body(n) is None]
    return {
        "title": title or None,
        "outcome": outcome,
        "acceptance_criteria": criteria,
        "acceptance_lines": acc_lines,
        "decisions": decisions or [],
        "defaults": defaults,
        "capabilities": caps or {},
        "allowed_paths": allowed or [],
        "tier": tier,
        "out_of_scope": items(body("out of scope") or []),
        "evidence_plan": items(body("evidence plan") or []),
        "missing": missing,
        "optional_missing": optional_missing,
        "unknown": unknown,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- ledger shapes


def acceptance_rows(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """``node.acceptance`` rows from the parsed criteria: ``{id, text, source: "contract"}`` plus ``name`` (the
    criterion's test name, as written; not a pytest node id, so it is not ``test``) and ``negative`` when set."""
    rows = []
    for r in parsed.get("acceptance_criteria") or []:
        row: dict[str, Any] = {"id": r["id"], "text": r["text"], "source": "contract"}
        if r.get("name"):
            row["name"] = r["name"]
        if r.get("negative"):
            row["negative"] = True
        rows.append(row)
    return rows


def touched_from_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """``(touched_symbols, skipped)`` from Allowed paths: a glob reduces to its literal directory prefix
    (``src/lib/api/**`` -> ``src/lib/api/``); a path with no literal prefix (``**``) or one ``validate_touched``
    refuses is skipped. ``[id]`` and ``(group)`` segments are literal (Next.js routes), not globs."""
    from .nodes import validate_touched

    out: list[str] = []
    skipped: list[str] = []
    for raw in paths or []:
        p = raw.strip().removeprefix("./")
        segs = p.split("/")
        if any(ch in p for ch in "*?"):
            lit = []
            for s in segs:
                if any(ch in s for ch in "*?"):
                    break
                lit.append(s)
            p = "/".join(lit) + "/" if lit else ""
        if not p:
            skipped.append(raw)
            continue
        try:
            got = validate_touched([p])
        except ValueError:
            skipped.append(raw)
            continue
        for g in got:
            if g not in out:
                out.append(g)
    return out, skipped


def summary(parsed: dict[str, Any]) -> dict[str, Any]:
    """Counts per field, for reports."""
    caps = parsed.get("capabilities") or {}
    return {"title": parsed.get("title"), "acceptance_criteria": len(parsed.get("acceptance_criteria") or []),
            "acceptance_lines": len(parsed.get("acceptance_lines") or []),
            "decisions": len(parsed.get("decisions") or []),
            "decisions_confirmed": [d["n"] for d in parsed.get("decisions") or [] if d.get("confirmed")],
            "defaults": len(parsed.get("defaults") or []),
            "capabilities": {k: len(v) if isinstance(v, list) else v for k, v in caps.items()},
            "allowed_paths": len(parsed.get("allowed_paths") or []), "tier": parsed.get("tier"),
            "out_of_scope": len(parsed.get("out_of_scope") or []),
            "evidence_plan": len(parsed.get("evidence_plan") or []), "missing": parsed.get("missing") or []}
