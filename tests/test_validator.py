"""Validator tests."""

from __future__ import annotations

from datetime import date, timedelta

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
    Collects,
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
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
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


def _closing_state(name: str) -> State:
    return State(
        name=name,
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )


def test_closing_state_with_outgoing_transition_errors() -> None:
    """A closing state is a sink — an outgoing transition is an ERROR
    (previously implicit; now explicit since closing states are resting)."""
    sm = StateMachine(name="t")
    sm.states["done"] = _closing_state("done")
    sm.states["after"] = State(
        name="after",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        issue_types=("bug",),
    )
    sm.transitions.append(
        Transition(
            source="done",
            destination="after",
            label="reopen (external)",
            transition_type=TransitionType.EVENT,
        )
    )
    findings = validate_state_machine(sm, catalog=None, grants={})
    sink = [
        f for f in findings if "closing state" in f.message.lower() and "sink" in f.message.lower()
    ]
    assert sink, "Expected a sink-invariant finding for the closing state"
    assert all(f.severity is Severity.ERROR for f in sink)


def test_closing_state_as_pure_sink_passes() -> None:
    sm = StateMachine(name="t")
    sm.states["done"] = _closing_state("done")
    findings = validate_state_machine(sm, catalog=None, grants={})
    assert not [
        f for f in findings if "closing state" in f.message.lower() and "sink" in f.message.lower()
    ]


def test_closes_mutually_exclusive_with_other_annotations() -> None:
    """A closing state can't also be an entry (`is_initial`), a collector
    (`collects`), a handoff, or carry `issue_types` (ADR-0002)."""
    from workflow.core.model.state_machine import Collects

    cases = {
        "is_initial": dict(is_initial=True),
        "collects": dict(collects=Collects(from_states=("x",))),
        "handoff": dict(handoff=True),
        "issue_types": dict(issue_types=("bug",)),
    }
    for label, extra in cases.items():
        sm = StateMachine(name="t")
        sm.states["done"] = State(
            name="done",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.IRREVERSIBLE,
            closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
            **extra,
        )
        findings = validate_state_machine(sm, catalog=None, grants={})
        excl = [f for f in findings if "closes" in f.message.lower() and label in f.message]
        assert excl, f"Expected exclusivity finding for closes+{label}"
        assert all(f.severity is Severity.ERROR for f in excl)


def test_closes_forbids_spawn_advance_on() -> None:
    """A spawn on a closing state can't carry `advance_on` — the parent is
    already closed, so there's nothing to advance."""
    from workflow.core.model.state_machine import Spawn

    sm = StateMachine(name="t")
    sm.states["done"] = State(
        name="done",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        closes=Closes(taxonomy=ClosureTaxonomy.SUPERSEDED, reason="completed"),
        spawns=(
            Spawn(issue_type="chore", initial_state="ready", advance_on=(("shipped", "next"),)),
        ),
    )
    findings = validate_state_machine(sm, catalog=None, grants={})
    excl = [f for f in findings if "advance_on" in f.message and "closing" in f.message.lower()]
    assert excl, "Expected an advance_on-on-closing finding"
    assert all(f.severity is Severity.ERROR for f in excl)


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
    workflow.transitions.extend(
        [
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
        ]
    )
    findings = validate_state_machine(workflow, catalog=None, grants={})
    multi_source = [
        f
        for f in findings
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
    workflow.transitions.extend(
        [
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
        ]
    )
    findings = validate_state_machine(workflow, catalog=None, grants={})
    assert not any("multiple source states" in f.message for f in findings)


def test_audit_with_irreversible_destination_errors() -> None:
    """Audit-default-level on a gate that lands on an irreversible state.
    Reversibility is derived from the destination state via the gated
    transition, so the state machine and the gate transition must exist."""
    workflow = StateMachine(name="t")
    workflow.states["src"] = State(name="src", state_class=StateClass.WORKING)
    workflow.states["irrev_dst"] = State(
        name="irrev_dst",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
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
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["gate"] = HumanGate(
        gate_name="gate",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK, HumanGateLevel.AUDIT],
        default_level=HumanGateLevel.AUDIT,
    )
    findings = validate_state_machine(workflow, catalog, {})
    errors = [f for f in findings if f.severity is Severity.ERROR]
    assert errors
    assert any(f.principle_cite == "hitl-principles.md#4" for f in errors)


def test_agent_prepares_missing_warns() -> None:
    workflow = StateMachine(name="t")
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["gate"] = HumanGate(
        gate_name="gate",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK],
        default_level=HumanGateLevel.BLOCK,
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
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["gate_b"] = HumanGate(
        gate_name="gate_b",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK],
        default_level=HumanGateLevel.BLOCK,
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
            current_level=HumanGateLevel.AUDIT,
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


def _collector_workflow(collects: Collects) -> StateMachine:
    """Build a small collector workflow (single resting state with collects)."""
    sm = StateMachine(name="collector_proc")
    sm.states["accumulating"] = State(
        name="accumulating",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        collects=collects,
    )
    return sm


def _source_workflow_with_closing_staged() -> StateMachine:
    """A minimal source workflow with a `staged` closing state."""
    sm = StateMachine(name="src_proc")
    sm.states["draft"] = State(
        name="draft",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    sm.states["staged"] = State(
        name="staged",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    return sm


def test_collects_unknown_process_errors() -> None:
    parent = _collector_workflow(Collects(process="ghost", from_states=("staged",)))
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"collector_proc": parent},
    )
    assert any(
        "collects.process" in f.message and "not a known process" in f.message for f in findings
    )


def test_collects_unknown_from_state_errors() -> None:
    parent = _collector_workflow(Collects(process="src_proc", from_states=("nope",)))
    src = _source_workflow_with_closing_staged()
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"collector_proc": parent, "src_proc": src},
    )
    assert any(
        "collects.from_states" in f.message and "not declared" in f.message for f in findings
    )


def test_collects_working_from_state_errors() -> None:
    parent = _collector_workflow(Collects(process="src_proc", from_states=("doing",)))
    src = StateMachine(name="src_proc")
    src.states["doing"] = State(
        name="doing",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("bug",),
    )
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"collector_proc": parent, "src_proc": src},
    )
    assert any("Collect only from resting or closing state" in f.message for f in findings)


def test_entry_with_collects_on_same_state_errors() -> None:
    """A state declaring `collects` cannot also have `is_initial=True` —
    the two describe contradictory provenance."""
    sm = StateMachine(name="bad")
    sm.states["accumulating"] = State(
        name="accumulating",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        is_initial=True,
        initial_label="new",
        collects=Collects(process="src", from_states=("staged",)),
    )
    src = StateMachine(name="src")
    src.states["staged"] = State(
        name="staged",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    findings = validate_state_machine(
        sm,
        catalog=None,
        sibling_machines={"bad": sm, "src": src},
    )
    assert any(
        f.severity is Severity.ERROR and "contradictory entry paths" in f.message for f in findings
    )


def test_entry_with_inbound_spawn_target_errors() -> None:
    """A state that is the initial_state of an inbound spawn cannot also
    have `is_initial=True`."""
    from workflow.core.model.state_machine import Spawn

    target = StateMachine(name="target")
    target.states["queue"] = State(
        name="queue",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        is_initial=True,
        initial_label="created",
    )
    parent = StateMachine(name="parent")
    parent.states["working_state"] = State(
        name="working_state",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("bug",),
        spawns=(
            Spawn(
                process="target",
                issue_type="bug",
                initial_state="queue",
            ),
        ),
    )
    findings = validate_state_machine(
        target,
        catalog=None,
        sibling_machines={"target": target, "parent": parent},
    )
    assert any(f.severity is Severity.ERROR and "spawn target" in f.message for f in findings)


def test_orphan_process_warns() -> None:
    """A process with no `[*]→`, no inbound spawn, no shared handoff is
    unreachable — the validator surfaces a WARNING."""
    orphan = StateMachine(name="orphan")
    orphan.states["resting"] = State(
        name="resting",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    findings = validate_state_machine(
        orphan,
        catalog=None,
        sibling_machines={"orphan": orphan},
    )
    assert any(f.severity is Severity.WARNING and "cannot reach it" in f.message for f in findings)


def test_process_with_initial_state_does_not_warn() -> None:
    sm = StateMachine(name="entry_proc")
    sm.states["raw"] = State(
        name="raw",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        is_initial=True,
        initial_label="issue created",
    )
    findings = validate_state_machine(
        sm,
        catalog=None,
        sibling_machines={"entry_proc": sm},
    )
    assert not any("cannot reach it" in f.message for f in findings)


def test_process_reached_via_spawn_does_not_warn() -> None:
    from workflow.core.model.state_machine import Spawn

    target = StateMachine(name="child_proc")
    target.states["queue"] = State(
        name="queue",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    parent = StateMachine(name="parent_proc")
    parent.states["working_state"] = State(
        name="working_state",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("bug",),
        spawns=(
            Spawn(
                process="child_proc",
                issue_type="bug",
                initial_state="queue",
            ),
        ),
    )
    parent.states["entry"] = State(
        name="entry",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        is_initial=True,
    )
    findings = validate_state_machine(
        target,
        catalog=None,
        sibling_machines={"child_proc": target, "parent_proc": parent},
    )
    assert not any("cannot reach it" in f.message for f in findings)


def test_collects_valid_passes() -> None:
    parent = _collector_workflow(Collects(process="src_proc", from_states=("staged",)))
    src = _source_workflow_with_closing_staged()
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"collector_proc": parent, "src_proc": src},
    )
    # No findings cite the `collects` checks for this happy path.
    assert not any("collects" in f.message for f in findings)


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
            spawns=(
                Spawn(
                    process="child",
                    issue_type="bug",
                    initial_state="queue",
                    advance_on=(("done", "doing"),),
                ),
            ),
        ),
        "doing": State(
            name="doing",
            state_class=StateClass.WORKING,
            roles=("worker",),
            issue_types=("bug",),
        ),
    }
    # Child process with the right type + initial state + closing state.
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
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.IRREVERSIBLE,
            closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
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


def test_multi_spawn_duplicate_issue_type_initial_state_errors() -> None:
    """Two rules on the same state may not share both issue_type AND
    initial_state — the CLI would have no way to disambiguate them."""
    from workflow.core.model.state_machine import Spawn

    parent = StateMachine(name="parent")
    parent.states["working_state"] = State(
        name="working_state",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("bug",),
        spawns=(
            Spawn(process="target", issue_type="bug", initial_state="queue"),
            Spawn(process="target", issue_type="bug", initial_state="queue"),
        ),
    )
    target = StateMachine(name="target")
    target.states["queue"] = State(
        name="queue",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"parent": parent, "target": target},
    )
    assert any(
        f.severity is Severity.ERROR and "two `spawns` entries share" in f.message for f in findings
    )


def test_multi_spawn_advance_on_targets_must_agree() -> None:
    """Across all spawn rules on one state, every advance_on value must
    point at the SAME parent-next-state — the wait-for-all cascade
    cannot pick between alternatives."""
    from workflow.core.model.state_machine import Spawn

    parent = StateMachine(name="parent")
    parent.states["working_state"] = State(
        name="working_state",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("bug", "feat"),
        spawns=(
            Spawn(
                process="target",
                issue_type="bug",
                initial_state="queue_a",
                advance_on=(("done", "next_a"),),
            ),
            Spawn(
                process="target",
                issue_type="feat",
                initial_state="queue_b",
                advance_on=(("done", "next_b"),),  # disagrees
            ),
        ),
    )
    parent.states["next_a"] = State(
        name="next_a",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    parent.states["next_b"] = State(
        name="next_b",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    target = StateMachine(name="target")
    target.states = {
        "queue_a": State(
            name="queue_a",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
        ),
        "queue_b": State(
            name="queue_b",
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
        ),
    }
    findings = validate_state_machine(
        parent,
        catalog=None,
        sibling_machines={"parent": parent, "target": target},
    )
    assert any(
        f.severity is Severity.ERROR
        and "advance_on" in f.message
        and "disagree" in f.message.lower()
        for f in findings
    )
