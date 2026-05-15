"""HCP catalog model — the per-gate catalog rows from process docs.

Mirrors `hitl-principles.md` principle 8 (catalog row schema) and principle 5
(the eleven-operation vocabulary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from workflow.core.model.state_machine import ReversibilityClass


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

    `destinations` lists one state for binary HCPs and multiple for
    verdict-style. The `approve` operation always names a specific destination
    (which is the human's choice).
    """

    gate_name: str
    source_state: str
    destinations: list[str]
    triggering_role: str
    hcp_type: HCPType
    reversibility: ReversibilityClass  # For verdict-style, the worst-case among destinations
    allowed_levels: list[HCPLevel]
    default_level: HCPLevel
    agent_prepares_path: str | None = None
    rationale: str | None = None
    source_doc: str | None = None  # Process doc filename

    @property
    def is_verdict_style(self) -> bool:
        return len(self.destinations) > 1

    @property
    def is_binary(self) -> bool:
        return len(self.destinations) == 1

    def can_relax_to_audit(self) -> bool:
        """Audit is allowed only when the destination is reversible.

        For verdict-style HCPs, the worst-case destination's class governs (per
        hitl-principles.md principle 4 + 8).
        """
        return (
            self.reversibility is not ReversibilityClass.IRREVERSIBLE
            and HCPLevel.AUDIT in self.allowed_levels
        )


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
