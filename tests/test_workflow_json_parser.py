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
            "a": {"class": "resting", "reversibility": "reversible-fast"},
            "b": {
                "class": "working",
                "roles": ["product-manager"],
                "issue_types": ["bug"],
            },
            "c": {
                "class": "terminal",
                "reversibility": "reversible-fast",
                "terminal_taxonomy": "shipped",
                "close_reason": "completed",
            },
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
    assert workflow.states["a"].roles == ()
    assert workflow.states["b"].state_class is StateClass.WORKING
    assert workflow.states["b"].roles == ("product-manager",)
    assert workflow.states["c"].state_class is StateClass.TERMINAL
    assert workflow.states["c"].terminal_taxonomy is TerminalTaxonomy.SHIPPED
    assert len(workflow.transitions) == 3
    assert workflow.transitions[1].transition_type is TransitionType.CLAIM


def test_terminal_requires_taxonomy() -> None:
    bad = _minimal()
    bad["states"]["c"] = {"class": "terminal", "reversibility": "reversible-fast"}
    with pytest.raises(ParseError, match="terminal_taxonomy"):
        parse_state_machine(json.dumps(bad))


def test_resting_state_requires_reversibility() -> None:
    bad = _minimal()
    del bad["states"]["a"]["reversibility"]
    with pytest.raises(ParseError, match="reversibility.*required"):
        parse_state_machine(json.dumps(bad))


def test_terminal_state_requires_reversibility() -> None:
    bad = _minimal()
    del bad["states"]["c"]["reversibility"]
    with pytest.raises(ParseError, match="reversibility.*required"):
        parse_state_machine(json.dumps(bad))


def test_working_state_rejects_reversibility() -> None:
    bad = _minimal()
    bad["states"]["b"]["reversibility"] = "reversible-fast"
    with pytest.raises(ParseError, match="not valid on working"):
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
    """The shipped refinement example parses, derives its accepted types
    from its working states, and carries cross-process transitions to
    inner-loop (shared) plus an entry from inner-loop for bounced issues."""
    workflow = parse_state_machine(refinement_workflow_path)
    assert workflow.name == "refinement"
    assert "raw" in workflow.states
    assert "ready_for_dev" in workflow.states
    assert "ready_bounced" in workflow.states
    assert "wont_fix" in workflow.states
    assert "duplicate" in workflow.states
    # Derived umbrella = union of working states' issue_types.
    assert set(workflow.accepted_issue_types) >= {"bug", "feature"}
    cross = [t for t in workflow.transitions if t.transition_type is TransitionType.CROSS_PROCESS]
    # At minimum: entry from inner-loop, exit to inner-loop (×2 — ready_for_dev + ready_for_experiment).
    assert len(cross) >= 3
    targets = {t.cross_process_other for t in cross}
    assert "inner-loop" in targets


def test_pr_process_accepts_pr_issue_type(workflow_dir: Path) -> None:
    """The PR process's working states accept the `pr` type; the derived
    umbrella reflects it."""
    workflow = parse_state_machine(workflow_dir / "pr-states.json")
    assert workflow.name == "pr"
    assert workflow.accepted_issue_types == ["pr"]


def test_top_level_issue_types_rejected() -> None:
    bad = _minimal()
    bad["issue_types"] = ["bug"]
    with pytest.raises(ParseError, match="Top-level.*issue_types"):
        parse_state_machine(json.dumps(bad))


def test_working_state_requires_issue_types() -> None:
    bad = _minimal()
    del bad["states"]["b"]["issue_types"]
    with pytest.raises(ParseError, match="issue_types.*required on working"):
        parse_state_machine(json.dumps(bad))


def test_working_state_requires_roles() -> None:
    bad = _minimal()
    del bad["states"]["b"]["roles"]
    with pytest.raises(ParseError, match="roles.*required on working"):
        parse_state_machine(json.dumps(bad))


def test_terminal_state_requires_close_reason() -> None:
    bad = _minimal()
    del bad["states"]["c"]["close_reason"]
    with pytest.raises(ParseError, match="close_reason.*required on terminal"):
        parse_state_machine(json.dumps(bad))


def test_parses_real_inner_loop_workflow(inner_loop_workflow_path: Path) -> None:
    """Inner-loop has four variation groups (feature/bug/chore, experiment,
    spike, hotfix), each with entry, claim, PR-review, and staged states."""
    workflow = parse_state_machine(inner_loop_workflow_path)
    assert workflow.name == "inner-loop"
    assert workflow.states["ready_for_dev"].state_class is StateClass.RESTING
    # Role-restriction lives on the working state reached by CLAIM, not on
    # the resting queue.
    assert "developer" in workflow.states["implementing"].roles
    assert "implementing" in workflow.states
    assert "implementing_experiment" in workflow.states
    assert "implementing_spike" in workflow.states
    assert "implementing_hotfix" in workflow.states
    # Spike entry is a spawn (new work item starts on inner-loop).
    spike_entry = next(
        (
            t
            for t in workflow.transitions
            if t.source == "[*]"
            and t.destination == "ready_for_spike"
            and t.transition_type is TransitionType.CROSS_PROCESS
        ),
        None,
    )
    assert spike_entry is not None
    assert spike_entry.cross_process_kind == "spawn"
