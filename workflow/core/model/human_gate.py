"""Human-gate catalog model — the per-gate catalog rows from process docs.

Mirrors `hitl-principles.md` principle 8 (catalog row schema) and principle 5
(the eleven-operation vocabulary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HumanGateType(Enum):
    """Per hitl-principles.md principle 2.

    The kind of input the human is being asked for. Drives the comment template
    and the eval-scenario shape.
    """

    AUTHORITY = "authority"
    KNOWLEDGE = "knowledge"
    JUDGMENT = "judgment"  # taste
    REALITY = "reality"  # a sub-case of knowledge, called out separately


class HumanGateLevel(Enum):
    """Per hitl-principles.md principle 3.

    The two HITL levels: block (agent waits for signal) and audit (agent acts,
    human reviews retroactively). The default level is `block` for every gate
    until a team relaxes via trust grant.
    """

    BLOCK = "block"
    AUDIT = "audit"


@dataclass
class HumanGate:
    """A single catalogued human gate — one row in a process doc's catalog.

    `gate_name` is the suffix used in operations: a destination state name for
    binary gates, or a named decision for verdict-style gates (per principle 8).

    The gate carries only **policy** fields — the kind of input the human
    is asked for, which levels are allowed, the default, and pointers to
    a packet template and rationale. Structural information (source
    state, destinations, triggering roles, reversibility) is derived
    from the paired state machine via `StateMachine.gate_*` helpers.
    """

    gate_name: str
    gate_type: HumanGateType
    allowed_levels: list[HumanGateLevel]
    default_level: HumanGateLevel
    agent_prepares_path: str | None = None
    rationale: str | None = None
    source_doc: str | None = None  # Process doc filename


@dataclass
class HumanGateCatalog:
    """The set of catalogued human gates declared in a process doc."""

    process_name: str
    entries: dict[str, HumanGate] = field(default_factory=dict)  # gate_name -> HumanGate
    source_path: str | None = None

    def get(self, gate_name: str) -> HumanGate:
        if gate_name not in self.entries:
            raise KeyError(
                f"Human gate {gate_name!r} not found in catalog for {self.process_name!r}"
            )
        return self.entries[gate_name]

    def has(self, gate_name: str) -> bool:
        return gate_name in self.entries

    def gates(self) -> list[str]:
        return list(self.entries.keys())
