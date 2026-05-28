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

from dataclasses import dataclass

from workflow.core.model.human_gate import HumanGate, HumanGateCatalog, HumanGateLevel
from workflow.core.model.state_machine import (
    ReversibilityClass,
    StateClass,
    StateMachine,
    TerminalTaxonomy,
    TransitionType,
)
from workflow.core.model.trust_grant import TrustGrant


@dataclass(frozen=True)
class AvailableTransition:
    """One transition out of a source state, enriched with gate/grant info.

    `effective_level` is the HumanGate level after applying the team's trust grant
    (if any active). `default_level` is the catalog row's default — so the UI
    can call out when the team has relaxed (or tightened) the gate.
    """

    label: str
    source: str
    destination: str  # state name or "[*]" for cross-process exits
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
    destination_terminal_taxonomy: TerminalTaxonomy | None = None
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

        dst_state = (
            state_machine.states.get(t.destination) if t.destination != "[*]" else None
        )

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
                destination_terminal_taxonomy=(
                    dst_state.terminal_taxonomy if dst_state else None
                ),
                destination_roles=dst_state.roles if dst_state else (),
            )
        )
    return out
