"""Validator tests."""

from __future__ import annotations

from datetime import date, timedelta

from workflow.backends.base import IssueState
from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
from workflow.core.model.state_machine import (
    ReversibilityClass,
    State,
    StateClass,
    StateMachine,
    Transition,
    TransitionType,
)
from workflow.core.model.trust_grant import (
    Evidence,
    TrustGrant,
    TrustGrantParameters,
)
from workflow.core.validator import (
    Severity,
    validate_issue_markers,
    validate_state_machine,
)


def _irreversible_workflow_without_hitl() -> StateMachine:
    workflow = StateMachine(name="t")
    workflow.states["working"] = State(name="working", state_class=StateClass.WORKING)
    workflow.states["released"] = State(
        name="released",
        state_class=StateClass.TERMINAL,
        reversibility=ReversibilityClass.IRREVERSIBLE,
    )
    workflow.transitions.append(
        Transition(
            source="working",
            destination="released",
            label="agent releases",
            # No gate_name → is_gated=False; combined with irreversible
            # destination this is the principle-11 violation under test.
            transition_type=TransitionType.ADVANCE,
        )
    )
    return workflow


def test_irreversible_destination_without_hitl_fires_warning() -> None:
    workflow = _irreversible_workflow_without_hitl()
    findings = validate_state_machine(workflow, catalog=None, grants={})
    matches = [f for f in findings if f.principle_cite == "state-machine-principles.md#11"]
    assert matches, "Expected a finding for state-machine-principles.md#11"
    assert any("irreversible" in f.message.lower() and "released" in f.message for f in matches)


def test_irreversible_destination_with_gate_passes() -> None:
    workflow = _irreversible_workflow_without_hitl()
    # Re-emit the transition with the gate set → marks it as HITL-gated.
    workflow.transitions = [
        Transition(
            source="working",
            destination="released",
            label="agent releases",
            transition_type=TransitionType.ADVANCE,
            gate_name="release",
        )
    ]
    findings = validate_state_machine(workflow, catalog=None, grants={})
    assert not any(f.principle_cite == "state-machine-principles.md#11" for f in findings)


def test_terminal_without_taxonomy_warns() -> None:
    workflow = StateMachine(name="t")
    workflow.states["working"] = State(name="working", state_class=StateClass.WORKING)
    workflow.states["done"] = State(
        name="done",
        state_class=StateClass.TERMINAL,
        terminal_taxonomy=None,
    )
    workflow.transitions.append(
        Transition(source="working", destination="done", label="agent done")
    )
    findings = validate_state_machine(workflow, None, {})
    assert any(f.principle_cite == "state-machine-principles.md#8" for f in findings)


def test_gate_with_multiple_source_states_errors() -> None:
    """A gate must fire from exactly one source state. Two transitions
    sharing a gate name but originating from different sources is an
    error — the `hitl:awaiting-<gate>` label would be ambiguous."""
    workflow = StateMachine(name="t")
    workflow.states["src_a"] = State(name="src_a", state_class=StateClass.WORKING)
    workflow.states["src_b"] = State(name="src_b", state_class=StateClass.WORKING)
    workflow.states["dst"] = State(
        name="dst",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    workflow.transitions.extend([
        Transition(
            source="src_a",
            destination="dst",
            label="A fires shared gate",
            transition_type=TransitionType.ADVANCE,
            gate_name="shared",
        ),
        Transition(
            source="src_b",
            destination="dst",
            label="B fires shared gate",
            transition_type=TransitionType.ADVANCE,
            gate_name="shared",
        ),
    ])
    findings = validate_state_machine(workflow, catalog=None, grants={})
    multi_source = [
        f for f in findings
        if f.severity is Severity.ERROR and "multiple source states" in f.message
    ]
    assert multi_source, "Expected error about multi-source gate sharing"
    assert "'shared'" in multi_source[0].message


def test_gate_with_same_source_multiple_destinations_passes() -> None:
    """Verdict-style: same source, several destinations sharing the gate
    name is fine."""
    workflow = StateMachine(name="t")
    workflow.states["src"] = State(name="src", state_class=StateClass.WORKING)
    workflow.states["a"] = State(
        name="a",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    workflow.states["b"] = State(
        name="b",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    workflow.transitions.extend([
        Transition(
            source="src",
            destination="a",
            label="verdict → a",
            transition_type=TransitionType.ADVANCE,
            gate_name="verdict",
        ),
        Transition(
            source="src",
            destination="b",
            label="verdict → b",
            transition_type=TransitionType.ADVANCE,
            gate_name="verdict",
        ),
    ])
    findings = validate_state_machine(workflow, catalog=None, grants={})
    assert not any(
        "multiple source states" in f.message for f in findings
    )


def test_audit_with_irreversible_destination_errors() -> None:
    """Audit-default-level on a gate that lands on an irreversible state.
    Reversibility is derived from the destination state via the gated
    transition, so the state machine and the gate transition must exist."""
    workflow = StateMachine(name="t")
    workflow.states["src"] = State(name="src", state_class=StateClass.WORKING)
    workflow.states["irrev_dst"] = State(
        name="irrev_dst",
        state_class=StateClass.TERMINAL,
        reversibility=ReversibilityClass.IRREVERSIBLE,
    )
    workflow.transitions.append(
        Transition(
            source="src",
            destination="irrev_dst",
            label="fire gate",
            transition_type=TransitionType.ADVANCE,
            gate_name="gate",
        )
    )
    catalog = HCPCatalog(process_name="t")
    catalog.entries["gate"] = HCP(
        gate_name="gate",
        hcp_type=HCPType.AUTHORITY,
        allowed_levels=[HCPLevel.BLOCK, HCPLevel.AUDIT],
        default_level=HCPLevel.AUDIT,
    )
    findings = validate_state_machine(workflow, catalog, {})
    errors = [f for f in findings if f.severity is Severity.ERROR]
    assert errors
    assert any(f.principle_cite == "hitl-principles.md#4" for f in errors)


def test_agent_prepares_missing_warns() -> None:
    workflow = StateMachine(name="t")
    catalog = HCPCatalog(process_name="t")
    catalog.entries["gate"] = HCP(
        gate_name="gate",
        hcp_type=HCPType.AUTHORITY,
        allowed_levels=[HCPLevel.BLOCK],
        default_level=HCPLevel.BLOCK,
        agent_prepares_path=None,
    )
    findings = validate_state_machine(workflow, catalog, {})
    assert any(
        f.principle_cite == "hitl-principles.md#8" and "Agent prepares" in f.message
        for f in findings
    )


def test_legend_catalog_drift_warns() -> None:
    workflow = StateMachine(name="t")
    workflow.gates_in_legend["gate_a"] = ReversibilityClass.REVERSIBLE_SLOW
    catalog = HCPCatalog(process_name="t")
    catalog.entries["gate_b"] = HCP(
        gate_name="gate_b",
        hcp_type=HCPType.AUTHORITY,
        allowed_levels=[HCPLevel.BLOCK],
        default_level=HCPLevel.BLOCK,
        agent_prepares_path="x.md",
    )
    findings = validate_state_machine(workflow, catalog, {})
    assert any(
        f.principle_cite == "hitl-principles.md#5" and "Legend gates" in f.message for f in findings
    )


def test_expired_trust_grant_warns() -> None:
    workflow = StateMachine(name="t")
    in_past_start = date.today() - timedelta(days=60)
    in_past_end = date.today() - timedelta(days=1)
    grants = {
        "gate": TrustGrant(
            control_point="gate",
            workflow="t",
            team="acme",
            current_level=HCPLevel.AUDIT,
            parameters=TrustGrantParameters(cadence="daily"),
            evidence=[Evidence(source="manual", metric="x", window="x", detail="x")],
            granted_by="x@example.com",
            granted_at=in_past_start,
            expires_at=in_past_end,
        )
    }
    findings = validate_state_machine(workflow, None, grants)
    assert any(
        f.principle_cite == "trust-grant-schema.md#7" and "expired" in f.message.lower()
        for f in findings
    )


def test_runtime_two_hitl_gates_errors() -> None:
    state = IssueState(
        issue_id="1",
        state="working",
        agent_claim="product-manager",
        awaiting_gate="ready_for_dev",
        audit_pending="some_audit",
    )
    findings = validate_issue_markers(state)
    assert any(f.severity is Severity.ERROR for f in findings)
    assert any(f.principle_cite == "hitl-principles.md#6" for f in findings)


def test_runtime_two_claim_singletons_errors() -> None:
    state = IssueState(
        issue_id="1",
        state="working",
        agent_claim="product-manager",
        reviewing=True,
        auditing=True,
    )
    findings = validate_issue_markers(state)
    assert any(
        f.severity is Severity.ERROR
        and f.principle_cite == "hitl-principles.md#6"
        and "singleton" in f.message.lower()
        for f in findings
    )


def test_resting_spawn_cannot_advance_into_working() -> None:
    """Resting-state spawn's advance_on targets must be non-working —
    auto-advancing into a working state would bypass the
    claim-before-working invariant."""
    from workflow.core.model.state_machine import Spawn

    # Parent process with a resting state that spawns and tries to advance
    # to a working state.
    parent = StateMachine(name="parent")
    parent.states = {
        "waiting": State(
            name="waiting",
            state_class=StateClass.RESTING,
            spawns=Spawn(
                process="child",
                issue_type="bug",
                initial_state="queue",
                advance_on=(("done", "doing"),),
            ),
        ),
        "doing": State(
            name="doing",
            state_class=StateClass.WORKING,
            roles=("worker",),
            issue_types=("bug",),
        ),
    }
    # Child process with the right type + initial state + terminal.
    child = StateMachine(name="child")
    child.states = {
        "queue": State(
            name="queue",
            state_class=StateClass.RESTING,
        ),
        "doing_child": State(
            name="doing_child",
            state_class=StateClass.WORKING,
            roles=("worker",),
            issue_types=("bug",),
        ),
        "done": State(
            name="done",
            state_class=StateClass.TERMINAL,
            terminal_taxonomy=__import__(
                "workflow.core.model.state_machine", fromlist=["TerminalTaxonomy"]
            ).TerminalTaxonomy.SHIPPED,
            close_reason="completed",
        ),
    }
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"parent": parent, "child": child},
    )
    assert any(
        f.principle_cite == "state-machine-principles.md#3"
        and "bypassing the claim-before-working invariant" in f.message
        for f in findings
    )
