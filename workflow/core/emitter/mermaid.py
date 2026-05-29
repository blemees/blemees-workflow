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
    StateClass,
    StateMachine,
    Transition,
    TransitionType,
)


def emit_mermaid(
    state_machine: StateMachine,
    *,
    spawn_targets: set[str] | None = None,
) -> str:
    """Render a `StateMachine` as a `stateDiagram-v2` mermaid document.

    The output includes:
    - The cross-process legend (entries + exits) when cross-process
      transitions exist (principle 9).
    - The HITL gate legend with reversibility (principle 11).
    - All transitions in JSON order.
    - Entry / exit / handoff sinks anchored to the `[*]` sentinel.

    `spawn_targets`, when supplied, is the set of state names in this
    process that are the `initial_state` of some sibling process's
    spawn. Each such state gets a `[*] --> state: spawn` arrow on the
    diagram so the reader sees "issues arrive here from another
    process's spawn". The CLI computes this set per-process from the
    registry; tests can omit it.

    Per-state metadata (roles, issue_types, reversibility,
    terminal_taxonomy, handoff flag) is intentionally NOT rendered
    on the diagram — it would clutter the layout. The per-process
    markdown (`<process>.md`) carries the full states table, which is
    the right place for that detail.
    """
    spawn_targets = spawn_targets or set()
    lines: list[str] = ["stateDiagram-v2", "    direction TB"]

    cross_legend = _emit_cross_process_legend(state_machine)
    if cross_legend:
        lines.extend(cross_legend)
        lines.append("")

    hitl_legend = _emit_hitl_legend(state_machine)
    if hitl_legend:
        lines.extend(hitl_legend)
        lines.append("")

    # External entry edges (`[*] --> state`) are derived from each state's
    # `is_initial` flag. Emitted before the authored transitions so the
    # diagram reads entry → body → exit.
    for state in state_machine.states.values():
        if not state.is_initial:
            continue
        if state.initial_label:
            lines.append(f"    [*] --> {state.name}: {state.initial_label}")
        else:
            lines.append(f"    [*] --> {state.name}")

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

    # Auto-emit handoff arrows for each `handoff: true` state. The
    # direction(s) emitted depend on this process's role:
    #   - sender side    (no local claim out) → `state --> [*]: handoff`
    #   - receiver side  (no local advance in) → `[*] --> state: handoff`
    # A state can be both at once (silent declaration or genuinely
    # bidirectional handoff); both arrows render. A state that's neither
    # (local advance in AND local claim out) is fully internal — no
    # handoff arrows needed beyond its in-process transitions.
    claims_out_of: set[str] = {
        t.source
        for t in state_machine.transitions
        if t.transition_type is TransitionType.CLAIM
    }
    advances_into: set[str] = {
        t.destination
        for t in state_machine.transitions
        if t.transition_type
        in (TransitionType.ADVANCE, TransitionType.EVENT)
    }
    for state in state_machine.states.values():
        if not state.handoff:
            continue
        if state.name not in advances_into:
            # Receiver side: issue arrives at this state from outside.
            lines.append(f"    [*] --> {state.name}: handoff")
        if state.name not in claims_out_of:
            # Sender side: issue leaves this state to another process.
            lines.append(f"    {state.name} --> [*]: handoff")

    # Spawn-target arrows. Sibling processes that spawn into this
    # process declare an `initial_state`; from this process's diagram
    # the issue arrives at that state from outside (the spawning
    # parent process), so an `[*] -->` arrow visually anchors it.
    for state_name in sorted(spawn_targets):
        if state_name in state_machine.states:
            lines.append(f"    [*] --> {state_name}: spawn")

    # Collect entry arrows. States declaring `collects` are entered via
    # `create-issue --to <state>` (with the contributor refs); the
    # collector issue materializes at this state. Visually that's an
    # entry from outside the local transitions.
    for state in state_machine.states.values():
        if state.collects is not None:
            lines.append(f"    [*] --> {state.name}: collect")

    return "\n".join(lines) + "\n"


def _emit_cross_process_legend(state_machine: StateMachine) -> list[str]:
    handoffs = [s for s in state_machine.states.values() if s.handoff]
    spawners = [s for s in state_machine.states.values() if s.spawns]
    if not handoffs and not spawners:
        return []

    lines: list[str] = ["    %% Cross-process interfaces:"]
    for s in handoffs:
        lines.append(f"    %%   Handoff: {s.name} (shared resting state)")
    for s in spawners:
        for sp in s.spawns:
            process_label = sp.process or "(derived from initial_state)"
            lines.append(
                f"    %%   Spawn:   {s.name} → process {process_label} "
                f"(issue_type={sp.issue_type}, initial={sp.initial_state})"
            )
    lines.append("    %%")
    return lines


def _emit_hitl_legend(state_machine: StateMachine) -> list[str]:
    if not state_machine.gates_in_legend:
        return []
    pointer = f"{state_machine.name}-human-gates.json"
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

    body = " ".join(label_parts).strip()
    if body:
        return f"{t.source} --> {t.destination}: {body}"
    return f"{t.source} --> {t.destination}"


