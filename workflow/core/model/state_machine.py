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

    Six buckets covering the meaningful outcome categories:

    - `shipped`: user-facing change went out.
    - `resolved`: meta-work / internal process complete (postmortem, ADR,
      retro) — no user-facing output.
    - `reverted`: work shipped then withdrawn.
    - `abandoned`: stopped on purpose without shipping (whether at intake
      or in flight — `wont_fix`, `killed`, experiment aborted).
    - `deduplicated`: not shipped because it was a duplicate of other work.
    - `superseded`: work continues on a follow-up issue. Covers both
      same-process iteration (failed experiment → new experiment) and
      cross-process handoff (incident stabilized → postmortem opens).
    """

    SHIPPED = "shipped"
    RESOLVED = "resolved"
    REVERTED = "reverted"
    ABANDONED = "abandoned"
    DEDUPLICATED = "deduplicated"
    SUPERSEDED = "superseded"


class TransitionType(Enum):
    """Per state-machine-principles.md principle 2.

    The four transition types the framework distinguishes. The mermaid parser
    infers the type from label patterns and source/destination state classes;
    the validator enforces principle 2's allowed combinations.
    """

    CLAIM = "claim"  # resting → working
    ADVANCE = "advance"  # working → resting or terminal (agent-driven)
    EVENT = "event"  # system/time event (fired by automation, not an agent)


@dataclass(frozen=True)
class Spawn:
    """Subprocess / spawn contract carried by a working or terminal state.

    On a **working** state: the parent stays in the working state while a
    child issue runs on another process. When the child reaches a terminal,
    the parent auto-advances ONLY if that terminal appears in `advance_on`
    — the field is selective, not exhaustive. Child terminals not in the
    map leave the parent in its current state for the agent to advance
    manually.

    On a **resting** state: the parent waits at this resting queue while
    a child issue runs. When the child reaches a terminal in `advance_on`,
    the framework fires an EVENT-style advance on the parent (no agent
    claim needed). `advance_on` targets MUST be non-working states (you
    can't auto-advance into a working state — that would bypass the
    claim-before-working invariant).

    On a **terminal** state: an "independent spawn" — when the parent
    closes, a new child issue is created. `advance_on` is empty because
    the parent is already terminating; there's nothing left to advance.
    Typical use: postmortem opens when an incident closes.
    """

    process: str            # target process name
    issue_type: str         # issue type to create on the target
    initial_state: str      # initial state to create the child at
    advance_on: tuple[tuple[str, str], ...] = ()  # (child_terminal, parent_next_state)

    def parent_next_state(self, child_terminal: str) -> str | None:
        for k, v in self.advance_on:
            if k == child_terminal:
                return v
        return None


@dataclass(frozen=True)
class State:
    """A node on the workflow diagram.

    Reversibility is required for non-resting states that participate in HITL
    gates; the validator enforces this where it matters.

    `roles` lists every agent role that may occupy this working state.
    Only valid on working states — resting states are open queues, and the
    role-restriction lives on the working state they CLAIM into. Discovery
    queries (`workflow inbox`, `workflow list --role X`) traverse outgoing
    CLAIM transitions to find resting states whose downstream working
    state(s) include role X in `roles`.

    `issue_types` narrows which issue types may occupy this working state.
    Only valid on working states. Empty = accepts any of the process-level
    `issue_types` (the umbrella). Each entry must be present in the
    process-level set (cross-checked by the validator). Runtime: claiming
    an issue into this state requires the issue's type to be in the set.
    """

    name: str
    state_class: StateClass
    reversibility: ReversibilityClass | None = None
    terminal_taxonomy: TerminalTaxonomy | None = None
    roles: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()
    # Handover contract — `handoff: true` on a resting state declares it as
    # the interface between two processes. The same state name appears in
    # the other process(es)' state machines, also with `handoff: true`.
    # The registry resolves the other end(s) by name. Only valid on resting
    # states. Working / terminal states cannot be handover interfaces.
    handoff: bool = False
    # Subprocess / spawn contract. Only valid on working or terminal states.
    # See Spawn docstring for the working-vs-terminal distinction.
    spawns: Spawn | None = None
    # When True, advancing into this state flips the underlying PR from
    # draft to ready-for-review on the tracker (via `gh pr ready` on GitHub).
    # No-op when the issue isn't a pull request. Only valid on resting or
    # working states — terminals have already reached final form.
    mark_pr_ready: bool = False
    # Input topics agents may invoke `request-input` on at this state.
    # Topic ids resolve against the shared `input-topics.json`. Only valid
    # on working states. Empty / absent means `request-input` is forbidden
    # at this state — agents must release the issue if they're stuck.
    input_topics: tuple[str, ...] = ()
    # Backend-specific close reason for terminal states. REQUIRED on
    # terminals — every terminal closes the tracker's issue with this
    # reason (GitHub: "completed" or "not planned"). FORBIDDEN on resting
    # and working states. Cross-process handoffs that keep the same issue
    # open use shared resting states, not terminals.
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

    `gate_name` names the HCP catalog gate this transition fires (when set).
    Presence of `gate_name` IS the HITL marker — the standalone `hitl` flag
    was removed. The validator cross-references the gate name against the
    HCP catalog.

    Gate-sharing rules:

    - A single transition's `gate_name` may match the destination state name
      (a useful convention for binary gates — approval directly implies the
      destination), but doesn't have to.
    - Multiple transitions may share the same `gate_name` only when they
      originate from the **same source state** — this is the verdict-style
      pattern (one gate, several possible destinations, human picks on
      approve). The validator enforces single-origin.
    - Sharing a `gate_name` across transitions with different source states
      is forbidden — the `hitl:awaiting-<gate>` label would be ambiguous.

    Cross-process relationships no longer use a transition type — shared
    handovers live on resting states (`handoff: true`) and subprocess /
    independent spawns live on working / terminal states (`spawns: {...}`).
    """

    source: str  # State name
    destination: str  # State name
    label: str  # The transition label
    transition_type: TransitionType = TransitionType.ADVANCE
    gate_name: str | None = None  # HCP catalog gate_name; presence = HITL-gated

    @property
    def is_gated(self) -> bool:
        return self.gate_name is not None


@dataclass
class StateMachine:
    """A parsed workflow .mermaid file.

    `gates_in_legend` is the strict listing extracted from the legend block — a
    map from gate name to its declared reversibility class. The validator
    cross-checks against `transitions` (every `[hitl]` marker should have a
    legend entry; every legend entry should have a matching marker).

    The HCP catalog path is derived by convention from `name`:
    `<name>-hcps.json`. There's no explicit field for it.

    Issue types live on working states (`State.issue_types`); the process's
    overall accepted set is derived as the union — see `accepted_issue_types`.
    """

    name: str
    states: dict[str, State] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    gates_in_legend: dict[str, ReversibilityClass] = field(default_factory=dict)
    source_path: str | None = None

    @property
    def accepted_issue_types(self) -> list[str]:
        """Sorted union of every working state's `issue_types`. This is the
        umbrella for the process — the set of types that can be created
        with `--to <some resting state of this process>`."""
        seen: set[str] = set()
        for st in self.states.values():
            if st.state_class is StateClass.WORKING:
                seen.update(st.issue_types)
        return sorted(seen)

    def state(self, name: str) -> State:
        if name not in self.states:
            raise KeyError(f"State {name!r} not found in workflow {self.name!r}")
        return self.states[name]

    def gated_transitions(self) -> list[Transition]:
        return [t for t in self.transitions if t.is_gated]

    def transitions_for_gate(self, gate_name: str) -> list[Transition]:
        """Every transition declaring this `gate_name` (1 for binary HCP,
        2+ for verdict-style)."""
        return [t for t in self.transitions if t.gate_name == gate_name]

    def gate_source(self, gate_name: str) -> str | None:
        """Source state of the gate. All transitions sharing a gate MUST
        originate from the same working state (the validator enforces
        this); returns that state name, or None if the gate is unknown."""
        ts = self.transitions_for_gate(gate_name)
        return ts[0].source if ts else None

    def gate_destinations(self, gate_name: str) -> list[str]:
        """All destinations reachable via this gate, declared order."""
        return [t.destination for t in self.transitions_for_gate(gate_name)]

    def gate_triggering_roles(self, gate_name: str) -> tuple[str, ...]:
        """Roles that can trigger this gate — derived from the source
        working state's `roles` list. Empty tuple if the source is
        unknown or has no `roles`."""
        src_name = self.gate_source(gate_name)
        if src_name is None:
            return ()
        src = self.states.get(src_name)
        return src.roles if src is not None else ()

    def gate_reversibility(self, gate_name: str) -> ReversibilityClass | None:
        """Worst-case reversibility across all destinations of the gate
        (per principle 4). Returns None if the gate is unknown or no
        destination declares reversibility."""
        order = (
            ReversibilityClass.IRREVERSIBLE,
            ReversibilityClass.REVERSIBLE_SLOW,
            ReversibilityClass.REVERSIBLE_FAST,
        )
        worst: ReversibilityClass | None = None
        for t in self.transitions_for_gate(gate_name):
            dst = self.states.get(t.destination)
            if dst is None or dst.reversibility is None:
                continue
            if worst is None or order.index(dst.reversibility) < order.index(worst):
                worst = dst.reversibility
        return worst

    def gate_is_verdict_style(self, gate_name: str) -> bool:
        """True iff this gate has more than one destination."""
        return len(self.transitions_for_gate(gate_name)) > 1

    def transitions_from(self, state_name: str) -> list[Transition]:
        return [t for t in self.transitions if t.source == state_name]

    def transitions_to(self, state_name: str) -> list[Transition]:
        return [t for t in self.transitions if t.destination == state_name]
