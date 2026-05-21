"""Markdown documentation emitters — agent/human-readable views of the workflow.

`generate-docs` invokes these to materialise `<process>.md`, `roles.md`,
`issue-types.md`, and `README.md` alongside the canonical JSON. The output
is a flat, link-light reference: everything an agent needs to operate on a
process lives in that process's single markdown file (states, transitions,
HCPs, cross-process handoffs, active trust grants), so an LLM can ingest it
without chasing links.

The emitters are pure — they take in-memory model objects and return strings.
No file I/O happens here; the CLI handler writes the files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflow.core.emitter.mermaid import emit_mermaid
from workflow.core.model.hcp import HCPCatalog, HCPLevel
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
    catalog: HCPCatalog | None = None
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
        out.append(f"> HCP catalog: `{sm.name}-hcps.json`")
    out.append("")

    if sm.issue_types:
        out.extend(_section_issue_types(sm.issue_types, inputs.issue_type_directory))

    out.extend(_section_diagram(sm))
    out.extend(_section_states(sm))
    out.extend(_section_transitions(sm, inputs.catalog, inputs.grants))
    if inputs.catalog is not None and inputs.catalog.entries:
        out.extend(_section_hcps(inputs.catalog, inputs.grants))
    out.extend(_section_cross_process(sm))

    active_grants = _grants_for_process(sm, inputs.grants)
    if active_grants:
        out.extend(_section_trust_grants(active_grants))

    return "\n".join(out).rstrip() + "\n"


def emit_roles_doc(directory: RoleDirectory) -> str:
    """Render the role directory as markdown."""
    out: list[str] = ["# Roles", ""]
    if not directory.roles:
        out.append("_(no roles defined)_")
        return "\n".join(out) + "\n"
    out.append("This workflow defines these roles:")
    out.append("")
    for role_id, role in directory.roles.items():
        out.append(f"## `{role_id}` — {role.name}")
        out.append("")
        out.append(role.responsibility)
        out.append("")
        if role.processes:
            out.append(f"- **Processes**: {', '.join(role.processes)}")
        if role.wakes_on:
            out.append(f"- **Wakes on**: {', '.join(role.wakes_on)}")
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

    # Collect edges from both sides. Shared handoffs use the same state
    # name on both ends, so the dedup (src, dst, label, kind) collapses
    # them. Spawn handoffs may use different state names on each side —
    # we keep both edges, since either may carry information the other
    # doesn't (e.g., a spawn entry with no corresponding exit on the
    # source diagram still represents a real handoff).
    edges: set[tuple[str, str, str, str]] = set()
    for p in processes:
        for t in p.state_machine.transitions:
            if t.transition_type is not TransitionType.CROSS_PROCESS:
                continue
            if t.cross_process_other is None:
                continue
            kind = t.cross_process_kind or "shared"
            if t.destination == "[*]":
                edges.add((p.process_name, t.cross_process_other, t.source, kind))
            elif t.source == "[*]":
                edges.add((t.cross_process_other, p.process_name, t.destination, kind))

    if edges:
        lines.append("")
    for src, dst, label, kind in sorted(edges):
        arrow = "==>" if kind == "shared" else "-.->"
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
        out.append(f"- [`{name}`](./{name}.md) — state machine, HCPs, handoffs")
    if has_roles or has_issue_types:
        out.append("")
        out.append("## Shared resources")
        out.append("")
        if has_roles:
            out.append("- [Roles](./roles.md)")
        if has_issue_types:
            out.append("- [Issue types](./issue-types.md)")
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
        "Terminal taxonomy | Close reason |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for name, st in sm.states.items():
        cls = st.state_class.value
        rev = st.reversibility.value if st.reversibility else "—"
        roles = ", ".join(st.roles) if st.roles else "—"
        types = ", ".join(st.issue_types) if st.issue_types else "—"
        tax = st.terminal_taxonomy.value if st.terminal_taxonomy else "—"
        close = st.close_reason or "—"
        out.append(
            f"| `{name}` | {cls} | {rev} | {roles} | {types} | {tax} | {close} |"
        )
    out.append("")
    return out


def _section_transitions(
    sm: StateMachine,
    catalog: HCPCatalog | None,
    grants: dict[str, TrustGrant] | None,
) -> list[str]:
    out = ["## Transitions", ""]
    out.append("| From | To | Type | Label | Gate | HITL level |")
    out.append("|---|---|---|---|---|---|")
    for t in sm.transitions:
        gate = t.gate_name or "—"
        level = "—"
        if t.is_gated and catalog is not None and t.gate_name and catalog.has(t.gate_name):
            hcp = catalog.get(t.gate_name)
            effective = hcp.default_level
            if grants is not None:
                grant = grants.get(hcp.gate_name)
                if grant is not None and grant.effective_today:
                    effective = grant.current_level
            level = effective.value
            if effective is not hcp.default_level:
                level += f" (default {hcp.default_level.value})"
        out.append(
            f"| `{t.source}` | `{t.destination}` | {t.transition_type.value} | "
            f"{t.label!r} | {gate} | {level} |"
        )
    out.append("")
    return out


def _section_hcps(
    catalog: HCPCatalog, grants: dict[str, TrustGrant] | None
) -> list[str]:
    out = ["## HCPs (Human Control Points)", ""]
    for gate_name, hcp in catalog.entries.items():
        effective = hcp.default_level
        grant_note = ""
        if grants is not None:
            grant = grants.get(hcp.gate_name)
            if grant is not None and grant.effective_today:
                effective = grant.current_level
                if effective is not hcp.default_level:
                    grant_note = (
                        f" _(relaxed from {hcp.default_level.value} via active trust grant)_"
                    )
        out.append(f"### `{gate_name}` — {effective.value}{grant_note}")
        out.append("")
        out.append(f"- **Source state**: `{hcp.source_state}`")
        out.append(f"- **Destinations**: {', '.join(f'`{d}`' for d in hcp.destinations)}")
        out.append(f"- **Triggering role**: `{hcp.triggering_role}`")
        out.append(f"- **HCP type**: {hcp.hcp_type.value}")
        out.append(f"- **Destination reversibility**: {hcp.reversibility.value}")
        out.append(
            f"- **Allowed levels**: {', '.join(lvl.value for lvl in hcp.allowed_levels)}"
        )
        out.append(f"- **Default level**: {hcp.default_level.value}")
        if hcp.agent_prepares_path:
            out.append(f"- **Agent prepares**: `{hcp.agent_prepares_path}`")
        if hcp.rationale:
            out.append("")
            out.append(f"> {hcp.rationale}")
        out.append("")
    return out


def _section_cross_process(sm: StateMachine) -> list[str]:
    cross = [t for t in sm.transitions if t.transition_type is TransitionType.CROSS_PROCESS]
    if not cross:
        return []
    out = ["## Cross-process handoffs", ""]
    entries = [t for t in cross if t.source == "[*]"]
    exits = [t for t in cross if t.destination == "[*]"]
    if entries:
        out.append("**Entries** (issues arriving from other processes):")
        out.append("")
        for t in entries:
            kind = t.cross_process_kind or "shared"
            other = t.cross_process_other or "?"
            out.append(
                f"- `{t.destination}` ← process `{other}` ({kind}) — `{t.label}`"
            )
        out.append("")
    if exits:
        out.append("**Exits** (issues handed to other processes):")
        out.append("")
        for t in exits:
            kind = t.cross_process_kind or "shared"
            other = t.cross_process_other or "?"
            out.append(
                f"- `{t.source}` → process `{other}` ({kind}) — `{t.label}`"
            )
        out.append("")
    return out


def _section_trust_grants(grants: list[TrustGrant]) -> list[str]:
    out = ["## Active trust grants", ""]
    out.append(
        "Per-team relaxations of catalogued HCP levels. Grants expire and "
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


# Defensive: avoid the unused-import lint if HCPLevel becomes unreferenced.
_ = (HCPLevel, StateClass)
