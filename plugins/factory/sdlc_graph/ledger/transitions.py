"""State machines and guard evaluation.

Machines are data (``state_machines.json``): per kind, ``states`` (``states[0]`` is the initial
state; a state with no outgoing transition is terminal) and ``transitions`` with ``guards``.

Guard mini-language (every guard string is one of):

``evidence:<kind>``
    an evidence row of that kind is available (see "Evidence resolution").
``evidence:<kind>.<seg>[.<seg>...]``
    segments are applied left to right. A reserved segment is a boolean check on the resolved
    row and does not move the cursor: ``binding`` (source starts with ``ci:`` and signed_by is
    non-empty), ``green`` (payload ``conclusion == "success"``), ``non_author`` (payload
    ``reviewer`` and ``author`` both non-empty and different, compared case-insensitively
    without a leading ``@``). Any other segment descends into the payload (cursor = cursor[seg]).
    The guard holds when every reserved check holds and, if the last segment was a payload
    step, the final value is truthy.
``evidence:<kind>.<path>==<value>``
    as above, then the final value equals ``<value>`` parsed as JSON (``0``, ``true``, ``"x"``),
    falling back to the raw string when it is not valid JSON.
``actor:<class>[|<class>...]``
    the acting actor's class is one of ``human`` (``@handle``), ``ci`` (``ci:<system>``),
    ``agent`` (``agent:<alias>``).
``decisions_resolved``      the node has no decision with ``resolved_at`` null, and every row of the contract.md
                            Decisions table stored on the node (``node.attrs["decisions"]``, written by
                            ``intake.contract_draft`` / ``contract_update`` from ``ledger.contract_md``) has a Value;
                            at tier ``medium`` or ``high`` every row is also confirmed by a human handle (Decided by
                            names an ``@handle``, not ``recommended, unconfirmed`` and not ``agent:*``). At tier
                            ``trivial``, ``low`` or no tier a recommended value passes, as the pack's ready check
                            lets it.
``acceptance_present``      ``node.acceptance`` is a non-empty list of rows that each have a non-empty
                            ``id`` and ``text``.
``allowed_paths_present``   ``node.capabilities["paths"]`` is a non-empty list of non-empty strings,
                            none of them a bare wildcard (``**``, ``*``, ``.``).
``defaults_present``        ``node.attrs["defaults"]`` is a non-empty list of non-empty strings (or one
                            non-empty string): the contract's ``## Defaults when ambiguous`` block, the
                            policy an unattended session applies instead of asking (design section 14).
                            ``sdlc_graph.orchestrator.contract_blocks`` parses it from contract.md.
``capabilities_present``    ``node.capabilities`` declares every key of the ``## Capabilities`` block
                            (``paths``, ``commands``, ``egress``, ``data``), each a list; an empty list is
                            an explicit "none". A contract drafted before the block existed has no
                            ``commands`` key and is refused, so nothing reaches execution without its
                            permissions written down.
``capabilities_approved``   a node whose capabilities widen the sandbox (egress, secrets, shell
                            ``commands`` or a ``test_command``) needs a human actor; plain path-only
                            capabilities pass.
``ticket_text_reviewed``    acceptance rows taken from a ticket (``source: "ticket"``) need a human
                            actor to mark the contract ready.
``acceptance_covered``      the newest ``acceptance`` evidence (or verify_stamp sub-object) shows
                            ``passed >= len(node.acceptance)`` (at least 1): zero tests run is not
                            a pass. (``failed == 0`` stays its own evidence guard.)
``failing_first_reasons``   when the newest ``tests_failing_first`` gives a reason per test (``{id, reason}``
                            entries or a ``reasons`` map), none is a wrong reason (``evidence.WRONG_FAILURE_REASONS``:
                            import_error, attribute_error, name_error, syntax_error, collection_error): such a test
                            fails because it is broken, not because the code is missing. ``missing`` then holds the
                            guard name followed by ``"test <id> fails for the wrong reason: <reason>"`` for each
                            such test. A payload without reasons passes.
``frozen_tests_match``      the newest ``tests_failing_first`` lists a test for every
                            ``node.acceptance[].test`` (the id itself, a test inside it, ``id::...``,
                            or a parametrize case, ``id[...]``) and the newest ``tests_frozen`` is on
                            the same commit (a missing row is reported by its own evidence guard).
``ci_matches_approval``      the NEWEST binding ``ci_run`` (not the newest green one) is green and its ``sha`` equals the
                            newest ``review_approval``'s ``commit``, so ``approved`` proves CI passed on the commit the
                            reviewer approved and that a later red or newer run has not replaced it. A shorter sha
                            matches a longer one when it is a prefix of it (min 7 chars). A missing run, a red run, a
                            run without ``sha``, an approval without ``commit`` or a mismatch fails;
                            ``missing`` then holds the guard name followed by the reason.
``merge_head_matches_ci``   when the newest ``merge_sha`` carries ``head`` (the PR head at merge), it equals the sha of the
                            newest binding ``ci_run``. Without ``head`` the guard passes: a squash merge creates a new
                            sha and some flows cannot supply the head, so ``sha`` itself is never compared.
``edge_out:<rel>``          the node has an outgoing ledger edge with that rel.
``edge_in:<rel>``           the node has an incoming ledger edge with that rel.
``all_out:<rel>:<kind>=<s1>|<s2>``
                            every outgoing ``<rel>`` edge to a node of ``<kind>`` lands in one of those states. It holds
                            trivially when there are no such edges, so pair it with ``edge_out:<rel>`` when at least one
                            child is required.
``all_in:<rel>:<kind>=<states>``  the same check over incoming edges.
``decision:<key>``          the node has a resolved ``decision`` row with ``meta->>'key' = <key>`` whose ``decided_by``
                            is a human ``@handle``.
``owner_present``           ``attrs.owner`` is a human ``@handle``.
``actor_is_owner``          the acting handle equals ``attrs.owner``, compared case-insensitively.
``record_file_present``     ``path`` and ``commit_sha`` are set and ``attrs.record_type`` is ``pdr``, ``adr`` or ``ddr``.
``story_criteria_covered``  the union of the child contracts' ``acceptance[].story_ac`` includes every
                            ``node.acceptance[].id``.
``criteria_signed_off``     the newest ``acceptance_signoff`` evidence's ``criteria`` includes every
                            ``node.acceptance[].id``.
``risk_expired``            the ``decision:risk_acceptance`` row's ``due`` is before today.
``problem_present``         ``attrs.problem``, ``attrs.who`` and ``attrs.evidence`` are all non-empty.

Evidence resolution for ``evidence:<kind>``: candidates are the evidence rows of that kind
PLUS every ``verify_stamp`` row whose payload has a top-level ``<kind>`` key (its sub-object
becomes the payload; the row's source/signed_by are kept). The newest candidate (by ``at``,
then ``id``) is used. So ``evidence:acceptance.failed==0`` and ``evidence:ratchet.new==0`` are
satisfied by a single verify_stamp, or by standalone acceptance/ratchet rows if newer.

Human-only targets: a transition INTO ``approved``, ``merged``, ``released`` or ``closed`` by an
``agent:*`` actor fails with ``"actor:not_agent"`` whatever the guards say.

Refused transitions are not exceptions; ``transition`` appends them (target, actor, missing, at) to
``node.attrs["refused_transitions"]`` (newest last, capped at ``REFUSAL_LOG_MAX``) so
``metrics.snapshot`` can count them without a separate table. ``GuardError`` exists for callers
that prefer an exception (``transition(..., strict=True)``).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.shapes import NODE_KINDS, STATELESS_KINDS, GuardResult

#: States an agent actor may never move a node into. Each is guarded by a non-agent ``actor:`` guard in every machine
#: (tests/graph/test_registry_conformance.py); the second line lists the states the lifecycle machines added.
PROTECTED_STATES: frozenset[str] = frozenset({
    "approved", "merged", "released", "closed",
    "accepted", "rejected", "split", "superseded", "risk_accepted", "false_positive", "duplicate", "shortlisted",
    "promoted", "parked"})
RESERVED_SEGMENTS: frozenset[str] = frozenset({"binding", "green", "non_author"})
#: State used by node kinds that have no machine (``shapes.STATELESS_KINDS``).
STATELESS = "active"
ACTOR_CLASSES: tuple[str, ...] = ("human", "agent", "ci")
REFUSAL_LOG_MAX = 50

_HUMAN = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
_AGENT = re.compile(r"^agent:[A-Za-z0-9][A-Za-z0-9_.\-/\[\]]*$")
_CI = re.compile(r"^ci:\S+$")
_SIMPLE_GUARDS = ("decisions_resolved", "acceptance_present", "allowed_paths_present", "defaults_present",
                  "capabilities_present", "capabilities_approved", "ticket_text_reviewed", "acceptance_covered",
                  "frozen_tests_match", "failing_first_reasons", "ci_matches_approval",
                  "merge_head_matches_ci", "owner_present", "actor_is_owner", "record_file_present",
                  "story_criteria_covered", "criteria_signed_off", "risk_expired", "problem_present")
#: Keys the contract's ``## Capabilities`` block declares (``capabilities_present``).
CAPABILITY_BLOCK_KEYS: tuple[str, ...] = ("paths", "commands", "egress", "data")
#: ``attrs.record_type`` values of a decision record (product, architecture, data design).
RECORD_TYPES: tuple[str, ...] = ("pdr", "adr", "ddr")
_BARE_WILDCARDS = frozenset({"", ".", "*", "**", "**/*", "*/**", "/", "/**"})


def acceptance_rows_ok(acc: Any) -> bool:
    """A non-empty list of rows with non-empty string ``id`` and ``text``."""
    return (isinstance(acc, list) and len(acc) > 0
            and all(isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k].strip() for k in ("id", "text"))
                    for r in acc))


def paths_ok(paths: Any) -> bool:
    """A non-empty list of non-empty strings, none a bare wildcard."""
    return (isinstance(paths, list) and len(paths) > 0
            and all(isinstance(p, str) and p.strip() and p.strip().removeprefix("./") not in _BARE_WILDCARDS
                    for p in paths))


#: The four standard defaults every contract carries (key phrase of each, compared case-insensitively).
STANDARD_DEFAULT_PHRASES: tuple[str, ...] = ("smallest change", "nearest existing pattern", "decisions", "reversible option")
#: A "default" that asks, stops or overrides a decision is not one (review finding: ``Ask the human whenever something
#: is unclear`` and ``Ignore the decisions above`` both passed the guard).
_BAD_DEFAULT = re.compile(
    r"^\W*(?:ask|stop|escalate|wait|pause|halt|confirm|check\s+with|consult|defer|prompt|query)\b"
    r"|\b(?:ignore|override|disregard|overrule|bypass|contradict|supersede)\b[^.]*\bdecisions?\b"
    r"|\bdecisions?\b[^.]*\b(?:ignored|overridden|disregarded|overruled|bypassed|do\s+not\s+apply|don't\s+apply)\b"
    r"|\b(?:ask|escalate\s+to)\s+(?:the\s+)?(?:human|user|product|owner|reviewer|team|me)\b", re.I)


def defaults_lines(attrs: Any) -> list[str]:
    d = (attrs if isinstance(attrs, dict) else {}).get("defaults")
    if isinstance(d, str):
        return [d] if d.strip() else []
    return [x for x in d if isinstance(x, str)] if isinstance(d, list) else []


def defaults_problems(attrs: Any) -> list[str]:
    """Why ``attrs["defaults"]`` is not an acceptable defaults block (empty list: it is)."""
    d = (attrs if isinstance(attrs, dict) else {}).get("defaults")
    lines = defaults_lines(attrs)
    if isinstance(d, list) and (not d or not all(isinstance(x, str) and x.strip() for x in d)):
        return ["defaults must be a non-empty list of non-empty lines"]
    if not lines or not any(x.strip() for x in lines):
        return ["no defaults block"]
    text = " ".join(lines).lower()
    problems = [f"missing the standard default about '{p}'" for p in STANDARD_DEFAULT_PHRASES if p not in text]
    problems += [f"line {x.strip()[:80]!r} asks, stops or overrides a decision; a default settles ambiguity on its own"
                 for x in lines if _BAD_DEFAULT.search(x)]
    return problems


def defaults_ok(attrs: Any) -> bool:
    """``attrs["defaults"]`` is a non-empty string or list of non-empty lines that carries the standard four defaults
    and no line that asks, stops or overrides a decision (:func:`defaults_problems`)."""
    return not defaults_problems(attrs)


#: Tiers at which every contract decision needs a human confirmation for ``ready``.
CONFIRM_TIERS: frozenset[str] = frozenset({"medium", "high"})
#: Decisions a human must confirm at ANY tier: money, security, data, customer-visible behaviour. The same words as
#: ``SENSITIVE`` in the pack's ``scripts/acceptance.py`` (its ``--ready`` check), so the ledger's ready guard never lets
#: through an unconfirmed decision the contract's own ready check refuses (tests/graph pins the two to one pattern).
SENSITIVE = re.compile(
    r"\b(money|payments?|prices?|pricing|billing|invoice\w*|refunds?|charge[sd]?|secur\w*|auth\w*|permissions?|credentials?|secrets?|"
    r"tokens?|encrypt\w*|pii|personal|privacy|gdpr|data|retention|delet\w*|migrat\w*|schema|customers?|user[- ]facing|"
    r"user[- ]visible|visible|public|notif\w*|emails?)\b", re.I)


def _decision_topic(d: dict[str, Any]) -> str:
    options = d.get("options")
    opts = " ".join(map(str, options)) if isinstance(options, list) else str(options or "")
    return " ".join([str(d.get("question") or ""), opts, str(d.get("value") or "")])


def contract_decision_problems(node: dict[str, Any]) -> list[str]:
    """Why the contract.md decisions stored on the node (``attrs["decisions"]``) do not let it be ready (empty: they
    do): a row with no Value, or a row whose Decided by is not a human handle at tier medium or high, or at any tier
    when the decision touches money, security, data or what customers see (``SENSITIVE``)."""
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    rows = attrs.get("decisions") if isinstance(attrs.get("decisions"), list) else []
    tier = str(node.get("tier") or "").lower()
    out: list[str] = []
    for i, d in enumerate(rows, 1):
        if not isinstance(d, dict):
            out.append(f"decision row {i} is not an object")
            continue
        n = d.get("n", i)
        if not (isinstance(d.get("value"), str) and d["value"].strip()):
            out.append(f"decision {n} has no Value")
        elif not decision_confirmed(d) and (tier in CONFIRM_TIERS or SENSITIVE.search(_decision_topic(d))):
            why = f"at tier {tier}" if tier in CONFIRM_TIERS else "and it touches money, security, data or what customers see"
            out.append(f"decision {n} is not confirmed by a human (Decided by: {str(d.get('decided_by') or '')[:60]!r}) {why}")
    return out


def decision_confirmed(d: dict[str, Any]) -> bool:
    """A contract decision row counts as confirmed only when it says so and its handle is a human ``@handle``."""
    handle = d.get("handle")
    return bool(d.get("confirmed")) and isinstance(handle, str) and bool(_HUMAN.fullmatch(handle))


def wrong_failure_reasons(payload: Any) -> list[str]:
    """``"test <id> fails for the wrong reason: <reason>"`` for each test of a ``tests_failing_first`` payload whose
    reason is in ``evidence.WRONG_FAILURE_REASONS``."""
    from .evidence import WRONG_FAILURE_REASONS, failing_first_tests

    return [f"test {tid} fails for the wrong reason: {reason}" for tid, reason in failing_first_tests(payload)
            if reason in WRONG_FAILURE_REASONS]


def capabilities_missing(caps: Any) -> list[str]:
    """The ``## Capabilities`` keys the node does not declare as a list (empty list = declared none)."""
    caps = caps if isinstance(caps, dict) else {}
    return [k for k in CAPABILITY_BLOCK_KEYS if not isinstance(caps.get(k), list)]


def capabilities_elevated(caps: Any) -> bool:
    """True when the capabilities widen the sandbox beyond write paths (shell commands included: each
    compiles to a Bash allow rule, so a human approves them)."""
    caps = caps if isinstance(caps, dict) else {}
    return bool(caps.get("egress") or caps.get("secrets") or caps.get("test_command") or caps.get("commands"))


def _covers(listed: list[Any], wanted: str) -> bool:
    """A listed node id covers ``wanted`` when it is ``wanted``, a test inside it (``wanted::...``) or one of its
    pytest parametrize cases (``wanted[...]``); ``wanted_other`` does not."""
    return any(isinstance(t, str) and (t == wanted or t.startswith((wanted + "::", wanted + "["))) for t in listed)


class GuardError(Exception):
    """A refused transition, raised only by ``transition(..., strict=True)``. ``result`` is the
    GuardResult and ``missing`` its failed guard strings."""

    def __init__(self, result: GuardResult):
        self.result = result
        self.missing = list(result["missing"])
        super().__init__(f"{result['slug']}: {result['from_state']} -> {result['to_state']} refused; missing: "
                         + ", ".join(self.missing))


# --------------------------------------------------------------------------- machines


def load_machines(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate ``state_machines.json`` (default: ``ledger.STATE_MACHINES_PATH``).

    Raises:
        ValueError: a transition names an unknown state, or a guard string does not parse.
    """
    if path is None:
        return _default_machines()
    return _load(Path(path))


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be an object of kind -> machine")
    for kind, m in data.items():
        if kind not in NODE_KINDS:
            raise ValueError(f"{path}: unknown node kind {kind!r}")
        if kind in STATELESS_KINDS:
            raise ValueError(f"{path}: {kind!r} is a stateless kind (shapes.STATELESS_KINDS) and has no machine")
        states = m.get("states")
        if not isinstance(states, list) or not states or len(set(states)) != len(states):
            raise ValueError(f"{path}: {kind}.states must be a non-empty list of distinct states")
        for t in m.get("transitions", []):
            for end in ("from", "to"):
                if t.get(end) not in states:
                    raise ValueError(f"{path}: {kind} transition names unknown state {t.get(end)!r}")
            for g in t.get("guards", []):
                parse_guard(g)
    for kind, m in data.items():
        for t in m.get("transitions", []):
            for g in t.get("guards", []):
                parsed = parse_guard(g)
                if parsed["type"] in ("all_out", "all_in") and parsed["kind"] in data:
                    unknown = [x for x in parsed["states"] if x not in data[parsed["kind"]]["states"]]
                    if unknown:
                        raise ValueError(f"{path}: {kind} guard {g!r} names unknown {parsed['kind']} state(s) {unknown}")
    return data


@lru_cache(maxsize=1)
def _default_machines() -> dict[str, Any]:
    from . import STATE_MACHINES_PATH

    return _load(STATE_MACHINES_PATH)


def machine(kind: str) -> dict[str, Any] | None:
    """The machine for ``kind`` or None for kinds without one."""
    return _default_machines().get(kind)


def initial_state(kind: str) -> str:
    """``states[0]`` of the kind's machine, or ``STATELESS``. Raises ValueError for unknown kinds."""
    if kind not in NODE_KINDS:
        raise ValueError(f"unknown node kind {kind!r}; expected one of {', '.join(NODE_KINDS)}")
    m = machine(kind)
    return m["states"][0] if m else STATELESS


def terminal_states(kind: str) -> frozenset[str]:
    """States of ``kind`` with no outgoing transition (contract: {merged})."""
    m = machine(kind)
    if m is None:
        return frozenset()
    sources = {t["from"] for t in m["transitions"]}
    return frozenset(s for s in m["states"] if s not in sources)


def transitions_from(kind: str, state: str) -> list[dict[str, Any]]:
    """Outgoing transitions of ``state`` in machine order (empty for stateless kinds)."""
    m = machine(kind)
    return [t for t in (m["transitions"] if m else []) if t["from"] == state]


# --------------------------------------------------------------------------- actors and guards


def actor_class(actor: str) -> str:
    """``"human"`` for ``@handle``, ``"agent"`` for ``agent:<alias>``, ``"ci"`` for ``ci:<system>``.

    Raises:
        ValueError: any other form (empty, missing prefix, whitespace).
    """
    if isinstance(actor, str):
        if _HUMAN.fullmatch(actor):
            return "human"
        if _AGENT.fullmatch(actor):
            return "agent"
        if _CI.fullmatch(actor):
            return "ci"
    raise ValueError(f"bad actor {actor!r}: expected @handle (human), agent:<model alias> or ci:<system>")


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_guard(guard: str) -> dict[str, Any]:
    """Parse one guard string into ``{"type": ..., "guard": ...}`` plus: for evidence
    ``{"kind", "segments": [...], "expected": <value>|<absent>}``; for actor ``{"classes": [...]}``; for edge_out and
    edge_in ``{"rel"}``; for all_out and all_in ``{"rel", "kind", "states": [...]}``; for decision ``{"key"}``; a
    simple guard has the type of its own name. Raises ValueError on anything else."""
    if not isinstance(guard, str) or not guard:
        raise ValueError(f"bad guard {guard!r}")
    if guard in _SIMPLE_GUARDS:
        return {"type": guard, "guard": guard}
    prefix, sep, rest = guard.partition(":")
    if not sep or not rest:
        raise ValueError(f"bad guard {guard!r}")
    if prefix == "actor":
        classes = rest.split("|")
        if not all(c in ACTOR_CLASSES for c in classes):
            raise ValueError(f"bad actor guard {guard!r}: classes are {', '.join(ACTOR_CLASSES)}")
        return {"type": "actor", "guard": guard, "classes": classes}
    if prefix in ("edge_out", "edge_in"):
        from sdlc_graph.shapes import NODE_RELS

        if rest not in NODE_RELS:
            raise ValueError(f"bad edge guard {guard!r}: unknown rel {rest!r}")
        return {"type": prefix, "guard": guard, "rel": rest}
    if prefix in ("all_out", "all_in"):
        from sdlc_graph.shapes import NODE_RELS

        rel, _, tail = rest.partition(":")
        kind, eq, states = tail.partition("=")
        listed = states.split("|") if eq else []
        if rel not in NODE_RELS or kind not in NODE_KINDS or not listed or not all(listed):
            raise ValueError(f"bad {prefix} guard {guard!r}: expected {prefix}:<rel>:<kind>=<state>|<state>")
        return {"type": prefix, "guard": guard, "rel": rel, "kind": kind, "states": listed}
    if prefix == "decision":
        if not re.fullmatch(r"[a-z][a-z0-9_]*", rest):
            raise ValueError(f"bad decision guard {guard!r}: the key is lower_snake_case")
        return {"type": "decision", "guard": guard, "key": rest}
    if prefix == "evidence":
        from .evidence import KINDS

        path, eq, value = rest.partition("==")
        kind, *segments = path.split(".")
        if kind not in KINDS:
            raise ValueError(f"bad evidence guard {guard!r}: unknown evidence kind {kind!r}")
        if any(not s for s in segments) or (eq and not segments) or (eq and not value):
            raise ValueError(f"bad evidence guard {guard!r}")
        out: dict[str, Any] = {"type": "evidence", "guard": guard, "kind": kind, "segments": segments}
        if eq:
            out["expected"] = _parse_value(value)
        return out
    raise ValueError(f"bad guard {guard!r}: unknown guard type {prefix!r}")


def _sha_eq(a: Any, b: Any) -> bool:
    a, b = str(a or "").lower(), str(b or "").lower()
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


def newest_binding_ci_run(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest live ``ci_run`` row that is binding, whatever its conclusion."""
    from .evidence import _order_key, is_binding, live

    rows = [e for e in live(list(evidence)) if e.get("kind") == "ci_run"
            and is_binding(e.get("source") or "", e.get("signed_by"))]
    return max(rows, key=_order_key) if rows else None


def ci_matches_approval_problem(evidence: list[dict[str, Any]]) -> str | None:
    """Why the newest binding CI run does not vouch for the approved commit, or None when it does."""
    from .evidence import resolve

    ci, appr = newest_binding_ci_run(evidence), resolve(evidence, "review_approval")
    cp = ci.get("payload") if ci and isinstance(ci.get("payload"), dict) else {}
    ap = appr.get("payload") if appr and isinstance(appr.get("payload"), dict) else {}
    if ci is None:
        return None  # evidence:ci_run.green.binding reports it
    if appr is None:
        return None  # evidence:review_approval.non_author reports it
    commit, sha = ap.get("commit"), cp.get("sha")
    if not commit:
        return "the approval names no commit (review_approval.commit), so CI cannot be matched to it"
    if cp.get("conclusion") != "success":
        return f"the newest CI run is {cp.get('conclusion') or 'not green'}" + (f" on {str(sha)[:7]}" if sha else "")
    if not sha:
        return "the newest CI run names no sha (ci_run.sha)"
    if not _sha_eq(sha, commit):
        return f"CI is green for {str(sha)[:7]}, the approval is for {str(commit)[:7]}"
    return None


def merge_head_problem(evidence: list[dict[str, Any]]) -> str | None:
    from .evidence import resolve

    m = resolve(evidence, "merge_sha")
    head = (m.get("payload") or {}).get("head") if m and isinstance(m.get("payload"), dict) else None
    if not head:
        return None
    ci = newest_binding_ci_run(evidence)
    sha = (ci.get("payload") or {}).get("sha") if ci and isinstance(ci.get("payload"), dict) else None
    if not sha:
        return f"the merge names head {str(head)[:7]} but no binding CI run verified a sha"
    if not _sha_eq(sha, head):
        return f"the merge is for head {str(head)[:7]}, CI verified {str(sha)[:7]}"
    return None


def _strip_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _check_evidence(g: dict[str, Any], row: dict[str, Any] | None) -> bool:
    from .evidence import is_binding

    if row is None:
        return False
    cursor: Any = row.get("payload")
    payload_step = False
    for seg in g["segments"]:
        if seg == "binding":
            if not is_binding(row.get("source") or "", row.get("signed_by")):
                return False
        elif seg == "green":
            if not (isinstance(row.get("payload"), dict) and row["payload"].get("conclusion") == "success"):
                return False
        elif seg == "non_author":
            p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            reviewer, author = _strip_handle(p.get("reviewer")), _strip_handle(p.get("author"))
            if not reviewer or not author or reviewer == author:
                return False
        else:
            if not isinstance(cursor, dict) or seg not in cursor:
                return False
            cursor = cursor[seg]
            payload_step = True
    if "expected" in g:
        expected = g["expected"]
        if isinstance(expected, bool):
            return cursor is expected
        if isinstance(cursor, bool):  # True must not satisfy ==1, nor False ==0
            return False
        return cursor == expected
    if payload_step:
        return bool(cursor)
    return True


def _is_handle(value: Any) -> bool:
    return isinstance(value, str) and bool(_HUMAN.fullmatch(value))


def _blank(value: Any) -> bool:
    """True for None, an empty or blank string and an empty list or object."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or (isinstance(value, (list, dict)) and not value)


def keyed_decision(decisions: Iterable[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """The newest resolved ``decision`` row with ``meta.key == key`` whose ``decided_by`` is a human handle."""
    rows = [d for d in decisions if isinstance(d.get("meta"), dict) and d["meta"].get("key") == key
            and d.get("resolved_at") is not None and _is_handle(d.get("decided_by"))]
    return max(rows, key=lambda d: d.get("id") or 0) if rows else None


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def _story_ac_ids(children: Iterable[dict[str, Any]]) -> set[str]:
    """Every criterion id the child contracts cover through ``acceptance[].story_ac`` (a string or a list of them)."""
    out: set[str] = set()
    for c in children:
        if c.get("kind") != "contract" or c.get("rel") != "decomposes_into":
            continue
        for row in c.get("acceptance") or []:
            ref = row.get("story_ac") if isinstance(row, dict) else None
            out.update([ref] if isinstance(ref, str) else [r for r in ref or [] if isinstance(r, str)])
    return out


def _criterion_ids(node: dict[str, Any]) -> list[str]:
    return [r["id"] for r in node.get("acceptance") or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]


def evaluate_detail(node: dict[str, Any], to_state: str, evidence: Iterable[dict[str, Any]], actor: str, *,
                    open_decisions: int = 0, edges_out: Iterable[str] = (), edges_in: Iterable[str] = (),
                    children: Iterable[dict[str, Any]] = (), parents: Iterable[dict[str, Any]] = (),
                    decisions: Iterable[dict[str, Any]] = (), today: dt.date | None = None) -> dict[str, Any]:
    """Pure guard evaluation (no database).

    Args:
        node: a node row/dict (needs ``kind``, ``state``, ``acceptance``, ``capabilities``).
        to_state: requested target state.
        evidence: candidate evidence dicts (``Evidence.to_dict()`` shape: id, kind, source,
            signed_by, payload, at).
        actor: acting actor string.
        open_decisions: count of unresolved decisions on the node.
        edges_out / edges_in: rels of the node's outgoing / incoming ledger edges.
        children / parents: ``[{rel, kind, state, slug, acceptance, attrs}]`` over the node's outgoing / incoming
            edges (``all_out``, ``all_in``, ``story_criteria_covered``).
        decisions: the node's resolved decision rows (``id``, ``decided_by``, ``resolved_at``, ``due``, ``meta``)
            for ``decision:<key>`` and ``risk_expired``.
        today: the date ``risk_expired`` compares with (default: today).

    Returns:
        ``{"ok": bool, "missing": [guard strings, in machine order, plus special entries],
        "evidence_ids": [ids of the rows that satisfied evidence guards]}``.

    Raises:
        ValueError: bad actor string.
    """
    from .evidence import resolve

    cls = actor_class(actor)
    kind, state = node["kind"], node["state"]
    missing: list[str] = []
    cited: list[int] = []
    edge = next((t for t in transitions_from(kind, state) if t["to"] == to_state), None)
    if edge is None:
        missing.append(f"transition:{state}->{to_state}")
    if cls == "agent" and to_state in PROTECTED_STATES:
        missing.append("actor:not_agent")
    if edge is None:
        return {"ok": False, "missing": missing, "evidence_ids": []}
    rows = list(evidence)
    outs, ins = set(edges_out), set(edges_in)
    kids, folks, decs = list(children), list(parents), list(decisions)
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    for guard in edge["guards"]:
        g = parse_guard(guard)
        t = g["type"]
        if t == "evidence":
            row = resolve(rows, g["kind"])
            held = _check_evidence(g, row)
            if held and row is not None and row.get("id") is not None and int(row["id"]) not in cited:
                cited.append(int(row["id"]))
        elif t == "actor":
            held = cls in g["classes"]
        elif t == "decisions_resolved":
            held = int(open_decisions or 0) == 0 and not contract_decision_problems(node)
        elif t == "acceptance_present":
            held = acceptance_rows_ok(node.get("acceptance"))
        elif t == "allowed_paths_present":
            caps = node.get("capabilities") if isinstance(node.get("capabilities"), dict) else {}
            held = paths_ok(caps.get("paths"))
        elif t == "defaults_present":
            held = defaults_ok(node.get("attrs"))
        elif t == "capabilities_present":
            held = not capabilities_missing(node.get("capabilities"))
        elif t == "capabilities_approved":
            held = cls == "human" or not capabilities_elevated(node.get("capabilities"))
        elif t == "ticket_text_reviewed":
            rows_a = node.get("acceptance") if isinstance(node.get("acceptance"), list) else []
            held = cls == "human" or not any(isinstance(r, dict) and r.get("source") == "ticket" for r in rows_a)
        elif t == "acceptance_covered":
            row = resolve(rows, "acceptance")
            p = row.get("payload") if row and isinstance(row.get("payload"), dict) else {}
            acc = node.get("acceptance") if isinstance(node.get("acceptance"), list) else []
            passed, failed = p.get("passed"), p.get("failed")
            # a missing row or a failed count is reported by the evidence:acceptance.failed==0 guard
            held = row is None or (isinstance(passed, int) and not isinstance(passed, bool)
                                   and passed >= max(len(acc), 1))
        elif t == "frozen_tests_match":
            first, frozen = resolve(rows, "tests_failing_first"), resolve(rows, "tests_frozen")
            fp = first.get("payload") if first and isinstance(first.get("payload"), dict) else {}
            zp = frozen.get("payload") if frozen and isinstance(frozen.get("payload"), dict) else {}
            from .evidence import failing_first_tests

            listed = [tid for tid, _ in failing_first_tests(fp)]
            wanted = [r["test"] for r in (node.get("acceptance") or []) if isinstance(r, dict)
                      and isinstance(r.get("test"), str) and r["test"].strip()]
            # a missing row is reported by its own evidence: guard
            held = first is None or frozen is None or (
                bool(fp.get("commit")) and fp.get("commit") == zp.get("commit")
                and all(_covers(listed, w) for w in wanted))
        elif t == "failing_first_reasons":
            first = resolve(rows, "tests_failing_first")
            wrong = wrong_failure_reasons(first.get("payload") if first else None)
            if wrong:
                missing.append(guard)
                missing.extend(wrong)
            continue
        elif t in ("ci_matches_approval", "merge_head_matches_ci"):
            why = (ci_matches_approval_problem if t == "ci_matches_approval" else merge_head_problem)(rows)
            if why:
                missing.append(guard)
                missing.append(why)
            continue
        elif t == "edge_out":
            held = g["rel"] in outs
        elif t == "edge_in":
            held = g["rel"] in ins
        elif t in ("all_out", "all_in"):
            pool = kids if t == "all_out" else folks
            held = all(c.get("state") in g["states"] for c in pool if c.get("rel") == g["rel"] and c.get("kind") == g["kind"])
        elif t == "decision":
            held = keyed_decision(decs, g["key"]) is not None
        elif t == "owner_present":
            held = _is_handle(attrs.get("owner"))
        elif t == "actor_is_owner":
            held = _is_handle(attrs.get("owner")) and _strip_handle(actor) == _strip_handle(attrs.get("owner"))
        elif t == "record_file_present":
            held = (not _blank(node.get("path")) and not _blank(node.get("commit_sha"))
                    and attrs.get("record_type") in RECORD_TYPES)
        elif t == "story_criteria_covered":
            held = set(_criterion_ids(node)) <= _story_ac_ids(kids)
        elif t == "criteria_signed_off":
            row = resolve(rows, "acceptance_signoff")
            p = row.get("payload") if row and isinstance(row.get("payload"), dict) else {}
            held = row is not None and set(_criterion_ids(node)) <= set(p.get("criteria") or [])
        elif t == "risk_expired":
            row = keyed_decision(decs, "risk_acceptance")
            due = _as_date(row.get("due")) if row else None
            held = due is not None and due < (today or dt.date.today())
        else:  # problem_present
            held = not any(_blank(attrs.get(k)) for k in ("problem", "who", "evidence"))
        if not held:
            missing.append(guard)
    return {"ok": not missing, "missing": missing, "evidence_ids": sorted(cited) if not missing else []}


def evaluate(node: dict[str, Any], to_state: str, evidence: Iterable[dict[str, Any]], actor: str, *,
             open_decisions: int = 0, edges_out: Iterable[str] = (), edges_in: Iterable[str] = (),
             children: Iterable[dict[str, Any]] = (), parents: Iterable[dict[str, Any]] = (),
             decisions: Iterable[dict[str, Any]] = (), today: dt.date | None = None) -> tuple[bool, list[str]]:
    """``(ok, missing)`` from :func:`evaluate_detail`. ``missing`` entries are the failed guard
    strings exactly as written in the machine, plus ``"transition:<from>-><to>"`` when the machine
    has no such transition and ``"actor:not_agent"`` for an agent into a protected state."""
    d = evaluate_detail(node, to_state, evidence, actor, open_decisions=open_decisions, edges_out=edges_out,
                        edges_in=edges_in, children=children, parents=parents, decisions=decisions, today=today)
    return d["ok"], d["missing"]


# --------------------------------------------------------------------------- database


def context(conn: psycopg.Connection, node_row: dict[str, Any], evidence_ids: list[int] | None = None) -> dict[str, Any]:
    """Everything :func:`evaluate_detail` needs for a node row: ALL of the node's evidence (newest
    wins, so a caller cannot hide a newer failing row), open decision count, edge rels, the nodes at the ends of the
    edges (``children``, ``parents``) and the resolved decision rows (``decisions``).
    ``evidence_ids`` is only validated here; :func:`transition` then checks that the cited rows are
    the ones that satisfied the guards.

    Raises:
        ValueError: an evidence id that does not exist or belongs to another node.
    """
    from .models import Evidence

    nid = node_row["id"]
    if evidence_ids is not None:
        ids = sorted({int(i) for i in evidence_ids})
        given = conn.execute("SELECT id, node_id FROM evidence WHERE id = ANY(%s)", (ids,)).fetchall()
        found = {r["id"]: r for r in given}
        unknown = [i for i in ids if i not in found]
        foreign = [i for i in ids if i in found and found[i]["node_id"] != nid]
        if unknown:
            raise ValueError(f"unknown evidence id(s): {unknown}")
        if foreign:
            raise ValueError(f"evidence id(s) {foreign} belong to another node, not {node_row['slug']!r}")
    rows = conn.execute("SELECT * FROM evidence WHERE node_id = %s ORDER BY at, id", (nid,)).fetchall()
    open_decisions = conn.execute(
        "SELECT count(*) AS n FROM decision WHERE node_id = %s AND resolved_at IS NULL", (nid,)
    ).fetchone()["n"]
    outs = [r["rel"] for r in conn.execute("SELECT DISTINCT rel FROM edge WHERE src = %s", (nid,)).fetchall()]
    ins = [r["rel"] for r in conn.execute("SELECT DISTINCT rel FROM edge WHERE dst = %s", (nid,)).fetchall()]
    children = conn.execute(
        "SELECT e.rel, n.kind, n.state, n.slug, n.acceptance, n.attrs FROM edge e JOIN node n ON n.id = e.dst "
        "WHERE e.src = %s ORDER BY n.slug", (nid,)).fetchall()
    parents = conn.execute(
        "SELECT e.rel, n.kind, n.state, n.slug, n.acceptance, n.attrs FROM edge e JOIN node n ON n.id = e.src "
        "WHERE e.dst = %s ORDER BY n.slug", (nid,)).fetchall()
    resolved = conn.execute(
        "SELECT id, choice, decided_by, due, meta, resolved_at FROM decision WHERE node_id = %s AND resolved_at IS NOT NULL "
        "ORDER BY id", (nid,)).fetchall()
    return {"evidence": [Evidence.from_row(r).to_dict() for r in rows], "open_decisions": int(open_decisions),
            "edges_out": outs, "edges_in": ins, "children": [dict(r) for r in children],
            "parents": [dict(r) for r in parents], "decisions": [dict(r) for r in resolved]}


def transition(conn: psycopg.Connection, slug: str, to_state: str, actor: str, *,
               evidence_ids: list[int] | None = None, reason: str | None = None, strict: bool = False) -> GuardResult:
    """Evaluate and, when ok, record a transition (update ``node.state``/``updated_at`` and insert
    a ``transition`` row citing the satisfying evidence ids) in one transaction, locking the node
    row (``SELECT ... FOR UPDATE``). A refusal is appended to ``node.attrs["refused_transitions"]``.

    Args:
        evidence_ids: an extra check, not a filter: every guard resolves against ALL of the node's
            evidence (newest wins); when given, each id must belong to the node and be one of the
            rows that satisfied the guards, else the transition is refused with
            ``evidence_ids:not_current``.
        strict: raise :class:`GuardError` instead of returning a refused result (the refusal is
            still logged).

    Returns:
        GuardResult. A failed evaluation is a normal return (ok False), not an exception.

    Raises:
        LookupError: unknown slug. ValueError: bad actor, or evidence id of another node.
        GuardError: refused and ``strict``.
    """
    actor_class(actor)
    with conn.transaction():
        row = conn.execute("SELECT * FROM node WHERE slug = %s FOR UPDATE", (slug,)).fetchone()
        if row is None:
            raise LookupError(f"unknown node slug: {slug!r}")
        ctx = context(conn, row, evidence_ids)
        node = dict(row)
        detail = evaluate_detail(node, to_state, ctx["evidence"], actor, open_decisions=ctx["open_decisions"],
                                 edges_out=ctx["edges_out"], edges_in=ctx["edges_in"], children=ctx["children"],
                                 parents=ctx["parents"], decisions=ctx["decisions"])
        if detail["ok"] and evidence_ids is not None and not {int(i) for i in evidence_ids} <= set(detail["evidence_ids"]):
            detail = {"ok": False, "missing": ["evidence_ids:not_current"], "evidence_ids": []}
        result: GuardResult = {"ok": detail["ok"], "slug": slug, "from_state": row["state"], "to_state": to_state,
                               "missing": detail["missing"], "transition_id": None, "evidence_ids": []}
        if detail["ok"]:
            # Cited, never required (DESIGN 17.2): the newest review page shown for this transition since the last.
            pack = conn.execute(
                """SELECT e.id FROM evidence e WHERE e.node_id = %s AND e.kind = 'review_pack'
                   AND e.payload->'shown_for'->>'transition' = %s
                   AND e.at > COALESCE((SELECT max(t.at) FROM transition t WHERE t.node_id = %s),
                                       '-infinity'::timestamptz)
                   ORDER BY e.at DESC, e.id DESC LIMIT 1""", (row["id"], to_state, row["id"])).fetchone()
            if pack is not None and int(pack["id"]) not in detail["evidence_ids"]:
                detail["evidence_ids"] = [*detail["evidence_ids"], int(pack["id"])]
            conn.execute("UPDATE node SET state = %s, updated_at = now() WHERE id = %s", (to_state, row["id"]))
            tr = conn.execute(
                """INSERT INTO transition (node_id, from_state, to_state, actor, evidence_ids, reason)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (row["id"], row["state"], to_state, actor, detail["evidence_ids"], reason),
            ).fetchone()
            result["transition_id"] = int(tr["id"])
            result["evidence_ids"] = detail["evidence_ids"]
        else:
            attrs = dict(row["attrs"] or {})
            log = list(attrs.get("refused_transitions") or [])
            log.append({"from": row["state"], "to": to_state, "actor": actor, "missing": detail["missing"],
                        "at": dt.datetime.now(dt.timezone.utc).isoformat()})
            attrs["refused_transitions"] = log[-REFUSAL_LOG_MAX:]
            attrs["refused_count"] = int(attrs.get("refused_count") or 0) + 1
            conn.execute("UPDATE node SET attrs = %s WHERE id = %s", (Jsonb(attrs), row["id"]))
    if strict and not result["ok"]:
        raise GuardError(result)
    return result
