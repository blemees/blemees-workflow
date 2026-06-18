"""Planner-layer invariants — runtime preconditions checked while planning an
operation against the *live* issue state.

Planner rules raise `OperationError` rather than returning findings, so (like the
parser) each conceptual precondition is registered here as a data row (ADR-0004,
planner layer = hard stop). `tests/test_planner_invariants.py` exercises each one
and asserts the registry is fully covered.

Scope: the state-machine operations — claim / release / advance. The catalogued
(gate), recognized (request-input), and creating (spawn / collect) operations
register their rows in a follow-up.
"""

from __future__ import annotations

from workflow.core.invariants import Invariant, Severity, register_invariant

_ADVANCE = "workflow.core.planner._plan_advance"
_CLAIM = "workflow.core.planner._plan_claim"
_RELEASE = "workflow.core.planner._plan_release"


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


def register_planner_invariants() -> None:
    """Register every planner-layer invariant (call once)."""
    for inv in PLANNER_INVARIANTS:
        register_invariant(inv)


register_planner_invariants()
