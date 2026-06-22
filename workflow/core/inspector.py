"""Inspector — describe an issue's next-step options to the agent.

The planner *decides* what an operation does. The inspector *describes* what
an agent at a given current state could do next, with HumanGate catalog rows and
trust grants resolved to effective levels. CLI commands like `view`, `claim`,
and `create --claim` call into this to surface next actions so the agent
doesn't have to look up the process to know how to advance.

The inspector is read-only — it builds plain dataclasses, never mutates the
issue or the backend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workflow.core.model.human_gate import HumanGate, HumanGateCatalog, HumanGateLevel
from workflow.core.model.state_machine import (
    ClosureTaxonomy,
    ReversibilityClass,
    StateClass,
    StateMachine,
    TransitionType,
)
from workflow.core.model.trust_grant import TrustGrant

if TYPE_CHECKING:
    from workflow.backends.base import IssueState, TrackerBackend
    from workflow.config import Workflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AvailableTransition:
    """One transition out of a source state, enriched with gate/grant info.

    `effective_level` is the HumanGate level after applying the team's trust grant
    (if any active). `default_level` is the catalog row's default — so the UI
    can call out when the team has relaxed (or tightened) the gate.
    """

    label: str
    source: str
    destination: str  # state name (always — `[*]` endpoints aren't authored)
    transition_type: TransitionType

    # HITL enrichment (None / empty when transition is not gated)
    is_gated: bool = False
    gate_name: str | None = None
    default_level: HumanGateLevel | None = None
    effective_level: HumanGateLevel | None = None
    triggering_roles: tuple[str, ...] = ()
    agent_prepares_path: str | None = None

    # Destination state info (None when destination is `[*]`)
    destination_state_class: StateClass | None = None
    destination_reversibility: ReversibilityClass | None = None
    destination_closure_taxonomy: ClosureTaxonomy | None = None
    # Roles permitted to occupy the destination state. Empty when the
    # destination is unrestricted, isn't a working state, or is `[*]`.
    destination_roles: tuple[str, ...] = ()

    @property
    def grant_relaxed(self) -> bool:
        """True when an active trust grant changed the effective level."""
        return (
            self.is_gated
            and self.default_level is not None
            and self.effective_level is not None
            and self.default_level != self.effective_level
        )


def available_transitions(
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
    grants: dict[str, TrustGrant] | None,
    source_state: str,
) -> list[AvailableTransition]:
    """Enumerate transitions out of `source_state`, with gate/grant info resolved.

    Returns transitions in declared order (matching the state machine JSON).
    Skips transitions whose source isn't `source_state`.
    """
    grants = grants or {}
    out: list[AvailableTransition] = []
    for t in state_machine.transitions:
        if t.source != source_state:
            continue

        dst_state = state_machine.states.get(t.destination)

        gate: HumanGate | None = None
        if t.is_gated and catalog is not None and t.gate_name is not None:
            gate = catalog.entries.get(t.gate_name)

        default_level = gate.default_level if gate else None
        effective_level = default_level
        if gate is not None:
            grant = grants.get(gate.gate_name)
            if grant is not None and grant.effective_today:
                effective_level = grant.current_level

        triggering_roles = (
            state_machine.gate_triggering_roles(t.gate_name)
            if gate is not None and t.gate_name is not None
            else ()
        )
        out.append(
            AvailableTransition(
                label=t.label,
                source=t.source,
                destination=t.destination,
                transition_type=t.transition_type,
                is_gated=t.is_gated,
                gate_name=t.gate_name,
                default_level=default_level,
                effective_level=effective_level,
                triggering_roles=triggering_roles,
                agent_prepares_path=gate.agent_prepares_path if gate else None,
                destination_state_class=dst_state.state_class if dst_state else None,
                destination_reversibility=dst_state.reversibility if dst_state else None,
                destination_closure_taxonomy=(
                    dst_state.closes.taxonomy if dst_state and dst_state.closes else None
                ),
                destination_roles=dst_state.roles if dst_state else (),
            )
        )
    return out


def inbox_states_for_role(state_machine: StateMachine, role: str) -> set[str]:
    """Find resting states from which `role` can claim into a working state.

    The role-restriction lives on the working state's `roles` list, not on the
    resting state. To enumerate the role's inbox, walk every CLAIM transition
    and collect its source if the destination working state permits the role
    (empty `roles` means unrestricted — included for any role). Role match is
    case-insensitive and ignores `{...}` braces.
    """
    role_normalized = role.strip("{}").lower()
    matches: set[str] = set()
    for t in state_machine.transitions:
        if t.transition_type is not TransitionType.CLAIM:
            continue
        dest = state_machine.states.get(t.destination)
        if dest is None:
            continue
        if dest.roles:
            allowed = {r.strip("{}").lower() for r in dest.roles}
            if role_normalized not in allowed:
                continue
        # dest.roles empty = open queue; any role may claim.
        matches.add(t.source)
    return matches


def inbox_for_role(
    registry: Workflow,
    backend: TrackerBackend,
    role: str,
    limit: int,
) -> list[IssueState]:
    """Compute the role's open work across ALL workflows: inbox + actionable wip.

    Roles often participate in multiple workflows (a product-manager does
    refinement, postmortem, prioritization). The query uses the workflow
    registry to aggregate inbox states: resting states from which `role` can
    CLAIM into a working state that declares `role` in its `roles` list (or has
    no role restriction).

    Two categories, deduplicated by issue id:

    1. **Inbox** — items in those discovered resting states with no current
       claim. Aggregated across all workflows.

    2. **Actionable wip** — items with `claimed/{role}` where the agent is not
       blocked waiting on a human: no `hitl-blocked/*`, no `hitl-audit/*`,
       no `hitl-input/*`. (Backend-level filter; not workflow-scoped.)

    Excludes items where the agent is blocked on a human signal, and items
    already claimed by another role. Read-only: never mutates issue or backend.
    """
    from workflow.backends.base import IssueFilters

    seen: dict[str, IssueState] = {}
    inbox_states: set[str] = set()

    # Aggregate inbox states from every workflow in the registry.
    for wf_name in registry.discovered_processes():
        try:
            wf_context = registry.get_process(wf_name)
        except Exception as exc:  # skip malformed workflows; inbox is best-effort
            logger.debug("Skipping workflow %r: %s", wf_name, exc)
            continue
        inbox_states |= inbox_states_for_role(wf_context.state_machine, role)

    # 1. Inbox: query the backend for each discovered inbox state.
    for state_name in inbox_states:
        for item in backend.list_issues(IssueFilters(state=state_name, limit=limit)):
            if item.agent_claim is None and item.issue_id not in seen:
                seen[item.issue_id] = item

    # 2. Actionable wip: claimed/{role} AND no awaiting/audit/awaiting-input markers.
    for item in backend.list_issues(IssueFilters(claim_role=role, limit=limit)):
        if (
            item.awaiting_gate is None
            and item.audit_pending is None
            and not item.awaiting_input
            and item.issue_id not in seen
        ):
            seen[item.issue_id] = item

    return list(seen.values())
