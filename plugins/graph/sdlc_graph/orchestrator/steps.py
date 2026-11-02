"""The molecule: contract state -> the one step a session runs, and what the gates expect after it.

Every row maps a contract state (from ``state_machines.json``) to one slash command of the
sdlc pack. The routing-policy stage (and so the model) comes from ``workflow.next_step``;
this table adds what the orchestrator needs that the workflow does not know: the evidence kinds the
session must have recorded, whether the frozen tests must be unchanged afterwards, and whether the
conformance gate runs.

=============  ============================  ==========================  =============  =========
state          command                       expected evidence           target state   extra
=============  ============================  ==========================  =============  =========
ready          /sdlc:tests           tests_failing_first,        tests_frozen
                                             tests_frozen
tests_frozen   /sdlc:implement       (a change in allowed paths) implementing   conformance
implementing   /sdlc:verify          verify_stamp                verified
verified       /sdlc:ship            pr_url                      in_review      push, PR
=============  ============================  ==========================  =============  =========

``drafted`` waits for a human (decisions, capabilities approval); ``in_review`` and later are
human or CI. In the default mode a contract is done at ``in_review``; with ``refinery`` at
``merged``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CMD = "/sdlc:"


@dataclass(frozen=True)
class Step:
    name: str
    command: str
    from_state: str
    to_state: str
    expects: tuple[str, ...] = ()
    frozen_check: bool = True       # frozen tests must hash the same after the session
    needs_change: bool = False      # the session must leave a change (commit or edit) in allowed paths
    conformance: bool = False       # run the conformance reviewer before transitioning
    egress: tuple[str, ...] = field(default=())  # extra network domains this step needs (push, PR)


STEPS: dict[str, Step] = {
    "ready": Step("tests", CMD + "tests", "ready", "tests_frozen",
                  expects=("tests_failing_first", "tests_frozen"), frozen_check=False, needs_change=True),
    "tests_frozen": Step("implement", CMD + "implement", "tests_frozen", "implementing",
                         needs_change=True, conformance=True),
    "implementing": Step("verify", CMD + "verify", "implementing", "verified", expects=("verify_stamp",)),
    "verified": Step("ship", CMD + "ship", "verified", "in_review", expects=("pr_url",),
                     egress=("github.com", "api.github.com")),
}

STEP_BY_NAME: dict[str, Step] = {s.name: s for s in STEPS.values()}

#: States a human (or CI) must move; the loop reports them and never dispatches.
HUMAN_STATES: frozenset[str] = frozenset({"drafted", "in_review", "approved"})

#: Contract states that count as done for the goal loop.
DONE_DEFAULT: frozenset[str] = frozenset({"in_review", "approved", "merged"})
DONE_REFINERY: frozenset[str] = frozenset({"merged"})

#: Run outcomes that count as a failure of the step (the third one escalates).
FAILURES: frozenset[str] = frozenset({
    "crashed", "stalled", "no_evidence", "no_change", "drift", "frozen_changed", "ratchet",
    "guard_refused", "dirty_worktree", "executor_error", "unverified", "unauthorized_transition",
})
#: A session that stopped to ask a human something (``.claude/work/<slug>/questions.md``): an escalation, never a failure.
NEEDS_HUMAN = "needs_human"
#: Conformance below threshold re-enters implement; it has its own budget of re-entries.
CONFORMANCE_LOW = "conformance_low"
#: The session was denied a tool call (dontAsk, no prompts): an escalation naming the missing rule, never a retry.
PERMISSION_DENIED = "permission_denied"
#: Outcome of a contract the loop will not dispatch: its defaults or capabilities block is missing.
CONTRACT_INCOMPLETE = "contract_incomplete"


def step_for(state: str) -> Step | None:
    """The step a contract in ``state`` runs, or None (human state, or done)."""
    return STEPS.get(state)


def done_states(refinery: bool = False) -> frozenset[str]:
    return DONE_REFINERY if refinery else DONE_DEFAULT
