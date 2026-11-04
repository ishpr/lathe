"""``epic.decompose``: proposed feature nodes from an epic's text. Read-only; it never writes a node.

The epic node's ``path`` (a repo-relative markdown file, the epic skill's format) is read from the checkout
(``root``, else ``CLAUDE_PROJECT_DIR``, else the working directory). The ``## Features`` section is a numbered or
bulleted list ``**Title** (enabler)? - description - moves ... - size S|M|L`` (the form in docs/epic/examples.md).

Walking-skeleton rule (the epic skill's): the first feature is the thinnest end-to-end slice. Among the vertical
features (not enablers, not marked "to be sliced") the smallest size wins, ties going to the author's order; it moves
to the front when it was not first. Enablers and the rest keep the author's order after it. Every other vertical
feature ``depends_on`` the skeleton; an enabler depends on nothing (it unlocks, it is not unlocked). Flags:
``not_vertical`` (an enabler: it ships no user-visible capability alone), ``needs_slicing`` (size L, or "to be sliced"),
``exists`` (a node with the suggested slug is already in the ledger). Accepting a proposal is the ordinary
``node create`` by a human, with the epic linked ``decomposes_into``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import psycopg

_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\*\*(?P<title>.+?)\*\*\s*(?P<rest>.*)$")
_FEATURE_H = re.compile(r"^#{2,4}\s+Feature\s*:\s*(?P<title>.+?)\s*$", re.I)
_SIZE = re.compile(r"\bsize\s*[:=]?\s*(XS|S|M|L|XL)\b", re.I)
_RANK = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}
_SEP = re.compile(r"\s+[—–]\s+|\s+-\s+")


def slugify(title: str, prefix: str = "feature-") -> str:
    body = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return prefix + (body or "untitled")


def _features_section(lines: list[str]) -> list[str]:
    start = None
    fenced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
        m = re.match(r"^(#{1,6})\s+(?:\d+\.\s*)?(.*)$", line)
        if fenced or not m:
            continue
        if start is None and m.group(2).strip().lower().startswith("features"):
            start, level = i + 1, len(m.group(1))
        elif start is not None and len(m.group(1)) <= level:
            return lines[start:i]
    return lines[start:] if start is not None else []


def parse_features(text: str) -> list[dict[str, Any]]:
    """``[{"title", "description", "enabler", "size", "to_slice"}]`` in the author's order (empty: none found)."""
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    for line in _features_section(lines):
        m = _ITEM.match(line)
        if not m:
            continue
        rest = m.group("rest").strip()
        size = _SIZE.search(rest)
        parts = [p.strip() for p in _SEP.split(rest) if p.strip()]
        out.append({"title": m.group("title").strip(), "description": parts[0] if parts else "",
                    "enabler": bool(re.search(r"\(\s*enabler\s*\)", rest, re.I) or re.search(r"\(\s*enabler\s*\)", line, re.I)),
                    "size": size.group(1).upper() if size else None,
                    "to_slice": bool(re.search(r"to be sliced|needs? slicing", rest, re.I))})
    if not out:  # an epic file that holds its features as "## Feature: <title>" headings
        for line in lines:
            m = _FEATURE_H.match(line)
            if m:
                out.append({"title": m.group("title"), "description": "", "enabler": "(enabler)" in m.group("title").lower(),
                            "size": None, "to_slice": False})
    return out


def propose(features: list[dict[str, Any]], epic_slug: str = "") -> list[dict[str, Any]]:
    """Order ``features`` (see :func:`parse_features`) walking-skeleton first and return the proposals:
    ``{"order", "title", "slug", "description", "size", "depends_on": [slug], "flags": [...], "reason"}``."""
    for f in features:
        f["slug"] = slugify(f["title"])
    seen: dict[str, int] = {}
    for f in features:  # two titles can collapse to one slug
        seen[f["slug"]] = seen.get(f["slug"], 0) + 1
        if seen[f["slug"]] > 1:
            f["slug"] += f"-{seen[f['slug']]}"
    vertical = [f for f in features if not f["enabler"] and not f["to_slice"]]
    pool = vertical or [f for f in features if not f["enabler"]]
    skeleton = min(pool, key=lambda f: (_RANK.get(f["size"] or "", 2), features.index(f))) if pool else None
    ordered = ([skeleton] if skeleton else []) + [f for f in features if f is not skeleton]
    out: list[dict[str, Any]] = []
    for n, f in enumerate(ordered, 1):
        flags = []
        if f["enabler"]:
            flags.append("not_vertical")
        if f["to_slice"] or f["size"] in ("L", "XL"):
            flags.append("needs_slicing")
        if f is skeleton:
            moved = features.index(f) != 0
            reason = ("walking skeleton: the thinnest vertical slice" + (", moved ahead of the author's earlier features" if moved else ""))
            deps: list[str] = []
        elif f["enabler"]:
            reason = "enabler: unlocks measurement or later work, ships no user-visible capability alone"
            deps = []
        else:
            reason = "vertical feature built on the skeleton"
            deps = [skeleton["slug"]] if skeleton else []
        out.append({"order": n, "title": f["title"], "slug": f["slug"], "description": f["description"],
                    "size": f["size"], "depends_on": deps, "flags": flags, "reason": reason})
    if out and skeleton is None:
        out[0]["flags"].append("no_vertical_skeleton")
        out[0]["reason"] = "no vertical feature found; the first enabler stays first and the epic needs a thin end-to-end feature"
    return out


def decompose(conn: psycopg.Connection, slug: str, root: str | Path | None = None) -> dict[str, Any]:
    """Proposed feature nodes for epic ``slug``: ``{"epic", "path", "proposals": [...], "committed": False}``.

    Raises:
        LookupError: unknown slug, or the epic's file is missing. ValueError: the node is not an epic, has no path,
        its path leaves the checkout, or the file lists no features.
    """
    from . import nodes

    row = nodes.get_row(conn, slug)
    if row["kind"] != "epic":
        raise ValueError(f"{slug} is a {row['kind']}, not an epic")
    rel = row["path"]
    if not rel:
        raise ValueError(f"epic {slug} has no artifact path; set the node's path to its markdown file")
    base = Path(root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"epic path {rel!r} is outside the repository")
    if not target.is_file():
        raise LookupError(f"epic file {rel} not found under {base}")
    features = parse_features(target.read_text(encoding="utf-8"))
    if not features:
        raise ValueError(f"no features found in {rel} (expected a '## Features' list of '**Title** - ...' items)")
    proposals = propose(features, slug)
    taken = {r["slug"] for r in conn.execute("SELECT slug FROM node WHERE slug = ANY(%s)", ([p["slug"] for p in proposals],)).fetchall()}
    for p in proposals:
        if p["slug"] in taken:
            p["flags"].append("exists")
    return {"epic": slug, "path": rel, "proposals": proposals, "committed": False,
            "note": "proposals only; a human accepts each with `sdlc-graph node create --kind feature` and links the epic decomposes_into"}
