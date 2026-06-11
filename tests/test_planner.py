"""Planner tests — one parametrized test per framework operation.

Builds minimal workflow + catalog fixtures inline; exercises each operation's
planner branch with valid inputs and asserts the MarkerChange shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow.backends.base import IssueState
from workflow.core.model.human_gate import (
    HumanGate,
    HumanGateCatalog,
    HumanGateLevel,
    HumanGateType,
)
from workflow.core.model.state_machine import (
    Closes,
    ClosureTaxonomy,
    ReversibilityClass,
    State,
    StateClass,
    StateMachine,
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


def _build_workflow() -> StateMachine:
    workflow = StateMachine(name="t")
    workflow.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
            human_inputs=("general", "clarify-scope"),
        ),
        "ready_for_dev": State(
            name="ready_for_dev",
            state_class=StateClass.RESTING,
        ),
        "promoted": State(
            name="promoted",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.IRREVERSIBLE,
            closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
        ),
        "killed": State(
            name="killed",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.IRREVERSIBLE,
            closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
        ),
        "implementing": State(
            name="implementing",
            state_class=StateClass.WORKING,
            roles=("product-owner",),
        ),
    }
    workflow.transitions = [
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
            gate_name="ready_for_dev",
        ),
        Transition(
            source="implementing",
            destination="promoted",
            label="PO promotes",
            gate_name="experiment-verdict",
        ),
        Transition(
            source="implementing",
            destination="killed",
            label="PO kills",
            gate_name="experiment-verdict",
        ),
    ]
    return workflow


def _build_catalog() -> HumanGateCatalog:
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["ready_for_dev"] = HumanGate(
        gate_name="ready_for_dev",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK, HumanGateLevel.AUDIT],
        default_level=HumanGateLevel.BLOCK,
        agent_prepares_path="dor.md",
    )
    catalog.entries["experiment-verdict"] = HumanGate(
        gate_name="experiment-verdict",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK],
        default_level=HumanGateLevel.BLOCK,
        agent_prepares_path="verdict-packet.md",
    )
    return catalog


# --------------------------------------------------------------------------- #
# Per-operation tests


def test_plan_advance() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="raw", agent_claim="product-manager")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE,
            issue_id="1",
            destination="refining",
            actor="product-manager",
        ),
        state,
        workflow,
    )
    assert plan.change.set_state == "refining"
    # raw → refining is a CLAIM transition: advance carries claim semantics
    # (#11) so the working state is never entered unowned.
    assert plan.change.set_agent_claim == "product-manager"
    assert plan.change.set_last_state == "raw"
    assert "state advance" in plan.audit_comment


def test_plan_advance_unknown_destination_errors() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="raw", agent_claim="product-manager")
    with pytest.raises(OperationError, match="No transition"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE_ISSUE,
                issue_id="1",
                destination="not_a_real_state",
            ),
            state,
            workflow,
        )


def test_plan_claim() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="raw", agent_claim=None)
    plan = plan_operation(
        OperationRequest(
            operation=Operation.CLAIM_ISSUE,
            issue_id="1",
            role="product-manager",
        ),
        state,
        workflow,
    )
    assert plan.change.set_agent_claim == "product-manager"
    # Claim records the origin so `release` can return the issue here.
    assert plan.change.set_last_state == "raw"
    # The single CLAIM transition out of `raw` is auto-picked → `refining`.
    assert plan.change.set_state == "refining"


def test_plan_claim_already_claimed_errors() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="raw", agent_claim="someone-else")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.CLAIM_ISSUE,
                issue_id="1",
                role="product-manager",
            ),
            state,
            workflow,
        )


def test_plan_release() -> None:
    workflow = _build_workflow()
    state = IssueState(
        issue_id="1", state="refining", agent_claim="product-manager", last_state="raw"
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.RELEASE_ISSUE, issue_id="1"),
        state,
        workflow,
    )
    assert plan.change.clear_agent_claim is True
    assert plan.change.clear_last_state is True
    assert plan.change.set_state == "raw"


def test_plan_release_without_last_state_errors() -> None:
    """A working state with no origin marker can't determine where to return."""
    workflow = _build_workflow()
    state = IssueState(
        issue_id="1", state="refining", agent_claim="product-manager", last_state=None
    )
    with pytest.raises(OperationError, match="last-state"):
        plan_operation(
            OperationRequest(operation=Operation.RELEASE_ISSUE, issue_id="1"),
            state,
            workflow,
        )


def test_closing_advance_closes_issue_as_completed() -> None:
    """Advancing into a closing state-shipped state plans close_issue with the
    `completed` reason. The backend will close the GitHub issue."""
    workflow = _build_workflow()
    workflow.transitions.append(
        Transition(
            source="implementing",
            destination="merged",
            label="developer ships",
            transition_type=TransitionType.ADVANCE,
        )
    )
    workflow.states["merged"] = State(
        name="merged",
        state_class=StateClass.RESTING,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    state = IssueState(
        issue_id="1", state="implementing", agent_claim="developer", last_state="ready_for_dev"
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.ADVANCE_ISSUE, issue_id="1", destination="merged"),
        state,
        workflow,
    )
    assert plan.change.close_issue is True
    assert plan.change.close_reason == "completed"


def test_advance_into_non_closing_state_does_not_close_issue() -> None:
    """Advancing into a plain resting state (no `closes`) leaves the issue
    open — e.g. a handoff-style exit where the work continues elsewhere on
    another process's diagram."""
    workflow = _build_workflow()
    workflow.transitions.append(
        Transition(
            source="implementing",
            destination="bounced",
            label="developer bounces",
            transition_type=TransitionType.ADVANCE,
        )
    )
    workflow.states["bounced"] = State(
        name="bounced",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        issue_types=("bug",),
        # No `closes` — a non-closing resting state keeps the issue open.
    )
    state = IssueState(
        issue_id="1", state="implementing", agent_claim="developer", last_state="ready_for_dev"
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.ADVANCE_ISSUE, issue_id="1", destination="bounced"),
        state,
        workflow,
    )
    assert plan.change.close_issue is False


def test_abandoned_closing_closes_as_not_planned() -> None:
    """Abandoned / deduplicated closing states close with `not planned`."""
    workflow = _build_workflow()
    workflow.transitions.append(
        Transition(
            source="implementing",
            destination="wont",
            label="developer abandons",
            transition_type=TransitionType.ADVANCE,
        )
    )
    workflow.states["wont"] = State(
        name="wont",
        state_class=StateClass.RESTING,
        closes=Closes(taxonomy=ClosureTaxonomy.ABANDONED, reason="not planned"),
    )
    state = IssueState(
        issue_id="1", state="implementing", agent_claim="developer", last_state="ready_for_dev"
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.ADVANCE_ISSUE, issue_id="1", destination="wont"),
        state,
        workflow,
    )
    assert plan.change.close_issue is True
    assert plan.change.close_reason == "not planned"


def test_plan_release_with_drifted_last_state_errors() -> None:
    """If last_state points at a state with no CLAIM transition to current, error."""
    workflow = _build_workflow()
    state = IssueState(
        issue_id="1", state="refining", agent_claim="product-manager", last_state="ready_for_dev"
    )
    with pytest.raises(OperationError, match="drifted"):
        plan_operation(
            OperationRequest(operation=Operation.RELEASE_ISSUE, issue_id="1"),
            state,
            workflow,
        )


def test_plan_release_without_claim_errors() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="refining", agent_claim=None)
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(operation=Operation.RELEASE_ISSUE, issue_id="1"),
            state,
            workflow,
        )


def test_plan_await_signal() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.AWAIT_SIGNAL,
            issue_id="1",
            gate="ready_for_dev",
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_awaiting_gate == "ready_for_dev"


def test_plan_await_signal_unknown_gate_errors() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.AWAIT_SIGNAL,
                issue_id="1",
                gate="not-a-real-gate",
            ),
            state,
            workflow,
            catalog,
        )


def test_plan_review() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_gate="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.REVIEW_BLOCKED, issue_id="1"),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_reviewing is True


def test_plan_review_without_awaiting_errors() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(operation=Operation.REVIEW_BLOCKED, issue_id="1"),
            state,
            workflow,
            catalog,
        )


def test_plan_approve_binary() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_gate="ready_for_dev",
        reviewing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.APPROVE_BLOCKED,
            issue_id="1",
            gate="ready_for_dev",
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_state == "ready_for_dev"
    assert plan.change.clear_awaiting_gate is True
    # record_approval carries the GATE name (not destination); the
    # destination is captured via `set_state`.
    assert plan.change.record_approval == "ready_for_dev"  # gate name happens to match


def test_plan_approve_verdict_requires_destination() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="implementing",
        agent_claim="po",
        awaiting_gate="experiment-verdict",
    )
    with pytest.raises(OperationError, match="requires --destination"):
        plan_operation(
            OperationRequest(
                operation=Operation.APPROVE_BLOCKED,
                issue_id="1",
                gate="experiment-verdict",
            ),
            state,
            workflow,
            catalog,
        )


def test_plan_approve_verdict_with_destination() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="implementing",
        agent_claim="po",
        awaiting_gate="experiment-verdict",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.APPROVE_BLOCKED,
            issue_id="1",
            gate="experiment-verdict",
            destination="promoted",
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_state == "promoted"
    # Verdict-style: state moves to the chosen destination, but
    # record_approval carries the gate name (not the destination).
    assert plan.change.record_approval == "experiment-verdict"


def test_plan_reject(tmp_path: Path) -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    feedback = tmp_path / "feedback.md"
    feedback.write_text("**HITL: rejected**\n\nReason.\n", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_gate="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REJECT_BLOCKED,
            issue_id="1",
            gate="ready_for_dev",
            body_text=feedback.read_text(encoding="utf-8"),
        ),
        state,
        workflow,
        catalog,
    )
    # State unchanged; gate cleared; rejection recorded.
    assert plan.change.set_state is None
    assert plan.change.clear_awaiting_gate is True
    assert plan.change.record_rejection == "ready_for_dev"
    assert plan.packet_body is not None
    assert "HITL: rejected" in plan.packet_body


def test_plan_record_action() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.RECORD_ACTION,
            issue_id="1",
            gate="ready_for_dev",
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_state == "ready_for_dev"
    assert plan.change.set_audit_pending == "ready_for_dev"


def test_plan_record_action_irreversible_errors() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="implementing", agent_claim="po")
    with pytest.raises(OperationError, match="reversible destination"):
        plan_operation(
            OperationRequest(
                operation=Operation.RECORD_ACTION,
                issue_id="1",
                gate="experiment-verdict",
                destination="promoted",
            ),
            state,
            workflow,
            catalog,
        )


def test_plan_audit() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.REVIEW_AUDIT, issue_id="1"),
        state,
        workflow,
        catalog,
    )
    assert plan.change.set_auditing is True


def test_plan_check() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(
        issue_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
        auditing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.APPROVE_AUDIT,
            issue_id="1",
            gate="ready_for_dev",
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.clear_audit_pending is True
    assert plan.change.record_confirm == "ready_for_dev"


def test_plan_revoke(tmp_path: Path) -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    concern = tmp_path / "concern.md"
    concern.write_text("**HITL: revoked**\n\nReason.\n", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="ready_for_dev",
        agent_claim=None,
        audit_pending="ready_for_dev",
        auditing=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REJECT_AUDIT,
            issue_id="1",
            gate="ready_for_dev",
            body_text=concern.read_text(encoding="utf-8"),
        ),
        state,
        workflow,
        catalog,
    )
    assert plan.change.clear_audit_pending is True
    assert plan.change.record_revoke == "ready_for_dev"
    assert plan.packet_body is not None and "revoked" in plan.packet_body


def test_plan_request_input(tmp_path: Path) -> None:
    workflow = _build_workflow()
    question = tmp_path / "q.md"
    question.write_text("**HITL: agent needs input**\n\nQuestion.\n", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.REQUEST_INPUT,
            issue_id="1",
            body_text=question.read_text(encoding="utf-8"),
            topic="general",
        ),
        state,
        workflow,
    )
    assert plan.change.set_awaiting_input is True
    assert plan.change.set_human_input == "general"
    assert plan.packet_body is not None


def test_plan_request_input_requires_declared_topic(tmp_path: Path) -> None:
    """The state's human_inputs list is closed — passing a topic that's
    not declared is rejected."""
    workflow = _build_workflow()
    q = tmp_path / "q.md"
    q.write_text("question", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
    )
    with pytest.raises(OperationError, match="is not declared on state"):
        plan_operation(
            OperationRequest(
                operation=Operation.REQUEST_INPUT,
                issue_id="1",
                body_text=q.read_text(encoding="utf-8"),
                topic="needs-ux-input",  # not in refining's human_inputs
            ),
            state,
            workflow,
        )


def test_plan_request_input_forbidden_when_state_has_no_topics(tmp_path: Path) -> None:
    """A state without `human_inputs` declared can't host request-input
    at all — agents must release the issue or stay put."""
    workflow = _build_workflow()
    # Strip topics from `refining` for this test.
    refining = workflow.states["refining"]
    workflow.states["refining"] = State(
        name=refining.name,
        state_class=refining.state_class,
        roles=refining.roles,
        # human_inputs deliberately omitted
    )
    q = tmp_path / "q.md"
    q.write_text("question", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
    )
    with pytest.raises(OperationError, match="does not declare `human_inputs`"):
        plan_operation(
            OperationRequest(
                operation=Operation.REQUEST_INPUT,
                issue_id="1",
                body_text=q.read_text(encoding="utf-8"),
                topic="general",
            ),
            state,
            workflow,
        )


def test_plan_request_input_blocks_during_catalogued_gate(tmp_path: Path) -> None:
    workflow = _build_workflow()
    q = tmp_path / "q.md"
    q.write_text("x", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_gate="ready_for_dev",
    )
    with pytest.raises(OperationError):
        plan_operation(
            OperationRequest(
                operation=Operation.REQUEST_INPUT,
                issue_id="1",
                body_text=q.read_text(encoding="utf-8"),
                topic="general",
            ),
            state,
            workflow,
        )


def test_plan_advise() -> None:
    workflow = _build_workflow()
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_input=True,
    )
    plan = plan_operation(
        OperationRequest(operation=Operation.REVIEW_REQUEST, issue_id="1"),
        state,
        workflow,
    )
    assert plan.change.set_advising is True


def test_plan_resolve(tmp_path: Path) -> None:
    workflow = _build_workflow()
    response = tmp_path / "r.md"
    response.write_text("**HITL: resolved**\n\nx\n", encoding="utf-8")
    state = IssueState(
        issue_id="1",
        state="refining",
        agent_claim="product-manager",
        awaiting_input=True,
        advising=True,
    )
    plan = plan_operation(
        OperationRequest(
            operation=Operation.RESPOND_REQUEST,
            issue_id="1",
            body_text=response.read_text(encoding="utf-8"),
        ),
        state,
        workflow,
    )
    assert plan.change.set_awaiting_input is False
    assert plan.change.record_response is True


# --------------------------------------------------------------------------- #
# Compact parametrize sweep to assert every operation produces a plan with
# valid inputs and no exceptions.


@pytest.mark.parametrize(
    "operation,builder",
    [
        (
            Operation.ADVANCE_ISSUE,
            lambda lc, cat, paths: (
                OperationRequest(
                    operation=Operation.ADVANCE_ISSUE,
                    issue_id="1",
                    destination="refining",
                    actor="product-manager",
                ),
                IssueState(issue_id="1", state="raw", agent_claim="product-manager"),
            ),
        ),
        (
            Operation.CLAIM_ISSUE,
            lambda lc, cat, paths: (
                OperationRequest(
                    operation=Operation.CLAIM_ISSUE, issue_id="1", role="product-manager"
                ),
                IssueState(issue_id="1", state="raw", agent_claim=None),
            ),
        ),
        (
            Operation.RELEASE_ISSUE,
            lambda lc, cat, paths: (
                OperationRequest(operation=Operation.RELEASE_ISSUE, issue_id="1"),
                IssueState(
                    issue_id="1",
                    state="refining",
                    agent_claim="product-manager",
                    last_state="raw",
                ),
            ),
        ),
    ],
)
def test_workflow_operations_parametrized(operation, builder, tmp_path: Path) -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    request, state = builder(workflow, catalog, tmp_path)
    plan = plan_operation(request, state, workflow, catalog)
    assert plan.operation is operation
    assert plan.audit_comment


# --------------------------------------------------------------------------- #
# Dispatch tests: advance consults the catalog and dispatches internally to
# await-signal or record-action depending on the transition's gate level.


def test_advance_on_block_gated_transition_dispatches_to_await_signal(
    tmp_path: Path,
) -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    packet = tmp_path / "ready-packet.md"
    packet.write_text("acceptance criteria…", encoding="utf-8")

    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE,
            issue_id="1",
            destination="ready_for_dev",
            body_text=packet.read_text(encoding="utf-8"),
        ),
        state,
        workflow,
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
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")

    with pytest.raises(OperationError, match="block level"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE_ISSUE,
                issue_id="1",
                destination="ready_for_dev",
            ),
            state,
            workflow,
            catalog,
        )


def test_advance_on_ungated_transition_changes_state() -> None:
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="raw", agent_claim="product-manager")

    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE,
            issue_id="1",
            destination="refining",
            actor="product-manager",
        ),
        state,
        workflow,
        catalog,
    )

    # No HumanGate — the planner returns a straightforward advance. raw →
    # refining is a CLAIM transition, so the advance carries claim semantics
    # (#11): ownership + origin marker, never an unowned working state.
    assert plan.operation is Operation.ADVANCE_ISSUE
    assert plan.change.set_state == "refining"
    assert plan.change.set_agent_claim == "product-manager"
    assert plan.change.set_last_state == "raw"
    assert plan.change.set_awaiting_gate is None


def test_advance_on_audit_gated_transition_dispatches_to_record_action(
    tmp_path: Path,
) -> None:
    """Audit-gated dispatch — synthesize a reversible-destination HumanGate at default audit."""
    from workflow.core.model.human_gate import (
        HumanGate,
        HumanGateCatalog,
        HumanGateLevel,
        HumanGateType,
    )
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        "working": State(name="working", state_class=StateClass.WORKING),
        "logged": State(
            name="logged",
            state_class=StateClass.RESTING,
        ),
    }
    workflow.transitions = [
        Transition(
            source="working",
            destination="logged",
            label="log finding",
            gate_name="logged",
        ),
    ]
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["logged"] = HumanGate(
        gate_name="logged",
        gate_type=HumanGateType.KNOWLEDGE,
        allowed_levels=[HumanGateLevel.BLOCK, HumanGateLevel.AUDIT],
        default_level=HumanGateLevel.AUDIT,
        agent_prepares_path="log-template.md",
    )

    state = IssueState(issue_id="1", state="working", agent_claim="developer")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE,
            issue_id="1",
            destination="logged",
        ),
        state,
        workflow,
        catalog,
    )

    # Audit-gated: state changes AND audit-pending marker is set, atomically.
    assert plan.operation is Operation.RECORD_ACTION
    assert plan.change.set_state == "logged"
    assert plan.change.set_audit_pending == "logged"


# --------------------------------------------------------------------------- #
# Role validation: claim transitions must match the destination working
# state's `roles` list, and gated transitions must match the HumanGate's
# triggering_role.


def test_claim_role_must_match_destination_working_state_roles() -> None:
    """An agent with role X can't claim into a working state whose `roles`
    doesn't include X."""
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    state = IssueState(issue_id="1", state="raw", agent_claim=None)

    with pytest.raises(OperationError, match="Role mismatch"):
        plan_operation(
            OperationRequest(
                operation=Operation.CLAIM_ISSUE,
                issue_id="1",
                role="developer",  # not in refining's roles
            ),
            state,
            workflow,
        )


def test_claim_rejects_wrong_issue_type_for_destination_state() -> None:
    """If the destination working state declares `issue_types`, the issue's
    type must be in the set. Mismatched types fail the claim."""
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        "ready": State(name="ready", state_class=StateClass.RESTING),
        "implementing_experiment": State(
            name="implementing_experiment",
            state_class=StateClass.WORKING,
            roles=("developer",),
            issue_types=("experiment",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="ready",
            destination="implementing_experiment",
            label="developer claims experiment",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    # A bug-typed issue tries to claim into the experiment-only state.
    state = IssueState(issue_id="1", state="ready", agent_claim=None, issue_type="bug")

    with pytest.raises(OperationError, match="not accepted by working state"):
        plan_operation(
            OperationRequest(operation=Operation.CLAIM_ISSUE, issue_id="1", role="developer"),
            state,
            workflow,
        )


def test_claim_allows_matching_issue_type() -> None:
    """An issue whose type is in the destination state's `issue_types`
    succeeds."""
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        "ready": State(name="ready", state_class=StateClass.RESTING),
        "implementing_experiment": State(
            name="implementing_experiment",
            state_class=StateClass.WORKING,
            roles=("developer",),
            issue_types=("experiment",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="ready",
            destination="implementing_experiment",
            label="developer claims experiment",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    state = IssueState(issue_id="1", state="ready", agent_claim=None, issue_type="experiment")

    plan = plan_operation(
        OperationRequest(operation=Operation.CLAIM_ISSUE, issue_id="1", role="developer"),
        state,
        workflow,
    )
    assert plan.change.set_state == "implementing_experiment"


def test_advance_into_mark_pr_ready_sets_marker() -> None:
    """Advancing into a state with mark_pr_ready: true sets MarkerChange.set_pr_ready."""
    workflow = _build_workflow()
    rfd = workflow.states["ready_for_dev"]
    workflow.states["ready_for_dev"] = State(
        name=rfd.name,
        state_class=rfd.state_class,
        reversibility=rfd.reversibility,
        roles=rfd.roles,
        issue_types=rfd.issue_types,
        closes=rfd.closes,
        handoff=rfd.handoff,
        spawns=rfd.spawns,
        mark_pr_ready=True,
        notes=rfd.notes,
    )
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE, issue_id="1", destination="ready_for_dev"
        ),
        state,
        workflow,
        catalog=None,
    )
    assert plan.change.set_pr_ready is True


def test_advance_into_normal_state_does_not_set_pr_ready() -> None:
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")
    plan = plan_operation(
        OperationRequest(
            operation=Operation.ADVANCE_ISSUE, issue_id="1", destination="ready_for_dev"
        ),
        state,
        workflow,
        catalog=None,
    )
    assert plan.change.set_pr_ready is False


def test_claim_role_match_succeeds() -> None:
    """Claiming with a role in the destination state's `roles` passes."""
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]
    state = IssueState(issue_id="1", state="raw", agent_claim=None)

    plan = plan_operation(
        OperationRequest(operation=Operation.CLAIM_ISSUE, issue_id="1", role="product-manager"),
        state,
        workflow,
    )
    assert plan.change.set_agent_claim == "product-manager"


def test_advance_to_gated_destination_requires_role_match() -> None:
    """Firing a catalogued HumanGate requires the actor's role to match
    the catalog row's triggering_role."""
    workflow = _build_workflow()
    catalog = _build_catalog()
    state = IssueState(issue_id="1", state="refining", agent_claim="product-manager")

    with pytest.raises(OperationError, match="Role mismatch"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE_ISSUE,
                issue_id="1",
                destination="ready_for_dev",
                actor="developer",  # wrong role; gate's triggering_role is {pm}
                body_text=None,
            ),
            state,
            workflow,
            catalog,
        )


def test_advance_over_claim_without_actor_errors() -> None:
    # Entering a working state without an owner is the #11 bug — the planner
    # must refuse rather than produce an unowned working state.
    workflow = _build_workflow()
    state = IssueState(issue_id="1", state="raw", agent_claim=None)
    with pytest.raises(OperationError, match="acting role"):
        plan_operation(
            OperationRequest(
                operation=Operation.ADVANCE_ISSUE,
                issue_id="1",
                destination="refining",
            ),
            state,
            workflow,
        )
