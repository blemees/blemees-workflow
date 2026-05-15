"""StateMachine JSON parser tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.model.state_machine import (
    ReversibilityClass,
    StateClass,
    TerminalTaxonomy,
    TransitionType,
)
from workflow.core.parser.state_machine import parse_state_machine
from workflow.errors import ParseError


def _minimal() -> dict:
    return {
        "name": "t",
        "states": {
            "a": {"class": "resting", "claim_role": "pm"},
            "b": {"class": "working"},
            "c": {"class": "terminal", "terminal_taxonomy": "shipped"},
        },
        "transitions": [
            {"source": "[*]", "destination": "a", "type": "external", "label": "in"},
            {"source": "a", "destination": "b", "type": "claim", "label": "pm claims a"},
            {"source": "b", "destination": "c", "type": "role_action", "label": "pm ships"},
        ],
    }


def test_parses_minimal_workflow() -> None:
    workflow = parse_state_machine(json.dumps(_minimal()))
    assert workflow.name == "t"
    assert workflow.states["a"].state_class is StateClass.RESTING
    assert workflow.states["a"].claim_role == "pm"
    assert workflow.states["b"].state_class is StateClass.WORKING
    assert workflow.states["c"].state_class is StateClass.TERMINAL
    assert workflow.states["c"].terminal_taxonomy is TerminalTaxonomy.SHIPPED
    assert len(workflow.transitions) == 3
    assert workflow.transitions[1].transition_type is TransitionType.CLAIM


def test_terminal_requires_taxonomy() -> None:
    bad = _minimal()
    bad["states"]["c"] = {"class": "terminal"}
    with pytest.raises(ParseError, match="terminal_taxonomy"):
        parse_state_machine(json.dumps(bad))


def test_non_terminal_rejects_taxonomy() -> None:
    bad = _minimal()
    bad["states"]["a"]["terminal_taxonomy"] = "shipped"
    with pytest.raises(ParseError, match="only valid for"):
        parse_state_machine(json.dumps(bad))


def test_unknown_state_class_rejected() -> None:
    bad = _minimal()
    bad["states"]["a"]["class"] = "halfway"
    with pytest.raises(ParseError, match="class"):
        parse_state_machine(json.dumps(bad))


def test_unknown_transition_type_rejected() -> None:
    bad = _minimal()
    bad["transitions"][0]["type"] = "wibble"
    with pytest.raises(ParseError, match="type"):
        parse_state_machine(json.dumps(bad))


def test_unknown_endpoint_state_rejected() -> None:
    bad = _minimal()
    bad["transitions"].append(
        {"source": "x", "destination": "b", "type": "role_action", "label": "noop"}
    )
    with pytest.raises(ParseError, match="not a declared state"):
        parse_state_machine(json.dumps(bad))


def test_hitl_requires_gate() -> None:
    bad = _minimal()
    bad["transitions"][2]["hitl"] = True
    with pytest.raises(ParseError, match="gate"):
        parse_state_machine(json.dumps(bad))


def test_gate_without_hitl_rejected() -> None:
    bad = _minimal()
    bad["transitions"][2]["gate"] = "ship"
    with pytest.raises(ParseError, match="only valid on hitl"):
        parse_state_machine(json.dumps(bad))


def test_cross_process_requires_metadata() -> None:
    bad = _minimal()
    bad["transitions"].append(
        {"source": "c", "destination": "[*]", "type": "cross_process", "label": "to x"}
    )
    with pytest.raises(ParseError, match="cross_process"):
        parse_state_machine(json.dumps(bad))


def test_cross_process_kind_validated() -> None:
    spec = _minimal()
    spec["transitions"].append(
        {
            "source": "c",
            "destination": "[*]",
            "type": "cross_process",
            "label": "to x",
            "cross_process": {"kind": "elsewhere", "process": "x"},
        }
    )
    with pytest.raises(ParseError, match="kind"):
        parse_state_machine(json.dumps(spec))


def test_gates_in_legend_built_from_hitl_transitions() -> None:
    spec = _minimal()
    # Make b → c a HITL transition with reversibility on c.
    spec["states"]["c"]["reversibility"] = "reversible-slow"
    spec["transitions"][2]["hitl"] = True
    spec["transitions"][2]["gate"] = "ship"
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.gates_in_legend == {"ship": ReversibilityClass.REVERSIBLE_SLOW}


def test_parses_real_refinement_workflow(refinement_workflow_path: Path) -> None:
    workflow = parse_state_machine(refinement_workflow_path)
    assert workflow.name == "refinement"
    assert "raw" in workflow.states
    assert "ready_for_dev" in workflow.states
    assert "ready_for_dev" in workflow.gates_in_legend
    assert "wont_fix" in workflow.gates_in_legend
    cross = [t for t in workflow.transitions if t.transition_type is TransitionType.CROSS_PROCESS]
    assert len(cross) == 1
    assert cross[0].cross_process_kind == "shared"
    assert cross[0].cross_process_other == "inner-loop"


def test_parses_real_inner_loop_workflow(inner_loop_workflow_path: Path) -> None:
    workflow = parse_state_machine(inner_loop_workflow_path)
    assert workflow.name == "inner-loop"
    assert workflow.states["ready_for_dev"].claim_role == "developer"
    assert workflow.states["ready_for_dev"].state_class is StateClass.RESTING
    assert workflow.gates_in_legend == {
        "merge_to_main": ReversibilityClass.REVERSIBLE_SLOW,
        "bounce_back": ReversibilityClass.REVERSIBLE_FAST,
    }
