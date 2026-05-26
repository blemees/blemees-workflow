"""Inspector tests — agent-facing next-action enumeration."""

from __future__ import annotations

from datetime import date, timedelta

from workflow.core.inspector import available_transitions
from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
from workflow.core.model.state_machine import (
    ReversibilityClass,
    State,
    StateClass,
    StateMachine,
    TerminalTaxonomy,
    Transition,
    TransitionType,
)
from workflow.core.model.trust_grant import Evidence, TrustGrant, TrustGrantParameters


def _build() -> tuple[StateMachine, HCPCatalog]:
    sm = StateMachine(name="t")
    sm.states = {
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
        ),
        "ready_for_dev": State(
            name="ready_for_dev",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        ),
        "wont_fix": State(
            name="wont_fix",
            state_class=StateClass.TERMINAL,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
            terminal_taxonomy=TerminalTaxonomy.ABANDONED,
        ),
    }
    sm.transitions = [
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
            transition_type=TransitionType.ADVANCE,
            gate_name="ready_for_dev",
        ),
        Transition(
            source="refining",
            destination="wont_fix",
            label="PM marks wont-fix",
            transition_type=TransitionType.ADVANCE,
            gate_name="wont_fix",
        ),
    ]
    catalog = HCPCatalog(process_name="t")
    catalog.entries["ready_for_dev"] = HCP(
        gate_name="ready_for_dev",
        hcp_type=HCPType.JUDGMENT,
        allowed_levels=[HCPLevel.BLOCK, HCPLevel.AUDIT],
        default_level=HCPLevel.BLOCK,
        agent_prepares_path="ready-packet.md",
    )
    catalog.entries["wont_fix"] = HCP(
        gate_name="wont_fix",
        hcp_type=HCPType.JUDGMENT,
        allowed_levels=[HCPLevel.BLOCK, HCPLevel.AUDIT],
        default_level=HCPLevel.AUDIT,
        agent_prepares_path="wont-fix-note.md",
    )
    return sm, catalog


def test_available_transitions_from_resting_returns_claim() -> None:
    sm, catalog = _build()
    actions = available_transitions(sm, catalog, {}, source_state="raw")
    assert len(actions) == 1
    assert actions[0].transition_type is TransitionType.CLAIM
    assert actions[0].destination == "refining"
    assert actions[0].is_gated is False


def test_available_transitions_from_working_returns_hitl_actions() -> None:
    sm, catalog = _build()
    actions = available_transitions(sm, catalog, {}, source_state="refining")
    assert len(actions) == 2
    by_dest = {a.destination: a for a in actions}

    ready = by_dest["ready_for_dev"]
    assert ready.is_gated
    assert ready.gate_name == "ready_for_dev"
    assert ready.default_level is HCPLevel.BLOCK
    assert ready.effective_level is HCPLevel.BLOCK
    assert ready.grant_relaxed is False
    assert "product-manager" in ready.triggering_roles
    assert ready.agent_prepares_path == "ready-packet.md"
    assert ready.destination_reversibility is ReversibilityClass.REVERSIBLE_SLOW
    assert ready.destination_state_class is StateClass.RESTING

    wont = by_dest["wont_fix"]
    assert wont.default_level is HCPLevel.AUDIT
    assert wont.destination_terminal_taxonomy is TerminalTaxonomy.ABANDONED


def test_trust_grant_relaxes_effective_level() -> None:
    sm, catalog = _build()
    today = date.today()
    grant = TrustGrant(
        control_point="ready_for_dev",
        workflow="t",
        team="example",
        current_level=HCPLevel.AUDIT,
        parameters=TrustGrantParameters(cadence="weekly"),
        evidence=[Evidence(source="manual", metric="trust", window="2026", detail="ok")],
        granted_by="lead@example.com",
        granted_at=today - timedelta(days=1),
        expires_at=today + timedelta(days=30),
    )
    actions = available_transitions(
        sm, catalog, {"ready_for_dev": grant}, source_state="refining"
    )
    by_dest = {a.destination: a for a in actions}
    ready = by_dest["ready_for_dev"]
    assert ready.default_level is HCPLevel.BLOCK
    assert ready.effective_level is HCPLevel.AUDIT
    assert ready.grant_relaxed is True
    # The other gate is untouched.
    wont = by_dest["wont_fix"]
    assert wont.grant_relaxed is False


def test_no_actions_for_terminal_state() -> None:
    sm, catalog = _build()
    assert available_transitions(sm, catalog, {}, source_state="wont_fix") == []
