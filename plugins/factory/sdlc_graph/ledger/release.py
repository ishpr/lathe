"""Releases: ``release_request`` opens a ``release`` node in ``requested`` for merged contracts.

The ``release`` machine (state_machines.json) is requested -> staged -> released -> verified | rolled_back.
Requesting one is a plain write any actor may make; moving it on is guarded by the ledger: ``staged`` needs a
binding green ``ci_run``, ``released`` needs a human actor, ``verified`` needs ``ready_poll`` (status 200) and
``smoke`` (exit 0) evidence and a human or CI actor, ``rolled_back`` a human or CI actor. The MCP server also
refuses an agent for ``released``/``closed``/... before the ledger is asked (``PROTECTED_STATES``).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import psycopg

from . import nodes

_SLUG = re.compile(r"[^a-z0-9]+")


def request(conn: psycopg.Connection, *, actor: str, contract_slugs: list[str] | None = None,
            feature_slug: str | None = None, slug: str | None = None, title: str | None = None) -> dict[str, Any]:
    """Create a ``release`` node in ``requested`` with a ``shipped_in`` edge from each merged contract.

    Give ``contract_slugs`` or a ``feature_slug`` (its ``decomposes_into`` contracts are used, and the feature
    gets a ``shipped_in`` edge too, which its ``building -> released`` guard needs). Refused unless every contract
    is ``merged``.

    Returns:
        ``{"release": Node.to_dict(), "contracts": [slug...], "feature": slug|None}``.

    Raises:
        ValueError: nothing named, a node that is not a contract/feature, or a contract that is not merged.
        LookupError: unknown slug.
    """
    if bool(contract_slugs) == bool(feature_slug):
        raise ValueError("give contract_slugs or feature_slug, one of them")
    feature = None
    if feature_slug:
        feature = nodes.get_row(conn, feature_slug)
        if feature["kind"] != "feature":
            raise ValueError(f"{feature_slug!r} is a {feature['kind']}, not a feature")
        contract_slugs = [n["node"]["slug"] for n in nodes.neighbours(conn, feature_slug, "decomposes_into", "out")
                          if n["node"]["kind"] == "contract"]
        if not contract_slugs:
            raise ValueError(f"feature {feature_slug!r} has no contracts to release")
    rows = [nodes.get_row(conn, s) for s in dict.fromkeys(contract_slugs or [])]
    bad = [f"{r['slug']} ({r['kind']})" for r in rows if r["kind"] != "contract"]
    if bad:
        raise ValueError(f"only contracts ship in a release: {', '.join(bad)}")
    unmerged = [f"{r['slug']} ({r['state']})" for r in rows if r["state"] != "merged"]
    if unmerged:
        raise ValueError(f"a release needs merged contracts; not merged: {', '.join(unmerged)}")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    base = _SLUG.sub("-", (feature_slug or rows[0]["slug"]).lower()).strip("-")[:40]
    slug = slug or f"release-{base}-{stamp}"
    names = ", ".join(r["slug"] for r in rows)
    with conn.transaction():
        rel = nodes.create(conn, kind="release", slug=slug, title=title or f"Release of {feature_slug or names}",
                           repo=rows[0]["repo"], created_by=actor, attrs={"contracts": [r["slug"] for r in rows],
                                                                          "feature": feature_slug})
        for r in rows:
            nodes.link(conn, r["slug"], slug, "shipped_in")
        if feature_slug:
            nodes.link(conn, feature_slug, slug, "shipped_in")
    return {"release": rel, "contracts": [r["slug"] for r in rows], "feature": feature_slug}
