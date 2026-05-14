"""Mermaid parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow.core.model.lifecycle import (
    ReversibilityClass,
    StateClass,
    TerminalTaxonomy,
    TransitionType,
)
from workflow.core.parser.mermaid import parse_lifecycle


def test_parse_refinement_lifecycle(refinement_lifecycle_path: Path) -> None:
    lifecycle = parse_lifecycle(refinement_lifecycle_path)

    # Some recognizable states from refinement-lifecycle.mermaid.
    expected_states = {
        "raw",
        "refining",
        "consulting",
        "ready_for_dev",
        "ready_for_experiment",
        "ready_bounced",
        "duplicate",
        "wont_fix",
        "deprioritized",
    }
    assert expected_states.issubset(set(lifecycle.states.keys()))

    # The `raw → refining` transition has the label "PM claims raw" and is a CLAIM.
    pm_claims = [
        t for t in lifecycle.transitions if t.source == "raw" and t.destination == "refining"
    ]
    assert len(pm_claims) == 1
    assert pm_claims[0].transition_type is TransitionType.CLAIM
    assert "PM claims raw" in pm_claims[0].label

    # The `refining → ready_for_dev` transition exists.
    ready_transitions = [
        t
        for t in lifecycle.transitions
        if t.source == "refining" and t.destination == "ready_for_dev"
    ]
    assert ready_transitions, "refining → ready_for_dev transition missing"


def test_terminal_taxonomy_extracted(refinement_lifecycle_path: Path) -> None:
    lifecycle = parse_lifecycle(refinement_lifecycle_path)

    assert "wont_fix" in lifecycle.states
    assert lifecycle.states["wont_fix"].state_class is StateClass.TERMINAL
    assert lifecycle.states["wont_fix"].terminal_taxonomy is TerminalTaxonomy.ABANDONED

    assert lifecycle.states["duplicate"].state_class is StateClass.TERMINAL
    assert lifecycle.states["duplicate"].terminal_taxonomy is TerminalTaxonomy.DEDUPLICATED


def test_pre_hitl_artifact_has_no_gates(refinement_lifecycle_path: Path) -> None:
    # The shipped refinement-lifecycle.mermaid predates the HITL refactor — it
    # has no `[hitl]` markers and no legend. The parser must accept this.
    lifecycle = parse_lifecycle(refinement_lifecycle_path)
    assert lifecycle.gated_transitions() == []
    assert lifecycle.gates_in_legend == {}
    assert lifecycle.canonical_catalog_path is None


HITL_DIAGRAM = """\
stateDiagram-v2
    %% HITL gates (canonical: refinement-process.md § Human control points):
    %%   ready_for_dev      reversible-slow
    %%   experiment-verdict mixed-reversibility (verdict-style)
    %%

    [*] --> raw: issue created
    raw --> refining: PM claims raw
    refining --> ready_for_dev: PM marks ready [hitl]
    ready_for_dev --> [*]: to process inner-loop

    note right of ready_for_dev: reversible-slow
"""


def test_hitl_markers_parsed_from_inline_diagram() -> None:
    lifecycle = parse_lifecycle(HITL_DIAGRAM, name="test-hitl")

    # The legend extracted the canonical-catalog pointer.
    assert lifecycle.canonical_catalog_path == "refinement-process.md § Human control points"

    # And the gate listing.
    assert "ready_for_dev" in lifecycle.gates_in_legend
    assert lifecycle.gates_in_legend["ready_for_dev"] is ReversibilityClass.REVERSIBLE_SLOW
    # mixed-reversibility is omitted (documentation-only marker).

    # The `[hitl]` token is stripped from the label and is_gated is set.
    gated = lifecycle.gated_transitions()
    assert len(gated) == 1
    assert gated[0].source == "refining"
    assert gated[0].destination == "ready_for_dev"
    assert "[hitl]" not in gated[0].label.lower()

    # Reversibility from legend / notes propagates to the state.
    assert lifecycle.states["ready_for_dev"].reversibility is ReversibilityClass.REVERSIBLE_SLOW


def test_cross_process_transitions_typed(refinement_lifecycle_path: Path) -> None:
    lifecycle = parse_lifecycle(refinement_lifecycle_path)
    cross = [t for t in lifecycle.transitions if t.transition_type is TransitionType.CROSS_PROCESS]
    assert cross, "Expected at least one cross-process transition (to/from process X)"


@pytest.mark.parametrize(
    "diagram,expected_type",
    [
        ("a --> b: PM claims raw", TransitionType.CLAIM),
        ("a --> b: PM marks ready", TransitionType.ROLE_ACTION),
        ("a --> b: issue created (external)", TransitionType.EXTERNAL),
        ("a --> b: from process inner-loop", TransitionType.CROSS_PROCESS),
        ("a --> b: to process inner-loop", TransitionType.CROSS_PROCESS),
    ],
)
def test_transition_type_inference(diagram: str, expected_type: TransitionType) -> None:
    full = f"""\
stateDiagram-v2
    {diagram}
"""
    lifecycle = parse_lifecycle(full, name="inference")
    transitions = lifecycle.transitions
    assert transitions
    assert transitions[0].transition_type is expected_type


def test_parse_claim_role_from_inline_note() -> None:
    """Lifecycle notes declare the role that claims a resting state."""
    diagram = """\
stateDiagram-v2
    [*] --> raw
    raw --> refining: PM claims raw
    note left of raw: claim-role=pm
"""
    lifecycle = parse_lifecycle(diagram)
    assert lifecycle.states["raw"].claim_role == "pm"


def test_parse_claim_role_with_quoted_value() -> None:
    """Quoted/wrapped role values are accepted."""
    diagram = """\
stateDiagram-v2
    [*] --> ready_for_dev
    ready_for_dev --> implementing: developer claims issue
    note right of ready_for_dev: claim-role="developer"
"""
    lifecycle = parse_lifecycle(diagram)
    assert lifecycle.states["ready_for_dev"].claim_role == "developer"


def test_claim_role_absent_yields_none() -> None:
    """When no note declares a claim-role, the state's claim_role stays None."""
    diagram = """\
stateDiagram-v2
    [*] --> raw
    raw --> refining: PM claims raw
"""
    lifecycle = parse_lifecycle(diagram)
    assert lifecycle.states["raw"].claim_role is None
