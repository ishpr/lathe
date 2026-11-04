"""Shared data shapes passed between codegraph -> impact -> ledger -> mcp.

These are ``TypedDict`` declarations (plain dicts at runtime, JSON-serialisable as-is) plus the
few helpers every layer needs to agree on: the symbol identity key and its string form.
Scaffold-owned and frozen: if a shape must change, raise it in ``needs_from_others`` rather than
editing this file from an owner's lane. docs/graph-architecture.md documents each shape with examples.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SymKind = Literal["module", "class", "function", "method", "route", "model", "env_var", "package",
                  "frontend_call", "test", "message", "command", "view", "route_fe", "submodel"]
SymRel = Literal["imports", "calls", "defines", "exposes_route", "includes_router", "reads_env",
                 "uses_model", "depends_on_package", "fetches", "tests", "handles", "calls_api", "emits", "renders", "routes_to"]

SYM_KINDS: tuple[str, ...] = ("module", "class", "function", "method", "route", "model", "env_var",
                              "package", "frontend_call", "test", "message", "command", "view", "route_fe", "submodel")
SYM_RELS: tuple[str, ...] = ("imports", "calls", "defines", "exposes_route", "includes_router",
                             "reads_env", "uses_model", "depends_on_package", "fetches", "tests", "handles",
                             "calls_api", "emits", "renders", "routes_to")

NODE_KINDS: tuple[str, ...] = ("epic", "feature", "story", "contract", "change", "release", "incident",
                               "decision", "rule", "repo", "finding", "idea")
NODE_RELS: tuple[str, ...] = ("decomposes_into", "depends_on", "verified_by", "shipped_in", "supersedes",
                              "caused_by", "governed_by", "promoted_to")
#: Node kinds with no state machine: their state is ``transitions.STATELESS``. Every other kind in ``NODE_KINDS``
#: has a machine in ``state_machines.json`` (tests/graph/test_registry_conformance.py checks both directions).
STATELESS_KINDS: tuple[str, ...] = ("change", "rule", "repo")
#: Evidence kinds the ledger validates; ``ledger.evidence.KINDS`` must have exactly these keys.
EVIDENCE_KINDS: tuple[str, ...] = (
    "tests_failing_first", "tests_frozen", "verify_stamp", "acceptance", "ratchet", "review_approval", "ci_run",
    "pr_url", "merge_sha", "ready_poll", "smoke", "guardrail_metric", "metrics_snapshot", "triage", "diagnosis",
    "superseded", "contract_updated", "graph_provisioned", "assumptions", "conformance",
    "acceptance_signoff", "design_review", "finding_triage", "regression_test", "fingerprint_absent", "finding_seen",
    "symbols_resolved")


# --------------------------------------------------------------------------- code graph


class SymKey(TypedDict):
    """Identity of a symbol across snapshots (sym ids differ per snapshot)."""

    kind: str
    qualname: str
    path: str


class Sym(TypedDict, total=False):
    """One code-graph symbol as extractors emit it and queries return it.

    Required: kind, name, qualname, path. ``id`` and ``snapshot_id`` are present only on rows read
    back from the database. ``attrs`` keys per kind are listed in docs/graph-architecture.md; any guess
    carries ``attrs["confidence"] = "low"``.
    """

    id: int
    snapshot_id: int
    kind: str
    name: str
    qualname: str
    path: str
    line_start: int | None
    line_end: int | None
    attrs: dict[str, Any]


class Edge(TypedDict, total=False):
    """A code-graph edge. Extractors emit ``src``/``dst`` as :class:`SymKey` dicts; rows read from
    the database also carry ``src_id``/``dst_id``."""

    src: SymKey
    dst: SymKey
    rel: str
    attrs: dict[str, Any]
    src_id: int
    dst_id: int


class ExtractResult(TypedDict):
    """What every ``extract_*.extract()`` returns and what ``snapshot.reconstruct()`` yields."""

    symbols: list[Sym]
    edges: list[Edge]


class OwnerRule(TypedDict):
    """One CODEOWNERS line. ``ord`` is its 0-based order; the last matching rule wins."""

    path_glob: str
    handles: list[str]
    ord: int


class AffectedSet(TypedDict, total=False):
    """Result of ``queries.impact`` (static reverse reachability) and ``impact.affected.affected``
    (the same plus mirror-path tests and reasons). All lists are sorted and de-duplicated.

    symbols: affected symbols as Sym dicts plus a top-level ``depth`` key (0 for the touched
        symbols themselves, n for symbols n reverse hops away).
    routes: route qualnames, e.g. ``"GET /items"``.
    tests: pytest node ids or bare test file paths, e.g. ``"tests/test_a.py::test_list"``.
    owners: ``{path: [handles]}`` for every path in ``symbols``.
    reasons: ``{test_id_or_route: "why"}`` (impact.affected only), e.g. ``"imports app.routes.a (depth 1)"``
        or ``"mirror path of app/routes/a.py"``.
    snapshot_id: the snapshot the answer was computed on.
    unresolved: ``[{"entry", "reason"}]`` for touched paths or symbols that do not resolve in the
        snapshot (``absent_in_snapshot`` or ``ambiguous``); impact.affected only.
    intent: the intent graph's side of the answer (open findings, governing records, in-flight stories on the
        touched symbols); filled from lifecycle increment 3 on, absent before.
    """

    symbols: list[dict[str, Any]]
    routes: list[str]
    tests: list[str]
    owners: dict[str, list[str]]
    reasons: dict[str, str]
    snapshot_id: int
    unresolved: list[dict[str, str]]
    intent: dict[str, Any]


# --------------------------------------------------------------------------- ledger


class GuardResult(TypedDict):
    """``contract_transition`` / ``ledger.transitions.transition`` result.

    ok: the transition was recorded. missing: the guard strings (exactly as written in
    state_machines.json) that failed, plus the special entries ``"transition:<from>-><to>"``
    (no such edge in the machine) and ``"actor:not_agent"`` (agent actor into a human-only
    state). ``transition_id`` and ``evidence_ids`` are set only when ok.
    """

    ok: bool
    slug: str
    from_state: str
    to_state: str
    missing: list[str]
    transition_id: int | None
    evidence_ids: list[int]


# --------------------------------------------------------------------------- helpers


def sym_key(sym: Sym | SymKey | dict[str, Any]) -> tuple[str, str, str]:
    """Hashable identity ``(kind, qualname, path)`` of a Sym or SymKey dict."""
    return (sym["kind"], sym["qualname"], sym["path"])


def key_dict(sym: Sym | SymKey | dict[str, Any]) -> SymKey:
    """The :class:`SymKey` dict of a Sym (drops every other field)."""
    return {"kind": sym["kind"], "qualname": sym["qualname"], "path": sym["path"]}


def key_str(sym: Sym | SymKey | dict[str, Any] | tuple[str, str, str]) -> str:
    """Canonical string form ``"kind:qualname@path"`` used in ``node.touched_symbols``,
    ``graph_deps(symbol=...)`` and CLI arguments. Example: ``"function:app.routes.a.list_items@app/routes/a.py"``.
    """
    kind, qualname, path = sym if isinstance(sym, tuple) else sym_key(sym)
    return f"{kind}:{qualname}@{path}"


def parse_key(text: str) -> SymKey:
    """Inverse of :func:`key_str`: kind is before the first ``:``, path after the last ``@``.

    Raises:
        ValueError: ``text`` is not of the form ``kind:qualname@path`` or kind is unknown.
    """
    kind, sep, rest = text.partition(":")
    qualname, at, path = rest.rpartition("@")
    if not sep or not at or kind not in SYM_KINDS or not qualname or not path:
        raise ValueError(f"not a symbol key (kind:qualname@path): {text!r}")
    return {"kind": kind, "qualname": qualname, "path": path}
