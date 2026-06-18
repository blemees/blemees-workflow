"""Planner-layer invariant coverage (ADR-0004).

Each registered planner invariant gets a minimal (request, state, state_machine)
that violates exactly that runtime precondition; planning it must raise
`OperationError`. A meta-test asserts the trigger table covers every registered
planner invariant across all operations.
"""

from __future__ import annotations

import pytest

import workflow.core.planner_invariants  # noqa: F401  (registers planner rows)
from tests.test_planner import _build_catalog, _build_workflow
from workflow.backends.base import IssueState
from workflow.core.invariants import invariants_for_layer
from workflow.core.model.state_machine import (
    ReversibilityClass,
    Spawn,
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
    cat = _build_catalog()
    spawn = Spawn(process="inner-loop", issue_type="hotfix", initial_state="ready", advance_on=())
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
        # gate operations (catalog required)
        "PLAN_GATE_REQUIRED": (
            _req(Operation.APPROVE_BLOCKED, gate=None),
            _state(state="refining"),
            wf,
            cat,
        ),
        "PLAN_GATE_IN_CATALOG": (
            _req(Operation.APPROVE_BLOCKED, gate="ghost"),
            _state(state="refining"),
            wf,
            cat,
        ),
        "PLAN_AWAIT_GATE_AT_SOURCE": (
            _req(Operation.AWAIT_SIGNAL, gate="ready_for_dev"),
            _state(state="raw"),
            wf,
            cat,
        ),
        "PLAN_SINGLE_GATE_IN_FLIGHT": (
            _req(Operation.AWAIT_SIGNAL, gate="ready_for_dev"),
            _state(state="refining", awaiting_gate="experiment-verdict"),
            wf,
            cat,
        ),
        "PLAN_GATE_AT_AWAITING_GATE": (
            _req(Operation.APPROVE_BLOCKED, gate="ready_for_dev"),
            _state(state="refining", awaiting_gate=None),
            wf,
            cat,
        ),
        "PLAN_GATE_DESTINATION_RESOLVES": (
            _req(Operation.APPROVE_BLOCKED, gate="experiment-verdict", destination=None),
            _state(state="implementing", awaiting_gate="experiment-verdict"),
            wf,
            cat,
        ),
        "PLAN_RECORD_ACTION_REVERSIBLE": (
            _req(Operation.RECORD_ACTION, gate="experiment-verdict", destination="promoted"),
            _state(state="implementing"),
            wf,
            cat,
        ),
        "PLAN_AUDIT_SINGLE_PENDING": (
            _req(Operation.RECORD_ACTION, gate="ready_for_dev", destination="ready_for_dev"),
            _state(state="refining", audit_pending="experiment-verdict"),
            wf,
            cat,
        ),
        "PLAN_AUDIT_AT_PENDING_GATE": (
            _req(Operation.APPROVE_AUDIT, gate="ready_for_dev"),
            _state(state="refining", audit_pending=None),
            wf,
            cat,
        ),
        # human-claim singletons
        "PLAN_HUMAN_CLAIM_REQUIRES_MARKER": (
            _req(Operation.REVIEW_BLOCKED),
            _state(state="refining", awaiting_gate=None),
            wf,
        ),
        "PLAN_HUMAN_CLAIM_NOT_HELD": (
            _req(Operation.REVIEW_BLOCKED),
            _state(state="refining", awaiting_gate="ready_for_dev", reviewing=True),
            wf,
        ),
        "PLAN_SINGLE_HUMAN_CLAIM_SINGLETON": (
            _req(Operation.REVIEW_BLOCKED),
            _state(state="refining", awaiting_gate="ready_for_dev", auditing=True),
            wf,
        ),
        # recognized input
        "PLAN_REQUEST_INPUT_NO_GATE_IN_FLIGHT": (
            _req(Operation.REQUEST_INPUT, body_text="q", topic="general"),
            _state(state="refining", awaiting_input=True),
            wf,
        ),
        "PLAN_REQUEST_INPUT_BODY_REQUIRED": (
            _req(Operation.REQUEST_INPUT, body_text=None, topic="general"),
            _state(state="refining"),
            wf,
        ),
        "PLAN_REQUEST_INPUT_TOPIC_DECLARED": (
            _req(Operation.REQUEST_INPUT, body_text="q", topic="nope"),
            _state(state="refining"),
            wf,
        ),
        "PLAN_RESPOND_REQUIRES_AWAITING_INPUT": (
            _req(Operation.RESPOND_REQUEST, body_text="a"),
            _state(state="refining", awaiting_input=False),
            wf,
        ),
        "PLAN_RESPOND_BODY_REQUIRED": (
            _req(Operation.RESPOND_REQUEST, body_text=None),
            _state(state="refining", awaiting_input=True),
            wf,
        ),
        # creating operations
        "PLAN_SPAWN_PARENT_HAS_STATE": (
            _req(Operation.SPAWN_ISSUE, extras={"spawn": spawn}),
            _state(state=None),
            wf,
        ),
        "PLAN_SPAWN_PR_REQUIRES_HEAD": (
            _req(Operation.SPAWN_ISSUE, extras={"spawn": spawn, "entity": "pull_request"}),
            _state(state="implementing"),
            wf,
        ),
        "PLAN_COLLECT_CONTRIBUTOR_HAS_STATE": (
            _req(Operation.COLLECT_INTO, extras={"collector_id": "C", "from_states": ("staged",)}),
            _state(state=None),
            wf,
        ),
        "PLAN_COLLECT_ELIGIBILITY": (
            _req(Operation.COLLECT_INTO, extras={"collector_id": "C", "from_states": ("staged",)}),
            _state(state="refining"),
            wf,
        ),
    }


CASES = _cases()


@pytest.mark.parametrize("invariant_id", sorted(CASES))
def test_planner_invariant_rejects_violation(invariant_id: str) -> None:
    entry = CASES[invariant_id]
    request, state, state_machine = entry[0], entry[1], entry[2]
    catalog = entry[3] if len(entry) > 3 else None
    with pytest.raises(OperationError):
        plan_operation(request, state, state_machine, catalog)


def test_every_registered_planner_invariant_has_a_trigger() -> None:
    registered = {inv.id for inv in invariants_for_layer("planner")}
    assert set(CASES) == registered, (
        "planner invariant registry and trigger table drifted: "
        f"unregistered={sorted(set(CASES) - registered)} "
        f"untriggered={sorted(registered - set(CASES))}"
    )
