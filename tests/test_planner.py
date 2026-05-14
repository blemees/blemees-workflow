"""Planner tests — one parametrized test per framework operation.

Builds minimal lifecycle + catalog fixtures inline; exercises each operation's
planner branch with valid inputs and asserts the MarkerChange shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow.backends.base import WorkItemState
from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
from workflow.core.model.lifecycle import (
    Lifecycle,
    ReversibilityClass,
    State,
    StateClass,
    Transition,
    TransitionType,
)
from workflow.core.planner import (
    Operation,
    OperationRequest,
    plan_operation,
)
from workflow.errors import OperationError

# --------------------------------------------------------------------------- #
# Fixtures


def _build_lifecycle() -> Lifecycle:
    lifecycle = Lifecycle(name="t")
    lifecycle.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(name="refining", state_class=StateClass.WORKING),
        "ready_for_dev": State(
            name="ready_for_dev",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        ),
        "promoted": State(
            name="promoted",
            state_class=StateClass.TERMINAL,
            reversibility=ReversibilityClass.IRREVERSIBLE,
        ),
        "killed": State(
            name="killed",
            state_class=StateClass.TERMINAL,
            reversibility=ReversibilityClass.IRREVERSIBLE,
        ),
        "implementing": State(name="implementing", state_class=StateClass.WORKING),
    }
    lifecycle.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="PM claims raw",
            transition_type=TransitionType.CLAIM,
        ),
        Transition(
            source="refining",
            destination="ready_for_dev",
            label="PM marks ready",
            is_gated=True,
        ),
        Transition(
            source="implementing",
            destination="promoted",
            label="PO promotes",
            is_gated=True,
        ),
    ]
    return lifecycle


def _build_catalog() -> HCPCatalog:
    catalog = HCPCatalog(process_name="t")
    catalog.entries["ready_for_dev"] = HCP(
        gate_name="ready_for_dev",
        source_state="refining",
        destinations=["ready_for_dev"],
        triggering_role="{pm}",
        hcp_type=HCPType.AUTHORITY,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        allowed_levels=[HCPLevel.BLOCK, HCPLevel.AUDIT],
        default_level=HCPLevel.BLOCK,
        agent_prepares_path="dor.md",
    )
    catalog.entries["experiment-verdict"] = HCP(
        gate_name="experiment-verdict",
        source_state="implementing",
        destinations=["promoted", "killed"],
        triggering_role="{product-owner}",
        hcp_type=HCPType.AUTHORITY,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        allowed_levels=[HCPLevel.BLOCK],
        default_level=HCPLevel.BLOCK,
        agent_prepares_path="verdict-packet.md",
    )
    return catalog


# --------------------------------------------------------------------------- #
# Per-operation tests


def test_plan_advance() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="raw", agent_claim="pm")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE,
            work_item_id="1",
            destination="refining",
        ),
        state,
        lifecycle,
    )
    assert plan.change.set_state == "refining"
    assert "state advance" in plan.audit_comment


def test_plan_advance_unknown_destination_errors() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="raw", agent_claim="pm")
    with pytest.raises(OperationError, match="No transition"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE,
                work_item_id="1",
                destination="not_a_real_state",
            ),
            state,
            lifecycle,
        )


def test_plan_claim() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="raw", agent_claim=None)
    plan = plan_operation(
        OperationRequest(
            operation=Operation.CLAIM,
            work_item_id="1",
            role="pm",
        ),
        state,
        lifecycle,
    )
    assert plan.change.set_agent_claim == "pm"


def test_plan_claim_already_claimed_errors() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="raw", agent_claim="someone-else")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.CLAIM,
                work_item_id="1",
                role="pm",
            ),
            state,
            lifecycle,
        )


def test_plan_release() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")
    plan = plan_operation(
        OperationRequest(operation=Operation.RELEASE, work_item_id="1"),
        state,
        lifecycle,
    )
    assert plan.change.clear_agent_claim is True


def test_plan_release_without_claim_errors() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim=None)
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(operation=Operation.RELEASE, work_item_id="1"),
            state,
            lifecycle,
        )


def test_plan_await_signal() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.AWAIT_SIGNAL,
            work_item_id="1",
            gate="ready_for_dev",
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_awaiting_gate == "ready_for_dev"


def test_plan_await_signal_unknown_gate_errors() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.AWAIT_SIGNAL,
                work_item_id="1",
                gate="not-a-real-gate",
            ),
            state,
            lifecycle,
            catalog,
        )


def test_plan_review() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_gate="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.REVIEW, work_item_id="1"),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_reviewing is True


def test_plan_review_without_awaiting_errors() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(operation=Operation.REVIEW, work_item_id="1"),
            state,
            lifecycle,
            catalog,
        )


def test_plan_approve_binary() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_gate="ready_for_dev",
        reviewing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.APPROVE,
            work_item_id="1",
            gate="ready_for_dev",
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_state == "ready_for_dev"
    assert plan.change.clear_awaiting_gate is True
    assert plan.change.record_approval == "ready_for_dev"


def test_plan_approve_verdict_requires_destination() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="implementing",
        agent_claim="po",
        awaiting_gate="experiment-verdict",
    )
    with pytest.raises(OperationError, match="requires --destination"):
        plan_operation(
            OperationRequest(
                operation=Operation.APPROVE,
                work_item_id="1",
                gate="experiment-verdict",
            ),
            state,
            lifecycle,
            catalog,
        )


def test_plan_approve_verdict_with_destination() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="implementing",
        agent_claim="po",
        awaiting_gate="experiment-verdict",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.APPROVE,
            work_item_id="1",
            gate="experiment-verdict",
            destination="promoted",
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_state == "promoted"
    assert plan.change.record_approval == "promoted"


def test_plan_reject(tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    feedback = tmp_path / "feedback.md"
    feedback.write_text("**HITL: rejected**\n\nReason.\n", encoding="utf-8")
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_gate="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REJECT,
            work_item_id="1",
            gate="ready_for_dev",
            body_path=str(feedback),
        ),
        state,
        lifecycle,
        catalog,
    )
    # State unchanged; gate cleared; rejection recorded.
    assert plan.change.set_state is None
    assert plan.change.clear_awaiting_gate is True
    assert plan.change.record_rejection == "ready_for_dev"
    assert plan.packet_body is not None
    assert "HITL: rejected" in plan.packet_body


def test_plan_record_action() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.RECORD_ACTION,
            work_item_id="1",
            gate="ready_for_dev",
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_state == "ready_for_dev"
    assert plan.change.set_audit_pending == "ready_for_dev"


def test_plan_record_action_irreversible_errors() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="implementing", agent_claim="po")
    with pytest.raises(OperationError, match="reversible destination"):
        plan_operation(
            OperationRequest(
                operation=Operation.RECORD_ACTION,
                work_item_id="1",
                gate="experiment-verdict",
                destination="promoted",
            ),
            state,
            lifecycle,
            catalog,
        )


def test_plan_audit() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.AUDIT, work_item_id="1"),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.set_auditing is True


def test_plan_check() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(
        work_item_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
        auditing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.CHECK,
            work_item_id="1",
            gate="ready_for_dev",
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.clear_audit_pending is True
    assert plan.change.record_check == "ready_for_dev"


def test_plan_revoke(tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    concern = tmp_path / "concern.md"
    concern.write_text("**HITL: revoked**\n\nReason.\n", encoding="utf-8")
    state = WorkItemState(
        work_item_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
        auditing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REVOKE,
            work_item_id="1",
            gate="ready_for_dev",
            body_path=str(concern),
        ),
        state,
        lifecycle,
        catalog,
    )
    assert plan.change.clear_audit_pending is True
    assert plan.change.record_revoke == "ready_for_dev"
    assert plan.packet_body is not None and "revoked" in plan.packet_body


def test_plan_request_input(tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    question = tmp_path / "q.md"
    question.write_text("**HITL: agent needs input**\n\nQuestion.\n", encoding="utf-8")
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REQUEST_INPUT,
            work_item_id="1",
            body_path=str(question),
        ),
        state,
        lifecycle,
    )
    assert plan.change.set_awaiting_input is True
    assert plan.packet_body is not None


def test_plan_request_input_blocks_during_catalogued_gate(tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    q = tmp_path / "q.md"
    q.write_text("x", encoding="utf-8")
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_gate="ready_for_dev",
    )
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.REQUEST_INPUT,
                work_item_id="1",
                body_path=str(q),
            ),
            state,
            lifecycle,
        )


def test_plan_advise() -> None:
    lifecycle = _build_lifecycle()
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_input=True,
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.ADVISE, work_item_id="1"),
        state,
        lifecycle,
    )
    assert plan.change.set_advising is True


def test_plan_resolve(tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    response = tmp_path / "r.md"
    response.write_text("**HITL: resolved**\n\nx\n", encoding="utf-8")
    state = WorkItemState(
        work_item_id="1",
        state="refining",
        agent_claim="pm",
        awaiting_input=True,
        advising=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.RESOLVE,
            work_item_id="1",
            body_path=str(response),
        ),
        state,
        lifecycle,
    )
    assert plan.change.set_awaiting_input is False
    assert plan.change.record_resolution is True


# --------------------------------------------------------------------------- #
# Compact parametrize sweep to assert every operation produces a plan with
# valid inputs and no exceptions.


@pytest.mark.parametrize(
    "operation,builder",
    [
        (
            Operation.ADVANCE,
            lambda lc, cat, paths: (
                OperationRequest(
                    operation=Operation.ADVANCE,
                    work_item_id="1",
                    destination="refining",
                ),
                WorkItemState(work_item_id="1", state="raw", agent_claim="pm"),
            ),
        ),
        (
            Operation.CLAIM,
            lambda lc, cat, paths: (
                OperationRequest(operation=Operation.CLAIM, work_item_id="1", role="pm"),
                WorkItemState(work_item_id="1", state="raw", agent_claim=None),
            ),
        ),
        (
            Operation.RELEASE,
            lambda lc, cat, paths: (
                OperationRequest(operation=Operation.RELEASE, work_item_id="1"),
                WorkItemState(work_item_id="1", state="refining", agent_claim="pm"),
            ),
        ),
    ],
)
def test_lifecycle_operations_parametrized(operation, builder, tmp_path: Path) -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    request, state = builder(lifecycle, catalog, tmp_path)
    plan = plan_operation(request, state, lifecycle, catalog)
    assert plan.operation is operation
    assert plan.audit_comment


# --------------------------------------------------------------------------- #
# Dispatch tests: advance consults the catalog and dispatches internally to
# await-signal or record-action depending on the transition's gate level.


def test_advance_on_block_gated_transition_dispatches_to_await_signal(
    tmp_path: Path,
) -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")
    packet = tmp_path / "ready-packet.md"
    packet.write_text("acceptance criteria…", encoding="utf-8")

    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE,
            work_item_id="1",
            destination="ready_for_dev",
            body_path=str(packet),
        ),
        state,
        lifecycle,
        catalog,
    )

    # The agent invoked ADVANCE; the planner resolved it to AWAIT_SIGNAL
    # because the catalog says this transition is block-gated.
    assert plan.operation is Operation.AWAIT_SIGNAL
    # State did NOT change; the awaiting marker was set instead.
    assert plan.change.set_state is None
    assert plan.change.set_awaiting_gate == "ready_for_dev"
    assert plan.packet_body == "acceptance criteria…"


def test_advance_on_block_gated_transition_without_packet_errors() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")

    with pytest.raises(OperationError, match="block level"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE,
                work_item_id="1",
                destination="ready_for_dev",
            ),
            state,
            lifecycle,
            catalog,
        )


def test_advance_on_ungated_transition_changes_state() -> None:
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="raw", agent_claim="pm")

    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE,
            work_item_id="1",
            destination="refining",
        ),
        state,
        lifecycle,
        catalog,
    )

    # No HCP — the planner returns a straightforward advance.
    assert plan.operation is Operation.ADVANCE
    assert plan.change.set_state == "refining"
    assert plan.change.set_awaiting_gate is None


def test_advance_on_audit_gated_transition_dispatches_to_record_action(
    tmp_path: Path,
) -> None:
    """Audit-gated dispatch — synthesize a reversible-destination HCP at default audit."""
    from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
    from workflow.core.model.lifecycle import (
        Lifecycle,
        ReversibilityClass,
        State,
        StateClass,
        Transition,
    )

    lifecycle = Lifecycle(name="t")
    lifecycle.states = {
        "working": State(name="working", state_class=StateClass.WORKING),
        "logged": State(
            name="logged",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
        ),
    }
    lifecycle.transitions = [
        Transition(
            source="working",
            destination="logged",
            label="log finding",
            is_gated=True,
        ),
    ]
    catalog = HCPCatalog(process_name="t")
    catalog.entries["logged"] = HCP(
        gate_name="logged",
        source_state="working",
        destinations=["logged"],
        triggering_role="{developer}",
        hcp_type=HCPType.KNOWLEDGE,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        allowed_levels=[HCPLevel.BLOCK, HCPLevel.AUDIT],
        default_level=HCPLevel.AUDIT,
        agent_prepares_path="log-template.md",
    )

    state = WorkItemState(work_item_id="1", state="working", agent_claim="developer")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE,
            work_item_id="1",
            destination="logged",
        ),
        state,
        lifecycle,
        catalog,
    )

    # Audit-gated: state changes AND audit-pending marker is set, atomically.
    assert plan.operation is Operation.RECORD_ACTION
    assert plan.change.set_state == "logged"
    assert plan.change.set_audit_pending == "logged"


# --------------------------------------------------------------------------- #
# Role validation: claim transitions must match the source state's claim_role,
# and gated transitions must match the HCP's triggering_role.


def test_claim_role_must_match_source_state_claim_role() -> None:
    """An agent with role X can't claim a state whose claim_role is Y."""
    from workflow.core.model.lifecycle import (
        Lifecycle,
        State,
        StateClass,
        Transition,
        TransitionType,
    )

    lifecycle = Lifecycle(name="t")
    lifecycle.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING, claim_role="pm"),
        "refining": State(name="refining", state_class=StateClass.WORKING),
    }
    lifecycle.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    state = WorkItemState(work_item_id="1", state="raw", agent_claim=None)

    with pytest.raises(OperationError, match="Role mismatch"):
        plan_operation(
            OperationRequest(
                operation=Operation.CLAIM,
                work_item_id="1",
                role="developer",  # wrong role for this state
            ),
            state,
            lifecycle,
        )


def test_claim_role_match_succeeds() -> None:
    """Claiming with the correct role passes validation."""
    from workflow.core.model.lifecycle import (
        Lifecycle,
        State,
        StateClass,
        Transition,
        TransitionType,
    )

    lifecycle = Lifecycle(name="t")
    lifecycle.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING, claim_role="pm"),
        "refining": State(name="refining", state_class=StateClass.WORKING),
    }
    lifecycle.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    state = WorkItemState(work_item_id="1", state="raw", agent_claim=None)

    plan = plan_operation(
        OperationRequest(operation=Operation.CLAIM, work_item_id="1", role="pm"),
        state,
        lifecycle,
    )
    assert plan.change.set_agent_claim == "pm"


def test_advance_to_gated_destination_requires_role_match() -> None:
    """Firing a catalogued HCP requires the actor's role to match
    the catalog row's triggering_role."""
    lifecycle = _build_lifecycle()
    catalog = _build_catalog()
    state = WorkItemState(work_item_id="1", state="refining", agent_claim="pm")

    with pytest.raises(OperationError, match="Role mismatch"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE,
                work_item_id="1",
                destination="ready_for_dev",
                actor="developer",  # wrong role; gate's triggering_role is {pm}
                body_path=None,
            ),
            state,
            lifecycle,
            catalog,
        )
