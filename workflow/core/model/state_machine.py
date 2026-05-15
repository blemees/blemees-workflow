"""StateMachine model — states, transitions, and the diagram-level metadata.

Mirrors the framework's `state-machine-principles.md`. The mermaid parser produces
a `StateMachine` from a `.mermaid` file; the validator and operations consume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StateClass(Enum):
    """Per state-machine-principles.md principle 1.

    Every state is exactly one of these classes. There are no other state types.
    """

    RESTING = "resting"
    WORKING = "working"
    TERMINAL = "terminal"


class ReversibilityClass(Enum):
    """Per hitl-principles.md principle 4.

    Reversibility is a property of the destination state. Transitions inherit
    their destination's class. The class is what constrains HITL levels.
    """

    IRREVERSIBLE = "irreversible"
    REVERSIBLE_FAST = "reversible-fast"
    REVERSIBLE_SLOW = "reversible-slow"


class TerminalTaxonomy(Enum):
    """Per state-machine-principles.md principle 8.

    Every terminal state must carry one of these tags. A terminal without a tag
    is an authoring error caught by the validator.
    """

    SHIPPED = "shipped"
    REVERTED = "reverted"
    ABANDONED = "abandoned"
    DEDUPLICATED = "deduplicated"
    ITERATED = "iterated"
    ABORTED = "aborted"
    STABILIZED = "stabilized"
    RESOLVED = "resolved"


class TransitionType(Enum):
    """Per state-machine-principles.md principle 2.

    The four transition types the framework distinguishes. The mermaid parser
    infers the type from label patterns and source/destination state classes;
    the validator enforces principle 2's allowed combinations.
    """

    CLAIM = "claim"  # resting → working
    ROLE_ACTION = "role_action"  # working → resting or terminal
    EXTERNAL = "external"  # system/time event
    CROSS_PROCESS = "cross_process"  # to/from another process's workflow


@dataclass(frozen=True)
class State:
    """A node on the workflow diagram.

    Reversibility is required for non-resting states that participate in HITL
    gates; the validator enforces this where it matters.

    `claim_role` names the agent role that claims this state (for resting
    states). It is declared structurally in the workflow file via a note
    such as `note left of raw: claim-role=pm`. Used by discovery queries
    (e.g., `workflow list --role pm` returns items in any state with
    `claim_role == 'pm'` and no current claim).
    """

    name: str
    state_class: StateClass
    reversibility: ReversibilityClass | None = None
    terminal_taxonomy: TerminalTaxonomy | None = None
    claim_role: str | None = None
    # Backend-specific close reason for terminal states. When set, advancing
    # into this state closes the tracker's issue with this reason (e.g.,
    # GitHub's "completed" or "not planned"). When None, the issue stays
    # open — used for handoff-style terminals where the work continues
    # elsewhere. Only valid on terminal states.
    close_reason: str | None = None
    notes: list[str] = field(default_factory=list, hash=False, compare=False)

    @property
    def is_terminal(self) -> bool:
        return self.state_class is StateClass.TERMINAL

    @property
    def is_irreversible(self) -> bool:
        return self.reversibility is ReversibilityClass.IRREVERSIBLE


@dataclass(frozen=True)
class Transition:
    """A directed edge from one state to another with a label.

    `is_gated` is True if the transition carries the `[hitl]` marker in the
    workflow file. The HCP catalog row corresponding to this transition lives
    in the process doc; the validator cross-references the two.

    `cross_process_kind` and `cross_process_other` carry the principle-9
    handoff metadata for CROSS_PROCESS transitions. `kind` is `"shared"`
    (same issue continues on the other process's diagram) or `"spawn"`
    (this transition creates a new issue on the other diagram). `other`
    names the other process. Both are None for non-CROSS_PROCESS transitions.
    """

    source: str  # State name
    destination: str  # State name
    label: str  # The transition label, with [hitl] stripped if present
    is_gated: bool = False
    transition_type: TransitionType = TransitionType.ROLE_ACTION
    gate_name: str | None = None  # HCP catalog gate_name when is_gated
    cross_process_kind: str | None = None  # "shared" | "spawn" | None
    cross_process_other: str | None = None  # name of the other process


@dataclass
class StateMachine:
    """A parsed workflow .mermaid file.

    `canonical_catalog_path` is extracted from the legend comment block's first
    line and points at the canonical HCP catalog location (the process doc
    section). The validator uses this to find the catalog when cross-checking.

    `gates_in_legend` is the strict listing extracted from the legend block — a
    map from gate name to its declared reversibility class. The validator
    cross-checks against `transitions` (every `[hitl]` marker should have a
    legend entry; every legend entry should have a matching marker).
    """

    name: str
    states: dict[str, State] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    canonical_catalog_path: str | None = None
    gates_in_legend: dict[str, ReversibilityClass] = field(default_factory=dict)
    source_path: str | None = None

    def state(self, name: str) -> State:
        if name not in self.states:
            raise KeyError(f"State {name!r} not found in workflow {self.name!r}")
        return self.states[name]

    def gated_transitions(self) -> list[Transition]:
        return [t for t in self.transitions if t.is_gated]

    def transitions_from(self, state_name: str) -> list[Transition]:
        return [t for t in self.transitions if t.source == state_name]

    def transitions_to(self, state_name: str) -> list[Transition]:
        return [t for t in self.transitions if t.destination == state_name]
