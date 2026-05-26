"""HCP catalog model — the per-gate catalog rows from process docs.

Mirrors `hitl-principles.md` principle 8 (catalog row schema) and principle 5
(the eleven-operation vocabulary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HCPType(Enum):
    """Per hitl-principles.md principle 2.

    The kind of input the human is being asked for. Drives the comment template
    and the eval-scenario shape.
    """

    AUTHORITY = "authority"
    KNOWLEDGE = "knowledge"
    JUDGMENT = "judgment"  # taste
    REALITY = "reality"  # a sub-case of knowledge, called out separately


class HCPLevel(Enum):
    """Per hitl-principles.md principle 3.

    The two HITL levels: block (agent waits for signal) and audit (agent acts,
    human reviews retroactively). The default level is `block` for every gate
    until a team relaxes via trust grant.
    """

    BLOCK = "block"
    AUDIT = "audit"


@dataclass
class HCP:
    """A single catalogued HCP — one row in a process doc's HCP catalog.

    `gate_name` is the suffix used in operations: a destination state name for
    binary HCPs, or a named decision for verdict-style HCPs (per principle 8).

    The HCP carries only **policy** fields — the kind of input the human
    is asked for, which levels are allowed, the default, and pointers to
    a packet template and rationale. Structural information (source
    state, destinations, triggering roles, reversibility) is derived
    from the paired state machine via `StateMachine.gate_*` helpers.
    """

    gate_name: str
    hcp_type: HCPType
    allowed_levels: list[HCPLevel]
    default_level: HCPLevel
    agent_prepares_path: str | None = None
    rationale: str | None = None
    source_doc: str | None = None  # Process doc filename


@dataclass
class HCPCatalog:
    """The set of catalogued HCPs declared in a process doc."""

    process_name: str
    entries: dict[str, HCP] = field(default_factory=dict)  # gate_name -> HCP
    source_path: str | None = None

    def get(self, gate_name: str) -> HCP:
        if gate_name not in self.entries:
            raise KeyError(f"HCP {gate_name!r} not found in catalog for {self.process_name!r}")
        return self.entries[gate_name]

    def has(self, gate_name: str) -> bool:
        return gate_name in self.entries

    def gates(self) -> list[str]:
        return list(self.entries.keys())
