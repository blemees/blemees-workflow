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
    # Handoff states appear in the legend as shared resting interfaces.
    assert "Handoff: ready_for_dev" in text


def test_emit_legend_lists_spawns(inner_loop_workflow_path: Path) -> None:
    """Spawns appear in the cross-process legend with `Spawn: <state> →
    process <other>` lines."""
    text = emit_mermaid(parse_state_machine(inner_loop_workflow_path))
    assert "%% Cross-process interfaces:" in text
    assert "Spawn:   implementing → process pr" in text


def test_emit_terminal_sink_has_taxonomy(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "wont_fix --> [*]: terminal (abandoned)" in text
    assert "duplicate --> [*]: terminal (deduplicated)" in text


def test_emit_inner_loop_renders_claim_groups(
    inner_loop_workflow_path: Path,
) -> None:
    text = emit_mermaid(parse_state_machine(inner_loop_workflow_path))
    # Standard PR-producing claims (bug/feature/chore/experiment) all
    # enter via the consolidated `ready_for_dev → implementing` claim —
    # the issue_type carries the variation. Hotfix has its own urgent
    # handoff state (reversible-fast). Spike has its own working state
    # because it doesn't spawn a PR.
    assert "ready_for_dev --> implementing" in text
    assert "ready_for_hotfix --> implementing" in text
    assert "ready_for_spike --> implementing_spike" in text


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

    # Diagram is stateDiagram-v2, top-to-bottom — same family as the
    # per-process state diagrams.
    assert text.startswith("stateDiagram-v2\n")
    assert "direction TB" in text

    # Hyphenated process names get an alias so the v2 parser accepts a
    # clean id while preserving the display name.
    assert 'state "incident-response" as incident_response' in text
    assert 'state "inner-loop" as inner_loop' in text

    # Known handoffs — `ready_for_dev` and `ready_for_experiment`
    # originate in refinement (PM advances into the state); inner-loop
    # claims out of them. `ready_bounced` flows the other way.
    # `ready_for_hotfix` is declared in mitigation without local
    # transitions (mitigation produces the hotfix ticket); inner-loop
    # claims out of it — so the implicit direction is mitigation →
    # inner-loop.
    assert "refinement --> inner_loop: ⇄ ready_for_dev" in text
    assert "inner_loop --> refinement: ⇄ ready_bounced" in text
    assert "mitigation --> inner_loop: ⇄ ready_for_hotfix" in text

    # Subprocess spawn: inner-loop.implementing → pr (one consolidated
    # implementing serves bug/feature/chore/experiment/hotfix).
    assert "inner_loop --> pr: ⤴ implementing→draft" in text
    # Independent spawn: incident-response.stabilized → postmortem.
    assert "incident_response --> postmortem: ⤴ stabilized→pending" in text
    # Collect (inverse of spawn): release has two collector states. `cut`
    # gathers bug/feature/chore/experiment work; `hotfix_cut` gathers
    # only hotfixes. Both pull from inner-loop.staged. The label
    # surfaces the issue-type filter in brackets.
    assert "inner_loop --> release: ⤵ staged→cut [bug,feature,chore,experiment]" in text
    assert "inner_loop --> release: ⤵ staged→hotfix_cut [hotfix]" in text

    # External entry: processes that declare an `is_initial` state
    # contribute an edge from the built-in `[*]` sentinel. `release` is
    # NOT here because its entry points (`cut`, `hotfix_cut`) are
    # `collects` states — entered via `create-issue --to <state>`, not
    # external materialization.
    assert "[*] --> refinement: ▶ raw" in text
    assert "[*] --> incident_response: ▶ declared" in text
    assert "[*] --> release: ▶ cut" not in text
    assert "[*] --> release: ▶ hotfix_cut" not in text

    # External exit: terminal states render as exit edges UNLESS the
    # terminal is named in some sibling's `spawn.advance_on` (those are
    # feedback terminals — the work continues in the parent process).
    assert "refinement --> [*]: ■ wont_fix" in text
    assert "release --> [*]: ■ released" in text
    assert "incident_response --> [*]: ■ stabilized" in text

    # Feedback terminals — these close the child issue but the parent
    # process auto-advances on them, so they're not real workflow exits.
    # `spike_completed` (refinement spawns spike; auto-returns to
    # consult_requested), `staged` (inner-loop's PR-spawn auto-advances
    # to staged), `mitigated` (incident-response's mitigation spawn
    # auto-advances to needs_verification).
    assert "inner_loop --> [*]: ■ spike_completed" not in text
    assert "pr --> [*]: ■ merged" not in text
    assert "mitigation --> [*]: ■ mitigated" not in text

    # Feedback edges — the round-trip of the spawn relationships above.
    # Each `advance_on` mapping renders as `child --> parent: ↺ ...`.
    assert "inner_loop --> refinement: ↺ spike_completed→spike_returned" in text
    assert "pr --> inner_loop: ↺ merged→staged" in text
    assert "mitigation --> incident_response: ↺ mitigated→needs_verification" in text
