"""The orchestrator: Gas Town's mechanisms on the ledger (docs/DESIGN.md section 12).

A deterministic Python loop walks a feature's contracts through the contract state machine. Each
step is one fresh executor session (``claude -p`` with the pack loaded, through the step-executor
protocol in :mod:`.dispatch`); after the session ends the loop checks what it left behind
(:mod:`.gates`) and only then calls the guarded ``transition``. The only model calls the loop
makes itself are the bounded judge (:mod:`.judge`, behind a flag) and the conformance reviewer.

Modules:
    steps     state -> step table (command, stage, expected evidence, target state)
    store     run columns, the per-feature lock row, escalation decisions with severity
    gitops    worktree helpers and allowed-path matching (the drift gate's matcher)
    dispatch  worktree + current.json + settings + prompt, starts the executor, records the run
    gates     after a session: evidence, drift, frozen tests, ratchet, conformance, transition
    watchdog  patrol: dead process, stall, third failure -> P1 decision
    judge     bounded ``claude -p --output-format json`` calls (judge, conformance reviewer)
    autonomy  ask before, never during (design section 14): assumptions, the compiled allow-list, denials
    contract_blocks  ``## Defaults when ambiguous`` and ``## Capabilities`` parsed from contract.md onto the node
    prime     what the SessionStart hook prints
    feed      ``sdlc-graph feed`` (activity) and ``sdlc-graph escalations``
    loop      ``orchestrate``: lock, topological order, tick
"""

from __future__ import annotations

from sdlc_graph.ledger import evidence as _evidence

#: Evidence kind the conformance gate writes: ``{"score": 0..1, "findings": [...]}``.
CONFORMANCE_KIND = "conformance"

#: Evidence kind for the defaults a session applied instead of asking (design section 14). The ledger
#: knows it (``sdlc_graph.ledger.evidence.KINDS``) and accepts both payload shapes:
#: ``{"assumptions": [{"default_applied", "where", "why"}, ...]}`` (what the orchestrator records; extra keys
#: such as ``decision`` are kept) and the pack's ``{"path", "count", "items"}``.
ASSUMPTIONS_KIND = "assumptions"
ASSUMPTION_KEYS: tuple[str, ...] = _evidence.ASSUMPTION_KEYS

# Register the conformance kind with the ledger's validator (KINDS is the single source the validator reads).
_evidence.KINDS.setdefault(CONFORMANCE_KIND, ("score", "findings"))


def check_assumptions_payload(payload: dict) -> None:
    """Validate an ``assumptions`` payload (either shape). Raises ValueError naming the item and key."""
    _evidence.validate(ASSUMPTIONS_KIND, payload)


#: Actor the daemon uses for the transitions it makes after its gates pass.
ACTOR = "agent:orchestrator"
