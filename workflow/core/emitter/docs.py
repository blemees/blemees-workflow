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

from workflow.core.emitter.mermaid import (
    SYMBOL_COLLECT,
    SYMBOL_ENTRY,
    SYMBOL_EXIT,
    SYMBOL_FEEDBACK,
    SYMBOL_HANDOFF,
    SYMBOL_RELEASE,
    SYMBOL_SPAWN,
    emit_mermaid,
)
from workflow.core.model.human_gate import HumanGateCatalog, HumanGateLevel
from workflow.core.model.human_input import HumanInputDirectory
from workflow.core.model.issue_type import IssueTypeDirectory
from workflow.core.model.role import RoleDirectory
from workflow.core.model.state_machine import (
    StateClass,
    StateMachine,
)
from workflow.core.model.trust_grant import TrustGrant


@dataclass(frozen=True)
class InboundSpawn:
    """A sibling process's spawn rule that creates a child on this process."""

    target_state: str
    source_process: str
    source_state: str
    issue_type: str


@dataclass(frozen=True)
class OutboundFeedback:
    """This process's closing state triggers a parent spawn's `advance_on` rule."""

    child_closing_state: str
    parent_process: str
    parent_state: str
    parent_next: str
    issue_type: str


@dataclass(frozen=True)
class OutboundCollect:
    """A sibling process's collector gathers contributors from this process.

    The mirror of the collector (inbound) side: `source_state` is a
    resting/closing state on *this* process that appears in another
    process's `collects.from_states`, so issues resting here can be pulled
    into that process's collector.
    """

    source_state: str
    collector_process: str
    collector_state: str
    issue_types: tuple[str, ...]


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
    inbound_spawns: tuple[InboundSpawn, ...] | None = None
    outbound_feedback: tuple[OutboundFeedback, ...] | None = None
    outbound_collects: tuple[OutboundCollect, ...] | None = None


def spawn_sources_from_inbound(
    inbound_spawns: tuple[InboundSpawn, ...] | None,
) -> dict[str, list[str]] | None:
    """Derive mermaid `[*] --> state: ᐉ parent` data from inbound spawn rows."""
    if not inbound_spawns:
        return None
    grouped: dict[str, set[str]] = {}
    for row in inbound_spawns:
        grouped.setdefault(row.target_state, set()).add(row.source_state)
    return {state: sorted(parents) for state, parents in sorted(grouped.items())}


def closing_states_from_outbound(
    outbound_feedback: tuple[OutboundFeedback, ...] | None,
) -> frozenset[str]:
    if not outbound_feedback:
        return frozenset()
    return frozenset(row.child_closing_state for row in outbound_feedback)


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

    out.extend(
        _section_diagram(
            sm,
            spawn_sources=spawn_sources_from_inbound(inputs.inbound_spawns),
            outbound_feedback=inputs.outbound_feedback,
        )
    )
    out.extend(_section_states(sm))
    out.extend(_section_transitions(sm, inputs.catalog, inputs.grants))
    if inputs.catalog is not None and inputs.catalog.entries:
        out.extend(_section_human_gates(sm, inputs.catalog, inputs.grants))
    out.extend(
        _section_cross_process(
            sm,
            inputs.inbound_spawns,
            inputs.outbound_feedback,
            inputs.outbound_collects,
        )
    )

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
            out.append(f"- **Declared on**: {', '.join(f'`{p}`' for p in places)} _(derived)_")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_process_map(processes: list[Any]) -> str:
    """Auto-generate a mermaid `stateDiagram-v2` of inter-process flow.

    Each process is a state node; the built-in `[*]` sentinel marks
    external entry and exit. Edge labels carry a kind prefix so readers
    can tell relationships apart in v2's single-arrow style:

    - `▶ <state>`           — entry: `[*] --> process` (where new issues land)
    - `■ <state>`           — exit: `process --> [*]` (where issues close;
      excludes feedback closing states — those are drawn separately).
    - `⊙ <state>`           — handoff: process → process, shared resting
      state. Bidirectional handoffs (each side both sends and receives)
      emit two edges in opposite directions.
    - `ᐉ <parent_state>`     — spawn: process creates a child issue on
      another process. Label names only the parent state; the child's
      initial state is shown on the child's own diagram.
    - `ꘜ <collector_state>`   — collect: receiver gathers contributors
      from another process. Label names only the collector state; the
      source's `from_states` are visible on the source process's own
      diagram.
    - `⊡ <state>`            — feedback: for a spawn's `advance_on`,
      the parent's next state after the child terminates; for a
      collect's `advance_on`, the collector's state that triggers
      contributor movement. The triggering / receiving counterpart is
      visible on the relevant process's own diagram.

    Processes with hyphenated names get an `as` alias so the v2 parser
    accepts a clean id while preserving the human label.

    Processes sharing a `group` value render inside a Mermaid composite
    state block — a bordered region clustering related processes. Pure
    layout sugar; cross-group edges still draw as normal.
    """
    lines: list[str] = ["stateDiagram-v2", "    direction LR", ""]
    sorted_processes = sorted(processes, key=lambda p: p.process_name)

    # Partition processes by their declared `group` so we can emit
    # grouped processes inside composite blocks. Insertion-ordered
    # so groups appear in the order their first member is encountered;
    # ungrouped processes render at top level.
    grouped: dict[str, list[Any]] = {}
    ungrouped: list[Any] = []
    for p in sorted_processes:
        g = getattr(p.state_machine, "group", None)
        if g:
            grouped.setdefault(g, []).append(p)
        else:
            ungrouped.append(p)

    def _emit_alias(p: Any, indent: str) -> None:
        # Hyphenated names need the `state "label" as id` alias. Names
        # without a hyphen are valid stateDiagram-v2 identifiers on
        # their own — but inside a composite we still need an explicit
        # declaration so Mermaid scopes the node into the block.
        if "-" in p.process_name:
            lines.append(f'{indent}state "{p.process_name}" as {_node_id(p.process_name)}')
        else:
            lines.append(f"{indent}{_node_id(p.process_name)}")

    for group_name, members in grouped.items():
        lines.append(f'    state "{group_name}" as {_node_id(group_name)} {{')
        for p in members:
            _emit_alias(p, "        ")
        lines.append("    }")
    for p in ungrouped:
        # Preserve the previous behaviour for ungrouped processes: only
        # emit an explicit declaration when the name needs an alias.
        if "-" in p.process_name:
            lines.append(f'    state "{p.process_name}" as {_node_id(p.process_name)}')

    if not sorted_processes:
        return "\n".join(lines) + "\n"

    # Build a state-name → process-name map so spawns omitting `process`
    # still resolve. Used throughout the rest of this function.
    state_to_process: dict[str, str] = {}
    for p in sorted_processes:
        for state_name in p.state_machine.states:
            state_to_process[state_name] = p.process_name

    def _spawn_process(sp: Any) -> str | None:
        return sp.process or state_to_process.get(sp.initial_state)

    def _collects_source_process(c: Any) -> str | None:
        if c.process:
            return c.process
        if c.from_states:
            return state_to_process.get(c.from_states[0])
        return None

    # A closing state that's named as a `spawn.advance_on` key on some other
    # process is a "feedback closing state" — the spawn child closes here,
    # then the parent process advances. From the workflow flow's
    # perspective the work continues in the parent; that's a feedback
    # signal, not a true workflow exit. Build the set so we can skip
    # exit edges for these.
    #
    # Same logic applies to a collector's own closing states: a `collects`
    # declaration names `advance_on` keys (collector closing states that fan
    # contributors forward) and `release_on` entries (collector
    # closing states that release contributors back to candidacy). Both kinds
    # cascade work into the contributor process — they aren't "the
    # workflow ends here," they're "the collection now propagates."
    feedback_closing_states_by_process: dict[str, set[str]] = {}
    for p in sorted_processes:
        for s in p.state_machine.states.values():
            for sp in s.spawns:
                target_proc = _spawn_process(sp)
                if target_proc is None:
                    continue
                for child_closing_state, _parent_next in sp.advance_on:
                    feedback_closing_states_by_process.setdefault(target_proc, set()).add(
                        child_closing_state
                    )
            if s.collects is not None:
                # Collector lives on THIS process; its advance_on /
                # release_on keys are this process's own states (the
                # collector's closing states/restings that trigger fan-out
                # to the contributor process).
                for rule in s.collects.advance_on:
                    feedback_closing_states_by_process.setdefault(p.process_name, set()).add(
                        rule.collector_state
                    )
                for collector_state in s.collects.release_on:
                    feedback_closing_states_by_process.setdefault(p.process_name, set()).add(
                        collector_state
                    )

    # Identify external entry / exit.
    entry_edges: list[tuple[str, str]] = []  # (process_name, destination_state)
    exit_edges: list[tuple[str, str]] = []  # (process_name, closing_state)
    for p in sorted_processes:
        feedback_closing_states = feedback_closing_states_by_process.get(p.process_name, set())
        for s in p.state_machine.states.values():
            if s.is_initial:
                entry_edges.append((p.process_name, s.name))
            if s.is_closing and s.name not in feedback_closing_states:
                # A closing state that spawns a follow-up issue isn't really an
                # exit — the work is superseded by the child item, not
                # closed off. The spawn edge (drawn elsewhere as
                # `process → target: ᐉ <state>`) already communicates the
                # continuation, so suppress the `[*]` sink to avoid the
                # misleading "this just ends" reading.
                if s.spawns:
                    continue
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
        # Cascade-driven send: a collects.advance_on rule on this
        # process whose target lands at this state means the cascade
        # pushes work INTO it — a send signal even without a
        # transition declaration. (Example: release.cut.collects
        # advances experiment contributors to `measuring`, which is a
        # handoff with experimentation; release is the sender.)
        for s in p.state_machine.states.values():
            if s.collects is None:
                continue
            for rule in s.collects.advance_on:
                for target in rule.all_targets():
                    if target == state_name:
                        is_sender = True
                        break
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

                label = f"{SYMBOL_HANDOFF} {state_name}"
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

    # Spawns. Label shows only the parent state (where the spawn fires);
    # the child's initial state is visible on the child process's own
    # diagram via its `[*] --> <initial>: spawn` arrow. A state may
    # declare multiple spawn rules — emit one edge per (parent_state,
    # target_process) pair; ties are deduped via the set.
    for p in sorted_processes:
        for s in p.state_machine.states.values():
            for sp in s.spawns:
                target_proc = _spawn_process(sp)
                if target_proc is None:
                    continue
                label = f"{SYMBOL_SPAWN} {s.name}"
                cross_edges.add((p.process_name, target_proc, label))

    # Collects.
    # Collect label shows only the collector (target) state; the source's
    # from_states are visible on the source process's own diagram. When
    # multiple from_states are declared, we still emit one edge per
    # from_state for the source-side render — but the label is the same
    # collector state across them, so we dedupe via the set.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.collects is None:
                continue
            type_suffix = f" [{','.join(s.collects.issue_types)}]" if s.collects.issue_types else ""
            label = f"{SYMBOL_COLLECT} {s.name}{type_suffix}"
            source_proc = s.collects.process or _collects_source_process(s.collects)
            if source_proc is None or source_proc == p.process_name:
                # Intra-process collect — no cross-process edge to draw.
                continue
            cross_edges.add((source_proc, p.process_name, label))

    # Feedback edges — the inverse of a spawn. When the child terminates
    # at a state listed in the spawn's `advance_on`, the parent
    # auto-advances. The feedback edge is sourced from the process that
    # actually owns the triggering child closing state — which can differ
    # from the spawn target when the child's lifecycle crosses
    # processes via handoff (e.g., mitigation spawns a hotfix into
    # inner-loop, but the hotfix is shipped via release, so the
    # feedback arrow originates at release). Label shows only the
    # parent's new state.
    for p in sorted_processes:
        for s in p.state_machine.states.values():
            for sp in s.spawns:
                spawn_target_proc = _spawn_process(sp)
                if spawn_target_proc is None:
                    continue
                for child_closing_state, parent_next in sp.advance_on:
                    feedback_source = state_to_process.get(child_closing_state, spawn_target_proc)
                    if feedback_source == p.process_name:
                        # Self-feedback would be a no-op cross-process
                        # edge; skip.
                        continue
                    label = f"{SYMBOL_FEEDBACK} {parent_next}"
                    cross_edges.add((feedback_source, p.process_name, label))

    # Collector → contributor feedback. The inverse of collect's data
    # flow: when the collector enters a listed state, contributors
    # either auto-advance (advance_on) or are released back to
    # candidacy without moving (release_on). Label shows only the
    # collector's triggering state; check the source process's own
    # diagram for the contributor's target.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.collects is None:
                continue
            source_proc = s.collects.process or _collects_source_process(s.collects)
            if source_proc is None or source_proc == p.process_name:
                # Intra-process collect — no cross-process feedback edge.
                continue
            for rule in s.collects.advance_on:
                label = f"{SYMBOL_FEEDBACK} {rule.collector_state}"
                cross_edges.add((p.process_name, source_proc, label))
            for collector_state in s.collects.release_on:
                label = f"{SYMBOL_RELEASE} {collector_state}"
                cross_edges.add((p.process_name, source_proc, label))

    if entry_edges or cross_edges or exit_edges:
        lines.append("")
    # Entry edges first (top), cross-process middle, exit edges last
    # (bottom) — preserves top-to-bottom flow in the v2 renderer.
    for process_name, dest_state in entry_edges:
        lines.append(f"    [*] --> {_node_id(process_name)}: {SYMBOL_ENTRY} {dest_state}")
    for src, dst, label in sorted(cross_edges):
        lines.append(f"    {_node_id(src)} --> {_node_id(dst)}: {label}")
    for process_name, closing_state in exit_edges:
        lines.append(f"    {_node_id(process_name)} --> [*]: {SYMBOL_EXIT} {closing_state}")

    return "\n".join(lines) + "\n"


def emit_index_doc(
    processes: dict[str, str | None],
    *,
    has_roles: bool,
    has_issue_types: bool,
    has_human_inputs: bool = False,
    process_map_mermaid: str | None = None,
    state_machines: list[StateMachine] | None = None,
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
        out.extend(
            [
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
                "`[*]` as each closing state is reached.",
                "",
                "Edge labels carry a symbol prefix indicating the relationship kind:",
                "",
                "- **`▶ <state>`** — entry: a new external issue materializes at the labelled state.",
                "- **`■ <state>`** — exit: an issue closes at the labelled closing state **and** no parent process has it listed as a spawn feedback target. Closing states named in some sibling's `spawn.advance_on` are treated as feedback (the work continues in the parent) and don't render as workflow exits, even though the child issue itself closes.",
                "- **`⊙ <state>`** — handoff: the same work item continues on the destination process. Bidirectional handoffs (each side both sends and receives) emit two edges in opposite directions.",
                "- **`ᐉ <parent_state>`** — spawn: the source process creates a child issue on the destination process. The label names only the parent state where the spawn fires; check the destination's own diagram for the child's initial state.",
                "- **`ꘜ <collector_state>`** — collect: the destination process (authored via `collects`) gathers contributors from another process. The label names only the collector state; the source's `from_states` are visible on the source process's own diagram.",
                "- **`⊡ <state>`** — feedback: for a spawn's `advance_on`, the parent's next state after the child terminates. For a collect's `advance_on`, the collector's state that triggers contributor movement. Pairs with the originating `ᐉ`/`ꘜ` edge to show the round-trip; the trigger / target counterpart is visible on the relevant process's own diagram.",
                "- **`⧄ <collector_state>`** — release: a collect's `release_on` entry. When the collector enters the labelled state, every contributor's `collected-by:<collector>` marker is cleared but no state change happens — the contributors are released back to candidacy and become eligible for a future collector.",
                "",
                "Edge labels name the state involved — the shared resting state for "
                "handoffs, or the originating → destination state pair for spawns.",
                "",
                "```mermaid",
                process_map_mermaid.rstrip(),
                "```",
                "",
                "> Raw mermaid source in: [`process-map.mermaid`](./process-map.mermaid).",
                "",
            ]
        )
    if state_machines:
        out.extend(_section_workflow_entry_points(state_machines))
    out.extend(
        [
            "## Processes",
            "",
        ]
    )
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


def _proc_link(process_name: str) -> str:
    """Markdown link to a sibling process's generated doc."""
    return f"[`{process_name}`](./{process_name}.md)"


def _collect_detail(collects: Any) -> str:
    """One-line summary of a `collects` block for a table cell.

    Joins issue-type scoping and per-collector advance / release rules with
    `; ` since markdown table cells can't hold nested bullet lists.
    """
    bits: list[str] = []
    if collects.issue_types:
        bits.append("types " + ", ".join(f"`{t}`" for t in collects.issue_types))
    for rule in collects.advance_on:
        if not rule.by_type:
            bits.append(f"on `{rule.collector_state}` → `{rule.default_target}`")
            continue
        pieces: list[str] = []
        if rule.default_target is not None:
            pieces.append(f"`*`→`{rule.default_target}`")
        for tk, tgt in rule.by_type:
            pieces.append(f"`{tk}`→`{tgt}`")
        bits.append(f"on `{rule.collector_state}`: " + ", ".join(pieces))
    for collector_state in collects.release_on:
        bits.append(f"on `{collector_state}` → released")
    return "; ".join(bits) if bits else "—"


def _interface_table(
    title: str, counterpart_header: str, rows: list[tuple[str, str, str, str]]
) -> list[str]:
    """Render `(state, kind, counterpart, detail)` rows as a markdown table."""
    out = [
        f"### {title}",
        "",
        f"| State | Kind | {counterpart_header} | Detail |",
        "|---|---|---|---|",
    ]
    for state, kind, counterpart, detail in rows:
        out.append(f"| `{state}` | {kind} | {counterpart} | {detail} |")
    out.append("")
    return out


# ----- sections -----


def _section_issue_types(declared: list[str], directory: IssueTypeDirectory | None) -> list[str]:
    out = ["## Issue types accepted", ""]
    for type_id in declared:
        if directory is not None and directory.has(type_id):
            entry = directory.get(type_id)
            out.append(f"- `{type_id}` — **{entry.name}**: {entry.description}")
        else:
            out.append(f"- `{type_id}`")
    out.append("")
    return out


def _section_workflow_entry_points(state_machines: list[StateMachine]) -> list[str]:
    """Render external entry points across the whole workflow.

    Only processes that declare at least one `initial` state contribute
    rows. Placed in the top-level README alongside the process map.
    """
    entries: list[tuple[str, str, str | None]] = []
    for sm in sorted(state_machines, key=lambda machine: machine.name):
        for state in sm.states.values():
            if state.is_initial:
                entries.append((sm.name, state.name, state.initial_label))
    if not entries:
        return []

    out = ["## External entry points", ""]
    out.append(
        "States where new issues materialize from outside the workflow — "
        "manual `create-issue --to <state>`, a webhook, or a scheduled "
        "job. These correspond to the `▶ <state>` edges from `[*]` on "
        "the process map above. Distinct from spawn / collect targets, "
        "which are reached via upstream work in another process; the "
        "framework enforces the two as mutually exclusive per state."
    )
    out.append("")
    for process_name, state_name, label in entries:
        trigger = (label or "").strip()
        suffix = f" — {trigger}" if trigger else ""
        out.append(f"- [`{process_name}`](./{process_name}.md) · `{state_name}`{suffix}")
    out.append("")
    return out


def _section_diagram(
    sm: StateMachine,
    *,
    spawn_sources: dict[str, list[str]] | None = None,
    outbound_feedback: tuple[OutboundFeedback, ...] | None = None,
) -> list[str]:
    return [
        "## State diagram",
        "",
        "```mermaid",
        emit_mermaid(
            sm,
            spawn_sources=spawn_sources,
            outbound_feedback=outbound_feedback,
        ).rstrip(),
        "```",
        "",
    ]


def _section_states(sm: StateMachine) -> list[str]:
    out = ["## States", ""]
    out.append(
        "| Name | Class | Reversibility | Roles | Issue types | "
        "Human inputs | Closure taxonomy | Close reason |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for name, st in sm.states.items():
        cls = st.state_class.value
        rev = st.reversibility.value if st.reversibility else "—"
        roles = ", ".join(st.roles) if st.roles else "—"
        types = ", ".join(st.issue_types) if st.issue_types else "—"
        human_inputs = ", ".join(st.human_inputs) if st.human_inputs else "—"
        tax = st.closes.taxonomy.value if st.closes else "—"
        close = st.closes.reason if st.closes else "—"
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
                f"- **Triggering role(s)**: {', '.join(f'`{r}`' for r in triggering)} _(derived)_"
            )
        out.append(f"- **HumanGate type**: {gate.gate_type.value}")
        if rev is not None:
            out.append(f"- **Destination reversibility**: {rev.value} _(derived, worst-case)_")
        out.append(f"- **Allowed levels**: {', '.join(lvl.value for lvl in gate.allowed_levels)}")
        out.append(f"- **Default level**: {gate.default_level.value}")
        if gate.agent_prepares_path:
            out.append(f"- **Agent prepares**: `{gate.agent_prepares_path}`")
        if gate.rationale:
            out.append("")
            out.append(f"> {gate.rationale}")
        out.append("")
    return out


def _section_cross_process(
    sm: StateMachine,
    inbound_spawns: tuple[InboundSpawn, ...] | None = None,
    outbound_feedback: tuple[OutboundFeedback, ...] | None = None,
    outbound_collects: tuple[OutboundCollect, ...] | None = None,
) -> list[str]:
    """Render cross-process interfaces as two tables — inbound and outbound.

    A row is `(state, kind, counterpart, detail)`. Inbound rows describe work
    or signals arriving at this process; outbound rows describe this process
    reaching out. Handoff states are bidirectional and appear in both tables.
    """
    inbound: list[tuple[str, str, str, str]] = []
    outbound: list[tuple[str, str, str, str]] = []

    # ----- inbound -----
    # External entry — new issues materialize here from outside the workflow.
    for s in sm.states.values():
        if not s.is_initial:
            continue
        trigger = (s.initial_label or "").strip()
        detail = f"`create-issue --to {s.name}`"
        if trigger:
            detail += f" — {trigger}"
        inbound.append((s.name, f"{SYMBOL_ENTRY} entry", "— (external)", detail))

    # Inbound spawns — child issues created here by an upstream process.
    for row in inbound_spawns or ():
        inbound.append(
            (
                row.target_state,
                f"{SYMBOL_SPAWN} spawn",
                f"{_proc_link(row.source_process)} · `{row.source_state}`",
                f"`{row.issue_type}` issue",
            )
        )

    # Inbound feedback — this process is the *parent*: a child it spawned
    # terminates and that advances us. Derived from our own `spawns.advance_on`
    # (the `parent_next` target lives on this process).
    for s in sm.states.values():
        for sp in s.spawns:
            counterpart_proc = _proc_link(sp.process) if sp.process else "_(derived)_"
            for child_closing_state, parent_next in sp.advance_on:
                inbound.append(
                    (
                        parent_next,
                        f"{SYMBOL_FEEDBACK} feedback",
                        f"{counterpart_proc} · `{child_closing_state}`",
                        f"child terminates → advance (spawned from `{s.name}`, `{sp.issue_type}`)",
                    )
                )

    # Collects — this process gathers contributors from another process.
    for s in sm.states.values():
        if s.collects is None:
            continue
        c = s.collects
        source = f"{_proc_link(c.process)} · " if c.process else "this process · "
        source += ", ".join(f"`{fs}`" for fs in c.from_states)
        inbound.append((s.name, f"{SYMBOL_COLLECT} collect", source, _collect_detail(c)))

    # ----- outbound -----
    # Outbound feedback — this process is the *child*: a closing state here advances
    # a parent on another process.
    for row in outbound_feedback or ():
        outbound.append(
            (
                row.child_closing_state,
                f"{SYMBOL_FEEDBACK} feedback",
                _proc_link(row.parent_process),
                f"advances parent to `{row.parent_next}` (spawn from "
                f"`{row.parent_state}`, `{row.issue_type}`)",
            )
        )

    # Outbound spawns — states here that create child issues elsewhere. The
    # return trip (advance_on) is shown above as inbound feedback.
    for s in sm.states.values():
        if not s.spawns:
            continue
        kind = "subprocess" if s.state_class is StateClass.WORKING else "independent"
        for sp in s.spawns:
            counterpart_proc = _proc_link(sp.process) if sp.process else "_(derived)_"
            outbound.append(
                (
                    s.name,
                    f"{SYMBOL_SPAWN} spawn",
                    f"{counterpart_proc} · `{sp.initial_state}`",
                    f"as `{sp.issue_type}` issue ({kind})",
                )
            )

    # Collected-from — another process's collector gathers from a state here.
    # The mirror of the collect (inbound) side, similar to outbound spawns.
    for row in outbound_collects or ():
        type_suffix = (
            " — types " + ", ".join(f"`{t}`" for t in row.issue_types) if row.issue_types else ""
        )
        outbound.append(
            (
                row.source_state,
                f"{SYMBOL_COLLECT} collected-from",
                f"{_proc_link(row.collector_process)} · `{row.collector_state}`",
                f"contributors pulled into collector{type_suffix}",
            )
        )

    # Handoffs — shared resting states declared in ≥2 processes. Bidirectional,
    # so they appear in both tables.
    handoffs = [s for s in sm.states.values() if s.handoff]
    for s in handoffs:
        inbound.append(
            (
                s.name,
                f"{SYMBOL_HANDOFF} handoff",
                "partner process(es)",
                "shared resting state (also outbound)",
            )
        )
    for s in handoffs:
        outbound.append(
            (
                s.name,
                f"{SYMBOL_HANDOFF} handoff",
                "partner process(es)",
                "shared resting state (also inbound)",
            )
        )

    # Bare closing states — true workflow exits: closing states with no feedback, spawn,
    # or handoff role. Feedback closing states (named in some parent's
    # `spawn.advance_on`) continue work in the parent, so they're excluded.
    feedback_closing_states = {row.child_closing_state for row in outbound_feedback or ()}
    for s in sm.states.values():
        if not s.is_closing:
            continue
        if s.name in feedback_closing_states or s.spawns or s.handoff:
            continue
        bits: list[str] = []
        if s.closes is not None:
            bits.append(s.closes.taxonomy.value)
            bits.append(f"closes `{s.closes.reason}`")
        detail = "; ".join(bits) if bits else "workflow exit"
        outbound.append((s.name, f"{SYMBOL_EXIT} exit", "— (closes)", detail))

    if not inbound and not outbound:
        return []

    out = ["## Cross-process interfaces", ""]
    if inbound:
        out.extend(_interface_table("Inbound", "From", inbound))
    if outbound:
        out.extend(_interface_table("Outbound", "To", outbound))
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
        out.append(f"- **Effective level**: {g.current_level.value}")
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
                out.append(f"- {e.source}: {e.metric} ({e.window}) — {e.detail}")
        out.append("")
    return out


def _grants_for_process(sm: StateMachine, grants: dict[str, TrustGrant] | None) -> list[TrustGrant]:
    """Return grants whose control_point matches a gate referenced in this process."""
    if not grants:
        return []
    gate_names = {t.gate_name for t in sm.transitions if t.gate_name}
    return [g for g in grants.values() if g.control_point in gate_names]


# Defensive: avoid the unused-import lint if HumanGateLevel becomes unreferenced.
_ = (HumanGateLevel, StateClass)
