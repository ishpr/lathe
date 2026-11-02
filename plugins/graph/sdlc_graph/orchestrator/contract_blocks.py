"""The contract's autonomy blocks: ``## Defaults when ambiguous`` and ``## Capabilities``
(docs/DESIGN.md section 14), parsed from contract.md onto the ledger node.

Every question is asked when the contract is written; execution runs on the answers. Two blocks carry
what no decision list anticipates:

``## Defaults when ambiguous``
    the policy an unattended session applies instead of asking. Stored as ``node.attrs["defaults"]``
    (a list of lines); the ``defaults_present`` guard on ``drafted -> ready`` requires it.
``## Capabilities``
    one ``- key: value, value`` line per key (``paths``, ``commands``, ``egress``, ``data``, optionally
    ``secrets`` and ``test_command``); backticked items are taken literally (a command may hold a comma),
    ``none`` declares an empty list. Merged into ``node.capabilities`` and validated by
    ``impact.capabilities.validate``; the ``capabilities_present`` guard requires paths, commands,
    egress and data to be declared. When the block has no ``paths`` line the pack's ``## Allowed paths``
    bullets are used.

Example::

    ## Defaults when ambiguous
    1. Make the smallest change that satisfies the acceptance criteria.
    2. Follow the nearest existing pattern in the code being changed.
    3. Prefer the option the Decisions above imply.
    4. When none applies, choose the reversible option.

    ## Capabilities
    - paths: `app/ratelimit/**`, `tests/ratelimit/**`
    - commands: `python3 -m pytest tests/ratelimit -q`, `bash scripts/smoke_rate_limit.sh`
    - egress: none
    - data: `redis:rw`

Who may attach: anyone while the contract is ``drafted`` (it is not ready until a guard says so, and
commands, egress or secrets need a human for ``ready``); after ``drafted`` only a human (``@handle``),
because the blocks then change what an approved contract may do. Every attach is logged in
``node.attrs["contract_blocks_log"]``.

CLI (one plain command, no heredoc)::

    python -m sdlc_graph.orchestrator.contract_blocks --slug rate-limit --file .claude/work/rate-limit/contract.md --actor @ish
    python -m sdlc_graph.orchestrator.contract_blocks --check --file contract.md      # parse only

Exit: 0 attached (or parsed) with both blocks, 1 a block is missing or a value is refused, 2 misuse.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.impact import capabilities as caps_mod
from sdlc_graph.ledger import transitions

DEFAULTS_HEADINGS = ("defaults when ambiguous", "defaults")
CAPABILITIES_HEADINGS = ("capabilities",)
_KEY_ALIASES = {"paths": "paths", "allowed paths": "paths", "allowed_paths": "paths", "write paths": "paths",
                "commands": "commands", "command": "commands", "egress": "egress", "network": "egress",
                "data": "data", "secrets": "secrets", "test_command": "test_command", "test command": "test_command"}
_NONE = {"", "none", "(none)", "-", "n/a", "nothing", "[]"}
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
LOG_MAX = 20

#: The standard four lines (the sdlc plugin's ``acceptance.py --ready`` requires them verbatim). Used when a
#: renderer needs an example or a caller asks for the standard policy, never as a silent fill-in for a contract
#: that has none.
POLICY_EXAMPLE = [
    "Make the smallest change that satisfies the acceptance criteria.",
    "Follow the nearest existing pattern in the code being changed.",
    "Prefer the option the Decisions above imply.",
    "When none applies, choose the reversible option.",
]


def sections(text: str) -> dict[str, list[str]]:
    """``{lower-cased heading: body lines}`` for every ``## `` heading."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if m and not line.startswith("###"):
            current = m.group(1).strip().lower()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line)
    return out


def _find(secs: dict[str, list[str]], names: tuple[str, ...]) -> list[str] | None:
    for n in names:
        if n in secs:
            return secs[n]
    return None


def _items(lines: list[str]) -> list[str]:
    """Bullet items (continuation lines joined); a body with no bullets is one item per paragraph."""
    items: list[str] = []
    bullets = any(_BULLET.match(ln) for ln in lines)
    para: list[str] = []
    for ln in lines:
        m = _BULLET.match(ln)
        if bullets:
            if m:
                items.append(m.group(1).strip())
            elif ln.strip() and items:
                items[-1] = f"{items[-1]} {ln.strip()}"
        else:
            if ln.strip():
                para.append(ln.strip())
            elif para:
                items.append(" ".join(para))
                para = []
    if para:
        items.append(" ".join(para))
    return [i for i in items if i and i.lower() not in _NONE]


def _values(text: str) -> list[str]:
    ticks = re.findall(r"`([^`]+)`", text)
    if ticks:
        return [t.strip() for t in ticks if t.strip() and t.strip().lower() not in _NONE]
    if text.strip().lower() in _NONE:
        return []
    return [v.strip() for v in text.split(",") if v.strip() and v.strip().lower() not in _NONE]


def parse(text: str) -> dict[str, Any]:
    """Parse the two blocks. Returns ``{"defaults": [..] | None, "capabilities": {..} | None,
    "missing": [block names], "unknown": [capability lines not understood]}``."""
    secs = sections(text)
    dlines = _find(secs, DEFAULTS_HEADINGS)
    defaults = _items(dlines) if dlines is not None else None
    clines = _find(secs, CAPABILITIES_HEADINGS)
    caps: dict[str, Any] | None = None
    unknown: list[str] = []
    if clines is not None:
        caps = {}
        for item in _items(clines) if any(ln.strip() for ln in clines) else []:
            key, sep, rest = item.partition(":")
            k = _KEY_ALIASES.get(key.strip().strip("`*").lower())
            if not sep or k is None:
                unknown.append(item)
                continue
            vals = _values(rest)
            if k == "test_command":
                if vals:
                    caps["test_command"] = vals[0]
                continue
            caps[k] = list(dict.fromkeys(caps.get(k, []) + vals))
        if "paths" not in caps:
            allowed = _find(secs, ("allowed paths",))
            if allowed is not None:
                caps["paths"] = [v for item in _items(allowed) for v in _values(item)]
    missing = []
    if not defaults:
        missing.append("## Defaults when ambiguous")
    if caps is None:
        missing.append("## Capabilities")
    else:
        absent = transitions.capabilities_missing(caps)
        if absent:
            missing.append("## Capabilities (" + ", ".join(absent) + ")")
    return {"defaults": defaults or None, "capabilities": caps, "missing": missing, "unknown": unknown}


def render(node: dict[str, Any]) -> list[str]:
    """The two blocks as contract.md lines, from the ledger row (dispatch renders contract.md with them)."""
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    d = attrs.get("defaults")
    defaults = [d] if isinstance(d, str) else list(d or [])
    caps = node.get("capabilities") if isinstance(node.get("capabilities"), dict) else {}
    lines = ["## Defaults when ambiguous", ""]
    lines += [f"- {x}" for x in defaults] or ["(missing: the orchestrator refuses to dispatch this contract)"]
    lines += ["", "## Capabilities", ""]
    for key in ("paths", "commands", "egress", "data", "secrets"):
        if key in caps:
            vals = caps.get(key) or []
            lines.append(f"- {key}: " + (", ".join(f"`{v}`" for v in vals) if vals else "none"))
        elif key in transitions.CAPABILITY_BLOCK_KEYS:
            lines.append(f"- {key}: (not declared)")
    if caps.get("test_command"):
        lines.append(f"- test_command: `{caps['test_command']}`")
    return lines


def set_blocks(conn: psycopg.Connection, slug: str, *, actor: str, defaults: list[str] | None = None,
               capabilities: dict[str, Any] | None = None, source: str | None = None) -> dict[str, Any]:
    """Store ``defaults`` in ``node.attrs`` and merge ``capabilities`` (the keys given replace the node's).

    Raises:
        LookupError: unknown slug. PermissionError: a non-human actor after ``drafted``.
        ValueError: bad actor, or capabilities that ``impact.capabilities.validate`` refuses.
    """
    cls = transitions.actor_class(actor)
    with conn.transaction():
        row = conn.execute("SELECT * FROM node WHERE slug = %s FOR UPDATE", (slug,)).fetchone()
        if row is None:
            raise LookupError(f"unknown node slug: {slug!r}")
        if row["kind"] != "contract":
            raise ValueError(f"{slug!r} is a {row['kind']}, not a contract")
        if row["state"] != "drafted" and cls != "human":
            raise PermissionError(f"{slug!r} is {row['state']}: after drafted only a human (@handle) may change its "
                                  "defaults or capabilities")
        caps = dict(row["capabilities"] or {})
        if capabilities:
            caps.update(capabilities)
            caps_mod.validate(caps)
        attrs = dict(row["attrs"] or {})
        if defaults is not None:
            clean = [d.strip() for d in defaults if isinstance(d, str) and d.strip()]
            if not clean:
                raise ValueError("defaults must hold at least one non-empty line")
            attrs["defaults"] = clean
        log = list(attrs.get("contract_blocks_log") or [])
        log.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "actor": actor,
                    "state": row["state"], "source": source, "defaults": defaults is not None,
                    "capability_keys": sorted(capabilities or {})})
        attrs["contract_blocks_log"] = log[-LOG_MAX:]
        conn.execute("UPDATE node SET attrs = %s, capabilities = %s, updated_at = now() WHERE id = %s",
                     (Jsonb(attrs), Jsonb(caps), row["id"]))
    return {"slug": slug, "state": row["state"], "defaults": attrs.get("defaults"), "capabilities": caps,
            "missing": ([] if transitions.defaults_ok(attrs) else ["## Defaults when ambiguous"])
                       + ([f"## Capabilities ({', '.join(m)})"] if (m := transitions.capabilities_missing(caps)) else [])}


def attach(conn: psycopg.Connection, slug: str, text: str, *, actor: str, source: str | None = None) -> dict[str, Any]:
    """Parse contract.md ``text`` and store what it holds on the node (see :func:`set_blocks`).
    Returns the :func:`set_blocks` result plus ``unknown`` lines and ``sha256`` of the text."""
    parsed = parse(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    out = set_blocks(conn, slug, actor=actor, defaults=parsed["defaults"], capabilities=parsed["capabilities"],
                     source=f"{source or 'contract.md'} sha256:{digest[:16]}")
    return {**out, "unknown": parsed["unknown"], "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m sdlc_graph.orchestrator.contract_blocks", description=__doc__.split("\n\n")[0])
    p.add_argument("--file", required=True, help="contract.md to read")
    p.add_argument("--slug", help="contract node to attach the blocks to")
    p.add_argument("--actor", help="@handle, or agent:<alias> while the contract is drafted")
    p.add_argument("--check", action="store_true", help="parse and print only; touch nothing")
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    try:
        text = Path(a.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"contract_blocks: cannot read {a.file}: {exc}", file=sys.stderr)
        return 2
    try:
        if a.check:
            out: dict[str, Any] = parse(text)
        else:
            if not a.slug or not a.actor:
                print("contract_blocks: --slug and --actor are required unless --check", file=sys.stderr)
                return 2
            from sdlc_graph import db

            with db.connect() as conn:
                out = attach(conn, a.slug, text, actor=a.actor, source=a.file)
    except (PermissionError, ValueError, LookupError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if a.json else f"contract_blocks: refused: {exc}")
        return 1
    except psycopg.Error as exc:
        print(f"contract_blocks: database error: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"defaults: {len(out.get('defaults') or [])} line(s); capabilities: "
              + ", ".join(f"{k}={len(v) if isinstance(v, list) else v}" for k, v in (out.get('capabilities') or {}).items()))
        for m in out.get("missing") or []:
            print(f"missing: {m}")
        for u in out.get("unknown") or []:
            print(f"not understood (ignored): {u}")
    return 1 if out.get("missing") else 0


if __name__ == "__main__":
    sys.exit(main())
