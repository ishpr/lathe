"""Evidence: what a transition may cite. Local evidence is advisory; CI evidence is binding.

``KINDS`` maps each evidence kind to its required payload keys (extra keys are allowed and
kept). Payload shapes (docs/graph-architecture.md has examples)::

    tests_failing_first {tests: [nodeid...], commit}
                        tests entries may be {id, reason} objects, or a reasons map {nodeid: reason} given beside
                        them; reason is one of FAILURE_REASONS (why the test fails before the code exists)
    tests_frozen        {files: {path: sha256}, commit}
    verify_stamp        {frozen_ok, acceptance: {passed, failed}, ratchet: {new, known}, db_backend, tool_versions}
    acceptance          {passed, failed, rows}
    ratchet             {new, known}
    review_approval     {reviewer, author, url}
    ci_run              {url, conclusion}            # conclusion: GitHub check conclusion, "success" is green
    pr_url              {url}
    merge_sha           {sha}
    ready_poll          {url, status, attempts}      # status: final HTTP status code (int)
    smoke               {command, exit}
    guardrail_metric    {metric, baseline, target}   # epic -> accepted
    metrics_snapshot    {metrics, since}             # epic -> measured, feature -> evaluated
    triage              {summary, severity}          # incident -> triaged
    assumptions         {assumptions: [{default_applied, where, why}]}   # the orchestrator's shape
                     or {path, count, items: [{...}]}                    # the pack's /verify shape
                                                 # (the defaults a session applied instead of asking)
    superseded          {ids: [evidence id...], reason}   # the orchestrator set the attempt that wrote them aside
    contract_updated    {changed: [field...], decisions_confirmed: [n...]}   # intake.contract_update re-read contract.md
    graph_provisioned   {provider, where, schema}    # repo node: /factory:graph provision built or found the database

Evidence rows are never updated or deleted. When the orchestrator sets an attempt's commits aside (a retry
reset, a drift reset) the ``tests_failing_first`` and ``tests_frozen`` rows that attempt recorded describe
commits that are no longer on the branch; a ``superseded`` row names them, and :func:`resolve` (so every
guard) and the orchestrator's frozen-test reading skip them. Only those two kinds can be superseded
(``SUPERSEDABLE``): a ``superseded`` row naming any other row is ignored, so it can never hide a failing
CI run, stamp or review.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Evidence

KINDS: dict[str, tuple[str, ...]] = {
    "tests_failing_first": ("tests", "commit"),
    "tests_frozen": ("files", "commit"),
    "verify_stamp": ("frozen_ok", "acceptance", "ratchet", "db_backend", "tool_versions"),
    "acceptance": ("passed", "failed", "rows"),
    "ratchet": ("new", "known"),
    "review_approval": ("reviewer", "author", "url"),
    "ci_run": ("url", "conclusion"),
    "pr_url": ("url",),
    "merge_sha": ("sha",),
    "ready_poll": ("url", "status", "attempts"),
    "smoke": ("command", "exit"),
    "guardrail_metric": ("metric", "baseline", "target"),
    "metrics_snapshot": ("metrics", "since"),
    "triage": ("summary", "severity"),
    "superseded": ("ids", "reason"),
    "contract_updated": ("changed", "decisions_confirmed"),
    "graph_provisioned": ("provider", "where", "schema"),
    # Either shape (see the module docstring); validate() checks which one the payload carries.
    "assumptions": (),
}

#: Why a test fails before the code exists (``tests_failing_first``). The first four are the right reasons for a new
#: test; the rest (``WRONG_FAILURE_REASONS``) mean the test itself is broken, and ``ready -> tests_frozen`` refuses them.
FAILURE_REASONS: tuple[str, ...] = ("not_implemented", "assertion", "status_mismatch", "other", "import_error",
                                    "attribute_error", "name_error", "syntax_error", "collection_error")
WRONG_FAILURE_REASONS: frozenset[str] = frozenset({"import_error", "attribute_error", "name_error", "syntax_error",
                                                   "collection_error"})
_TEST_ID_KEYS: tuple[str, ...] = ("id", "nodeid", "test")


def failing_first_tests(payload: dict[str, Any] | None) -> list[tuple[str, str | None]]:
    """``[(test id, reason or None)]`` of a ``tests_failing_first`` payload, whichever form each entry has: a node
    id string, or an object with ``id`` (``nodeid``, ``test``) and ``reason``; a top-level ``reasons`` map
    ``{id: reason}`` fills in reasons the entries lack. Entries without an id are skipped."""
    p = payload if isinstance(payload, dict) else {}
    extra = p.get("reasons") if isinstance(p.get("reasons"), dict) else {}
    out: list[tuple[str, str | None]] = []
    for t in p.get("tests") if isinstance(p.get("tests"), list) else []:
        if isinstance(t, str) and t.strip():
            tid, reason = t, None
        elif isinstance(t, dict):
            tid = next((t[k] for k in _TEST_ID_KEYS if isinstance(t.get(k), str) and t[k].strip()), None)
            reason = t.get("reason") if isinstance(t.get("reason"), str) else None
            if tid is None:
                continue
        else:
            continue
        if reason is None and isinstance(extra.get(tid), str):
            reason = extra[tid]
        out.append((tid, reason))
    return out


#: The kinds a ``superseded`` row can retire (see the module docstring).
SUPERSEDABLE: tuple[str, ...] = ("tests_failing_first", "tests_frozen")

#: Keys every assumption item carries once normalised (``assumption_items``).
ASSUMPTION_KEYS: tuple[str, ...] = ("default_applied", "where", "why")
# The pack's item keys, mapped onto ASSUMPTION_KEYS (first present wins).
_ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "default_applied": ("default_applied", "default", "choice"),
    "where": ("where", "stage", "file"),
    "why": ("why", "ambiguity", "reason"),
    "decision": ("decision", "relies_on"),
}
#: `decision: none` / `decision: D1` written as a value (a session put the table's Decision column into another field)
_DECISION_PREFIX = re.compile(r"^\s*decision\s*:\s*", re.IGNORECASE)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _need(cond: bool, kind: str, key: str, what: str) -> None:
    if not cond:
        raise ValueError(f"{kind}.{key} must be {what}")


def _counts(kind: str, key: str, obj: Any, names: tuple[str, ...]) -> None:
    _need(isinstance(obj, dict), kind, key, "an object")
    for n in names:
        _need(n in obj and _int(obj[n]) and obj[n] >= 0, kind, f"{key}.{n}", "a non-negative integer")


def _assumptions(p: dict[str, Any]) -> None:
    kind = "assumptions"
    if "assumptions" in p:
        items = p["assumptions"]
        _need(isinstance(items, list), kind, "assumptions", "a list of {default_applied, where, why}")
        for i, item in enumerate(items):
            _need(isinstance(item, dict), kind, f"assumptions[{i}]", "an object")
            for key in ASSUMPTION_KEYS:
                _need(_str(item.get(key)), kind, f"assumptions[{i}].{key}", "a non-empty string")
        return
    if all(k in p for k in ("path", "count", "items")):
        _need(_str(p["path"]), kind, "path", "a non-empty string (the assumptions.md path)")
        _need(_int(p["count"]) and p["count"] >= 0, kind, "count", "a non-negative integer")
        _need(isinstance(p["items"], list), kind, "items", "a list")
        for i, item in enumerate(p["items"]):
            _need(isinstance(item, dict) or _str(item), kind, f"items[{i}]", "an object or a non-empty string")
        return
    raise ValueError("assumptions payload needs either assumptions: [{default_applied, where, why}] "
                     "or path, count and items")


def assumption_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The items of an ``assumptions`` payload in the orchestrator's shape (``default_applied``, ``where``,
    ``why`` plus any other keys), whichever shape it was recorded in. Items lacking any of the three after
    mapping are kept with what they have; the conformance gate reports them."""
    p = payload if isinstance(payload, dict) else {}
    if isinstance(p.get("assumptions"), list):
        return [a for a in p["assumptions"] if isinstance(a, dict)]
    out: list[dict[str, Any]] = []
    for item in p.get("items") or []:
        if isinstance(item, str) and item.strip():
            out.append({"default_applied": item.strip()})
            continue
        if not isinstance(item, dict):
            continue
        row = dict(item)
        for key, names in _ITEM_ALIASES.items():
            for n in names:
                if isinstance(item.get(n), str) and item[n].strip():
                    row[key] = item[n].strip()
                    break
        if not str(row.get("decision") or "").strip():
            # the Decision column written into another field, as `decision: none` or `decision: D1`
            for v in item.values():
                if isinstance(v, str) and _DECISION_PREFIX.match(v):
                    row["decision"] = _DECISION_PREFIX.sub("", v).strip()
                    break
        if isinstance(row.get("decision"), str):
            row["decision"] = _DECISION_PREFIX.sub("", row["decision"]).strip()
        out.append(row)
    return out


def validate(kind: str, payload: dict[str, Any]) -> None:
    """Check ``kind`` is known and ``payload`` has its required keys with sane types
    (``verify_stamp.acceptance`` has passed/failed ints, ``ratchet`` has new/known ints,
    ``tests_frozen.files`` maps str->64-hex sha256).

    Raises:
        ValueError: unknown kind, missing key, wrong type (message names the key).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}; expected one of {', '.join(KINDS)}")
    if not isinstance(payload, dict):
        raise ValueError(f"{kind} payload must be an object")
    missing = [k for k in KINDS[kind] if k not in payload]
    if missing:
        raise ValueError(f"{kind} payload is missing key(s): {', '.join(missing)}")
    p = payload
    if kind == "tests_failing_first":
        def entry_ok(t: Any) -> bool:
            if isinstance(t, dict):
                return any(_str(t.get(k)) for k in _TEST_ID_KEYS) and (t.get("reason") is None or t.get("reason") in FAILURE_REASONS)
            return _str(t)

        _need(isinstance(p["tests"], list) and p["tests"] and all(entry_ok(t) for t in p["tests"]), kind, "tests",
              "a non-empty list of pytest node ids (or {id, reason} objects, reason one of "
              + ", ".join(FAILURE_REASONS) + ")")
        if "reasons" in p:
            _need(isinstance(p["reasons"], dict) and all(_str(k) and v in FAILURE_REASONS for k, v in p["reasons"].items()),
                  kind, "reasons", "an object of test id -> one of " + ", ".join(FAILURE_REASONS))
        _need(_str(p["commit"]), kind, "commit", "a commit sha")
    elif kind == "tests_frozen":
        files = p["files"]
        _need(isinstance(files, dict) and files, kind, "files", "a non-empty object of path -> sha256")
        for path, digest in files.items():
            _need(_str(path) and isinstance(digest, str) and bool(_SHA256.match(digest)), kind, f"files[{path!r}]",
                  "a 64-hex lowercase sha256")
        _need(_str(p["commit"]), kind, "commit", "a commit sha")
    elif kind == "verify_stamp":
        _need(isinstance(p["frozen_ok"], bool), kind, "frozen_ok", "a boolean")
        _counts(kind, "acceptance", p["acceptance"], ("passed", "failed"))
        _counts(kind, "ratchet", p["ratchet"], ("new", "known"))
        _need(_str(p["db_backend"]), kind, "db_backend", "a non-empty string")
        _need(isinstance(p["tool_versions"], dict), kind, "tool_versions", "an object")
    elif kind == "acceptance":
        _counts(kind, "payload", p, ("passed", "failed"))
        _need(isinstance(p["rows"], list), kind, "rows", "a list")
    elif kind == "ratchet":
        _counts(kind, "payload", p, ("new", "known"))
    elif kind == "review_approval":
        for k in ("reviewer", "author", "url"):
            _need(_str(p[k]), kind, k, "a non-empty string")
    elif kind == "ci_run":
        _need(_str(p["url"]), kind, "url", "a non-empty string")
        _need(_str(p["conclusion"]), kind, "conclusion", 'a check conclusion string ("success", "failure", ...)')
    elif kind == "pr_url":
        _need(_str(p["url"]), kind, "url", "a non-empty string")
    elif kind == "merge_sha":
        _need(isinstance(p["sha"], str) and bool(_GIT_SHA.match(p["sha"])), kind, "sha", "a hex commit sha (7-64 chars)")
    elif kind == "ready_poll":
        _need(_str(p["url"]), kind, "url", "a non-empty string")
        _need(_int(p["status"]), kind, "status", "an integer HTTP status")
        _need(_int(p["attempts"]) and p["attempts"] >= 0, kind, "attempts", "a non-negative integer")
    elif kind == "smoke":
        _need(_str(p["command"]), kind, "command", "a non-empty string")
        _need(_int(p["exit"]), kind, "exit", "an integer exit code")
    elif kind == "guardrail_metric":
        _need(_str(p["metric"]), kind, "metric", "a non-empty string")
    elif kind == "metrics_snapshot":
        _need(isinstance(p["metrics"], dict), kind, "metrics", "an object")
    elif kind == "triage":
        _need(_str(p["summary"]), kind, "summary", "a non-empty string")
        _need(_str(p["severity"]), kind, "severity", "a non-empty string")
    elif kind == "assumptions":
        _assumptions(p)
    elif kind == "contract_updated":
        _need(isinstance(p["changed"], list) and all(_str(c) for c in p["changed"]), kind, "changed",
              "a list of field names")
        _need(isinstance(p["decisions_confirmed"], list)
              and all(_int(n) or _str(n) for n in p["decisions_confirmed"]), kind, "decisions_confirmed",
              "a list of decision numbers")
    elif kind == "superseded":
        _need(isinstance(p["ids"], list) and bool(p["ids"]) and all(_int(i) and i > 0 for i in p["ids"]), kind, "ids",
              "a non-empty list of evidence ids")
        _need(_str(p["reason"]), kind, "reason", "a non-empty string")


def is_binding(source: str, signed_by: str | None) -> bool:
    """True when ``source`` starts with ``ci:`` and ``signed_by`` is non-empty.
    (No real OIDC verification in this increment; docs/graph-decisions.md records the TODO.)"""
    return isinstance(source, str) and source.startswith("ci:") and bool((signed_by or "").strip())


def add(conn: psycopg.Connection, slug: str, kind: str, payload: dict[str, Any], *, source: str = "local",
        signed_by: str | None = None) -> dict[str, Any]:
    """Validate and store one evidence row for the node. Never upgrades local evidence: a row is
    binding only by its own source/signed_by.

    Returns:
        ``Evidence.to_dict()`` (with ``binding``).

    Raises:
        LookupError: unknown slug. ValueError: validation failure or an empty source. A ``ci:``
        source without ``signed_by`` is stored and reported ``binding: false`` (not an error).
    """
    from .nodes import node_id

    if not isinstance(source, str) or not source.strip():
        raise ValueError("evidence source must be a non-empty string ('local', 'ci:<run url>', 'github:<review id>')")
    validate(kind, payload)
    nid = node_id(conn, slug)
    signed = signed_by.strip() if isinstance(signed_by, str) and signed_by.strip() else None
    row = conn.execute(
        "INSERT INTO evidence (node_id, kind, source, signed_by, payload) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (nid, kind, source.strip(), signed, Jsonb(payload)),
    ).fetchone()
    return Evidence.from_row(row).to_dict()


def list_for(conn: psycopg.Connection, slug: str, kind: str | None = None, ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Evidence dicts of a node, oldest first, optionally filtered by kind or ids.

    Raises:
        LookupError: unknown slug.
    """
    from .nodes import node_id

    nid = node_id(conn, slug)
    rows = conn.execute(
        """SELECT * FROM evidence WHERE node_id = %s AND (%s::text IS NULL OR kind = %s)
           AND (%s::bigint[] IS NULL OR id = ANY(%s)) ORDER BY at, id""",
        (nid, kind, kind, ids, ids),
    ).fetchall()
    return [Evidence.from_row(r).to_dict() for r in rows]


def _order_key(e: dict[str, Any]) -> tuple[Any, int]:
    at = e.get("at")
    if isinstance(at, str):
        try:
            at = dt.datetime.fromisoformat(at)
        except ValueError:
            at = None
    if isinstance(at, dt.datetime) and at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (at or floor, int(e.get("id") or 0))


def superseded_ids(evidence: list[dict[str, Any]]) -> set[int]:
    """Ids of the ``SUPERSEDABLE`` rows in ``evidence`` that a ``superseded`` row in ``evidence`` names."""
    named: set[int] = set()
    for e in evidence:
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if e.get("kind") == "superseded" and isinstance(p.get("ids"), list):
            named.update(i for i in p["ids"] if _int(i))
    return {int(e["id"]) for e in evidence
            if e.get("kind") in SUPERSEDABLE and _int(e.get("id")) and int(e["id"]) in named}


def live(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``evidence`` without the rows a ``superseded`` row in it retires (:func:`superseded_ids`)."""
    gone = superseded_ids(evidence)
    return [e for e in evidence if not (_int(e.get("id")) and int(e["id"]) in gone)]


def resolve(evidence: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """The guard-resolution rule (see transitions module docstring): newest row of ``kind`` or
    newest ``verify_stamp`` carrying a ``kind`` key, skipping superseded rows (:func:`live`). Returns
    the evidence dict with ``payload`` replaced by the sub-object in the latter case
    (``"via": "verify_stamp"`` added), else None."""
    candidates: list[dict[str, Any]] = []
    for e in live(list(evidence)):
        if e.get("kind") == kind:
            candidates.append(e)
        elif e.get("kind") == "verify_stamp" and kind != "verify_stamp":
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            if isinstance(payload.get(kind), dict):
                candidates.append({**e, "payload": payload[kind], "via": "verify_stamp"})
    if not candidates:
        return None
    return max(candidates, key=_order_key)


def summary(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{kind: {"count", "latest_id", "latest_at", "binding"}}`` for the node view.
    ``binding`` is the newest row's binding flag."""
    out: dict[str, dict[str, Any]] = {}
    for e in sorted(evidence, key=_order_key):
        s = out.setdefault(e["kind"], {"count": 0, "latest_id": None, "latest_at": None, "binding": False})
        s["count"] += 1
        s["latest_id"] = e.get("id")
        s["latest_at"] = e.get("at")
        s["binding"] = bool(e.get("binding", is_binding(e.get("source") or "", e.get("signed_by"))))
    return out
