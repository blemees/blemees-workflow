"""Planner-layer invariant coverage (ADR-0004).

Each registered planner invariant gets a minimal (request, state, state_machine)
that violates exactly that runtime precondition; planning it must raise
`OperationError`. A meta-test asserts the trigger table covers every registered
planner invariant.

Scope: the state-machine operations (claim / release / advance).
"""

from __future__ import annotations

import pytest

import workflow.core.planner_invariants  # noqa: F401  (registers planner rows)
from tests.test_planner import _build_workflow
from workflow.backends.base import IssueState
from workflow.core.invariants import invariants_for_layer
from workflow.core.model.state_machine import (
    ReversibilityClass,
    State,
    StateClass,
    StateMachine,
    Transition,
    TransitionType,
)
from workflow.core.planner import Operation, OperationRequest, plan_operation
from workflow.errors import OperationError


def _typed_wf() -> StateMachine:
    """A queue → working machine whose working state restricts type to `bug`."""
    wf = StateMachine(name="ty")
    wf.states = {
        "queue": State(
            name="queue",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
        ),
        "doing": State(
            name="doing", state_class=StateClass.WORKING, roles=("dev",), issue_types=("bug",)
        ),
    }
    wf.transitions = [
        Transition(
            source="queue",
            destination="doing",
            label="dev claims",
            transition_type=TransitionType.CLAIM,
        )
    ]
    return wf


def _req(op: Operation, **kw) -> OperationRequest:
    return OperationRequest(operation=op, issue_id=kw.pop("issue_id", "1"), **kw)


def _state(**kw) -> IssueState:
    kw.setdefault("issue_id", "1")
    kw.setdefault("agent_claim", None)
    return IssueState(**kw)


# id -> (request, state, state_machine) that trips exactly that precondition.
def _cases():
    wf = _build_workflow()
    ty = _typed_wf()
    return {
        # advance
        "PLAN_ADVANCE_DESTINATION_REQUIRED": (
            _req(Operation.ADVANCE_ISSUE, destination=None, actor="product-manager"),
            _state(state="raw", agent_claim="product-manager"),
            wf,
        ),
        "PLAN_ADVANCE_TRANSITION_EXISTS": (
            _req(Operation.ADVANCE_ISSUE, destination="nope"),
            _state(state="raw"),
            wf,
        ),
        "PLAN_ADVANCE_OVER_CLAIM_ROLE": (
            _req(Operation.ADVANCE_ISSUE, destination="refining", actor=None),
            _state(state="raw"),
            wf,
        ),
        "PLAN_ADVANCE_OVER_CLAIM_TYPE": (
            _req(Operation.ADVANCE_ISSUE, destination="doing", actor="dev"),
            _state(state="queue", issue_type="feature"),
            ty,
        ),
        # claim
        "PLAN_CLAIM_ROLE_REQUIRED": (
            _req(Operation.CLAIM_ISSUE),
            _state(state="raw"),
            wf,
        ),
        "PLAN_CLAIM_REQUIRES_STATE": (
            _req(Operation.CLAIM_ISSUE, role="product-manager"),
            _state(state=None),
            wf,
        ),
        "PLAN_CLAIM_NOT_ON_CLOSING": (
            _req(Operation.CLAIM_ISSUE, role="anyone"),
            _state(state="promoted"),
            wf,
        ),
        "PLAN_CLAIM_NOT_ALREADY_CLAIMED": (
            _req(Operation.CLAIM_ISSUE, role="developer"),
            _state(state="raw", agent_claim="someone-else"),
            wf,
        ),
        "PLAN_CLAIM_TRANSITION_EXISTS": (
            _req(Operation.CLAIM_ISSUE, role="product-manager"),
            _state(state="ready_for_dev"),
            wf,
        ),
        "PLAN_CLAIM_ROLE_PERMITTED": (
            _req(Operation.CLAIM_ISSUE, role="intruder"),
            _state(state="raw"),
            wf,
        ),
        "PLAN_CLAIM_TYPE_ACCEPTED": (
            _req(Operation.CLAIM_ISSUE, role="dev"),
            _state(state="queue", issue_type="feature"),
            ty,
        ),
        # release
        "PLAN_RELEASE_REQUIRES_CLAIM": (
            _req(Operation.RELEASE_ISSUE),
            _state(state="refining", agent_claim=None),
            wf,
        ),
        "PLAN_RELEASE_REQUIRES_ORIGIN": (
            _req(Operation.RELEASE_ISSUE),
            _state(state="refining", agent_claim="product-manager", last_state=None),
            wf,
        ),
        "PLAN_RELEASE_NO_MARKER_DRIFT": (
            _req(Operation.RELEASE_ISSUE),
            _state(state="implementing", agent_claim="product-owner", last_state="raw"),
            wf,
        ),
    }


CASES = _cases()


@pytest.mark.parametrize("invariant_id", sorted(CASES))
def test_planner_invariant_rejects_violation(invariant_id: str) -> None:
    request, state, state_machine = CASES[invariant_id]
    with pytest.raises(OperationError):
        plan_operation(request, state, state_machine)


def test_every_registered_planner_invariant_has_a_trigger() -> None:
    registered = {inv.id for inv in invariants_for_layer("planner")}
    assert set(CASES) == registered, (
        "planner invariant registry and trigger table drifted: "
        f"unregistered={sorted(set(CASES) - registered)} "
        f"untriggered={sorted(registered - set(CASES))}"
    )
