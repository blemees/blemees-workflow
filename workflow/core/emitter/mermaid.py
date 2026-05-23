"""Mermaid emitter — produce a `stateDiagram-v2` rendering of a `StateMachine`.

The output is the visualization artifact: it lives alongside the canonical
`<name>-states.json`, renders in markdown viewers, and is checked in
so PR diffs show diagram changes visually. Authors never edit mermaid
directly — they edit the JSON and regenerate.

The emitter is **deterministic** and round-trip-stable. Two runs of the
same JSON produce byte-identical mermaid. The order of states and
transitions in the output follows the JSON's order.
"""

from __future__ import annotations

from workflow.core.model.state_machine import (
    ReversibilityClass,
    State,
    StateClass,
    StateMachine,
    Transition,
    TransitionType,
)


def emit_mermaid(state_machine: StateMachine) -> str:
    """Render a `StateMachine` as a `stateDiagram-v2` mermaid document.

    The output includes:
    - The cross-process legend (entries + exits) when cross-process
      transitions exist (principle 9).
    - The HITL gate legend with reversibility (principle 11).
    - All transitions in JSON order.
    - Notes for states with roles, reversibility, terminal_taxonomy,
      or free-form notes — placed only when authored. For shared handoff
      states, the receiver carries the note; the sender's side leaves it
      blank (per the convention in `docs/state_machine-authoring.md`).
    """
    lines: list[str] = ["stateDiagram-v2"]

    cross_legend = _emit_cross_process_legend(state_machine)
    if cross_legend:
        lines.extend(cross_legend)
        lines.append("")

    hitl_legend = _emit_hitl_legend(state_machine)
    if hitl_legend:
        lines.extend(hitl_legend)
        lines.append("")

    for t in state_machine.transitions:
        lines.append(f"    {_emit_transition(t, state_machine)}")

    # Auto-emit terminal sinks for any TERMINAL state that has no
    # authored outgoing transition. The taxonomy is shown in the label so
    # the diagram visually classifies the terminal.
    sourced = {t.source for t in state_machine.transitions}
    for state in state_machine.states.values():
        if state.state_class is not StateClass.TERMINAL:
            continue
        if state.name in sourced:
            continue
        if state.terminal_taxonomy is None:
            lines.append(f"    {state.name} --> [*]")
        else:
            lines.append(
                f"    {state.name} --> [*]: terminal ({state.terminal_taxonomy.value})"
            )

    note_lines = _emit_notes(state_machine)
    if note_lines:
        lines.append("")
        lines.extend(note_lines)

    return "\n".join(lines) + "\n"


def _emit_cross_process_legend(state_machine: StateMachine) -> list[str]:
    handoffs = [s for s in state_machine.states.values() if s.handoff]
    spawners = [s for s in state_machine.states.values() if s.spawns is not None]
    if not handoffs and not spawners:
        return []

    lines: list[str] = ["    %% Cross-process interfaces:"]
    for s in handoffs:
        lines.append(f"    %%   Handoff: {s.name} (shared resting state)")
    for s in spawners:
        sp = s.spawns
        assert sp is not None
        lines.append(
            f"    %%   Spawn:   {s.name} → process {sp.process} "
            f"(issue_type={sp.issue_type}, initial={sp.initial_state})"
        )
    lines.append("    %%")
    return lines


def _emit_hitl_legend(state_machine: StateMachine) -> list[str]:
    if not state_machine.gates_in_legend:
        return []
    pointer = f"{state_machine.name}-hcps.json"
    lines = [f"    %% HITL gates (canonical: {pointer}):"]
    width = max(len(g) for g in state_machine.gates_in_legend) + 1
    for gate in state_machine.gates_in_legend:
        rev = state_machine.gates_in_legend[gate].value
        lines.append(f"    %%   {gate.ljust(width)} {rev}")
    lines.append("    %%")
    return lines


def _emit_transition(t: Transition, state_machine: StateMachine) -> str:
    label_parts: list[str] = []
    if t.label:
        label_parts.append(t.label)
    if t.is_gated:
        label_parts.append("[hitl]")

    # Terminal sinks include the taxonomy in the label for visual clarity.
    if t.destination == "[*]" and t.source in state_machine.states:
        src = state_machine.states[t.source]
        if src.state_class is StateClass.TERMINAL and src.terminal_taxonomy is not None:
            taxonomy = f"terminal ({src.terminal_taxonomy.value})"
            if not t.label:
                label_parts = [taxonomy]
            else:
                label_parts.append(taxonomy)

    body = " ".join(label_parts).strip()
    if body:
        return f"{t.source} --> {t.destination}: {body}"
    return f"{t.source} --> {t.destination}"


def _emit_notes(state_machine: StateMachine) -> list[str]:
    lines: list[str] = []
    for state in state_machine.states.values():
        text = _state_note_text(state, state_machine)
        if text is None:
            continue
        side = _choose_note_side(state, state_machine)
        lines.append(f"    note {side} of {state.name}: {text}")
    return lines


def _state_note_text(state: State, state_machine: StateMachine) -> str | None:
    """Build the per-state note text from typed fields + free-form notes.

    Returns None when the state has no metadata worth showing. Shared
    handoff states whose receiver-side metadata is missing get no note on
    the sender's side (per convention)."""
    parts: list[str] = []
    if state.roles:
        if len(state.roles) == 1:
            parts.append(f"role={state.roles[0]}")
        else:
            parts.append(f"roles={', '.join(state.roles)}")
    if state.issue_types:
        parts.append(f"types={', '.join(state.issue_types)}")
    if state.handoff:
        parts.append("handoff")
    # Reversibility on the state node only when it isn't already covered by
    # the HITL legend (the legend's reversibility column propagates back to
    # the state from the parser's view; emit on the state when there's no
    # legend entry for it).
    legend_has = state.name in state_machine.gates_in_legend
    if state.reversibility is not None and not legend_has:
        parts.append(_reversibility_token(state.reversibility))
    # Free-form notes from JSON come after structured metadata.
    if state.notes:
        parts.extend(state.notes)
    if not parts:
        return None
    return ", ".join(parts)


def _reversibility_token(rev: ReversibilityClass) -> str:
    return rev.value


def _choose_note_side(state: State, state_machine: StateMachine) -> str:
    """Pick a layout-friendly note side for a state.

    Heuristic: states near the top of the diagram (entry side) get
    `left of`, others get `right of`. Mermaid renders these by attaching
    the note to the named side of the state node; the choice is purely
    visual.
    """
    if not state_machine.transitions:
        return "right"
    first_state = next(
        (
            t.destination
            for t in state_machine.transitions
            if t.source == "[*]" and t.destination != "[*]"
        ),
        None,
    )
    return "left" if state.name == first_state else "right"
