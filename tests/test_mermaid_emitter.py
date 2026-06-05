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


def test_emit_closing_sink_uses_exit_symbol(refinement_workflow_path: Path) -> None:
    text = emit_mermaid(parse_state_machine(refinement_workflow_path))
    assert "wont_fix --> [*]: ■ wont_fix" in text
    assert "duplicate --> [*]: ■ duplicate" in text
    assert "closing state (abandoned)" not in text


def test_emit_star_edges_use_process_map_symbols(
    refinement_workflow_path: Path,
    inner_loop_workflow_path: Path,
    workflow_dir: Path,
) -> None:
    """Per-process `[*]` edges use the same symbol prefixes as the process map."""
    from workflow.config import build_registry
    from workflow.core.emitter import OutboundFeedback

    refinement = parse_state_machine(refinement_workflow_path)
    inner_loop = parse_state_machine(inner_loop_workflow_path)

    assert "[*] --> raw: ▶ raw" in emit_mermaid(refinement)
    assert "ready_for_dev --> [*]: ⊙ ready_for_dev" in emit_mermaid(refinement)
    assert "[*] --> ready_bounced: ⊙ ready_bounced" in emit_mermaid(refinement)

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    state_to_process = {
        state_name: process_name
        for process_name in registry.discovered_processes()
        for state_name in registry.get_process(process_name).state_machine.states
    }
    spawn_sources: dict[str, set[str]] = {}
    for process_name in registry.discovered_processes():
        process = registry.get_process(process_name)
        for state in process.state_machine.states.values():
            for sp in state.spawns:
                target_proc = sp.process or state_to_process.get(sp.initial_state)
                if target_proc is None:
                    continue
                spawn_sources.setdefault(sp.initial_state, set()).add(state.name)

    inner_loop_sources = {
        state: sorted(parents)
        for state, parents in spawn_sources.items()
        if state in inner_loop.states
    }
    inner_loop_text = emit_mermaid(
        inner_loop,
        spawn_sources=inner_loop_sources,
        outbound_feedback=(
            OutboundFeedback(
                child_closing_state="spike_completed",
                parent_process="refinement",
                parent_state="spiking",
                parent_next="spike_returned",
                issue_type="spike",
            ),
        ),
    )
    assert "[*] --> ready_for_dev: ▶ ready_for_dev" in inner_loop_text
    assert "[*] --> ready_for_dev: ⊙ ready_for_dev" in inner_loop_text
    assert "[*] --> ready_for_hotfix: ᐉ execute_mitigation" in inner_loop_text
    assert "[*] --> ready_for_spike: ᐉ spiking" in inner_loop_text
    assert "staged --> [*]: ⊙ staged" in inner_loop_text
    assert "ready_bounced --> [*]: ⊙ ready_bounced" in inner_loop_text
    assert "spike_completed --> [*]: ■ spike_completed" not in inner_loop_text


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


def test_emit_spawn_state_notes(inner_loop_workflow_path: Path) -> None:
    inner_loop = parse_state_machine(inner_loop_workflow_path)
    text = emit_mermaid(inner_loop)

    assert "note left of implementing" in text
    assert "ᐉ draft (pr)" in text
    assert "note left of staged" in text
    assert "⊡ merged (pr)" in text
    assert "advance_on:" not in text


def test_emit_spawn_state_notes_for_multi_spawn_closing(workflow_dir: Path) -> None:
    from workflow.config import build_registry

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    postmortem = registry.get_process("postmortem").state_machine
    text = emit_mermaid(postmortem)

    assert "note left of complete" in text
    assert "ᐉ raw (bug)" in text
    assert "ᐉ ready_for_dev (chore)" in text
    assert "ᐉ raw (feature)" in text


def test_emit_spawn_feedback_note_on_advance_target(workflow_dir: Path) -> None:
    from workflow.config import build_registry

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    refinement = registry.get_process("refinement").state_machine
    text = emit_mermaid(refinement)

    assert "note left of spiking" in text
    assert "ᐉ ready_for_spike (spike)" in text
    assert "note left of spike_returned" in text
    assert "⊡ spike_completed (spike)" in text


def test_release_staged_collect_advance_edges(workflow_dir: Path) -> None:
    from workflow.config import build_registry

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    release = registry.get_process("release").state_machine
    text = emit_mermaid(release)

    assert "staged --> shipped: ⊡ released" in text
    assert "staged --> measuring: ⊡ released [experiment]" in text
    assert "staged --> shipped: ⊡ released [*]" not in text
    assert "staged --> [*]: ⧄ abandoned" in text
    assert "staged --> [*]: ⧄ rolled_back" in text


def test_emit_index_doc_lists_workflow_entry_points(workflow_dir: Path) -> None:
    from workflow.config import build_registry
    from workflow.core.emitter import emit_index_doc

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    processes_loaded = [registry.get_process(name) for name in registry.discovered_processes()]
    text = emit_index_doc(
        {p.state_machine.name: p.state_machine.description for p in processes_loaded},
        has_roles=True,
        has_issue_types=True,
        has_human_inputs=True,
        state_machines=[p.state_machine for p in processes_loaded],
    )

    assert "## External entry points" in text
    assert "[`refinement`](./refinement.md) · `raw`" in text
    assert "[`inner-loop`](./inner-loop.md) · `ready_for_dev`" in text
    assert "[`incident-response`](./incident-response.md) · `declared`" in text


def test_emit_process_doc_lists_inbound_interfaces(workflow_dir: Path) -> None:
    from workflow.config import build_registry
    from workflow.core.emitter import (
        InboundSpawn,
        OutboundFeedback,
        ProcessDocInput,
        emit_process_doc,
    )

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    pr = registry.get_process("pr")
    text = emit_process_doc(
        ProcessDocInput(
            state_machine=pr.state_machine,
            inbound_spawns=(
                InboundSpawn(
                    target_state="draft",
                    source_process="inner-loop",
                    source_state="implementing",
                    issue_type="pr",
                ),
            ),
            outbound_feedback=(
                OutboundFeedback(
                    child_closing_state="merged",
                    parent_process="inner-loop",
                    parent_state="implementing",
                    parent_next="staged",
                    issue_type="pr",
                ),
            ),
        )
    )

    assert "## Cross-process interfaces" in text
    # Inbound spawn lands as a row in the Inbound table.
    assert "### Inbound" in text
    assert (
        "| `draft` | ᐉ spawn | [`inner-loop`](./inner-loop.md) · `implementing` "
        "| `pr` issue |"
    ) in text
    # Outbound feedback lands as a row in the Outbound table.
    assert "### Outbound" in text
    assert (
        "| `merged` | ⊡ feedback | [`inner-loop`](./inner-loop.md) | "
        "advances parent to `staged` (spawn from `implementing`, `pr`) |"
    ) in text
    assert "merged --> [*]: ■ merged" not in text
    assert "merged --> [*]: ⊡ staged" in text
    assert "note left of merged" not in text
    assert "## External entry points" not in text


def test_emit_process_doc_lists_external_entry_on_process(workflow_dir: Path) -> None:
    from workflow.config import build_registry
    from workflow.core.emitter import ProcessDocInput, emit_process_doc

    registry = build_registry(workflow_dir=workflow_dir)
    assert registry is not None
    refinement = registry.get_process("refinement")
    text = emit_process_doc(ProcessDocInput(state_machine=refinement.state_machine))

    # External entry lands as an Inbound table row with the ▶ entry kind.
    assert "### Inbound" in text
    assert "| `raw` | ▶ entry | — (external) | `create-issue --to raw`" in text
    # refinement has no inbound spawns when emitted standalone (no siblings) —
    # scope the check to the Inbound table (its outbound `spiking` spawn lives
    # under ### Outbound).
    inbound_section = text.split("### Outbound", 1)[0]
    assert "ᐉ spawn" not in inbound_section


def test_emit_process_doc_omits_entry_points(refinement_workflow_path: Path) -> None:
    from workflow.core.emitter import ProcessDocInput, emit_process_doc
    from workflow.core.parser.state_machine import parse_state_machine

    text = emit_process_doc(
        ProcessDocInput(state_machine=parse_state_machine(refinement_workflow_path))
    )
    assert "## External entry points" not in text


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

    # Diagram is stateDiagram-v2, left-to-right (process map reads as a
    # flow across processes, distinct from per-process diagrams' TB).
    assert text.startswith("stateDiagram-v2\n")
    assert "direction LR" in text

    # Hyphenated process names get an alias so the v2 parser accepts a
    # clean id while preserving the display name.
    assert 'state "incident-response" as incident_response' in text
    assert 'state "inner-loop" as inner_loop' in text

    # Group hint: processes sharing a `group` value render inside a
    # Mermaid composite state block. The shipped examples use
    # `delivery` (refinement/inner-loop/pr/release) and `incident`
    # (incident-response/mitigation/config-change/data-change/postmortem).
    assert 'state "delivery" as delivery {' in text
    assert 'state "incident" as incident {' in text
    # Hyphenated members inside a composite get a nested alias line.
    assert 'state "inner-loop" as inner_loop' in text
    assert 'state "incident-response" as incident_response' in text
    # Non-hyphenated members appear as bare identifiers inside the block.
    # (Bare identifier in indented body — a substring check is fine.)
    assert "        mitigation" in text
    assert "        postmortem" in text
    assert "        refinement" in text

    # Known handoffs — `ready_for_dev` originates in refinement (PM
    # advances into the state); inner-loop claims out of it.
    # `ready_bounced` flows the other way (inner-loop bounces back to
    # refinement). `ready_for_hotfix` is now a spawn target (mitigation
    # spawns hotfix work into inner-loop), so it appears as a spawn
    # edge rather than a handoff edge.
    assert "refinement --> inner_loop: ⊙ ready_for_dev" in text
    assert "inner_loop --> refinement: ⊙ ready_bounced" in text
    assert "mitigation --> inner_loop: ⊙ ready_for_hotfix" not in text
    assert "mitigation --> inner_loop: ᐉ execute_mitigation" in text

    # Subprocess spawn: inner-loop.implementing → pr (one consolidated
    # implementing serves bug/feature/chore/experiment/hotfix).
    assert "inner_loop --> pr: ᐉ implementing" in text
    # Independent spawn: incident-response.stabilized → postmortem.
    assert "incident_response --> postmortem: ᐉ stabilized" in text
    # Handoff: dev tickets cross from inner-loop into release via the
    # shared `staged` state. The release-side collect is intra-process
    # (release.cut / release.hotfix_cut collect from release.staged), so
    # there is no inner_loop → release collect edge anymore.
    assert "inner_loop --> release: ⊙ staged" in text
    assert "inner_loop --> release: ꘜ" not in text

    # External entry: processes that declare an `is_initial` state
    # contribute an edge from the built-in `[*]` sentinel. `release` is
    # NOT here because its entry points (`cut`, `hotfix_cut`) are
    # `collects` states — entered via `create-issue --to <state>`, not
    # external materialization.
    assert "[*] --> refinement: ▶ raw" in text
    assert "[*] --> incident_response: ▶ declared" in text
    assert "[*] --> release: ▶ cut" not in text
    assert "[*] --> release: ▶ hotfix_cut" not in text

    # External exit: closing states render as exit edges UNLESS:
    # (a) the closing state is named in some sibling's `spawn.advance_on`
    #     (feedback closing state — work continues in the parent process),
    # (b) the closing state itself spawns a follow-up issue (superseded —
    #     work continues as a new child item, and the spawn edge
    #     already shows the continuation), or
    # (c) the closing state is named in a `collects.advance_on` /
    #     `release_on` on the same process (collector closing state —
    #     reaching it fans the contributors out, the cascade carries
    #     the work back into the contributor process).
    assert "refinement --> [*]: ■ wont_fix" in text
    # incident-response.stabilized spawns postmortem — suppress sink.
    assert "incident_response --> [*]: ■ stabilized" not in text
    # release.{released,abandoned,rolled_back} are all collector
    # closing states on cut / hotfix_cut — suppress sinks.
    assert "release --> [*]: ■ released" not in text
    assert "release --> [*]: ■ abandoned" not in text
    assert "release --> [*]: ■ rolled_back" not in text
    # release.shipped IS a real exit — it's the contributor target
    # (where dev tickets land after the train ships), not a collector
    # trigger.
    assert "release --> [*]: ■ shipped" in text

    # Feedback closing states — these close the child issue but the parent
    # process auto-advances on them, so they're not real workflow exits.
    # `spike_completed` (refinement spawns spike; auto-returns to
    # consult_requested), `staged` (inner-loop's PR-spawn auto-advances
    # to staged), `mitigated` (incident-response's mitigation spawn
    # auto-advances to needs_verification).
    assert "inner_loop --> [*]: ■ spike_completed" not in text
    assert "pr --> [*]: ■ merged" not in text
    assert "mitigation --> [*]: ■ mitigated" not in text

    # Feedback edges — the round-trip of the spawn relationships above.
    # Spawn-feedback labels show only the parent's next state; collect-
    # feedback labels show only the collector's trigger state.
    assert "inner_loop --> refinement: ⊡ spike_returned" in text
    assert "pr --> inner_loop: ⊡ staged" in text
    assert "mitigation --> incident_response: ⊡ needs_verification" in text
