"""Docs emitter tests — the generated per-process markdown."""

from __future__ import annotations

from workflow.core.emitter import ProcessDocInput, emit_process_doc
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


def _gated_process() -> tuple[StateMachine, HumanGateCatalog]:
    sm = StateMachine(name="t")
    sm.states["working"] = State(name="working", state_class=StateClass.WORKING)
    sm.states["released"] = State(
        name="released",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.IRREVERSIBLE,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    sm.transitions.append(
        Transition(
            source="working",
            destination="released",
            label="agent releases",
            transition_type=TransitionType.ADVANCE,
            gate_name="release_gate",
        )
    )
    catalog = HumanGateCatalog(process_name="t")
    catalog.entries["release_gate"] = HumanGate(
        gate_name="release_gate",
        gate_type=HumanGateType.AUTHORITY,
        allowed_levels=[HumanGateLevel.BLOCK],
        default_level=HumanGateLevel.BLOCK,
        agent_prepares_path="x.md",
    )
    return sm, catalog


def test_transitions_table_renders_gate_name_not_repr() -> None:
    """Regression: the gate cell was shadowed by the HumanGate dataclass,
    rendering `HumanGate(gate_name=...)` reprs into the markdown table."""
    sm, catalog = _gated_process()
    doc = emit_process_doc(ProcessDocInput(state_machine=sm, catalog=catalog))
    assert "HumanGate(" not in doc
    table_rows = [
        line for line in doc.splitlines() if line.startswith("| `working` | `released` |")
    ]
    assert table_rows, "Expected the gated transition row in the transitions table"
    assert "release_gate" in table_rows[0]
    assert "block" in table_rows[0]
