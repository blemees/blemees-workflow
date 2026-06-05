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

from typing import Protocol, Sequence

from workflow.core.model.state_machine import (
    Spawn,
    StateMachine,
    Transition,
    TransitionType,
)

# Process-map edge symbols — shared with `emit_process_map` in docs.py.
SYMBOL_ENTRY = "▶"
SYMBOL_EXIT = "■"
SYMBOL_HANDOFF = "⊙"
SYMBOL_SPAWN = "ᐉ"
SYMBOL_COLLECT = "ꘜ"
SYMBOL_FEEDBACK = "⊡"
SYMBOL_RELEASE = "⧄"


class _OutboundFeedbackRow(Protocol):
    child_terminal: str
    parent_process: str
    parent_next: str
    issue_type: str


def emit_mermaid(
    state_machine: StateMachine,
    *,
    spawn_sources: dict[str, list[str]] | None = None,
    outbound_feedback: Sequence[_OutboundFeedbackRow] | None = None,
) -> str:
    """Render a `StateMachine` as a `stateDiagram-v2` mermaid document.

    The output includes:
    - The cross-process legend (entries + exits) when cross-process
      transitions exist (principle 9).
    - The HITL gate legend with reversibility (principle 11).
    - All transitions in JSON order.
    - Entry / exit / handoff sinks anchored to the `[*]` sentinel.
    - Spawn notes on states that declare `spawns` (`ᐉ initial_state (type)`).
    - Feedback notes on parent states targeted by local `advance_on`
      (`⊡ child_terminal (type)`).
    - Outbound feedback edges from child terminals that auto-advance a
      parent (`terminal --> [*]: ⊡ parent_next`), instead of `■` exits.
    - Collect-advance edges from contributor states (`from_state -->
      target: ⊡ collector_state`, with `[type]` suffix for per-type rules).

    `spawn_sources`, when supplied, maps each inbound-spawn target state
    to the parent state(s) that spawn into it. Each pair gets a
    `[*] --> state: ᐉ <parent_state>` arrow so the reader sees where
    issues arrive from. The CLI computes this map per-process from the
    registry; tests can omit it.
    """
    spawn_sources = spawn_sources or {}
    outbound = list(outbound_feedback or ())
    feedback_terminals = {row.child_terminal for row in outbound}
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
        lines.append(f"    [*] --> {state.name}: {SYMBOL_ENTRY} {state.name}")

    for t in state_machine.transitions:
        lines.append(f"    {_emit_transition(t, state_machine)}")

    # Auto-emit sinks for any closing state that has no authored outgoing
    # transition, so the diagram visually classifies the exit.
    sourced = {t.source for t in state_machine.transitions}
    for state in state_machine.states.values():
        if not state.is_closing:
            continue
        if state.name in sourced:
            continue
        if state.name in feedback_terminals:
            continue
        lines.append(f"    {state.name} --> [*]: {SYMBOL_EXIT} {state.name}")

    feedback_edges: set[tuple[str, str]] = set()
    for row in outbound:
        if row.child_terminal not in state_machine.states:
            continue
        feedback_edges.add((row.child_terminal, row.parent_next))
    for child_terminal, parent_next in sorted(feedback_edges):
        lines.append(
            f"    {child_terminal} --> [*]: {SYMBOL_FEEDBACK} {parent_next}"
        )

    lines.extend(_emit_collect_advance_edges(state_machine))

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
            lines.append(f"    [*] --> {state.name}: {SYMBOL_HANDOFF} {state.name}")
        if state.name not in claims_out_of:
            # Sender side: issue leaves this state to another process.
            lines.append(f"    {state.name} --> [*]: {SYMBOL_HANDOFF} {state.name}")

    # Spawn-target arrows. Sibling processes that spawn into this
    # process declare an `initial_state`; from this process's diagram
    # the issue arrives at that state from outside (the spawning
    # parent process), so an `[*] -->` arrow visually anchors it.
    for target_state in sorted(spawn_sources):
        if target_state not in state_machine.states:
            continue
        for parent_state in sorted(spawn_sources[target_state]):
            lines.append(
                f"    [*] --> {target_state}: {SYMBOL_SPAWN} {parent_state}"
            )

    # Collect entry arrows. States declaring `collects` are entered via
    # `create-issue --to <state>` (with the contributor refs); the
    # collector issue materializes at this state. Visually that's an
    # entry from outside the local transitions.
    for state in state_machine.states.values():
        if state.collects is not None:
            type_suffix = (
                f" [{','.join(state.collects.issue_types)}]"
                if state.collects.issue_types
                else ""
            )
            lines.append(
                f"    [*] --> {state.name}: {SYMBOL_COLLECT} {state.name}{type_suffix}"
            )

    interface_notes = _emit_interface_notes(state_machine)
    if interface_notes:
        lines.append("")
        lines.extend(interface_notes)

    return "\n".join(lines) + "\n"


def _emit_collect_advance_edges(state_machine: StateMachine) -> list[str]:
    """Emit contributor fan-out edges declared on `collects.advance_on`."""
    # (from_state, target) -> label; prefer untyped default over `[*]`.
    advance_edges: dict[tuple[str, str], str] = {}
    release_edges: dict[tuple[str, str], str] = {}

    for state in state_machine.states.values():
        if state.collects is None:
            continue
        collects = state.collects
        for from_state in collects.from_states:
            if from_state not in state_machine.states:
                continue
            for rule in collects.advance_on:
                if rule.default_target is not None:
                    label = f"{SYMBOL_FEEDBACK} {rule.collector_state}"
                    if rule.by_type:
                        label = f"{label} [*]"
                    key = (from_state, rule.default_target)
                    existing = advance_edges.get(key)
                    if existing is None or ("[*]" in existing and "[*]" not in label):
                        advance_edges[key] = label
                for issue_type, target in rule.by_type:
                    label = f"{SYMBOL_FEEDBACK} {rule.collector_state} [{issue_type}]"
                    key = (from_state, target)
                    advance_edges.setdefault(key, label)
            for collector_state in collects.release_on:
                label = f"{SYMBOL_RELEASE} {collector_state}"
                release_edges.setdefault((from_state, collector_state), label)

    lines = [
        f"    {from_state} --> {target}: {label}"
        for (from_state, target), label in sorted(advance_edges.items())
    ]
    lines.extend(
        f"    {from_state} --> [*]: {label}"
        for (from_state, _collector_state), label in sorted(release_edges.items())
    )
    return lines


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


def _spawn_target_label(sp: Spawn) -> str:
    return f"{SYMBOL_SPAWN} {sp.initial_state} ({sp.issue_type})"


def _feedback_label(child_terminal: str, issue_type: str) -> str:
    return f"{SYMBOL_FEEDBACK} {child_terminal} ({issue_type})"


def _emit_interface_notes(state_machine: StateMachine) -> list[str]:
    """Render spawn / inbound-feedback notes on states."""
    spawn_by_state: dict[str, list[str]] = {}
    inbound_feedback_by_state: dict[str, list[str]] = {}

    for state in state_machine.states.values():
        for sp in state.spawns:
            spawn_by_state.setdefault(state.name, []).append(_spawn_target_label(sp))
            for child_terminal, parent_next in sp.advance_on:
                if parent_next not in state_machine.states:
                    continue
                line = _feedback_label(child_terminal, sp.issue_type)
                existing = inbound_feedback_by_state.setdefault(parent_next, [])
                if line not in existing:
                    existing.append(line)

    lines: list[str] = []
    for state in state_machine.states.values():
        note_lines = (
            spawn_by_state.get(state.name, [])
            + inbound_feedback_by_state.get(state.name, [])
        )
        if note_lines:
            lines.extend(_emit_state_note(state.name, note_lines))
    return lines


def _emit_state_note(state_name: str, note_lines: list[str]) -> list[str]:
    return [
        f"    note left of {state_name}",
        *(f"        {line}" for line in note_lines),
        "    end note",
    ]


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


