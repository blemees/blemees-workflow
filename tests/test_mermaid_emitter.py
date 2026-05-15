"""Mermaid emitter tests — visualization of canonical workflow JSON."""

from __future__ import annotations

from pathlib import Path

from workflow.core.emitter import emit_mermaid
from workflow.core.parser.state_machine import parse_state_machine


def test_emit_is_deterministic(refinement_workflow_path: Path) -> None:
    """Two emits of the same JSON produce identical text."""
    workflow = parse_state_machine(refinement_workflow_path)
    assert emit_mermaid(workflow) == emit_mermaid(workflow)


def test_emit_includes_hitl_legend(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "%% HITL gates (canonical:" in text
    assert "ready_for_dev" in text
    assert "reversible-slow" in text


def test_emit_includes_cross_process_legend(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "%% Cross-process interfaces:" in text
    assert "to process inner-loop" in text


def test_emit_terminal_sink_has_taxonomy(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "wont_fix --> [*]: terminal (abandoned)" in text


def test_emit_hitl_marker_on_gated_transitions(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "PM marks ready [hitl]" in text
    assert "PM marks wont-fix [hitl]" in text


def test_emit_inner_loop_renders_intermediate_working_states(
    inner_loop_workflow_path: Path,
) -> None:
    text = emit_mermaid(parse_state_machine(inner_loop_workflow_path))
    # `merging` and `cancelling` are the working-state interludes before
    # merge / cancel terminals (principle 3: claim before working).
    assert "staged --> merging" in text
    assert "merging --> merged" in text
    assert "staged --> cancelling" in text
    assert "cancelling --> abandoned_dev" in text
