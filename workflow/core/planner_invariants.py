"""Planner-layer invariants — runtime preconditions checked while planning an
operation against the *live* issue state.

Planner rules raise `OperationError` rather than returning findings, so (like the
parser) each conceptual precondition is registered here as a data row (ADR-0004,
planner layer = hard stop). `tests/test_planner_invariants.py` exercises each one
and asserts the registry is fully covered.

Covers every framework operation: the state-machine ops (claim / release /
advance), the catalogued gate ops (await / approve / reject / record-action /
check / revoke), the recognized input ops (request-input / respond), and the
creating ops (spawn / collect).
"""

from __future__ import annotations

from workflow.core.invariants import Invariant, Severity, register_invariant

_ADVANCE = "workflow.core.planner._plan_advance"
_CLAIM = "workflow.core.planner._plan_claim"
_RELEASE = "workflow.core.planner._plan_release"
_AWAIT = "workflow.core.planner._plan_await_signal"
_APPROVE = "workflow.core.planner._plan_approve"
_RECORD = "workflow.core.planner._plan_record_action"
_CONFIRM = "workflow.core.planner._plan_confirm"
_REVIEW = "workflow.core.planner._plan_review"
_REQUEST_INPUT = "workflow.core.planner._plan_request_input"
_RESPOND = "workflow.core.planner._plan_respond"
_SPAWN = "workflow.core.planner._plan_spawn"
_COLLECT = "workflow.core.planner._plan_collect"


def _row(id: str, statement: str, symbol: str) -> Invariant:
    return Invariant(
        id=id,
        statement=statement,
        severity=Severity.ERROR,  # runtime precondition failures are hard stops
        layer="planner",
        principle="hitl-principles.md#5",
        enforcing_symbol=symbol,
    )


PLANNER_INVARIANTS: tuple[Invariant, ...] = (
    # advance-issue
    _row("PLAN_ADVANCE_DESTINATION_REQUIRED", "advance requires a `--to` destination.", _ADVANCE),
    _row(
        "PLAN_ADVANCE_TRANSITION_EXISTS",
        "advance's destination is reachable by a transition out of the current state.",
        _ADVANCE,
    ),
    _row(
        "PLAN_ADVANCE_OVER_CLAIM_ROLE",
        "Advancing over a CLAIM transition requires an acting role permitted by the "
        "destination working state and not conflicting with an existing claim.",
        _ADVANCE,
    ),
    _row(
        "PLAN_ADVANCE_OVER_CLAIM_TYPE",
        "Advancing over a CLAIM transition requires the issue's type to be accepted by "
        "the destination working state.",
        _ADVANCE,
    ),
    # claim-issue
    _row("PLAN_CLAIM_ROLE_REQUIRED", "claim requires an agent role.", _CLAIM),
    _row("PLAN_CLAIM_REQUIRES_STATE", "claim requires the issue to have a current state.", _CLAIM),
    _row(
        "PLAN_CLAIM_NOT_ON_CLOSING",
        "A closing state cannot be claimed (closing states are sinks).",
        _CLAIM,
    ),
    _row(
        "PLAN_CLAIM_NOT_ALREADY_CLAIMED",
        "An issue already claimed by another role cannot be re-claimed.",
        _CLAIM,
    ),
    _row(
        "PLAN_CLAIM_TRANSITION_EXISTS",
        "claim resolves to exactly one CLAIM transition out of the current state "
        "(a destination must exist and be unambiguous).",
        _CLAIM,
    ),
    _row(
        "PLAN_CLAIM_ROLE_PERMITTED",
        "The claiming role is permitted by the destination working state's `roles`.",
        _CLAIM,
    ),
    _row(
        "PLAN_CLAIM_TYPE_ACCEPTED",
        "The issue's type is accepted by the destination working state's `issue_types`.",
        _CLAIM,
    ),
    # release-issue
    _row("PLAN_RELEASE_REQUIRES_CLAIM", "release requires an active agent claim.", _RELEASE),
    _row(
        "PLAN_RELEASE_REQUIRES_ORIGIN",
        "release requires a `last-state` origin marker (set at claim time).",
        _RELEASE,
    ),
    _row(
        "PLAN_RELEASE_NO_MARKER_DRIFT",
        "release's origin → current must match a real CLAIM transition (no marker drift).",
        _RELEASE,
    ),
)


# Catalogued (gate), recognized (input), and creating (spawn/collect) operations.
CATALOGUED_PLANNER_INVARIANTS: tuple[Invariant, ...] = (
    # gate operations
    _row(
        "PLAN_GATE_REQUIRED",
        "A catalogued-gate operation (await / approve / reject / record-action / check / "
        "revoke) requires a `--gate`.",
        _APPROVE,
    ),
    _row(
        "PLAN_GATE_IN_CATALOG",
        "The named gate is loaded and present in the human-gate catalog.",
        _APPROVE,
    ),
    _row(
        "PLAN_AWAIT_GATE_AT_SOURCE",
        "await-signal fires only from the gate's source state.",
        _AWAIT,
    ),
    _row(
        "PLAN_SINGLE_GATE_IN_FLIGHT",
        "Starting a gate requires no other HITL gate or audit already in flight.",
        _AWAIT,
    ),
    _row(
        "PLAN_GATE_AT_AWAITING_GATE",
        "approve / reject target the gate the issue is currently awaiting.",
        _APPROVE,
    ),
    _row(
        "PLAN_GATE_DESTINATION_RESOLVES",
        "A verdict-style gate requires a `--destination` among its options; a binary gate's "
        "destination must match its single option.",
        _APPROVE,
    ),
    _row(
        "PLAN_RECORD_ACTION_REVERSIBLE",
        "record-action (audit) requires a reversible destination.",
        _RECORD,
    ),
    _row(
        "PLAN_AUDIT_SINGLE_PENDING",
        "record-action requires no other audit already pending on the issue.",
        _RECORD,
    ),
    _row(
        "PLAN_AUDIT_AT_PENDING_GATE",
        "check / revoke target the gate whose audit is currently pending.",
        _CONFIRM,
    ),
    # human-claim singletons (review / audit / advise)
    _row(
        "PLAN_HUMAN_CLAIM_REQUIRES_MARKER",
        "Taking a human-claim singleton requires its marker active (review→awaiting-gate, "
        "audit→audit-pending, advise→awaiting-input).",
        _REVIEW,
    ),
    _row(
        "PLAN_HUMAN_CLAIM_NOT_HELD",
        "A human-claim singleton can't be taken when it is already held.",
        _REVIEW,
    ),
    _row(
        "PLAN_SINGLE_HUMAN_CLAIM_SINGLETON",
        "At most one human-claim singleton (reviewing / auditing / advising) is active at a time.",
        _REVIEW,
    ),
    # recognized input
    _row(
        "PLAN_REQUEST_INPUT_NO_GATE_IN_FLIGHT",
        "request-input requires no input already awaited and no catalogued gate in flight.",
        _REQUEST_INPUT,
    ),
    _row(
        "PLAN_REQUEST_INPUT_BODY_REQUIRED",
        "request-input requires a `--body`.",
        _REQUEST_INPUT,
    ),
    _row(
        "PLAN_REQUEST_INPUT_TOPIC_DECLARED",
        "request-input's `--topic` is one the current working state declares in `human_inputs`.",
        _REQUEST_INPUT,
    ),
    _row(
        "PLAN_RESPOND_REQUIRES_AWAITING_INPUT",
        "respond requires an awaiting-input marker active.",
        _RESPOND,
    ),
    _row(
        "PLAN_RESPOND_BODY_REQUIRED",
        "respond requires a `--body`.",
        _RESPOND,
    ),
    # creating operations
    _row(
        "PLAN_SPAWN_PARENT_HAS_STATE",
        "spawn requires the parent issue to have a current state.",
        _SPAWN,
    ),
    _row(
        "PLAN_SPAWN_PR_REQUIRES_HEAD",
        "A pr-typed spawn requires a `--head` source branch.",
        _SPAWN,
    ),
    _row(
        "PLAN_COLLECT_CONTRIBUTOR_HAS_STATE",
        "collect requires the contributor to have a current state.",
        _COLLECT,
    ),
    _row(
        "PLAN_COLLECT_ELIGIBILITY",
        "A collected contributor is on one of the collector's `from_states`, of an accepted "
        "type, and not already collected (unless `--force`).",
        _COLLECT,
    ),
)


def register_planner_invariants() -> None:
    """Register every planner-layer invariant (call once)."""
    for inv in (*PLANNER_INVARIANTS, *CATALOGUED_PLANNER_INVARIANTS):
        register_invariant(inv)


register_planner_invariants()
