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


def emit_process_doc(inputs: ProcessDocInput) -> str:
    """Render the process documentation as a single markdown string."""
    sm = inputs.state_machine
    out: list[str] = []
    out.append(f"# Process: {sm.name}")
    out.append("")
    out.append(f"> Defined in: `{sm.name}-states.json`")
    if inputs.catalog is not None and inputs.catalog.entries:
        out.append(f"> HumanGate catalog: `{sm.name}-human-gates.json`")
    out.append("")

    accepted = sm.accepted_issue_types
    if accepted:
        out.extend(_section_issue_types(accepted, inputs.issue_type_directory))

    out.extend(_section_diagram(sm))
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
    """Auto-generate a mermaid flowchart of inter-process handoffs.

    Each process is a node; each cross-process transition becomes an edge.
    Edges are deduped across both ends of a shared handoff (the same edge
    appears in both processes' JSON — one as an exit, one as an entry).

    Shared handoffs (same work item continues) render as `==>` (thick);
    spawn events (new work item created) render as `-.->` (dashed). Edge
    labels name the shared state or spawn target state.
    """
    lines: list[str] = ["flowchart LR", ""]
    sorted_processes = sorted(processes, key=lambda p: p.process_name)
    for p in sorted_processes:
        lines.append(f"    {_node_id(p.process_name)}({p.process_name})")
    if not sorted_processes:
        return "\n".join(lines) + "\n"

    # Two edge sources:
    #
    # 1. Shared handoffs: resting states marked `handoff: true` that appear
    #    in two or more processes. Render as bidirectional thick edges
    #    between every pair of declaring processes. Symbol: `===`.
    # 2. Spawns: working / terminal states with `spawns: {...}` that create
    #    a new issue on another process. Render as dashed directed edges.
    #    Symbol: `-.->`.
    edges: set[tuple[str, str, str, str]] = set()

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
                edges.add((a, b, state_name, "handoff"))

    # Spawns — walk every state's `spawns` field. Subprocess spawns (working
    # states) and independent spawns (terminal states) both contribute edges.
    for p in processes:
        for s in p.state_machine.states.values():
            if s.spawns is None:
                continue
            label = f"{s.name}→{s.spawns.initial_state}"
            edges.add((p.process_name, s.spawns.process, label, "spawn"))

    if edges:
        lines.append("")
    for src, dst, label, kind in sorted(edges):
        arrow = "===" if kind == "handoff" else "-.->"
        lines.append(f"    {_node_id(src)} {arrow}|{label}| {_node_id(dst)}")

    return "\n".join(lines) + "\n"


def emit_process_map_doc() -> str:
    """A brief reader's guide for the auto-generated process map."""
    return (
        "# Process map\n"
        "\n"
        "Auto-generated overview of every process in this workflow and the "
        "handoffs between them. The canonical source is each "
        "`<process>-states.json`; this map is regenerated from those.\n"
        "\n"
        "## How to read it\n"
        "\n"
        "Nodes are processes; edges are cross-process handoffs. Edge styling:\n"
        "\n"
        "- **`==>` (thick solid)** — *shared* handoff: the same work item continues on the destination process's state machine. Both processes declare the shared resting state.\n"
        "- **`-.->` (dashed)** — *spawn*: a new work item is created on the destination process. The originating issue and the spawned issue are tracked independently.\n"
        "\n"
        "Edge labels name the state involved in the handoff — the shared "
        "resting state for shared handoffs, or the destination state for "
        "spawn events.\n"
        "\n"
        "## Diagram\n"
        "\n"
        "See [`process-map.mermaid`](./process-map.mermaid). It is regenerated by "
        "`workflow generate-docs` from the cross-process metadata in each "
        "`*-states.json`.\n"
        "\n"
        "## What this map does NOT show\n"
        "\n"
        "- **Editorial groupings** (Build / Ship / Respond / Learn lanes). The "
        "auto-generated map has no concept of phase — add a `phase` field to "
        "each state machine JSON if you want lanes.\n"
        "- **Edge tiers** (primary happy path vs feedback vs conditional). The "
        "auto-generated map distinguishes only shared vs spawn.\n"
        "- **Rolled-up labels** like `ready_for_dev / exp / spike` — each shared "
        "state appears as its own edge.\n"
    )


def emit_index_doc(
    process_names: list[str],
    *,
    has_roles: bool,
    has_issue_types: bool,
    has_human_inputs: bool = False,
    has_process_map: bool = False,
) -> str:
    """Top-level README linking to every generated doc."""
    out: list[str] = [
        "# Workflow",
        "",
        "Generated documentation for the processes defined in this workflow.",
        "Authored sources are the `*.json` files; regenerate with `workflow generate-docs`.",
        "",
    ]
    if has_process_map:
        out.append("- [Process map](./process-map.md) — auto-generated handoff overview")
        out.append("")
    out.extend([
        "## Processes",
        "",
    ])
    for name in sorted(process_names):
        out.append(f"- [`{name}`](./{name}.md) — state machine, human gates, handoffs")
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


def _section_diagram(sm: StateMachine) -> list[str]:
    return [
        "## State diagram",
        "",
        "```mermaid",
        emit_mermaid(sm).rstrip(),
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
    if not handoffs and not spawners:
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
