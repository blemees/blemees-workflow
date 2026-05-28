"""Markdown documentation emitters — agent/human-readable views of the workflow.

`generate-docs` invokes these to materialise `<process>.md`, `roles.md`,
`issue-types.md`, and `README.md` alongside the canonical JSON. The output
is a flat, link-light reference: everything an agent needs to operate on a
process lives in that process's single markdown file (states, transitions,
human gates, cross-process handoffs, active trust grants), so an LLM can ingest it
without chasing links.

The emitters are pure — they take in-memory model objects and return strings.
No file I/O happens here; the CLI handler writes the files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflow.core.emitter.mermaid import emit_mermaid
from workflow.core.model.human_gate import HumanGateCatalog, HumanGateLevel
from workflow.core.model.human_input import HumanInputDirectory
from workflow.core.model.issue_type import IssueTypeDirectory
from workflow.core.model.role import RoleDirectory
from workflow.core.model.state_machine import (
    StateClass,
    StateMachine,
    TransitionType,
)
from workflow.core.model.trust_grant import TrustGrant


@dataclass(frozen=True)
class ProcessDocInput:
    """Inputs the process-doc emitter needs.

    Bundled into a dataclass so the CLI doesn't have to thread positional
    arguments. All fields except `state_machine` are optional — they're
    rendered as sections when present, omitted otherwise.
    """

    state_machine: StateMachine
    catalog: HumanGateCatalog | None = None
    issue_type_directory: IssueTypeDirectory | None = None
    grants: dict[str, TrustGrant] | None = None
    spawn_targets: frozenset[str] | None = None


def emit_process_doc(inputs: ProcessDocInput) -> str:
    """Render the process documentation as a single markdown string."""
    sm = inputs.state_machine
    out: list[str] = []
    out.append(f"# Process: {sm.name}")
    out.append("")
    if sm.description:
        out.append(sm.description)
        out.append("")
    out.append(f"> Defined in: `{sm.name}-states.json`")
    if inputs.catalog is not None and inputs.catalog.entries:
        out.append(f"> HumanGate catalog: `{sm.name}-human-gates.json`")
    out.append("")

    accepted = sm.accepted_issue_types
    if accepted:
        out.extend(_section_issue_types(accepted, inputs.issue_type_directory))

    out.extend(_section_entry_points(sm))
    out.extend(_section_diagram(sm, spawn_targets=inputs.spawn_targets))
    out.extend(_section_states(sm))
    out.extend(_section_transitions(sm, inputs.catalog, inputs.grants))
    if inputs.catalog is not None and inputs.catalog.entries:
        out.extend(_section_human_gates(sm, inputs.catalog, inputs.grants))
    out.extend(_section_cross_process(sm))

    active_grants = _grants_for_process(sm, inputs.grants)
    if active_grants:
        out.extend(_section_trust_grants(active_grants))

    return "\n".join(out).rstrip() + "\n"


def emit_roles_doc(
    directory: RoleDirectory,
    state_machines: list[StateMachine] | None = None,
) -> str:
    """Render the role directory as markdown.

    When `state_machines` is supplied, the doc gains a derived
    "Participates in" line for each role — every process whose working
    states declare the role in their `roles` list.
    """
    out: list[str] = ["# Roles", ""]
    if not directory.roles:
        out.append("_(no roles defined)_")
        return "\n".join(out) + "\n"

    # Derive role → {process name} participation from state machines.
    participation: dict[str, set[str]] = {}
    for sm in state_machines or []:
        for st in sm.states.values():
            for role in st.roles:
                participation.setdefault(role, set()).add(sm.name)

    out.append("This workflow defines these roles:")
    out.append("")
    for role_id, role in directory.roles.items():
        out.append(f"## `{role_id}` — {role.name}")
        out.append("")
        out.append(role.responsibility)
        out.append("")
        procs = sorted(participation.get(role_id, set()))
        if procs:
            out.append(
                f"- **Participates in**: {', '.join(f'`{p}`' for p in procs)} "
                f"_(derived from working-state `roles`)_"
            )
        if role.does_not:
            out.append(f"- **Does not**: {', '.join(role.does_not)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_issue_types_doc(directory: IssueTypeDirectory) -> str:
    """Render the issue-type directory as markdown."""
    out: list[str] = ["# Issue types", ""]
    if not directory.types:
        out.append("_(no issue types defined)_")
        return "\n".join(out) + "\n"
    out.append(
        "Each process declares which of these it accepts via its "
        "`issue_types` field. Type is set at creation and immutable."
    )
    out.append("")
    for type_id, t in directory.types.items():
        out.append(f"## `{type_id}` — {t.name}")
        out.append("")
        out.append(t.description)
        out.append("")
        bits: list[str] = []
        if t.github_entity == "pull_request":
            bits.append("**GitHub entity**: pull request (no native Issue Type)")
        elif t.github_issue_type:
            bits.append(f"**GitHub Issue Type**: `{t.github_issue_type}`")
        if t.github_issue_type_color:
            bits.append(f"**Color**: `{t.github_issue_type_color}`")
        if bits:
            out.append(" · ".join(bits))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_human_inputs_doc(
    directory: HumanInputDirectory,
    state_machines: list[StateMachine] | None = None,
) -> str:
    """Render the human-input directory as markdown.

    When `state_machines` is supplied, each entry gains a derived
    "Declared on" line listing every `process.state` where it appears.
    """
    out: list[str] = ["# Human inputs", ""]
    if not directory.entries:
        out.append("_(no human inputs defined)_")
        return "\n".join(out) + "\n"

    declared_on: dict[str, list[str]] = {}
    for sm in state_machines or []:
        for st in sm.states.values():
            for entry_id in st.human_inputs:
                declared_on.setdefault(entry_id, []).append(f"{sm.name}.{st.name}")

    out.append(
        "Entries agents may invoke `request-input` on. Working states "
        "opt-in by listing ids on their `human_inputs` field; "
        "states with no list cannot escalate via `request-input`."
    )
    out.append("")
    for human_input_id, t in directory.entries.items():
        out.append(f"## `{human_input_id}` — {t.name}")
        out.append("")
        out.append(t.description)
        out.append("")
        if t.agent_prepares:
            out.append(f"- **Agent prepares**: `{t.agent_prepares}`")
        if t.rationale:
            out.append(f"- **Rationale**: {t.rationale}")
        places = sorted(declared_on.get(human_input_id, []))
        if places:
            out.append(
                f"- **Declared on**: {', '.join(f'`{p}`' for p in places)} "
                f"_(derived)_"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_process_map(processes: list[Any]) -> str:
    """Auto-generate a mermaid `stateDiagram-v2` of inter-process flow.

    Each process is a state node; the built-in `[*]` sentinel marks
    external entry and exit. Edge labels carry a kind prefix so readers
    can tell relationships apart in v2's single-arrow style:

    - `▶ <state>`           — entry: `[*] --> process` (where new issues land)
    - `■ <state>`           — exit: `process --> [*]` (where issues close;
      excludes feedback terminals — those are drawn separately).
    - `⇄ <state>`           — handoff: process → process, shared resting
      state. Bidirectional handoffs (each side both sends and receives)
      emit two edges in opposite directions.
    - `⤴ <src>→<dst>`        — spawn: process creates a child issue on
      another process.
    - `⤵ <src>→<dst>`        — collect: receiver gathers contributors from
      another process.
    - `↺ <child>→<parent>`   — feedback: the inverse of a spawn's
      `advance_on` mapping. When the child terminates at the labelled
      state, the parent auto-advances. Pairs with the `⤴` edge to show
      the round-trip.

    Processes with hyphenated names get an `as` alias so the v2 parser
    accepts a clean id while preserving the human label.
    """
    lines: list[str] = ["stateDiagram-v2", "    direction TB", ""]
    sorted_processes = sorted(processes, key=lambda p: p.process_name)

    # Emit process nodes. Hyphenated names need the `state "label" as id`
    # alias because stateDiagram-v2 identifiers can't include hyphens.
    for p in sorted_processes:
        if "-" in p.process_name:
            lines.append(
                f"    state \"{p.process_name}\" as {_node_id(p.process_name)}"
            )

    if not sorted_processes:
        return "\n".join(lines) + "\n"

    # A terminal that's named as a `spawn.advance_on` key on some other
    # process is a "feedback terminal" — the spawn child closes here,
    # then the parent process advances. From the workflow flow's
    # perspective the work continues in the parent; that's a feedback
    # signal, not a true workflow exit. Build the set so we can skip
    # exit edges for these.
    feedback_terminals_by_process: dict[str, set[str]] = {}
    for p in sorted_processes:
        for s in p.state_machine.states.values():
            if s.spawns is None:
                continue
            for child_terminal, _parent_next in s.spawns.advance_on:
                feedback_terminals_by_process.setdefault(
                    s.spawns.process, set()
                ).add(child_terminal)

    # Identify external entry / exit.
    entry_edges: list[tuple[str, str]] = []  # (process_name, destination_state)
    exit_edges: list[tuple[str, str]] = []  # (process_name, terminal_state)
    for p in sorted_processes:
        feedback_terminals = feedback_terminals_by_process.get(p.process_name, set())
        for s in p.state_machine.states.values():
            if s.is_initial:
                entry_edges.append((p.process_name, s.name))
            if s.state_class is StateClass.TERMINAL and s.name not in feedback_terminals:
                exit_edges.append((p.process_name, s.name))
    entry_edges.sort()
    exit_edges.sort()

    # Cross-process edges.
    process_by_name = {p.process_name: p for p in processes}

    def _sides_for(state_name: str, p: Any) -> tuple[bool, bool]:
        is_sender = False
        is_receiver = False
        for t in p.state_machine.transitions:
            if t.destination == state_name and t.source != state_name:
                is_sender = True
            if t.source == state_name and t.destination != state_name:
                is_receiver = True
        return is_sender, is_receiver

    # Edges: (src, dst, label). stateDiagram-v2 uses one arrow style so
    # the kind is encoded in the label prefix.
    cross_edges: set[tuple[str, str, str]] = set()

    # Handoffs — pair every (process, other) declaring the same state.
    handoff_by_state: dict[str, list[str]] = {}
    for p in processes:
        for s in p.state_machine.states.values():
            if s.handoff:
                handoff_by_state.setdefault(s.name, []).append(p.process_name)
    for state_name, procs in handoff_by_state.items():
        sorted_procs = sorted(procs)
        for i, a in enumerate(sorted_procs):
            for b in sorted_procs[i + 1 :]:
                a_send, a_recv = _sides_for(state_name, process_by_name[a])
                b_send, b_recv = _sides_for(state_name, process_by_name[b])
                a_silent = not a_send and not a_recv
                b_silent = not b_send and not b_recv
                flow_ab = (a_send or a_silent) and (b_recv or b_silent)
                flow_ba = (b_send or b_silent) and (a_recv or a_silent)
                if a_silent and b_silent:
                    flow_ab = flow_ba = False

                label = f"⇄ {state_name}"
                if flow_ab and flow_ba:
                    # Bidirectional — emit both directions as separate edges.
                    cross_edges.add((a, b, label))
                    cross_edges.add((b, a, label))
                elif flow_ab:
                    cross_edges.add((a, b, label))
                elif flow_ba:
                    cross_edges.add((b, a, label))
                else:
                    # Malformed handoff — pick a direction arbitrarily so
                    # the edge still appears in the map for visibility.
                    cross_edges.add((a, b, label))

    # Spawns.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.spawns is None:
                continue
            label = f"⤴ {s.name}→{s.spawns.initial_state}"
            cross_edges.add((p.process_name, s.spawns.process, label))

    # Collects.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.collects is None:
                continue
            type_suffix = (
                f" [{','.join(s.collects.issue_types)}]"
                if s.collects.issue_types
                else ""
            )
            for from_state in s.collects.from_states:
                label = f"⤵ {from_state}→{s.name}{type_suffix}"
                cross_edges.add((s.collects.process, p.process_name, label))

    # Feedback edges — the inverse of a spawn. When the child terminates
    # at a state listed in the spawn's `advance_on`, the parent
    # auto-advances. Drawing this lets readers see the full round-trip:
    # parent spawns child; child returns findings; parent moves on.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.spawns is None:
                continue
            for child_terminal, parent_next in s.spawns.advance_on:
                label = f"↺ {child_terminal}→{parent_next}"
                cross_edges.add((s.spawns.process, p.process_name, label))

    # Collector → contributor feedback. The inverse of collect's data
    # flow: when the collector enters a listed state, contributors
    # either auto-advance (advance_on) or are released back to
    # candidacy without moving (release_on). Drawn collector_process →
    # source_process to mirror "this is where contributors are
    # affected next".
    for p in processes:
        for s in p.state_machine.states.values():
            if s.collects is None:
                continue
            for collector_state, contributor_target in s.collects.advance_on:
                label = f"↺ {collector_state}→{contributor_target}"
                cross_edges.add((p.process_name, s.collects.process, label))
            for collector_state in s.collects.release_on:
                label = f"↩ {collector_state}"
                cross_edges.add((p.process_name, s.collects.process, label))

    if entry_edges or cross_edges or exit_edges:
        lines.append("")
    # Entry edges first (top), cross-process middle, exit edges last
    # (bottom) — preserves top-to-bottom flow in the v2 renderer.
    for process_name, dest_state in entry_edges:
        lines.append(
            f"    [*] --> {_node_id(process_name)}: ▶ {dest_state}"
        )
    for src, dst, label in sorted(cross_edges):
        lines.append(f"    {_node_id(src)} --> {_node_id(dst)}: {label}")
    for process_name, terminal_state in exit_edges:
        lines.append(
            f"    {_node_id(process_name)} --> [*]: ■ {terminal_state}"
        )

    return "\n".join(lines) + "\n"


def emit_index_doc(
    processes: dict[str, str | None],
    *,
    has_roles: bool,
    has_issue_types: bool,
    has_human_inputs: bool = False,
    process_map_mermaid: str | None = None,
) -> str:
    """Top-level README linking to every generated doc.

    `processes` is a map of process name → authored description (from the
    process's top-level `description` field in its `<process>-states.json`).
    Descriptions render as the tail of each list entry; processes without a
    description fall back to a generic "state machine, human gates,
    handoffs" suffix.

    When `process_map_mermaid` is provided, an embedded "Process map"
    section is rendered with the diagram inline plus a reader's guide.
    The standalone `process-map.mermaid` source is still emitted alongside
    for tools that want to ingest the raw mermaid.
    """
    out: list[str] = [
        "# Workflow",
        "",
        "Generated documentation for the processes defined in this workflow.",
        "Authored sources are the `*.json` files; regenerate with `workflow generate-docs`.",
        "",
    ]
    if process_map_mermaid is not None:
        out.extend([
            "## Process map",
            "",
            "Auto-generated overview of every process in this workflow and the "
            "handoffs between them. The canonical source is each "
            "`<process>-states.json`; the diagram is regenerated from those.",
            "",
            "Rendered as a `stateDiagram-v2` so it shares the visual "
            "language of the per-process state diagrams. Nodes are "
            "processes; the built-in `[*]` sentinel marks external entry "
            "(top) and external exit (bottom). The diagram reads "
            "top-to-bottom: new issues flow from `[*]`, through "
            "processes (handoffs and spawns between them), and back to "
            "`[*]` as each terminal state is reached.",
            "",
            "Edge labels carry a symbol prefix indicating the relationship "
            "kind:",
            "",
            "- **`▶ <state>`** — entry: a new external issue materializes at the labelled state.",
            "- **`■ <state>`** — exit: an issue closes at the labelled terminal **and** no parent process has it listed as a spawn feedback target. Terminals named in some sibling's `spawn.advance_on` are treated as feedback (the work continues in the parent) and don't render as workflow exits, even though the child issue itself closes.",
            "- **`⇄ <state>`** — handoff: the same work item continues on the destination process. Bidirectional handoffs (each side both sends and receives) emit two edges in opposite directions.",
            "- **`⤴ <src>→<dst>`** — spawn: the source process creates a child issue on the destination at the labelled initial state.",
            "- **`⤵ <src>→<dst>`** — collect: the destination process (authored via `collects`) gathers contributors from the source process's labelled state.",
            "- **`↺ <child>→<parent>`** — feedback: the inverse of a spawn's `advance_on` (child terminates → parent auto-advances) **or** a collect's `advance_on` (collector reaches a state → contributors advance). Pairs with the originating `⤴`/`⤵` edge to show the round-trip.",
            "- **`↩ <collector_state>`** — release: a collect's `release_on` entry. When the collector enters the labelled state, every contributor's `collected-by:<collector>` marker is cleared but no state change happens — the contributors are released back to candidacy and become eligible for a future collector.",
            "",
            "Edge labels name the state involved — the shared resting state for "
            "handoffs, or the originating → destination state pair for spawns.",
            "",
            "```mermaid",
            process_map_mermaid.rstrip(),
            "```",
            "",
            "The raw mermaid source is also available at "
            "[`process-map.mermaid`](./process-map.mermaid).",
            "",
            "**What this map does NOT show:** editorial groupings (Build / Ship "
            "lanes), edge tiers (happy path vs feedback), or rolled-up labels. "
            "Each shared state appears as its own edge.",
            "",
        ])
    out.extend([
        "## Processes",
        "",
    ])
    for name in sorted(processes):
        description = processes[name]
        tail = description.strip() if description else "state machine, human gates, handoffs"
        out.append(f"- [`{name}`](./{name}.md) — {tail}")
    if has_roles or has_issue_types or has_human_inputs:
        out.append("")
        out.append("## Shared resources")
        out.append("")
        if has_roles:
            out.append("- [Roles](./roles.md)")
        if has_issue_types:
            out.append("- [Issue types](./issue-types.md)")
        if has_human_inputs:
            out.append("- [Human inputs](./human-inputs.md)")
    return "\n".join(out).rstrip() + "\n"


# ----- helpers -----


def _node_id(process_name: str) -> str:
    """Mermaid flowchart node identifiers can't include hyphens directly in
    some renderers; replace `-` with `_` for safety."""
    return process_name.replace("-", "_")


# ----- sections -----


def _section_issue_types(
    declared: list[str], directory: IssueTypeDirectory | None
) -> list[str]:
    out = ["## Issue types accepted", ""]
    for type_id in declared:
        if directory is not None and directory.has(type_id):
            entry = directory.get(type_id)
            out.append(f"- `{type_id}` — **{entry.name}**: {entry.description}")
        else:
            out.append(f"- `{type_id}`")
    out.append("")
    return out


def _section_entry_points(sm: StateMachine) -> list[str]:
    """Render the list of external entry states (those with `initial`).

    Empty when no state declares `initial` (typical for spawn-only
    processes like `pr` or `postmortem`).
    """
    entries = [s for s in sm.states.values() if s.is_initial]
    if not entries:
        return []
    out = ["## External entry points", ""]
    out.append(
        "States where new issues materialize from outside the workflow — "
        "manual `create-issue --to <state>`, a webhook, or a scheduled "
        "job. Distinct from spawn / collect targets, which are reached "
        "via upstream work in another process; the framework enforces "
        "the two as mutually exclusive per state."
    )
    out.append("")
    for s in entries:
        label = (s.initial_label or "").strip()
        suffix = f" — {label}" if label else ""
        out.append(f"- `{s.name}`{suffix}")
    out.append("")
    return out


def _section_diagram(
    sm: StateMachine,
    *,
    spawn_targets: frozenset[str] | None = None,
) -> list[str]:
    targets = set(spawn_targets) if spawn_targets else None
    return [
        "## State diagram",
        "",
        "```mermaid",
        emit_mermaid(sm, spawn_targets=targets).rstrip(),
        "```",
        "",
    ]


def _section_states(sm: StateMachine) -> list[str]:
    out = ["## States", ""]
    out.append(
        "| Name | Class | Reversibility | Roles | Issue types | "
        "Human inputs | Terminal taxonomy | Close reason |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for name, st in sm.states.items():
        cls = st.state_class.value
        rev = st.reversibility.value if st.reversibility else "—"
        roles = ", ".join(st.roles) if st.roles else "—"
        types = ", ".join(st.issue_types) if st.issue_types else "—"
        human_inputs = ", ".join(st.human_inputs) if st.human_inputs else "—"
        tax = st.terminal_taxonomy.value if st.terminal_taxonomy else "—"
        close = st.close_reason or "—"
        out.append(
            f"| `{name}` | {cls} | {rev} | {roles} | {types} | {human_inputs} | {tax} | {close} |"
        )
    out.append("")
    return out


def _section_transitions(
    sm: StateMachine,
    catalog: HumanGateCatalog | None,
    grants: dict[str, TrustGrant] | None,
) -> list[str]:
    out = ["## Transitions", ""]
    out.append("| From | To | Type | Label | Gate | HITL level |")
    out.append("|---|---|---|---|---|---|")
    for t in sm.transitions:
        gate = t.gate_name or "—"
        level = "—"
        if t.is_gated and catalog is not None and t.gate_name and catalog.has(t.gate_name):
            gate = catalog.get(t.gate_name)
            effective = gate.default_level
            if grants is not None:
                grant = grants.get(gate.gate_name)
                if grant is not None and grant.effective_today:
                    effective = grant.current_level
            level = effective.value
            if effective is not gate.default_level:
                level += f" (default {gate.default_level.value})"
        out.append(
            f"| `{t.source}` | `{t.destination}` | {t.transition_type.value} | "
            f"{t.label!r} | {gate} | {level} |"
        )
    out.append("")
    return out


def _section_human_gates(
    sm: StateMachine,
    catalog: HumanGateCatalog,
    grants: dict[str, TrustGrant] | None,
) -> list[str]:
    """Render the human-gate catalog. Structural fields (source, destinations,
    triggering roles, reversibility) are derived from the state machine."""
    out = ["## Human gates", ""]
    for gate_name, gate in catalog.entries.items():
        effective = gate.default_level
        grant_note = ""
        if grants is not None:
            grant = grants.get(gate.gate_name)
            if grant is not None and grant.effective_today:
                effective = grant.current_level
                if effective is not gate.default_level:
                    grant_note = (
                        f" _(relaxed from {gate.default_level.value} via active trust grant)_"
                    )
        out.append(f"### `{gate_name}` — {effective.value}{grant_note}")
        out.append("")
        source = sm.gate_source(gate_name) or "?"
        destinations = sm.gate_destinations(gate_name)
        triggering = sm.gate_triggering_roles(gate_name)
        rev = sm.gate_reversibility(gate_name)
        out.append(f"- **Source state**: `{source}` _(derived)_")
        out.append(
            f"- **Destinations**: "
            f"{', '.join(f'`{d}`' for d in destinations) if destinations else '?'} "
            f"_(derived)_"
        )
        if triggering:
            out.append(
                f"- **Triggering role(s)**: "
                f"{', '.join(f'`{r}`' for r in triggering)} _(derived)_"
            )
        out.append(f"- **HumanGate type**: {gate.gate_type.value}")
        if rev is not None:
            out.append(f"- **Destination reversibility**: {rev.value} _(derived, worst-case)_")
        out.append(
            f"- **Allowed levels**: {', '.join(lvl.value for lvl in gate.allowed_levels)}"
        )
        out.append(f"- **Default level**: {gate.default_level.value}")
        if gate.agent_prepares_path:
            out.append(f"- **Agent prepares**: `{gate.agent_prepares_path}`")
        if gate.rationale:
            out.append("")
            out.append(f"> {gate.rationale}")
        out.append("")
    return out


def _section_cross_process(sm: StateMachine) -> list[str]:
    handoffs = [s for s in sm.states.values() if s.handoff]
    spawners = [s for s in sm.states.values() if s.spawns is not None]
    collectors = [s for s in sm.states.values() if s.collects is not None]
    if not handoffs and not spawners and not collectors:
        return []
    out = ["## Cross-process handoffs", ""]
    if handoffs:
        out.append("**Handoff states** (shared resting states declared in ≥2 processes):")
        out.append("")
        for s in handoffs:
            out.append(f"- `{s.name}` — interface state, also declared by the partner process(es).")
        out.append("")
    if spawners:
        out.append("**Spawns** (states that create child issues on other processes):")
        out.append("")
        for s in spawners:
            sp = s.spawns
            assert sp is not None
            kind = "subprocess" if s.state_class is StateClass.WORKING else "independent"
            head = (
                f"- `{s.name}` ({kind}) → process `{sp.process}` "
                f"as `{sp.issue_type}` issue at `{sp.initial_state}`"
            )
            out.append(head)
            for child_term, parent_next in sp.advance_on:
                out.append(
                    f"    - on child `{child_term}` → parent `{parent_next}`"
                )
        out.append("")
    if collectors:
        out.append(
            "**Collects** (states that gather contributors from other "
            "processes when an issue is created here):"
        )
        out.append("")
        for s in collectors:
            c = s.collects
            assert c is not None
            sources = ", ".join(f"`{fs}`" for fs in c.from_states)
            type_suffix = (
                f" (types: {', '.join(f'`{t}`' for t in c.issue_types)})"
                if c.issue_types
                else ""
            )
            out.append(
                f"- `{s.name}` ← process `{c.process}` from {sources}{type_suffix}"
            )
            for collector_state, contributor_target in c.advance_on:
                out.append(
                    f"    - on collector `{collector_state}` → contributors `{contributor_target}`"
                )
            for collector_state in c.release_on:
                out.append(
                    f"    - on collector `{collector_state}` → contributors released (back to candidacy)"
                )
        out.append("")
    return out


def _section_trust_grants(grants: list[TrustGrant]) -> list[str]:
    out = ["## Active trust grants", ""]
    out.append(
        "Per-team relaxations of catalogued HumanGate levels. Grants expire and "
        "must be re-justified with evidence."
    )
    out.append("")
    for g in grants:
        out.append(f"### `{g.control_point}` (team: {g.team})")
        out.append("")
        out.append(
            f"- **Effective level**: {g.current_level.value}"
        )
        out.append(f"- **Granted by**: {g.granted_by}")
        out.append(f"- **Granted at**: {g.granted_at.isoformat()}")
        out.append(f"- **Expires at**: {g.expires_at.isoformat()}")
        if g.review_cadence:
            out.append(f"- **Review cadence**: {g.review_cadence}")
        if g.parameters.cadence:
            out.append(f"- **Audit cadence**: {g.parameters.cadence}")
        if g.parameters.on_revoke:
            out.append(f"- **On revoke**: {g.parameters.on_revoke}")
        if g.evidence:
            out.append("")
            out.append("**Evidence**:")
            for e in g.evidence:
                out.append(
                    f"- {e.source}: {e.metric} ({e.window}) — {e.detail}"
                )
        out.append("")
    return out


def _grants_for_process(
    sm: StateMachine, grants: dict[str, TrustGrant] | None
) -> list[TrustGrant]:
    """Return grants whose control_point matches a gate referenced in this process."""
    if not grants:
        return []
    gate_names = {t.gate_name for t in sm.transitions if t.gate_name}
    return [g for g in grants.values() if g.control_point in gate_names]


# Defensive: avoid the unused-import lint if HumanGateLevel becomes unreferenced.
_ = (HumanGateLevel, StateClass)
