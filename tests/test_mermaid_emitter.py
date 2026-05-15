"""Mermaid emitter tests — visualization of canonical workflow JSON."""

from __future__ import annotations

from pathlib import Path

from workflow.core.emitter import emit_mermaid
from workflow.core.parser.state_machine import parse_state_machine


def test_emit_is_deterministic(refinement_workflow_path: Path) -> None:
    """Two emits of the same JSON produce identical text."""
    workflow = parse_state_machine(refinement_workflow_path)
    assert emit_mermaid(workflow) == emit_mermaid(workflow)


def test_emit_includes_cross_process_legend(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "%% Cross-process interfaces:" in text
    assert "to process inner-loop" in text


def test_emit_terminal_sink_has_taxonomy(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "wont_fix --> [*]: terminal (abandoned)" in text
    assert "duplicate --> [*]: terminal (deduplicated)" in text


def test_emit_inner_loop_renders_variation_groups(
    inner_loop_workflow_path: Path,
) -> None:
    text = emit_mermaid(parse_state_machine(inner_loop_workflow_path))
    # The example has four variation groups; each has its own implementing
    # state reached via a CLAIM from a ready_for_* state.
    assert "ready_for_dev --> implementing" in text
    assert "ready_for_experiment --> implementing_experiment" in text
    assert "ready_for_spike --> implementing_spike" in text
    assert "ready_for_hotfix --> implementing_hotfix" in text


def test_process_map_lists_all_processes_and_handoffs(
    workflow_dir: Path,
) -> None:
    """The process map auto-includes every discovered process as a node and
    every cross-process transition as an edge."""
    from workflow.config import build_registry
    from workflow.core.emitter import emit_process_map

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    processes = [registry.get_process(name) for name in registry.discovered_processes()]
    text = emit_process_map(processes)

    # Every process appears as a node.
    for p in processes:
        # node ids use underscores instead of hyphens
        node_id = p.process_name.replace("-", "_")
        assert f"{node_id}({p.process_name})" in text

    # Known shared handoffs render with `==>`:
    assert "refinement ==>|ready_for_dev| inner_loop" in text
    assert "mitigation ==>|ready_for_hotfix| inner_loop" in text
    assert "inner_loop ==>|ready_bounced| refinement" in text

    # Known spawn handoffs render with `-.->`:
    assert "incident_response -.->|pending| postmortem" in text
    # Refinement spawns a spike issue on inner-loop (entry-only, no exit
    # on refinement side — still surfaces on the map).
    assert "refinement -.->|ready_for_spike| inner_loop" in text
