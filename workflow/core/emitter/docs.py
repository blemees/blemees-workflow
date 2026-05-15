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
        if t.github_issue_type:
            bits.append(f"**GitHub Issue Type**: `{t.github_issue_type}`")
        if t.github_issue_type_color:
            bits.append(f"**Color**: `{t.github_issue_type_color}`")
        if bits:
            out.append(" · ".join(bits))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_index_doc(
    process_names: list[str],
    *,
    has_roles: bool,
    has_issue_types: bool,
) -> str:
    """Top-level README linking to every generated doc."""
    out: list[str] = [
        "# Workflow",
        "",
        "Generated documentation for the processes defined in this workflow.",
        "Authored sources are the `*.json` files; regenerate with `workflow generate-docs`.",
        "",
        "## Processes",
        "",
    ]
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
    out.append("| Name | Class | Reversibility | Claim role | Terminal taxonomy | Close reason |")
    out.append("|---|---|---|---|---|---|")
    for name, st in sm.states.items():
        cls = st.state_class.value
        rev = st.reversibility.value if st.reversibility else "—"
        claim = st.claim_role or "—"
        tax = st.terminal_taxonomy.value if st.terminal_taxonomy else "—"
        close = st.close_reason or "—"
        out.append(f"| `{name}` | {cls} | {rev} | {claim} | {tax} | {close} |")
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
