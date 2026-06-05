"""StateMachine model — states, transitions, and the diagram-level metadata.

Mirrors the framework's `state-machine-principles.md`. The mermaid parser produces
a `StateMachine` from a `.mermaid` file; the validator and operations consume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StateClass(Enum):
    """Per state-machine-principles.md principle 1.

    Every state is one of these two ownership classes. Termination is no
    longer a class — a closing state is a `resting` state carrying a `closes`
    annotation (see `Closes` and ADR-0002).
    """

    RESTING = "resting"
    WORKING = "working"


class ReversibilityClass(Enum):
    """Per hitl-principles.md principle 4.

    Reversibility is a property of the destination state. Transitions inherit
    their destination's class. The class is what constrains HITL levels.
    """

    IRREVERSIBLE = "irreversible"
    REVERSIBLE_FAST = "reversible-fast"
    REVERSIBLE_SLOW = "reversible-slow"


class ClosureTaxonomy(Enum):
    """Per state-machine-principles.md principle 8.

    Every closing state must carry one of these tags. A closing state without a tag
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
    ADVANCE = "advance"  # working → resting or closing state (agent-driven)
    EVENT = "event"  # system/time event (fired by automation, not an agent)


@dataclass(frozen=True)
class Spawn:
    """Subprocess / spawn contract carried by a working or closing state.

    On a **working** state: the parent stays in the working state while a
    child issue runs on another process. When the child reaches a closing state,
    the parent auto-advances ONLY if that closing state appears in `advance_on`
    — the field is selective, not exhaustive. Child closing states not in the
    map leave the parent in its current state for the agent to advance
    manually.

    On a **resting** state: the parent waits at this resting queue while
    a child issue runs. When the child reaches a closing state in `advance_on`,
    the framework fires an EVENT-style advance on the parent (no agent
    claim needed). `advance_on` targets MUST be non-working states (you
    can't auto-advance into a working state — that would bypass the
    claim-before-working invariant).

    On a **closing state** state: an "independent spawn" — when the parent
    closes, a new child issue is created. `advance_on` is empty because
    the parent is already terminating; there's nothing left to advance.
    Typical use: postmortem opens when an incident closes.
    """

    # Target process name. Optional in the authored JSON — when omitted,
    # the validator resolves it from `initial_state` via the
    # state-name-uniqueness invariant (every state belongs to exactly
    # one process, so the initial_state determines the process). When
    # authored, the validator cross-checks the resolved process matches.
    process: str | None = None
    issue_type: str = ""     # issue type to create on the target
    initial_state: str = ""  # initial state to create the child at
    advance_on: tuple[tuple[str, str], ...] = ()  # (child_closing_state, parent_next_state)

    def parent_next_state(self, child_closing_state: str) -> str | None:
        for k, v in self.advance_on:
            if k == child_closing_state:
                return v
        return None


@dataclass(frozen=True)
class CollectAdvanceRule:
    """One entry in `collects.advance_on`.

    When the collector reaches `collector_state`, contributors advance
    to a target state that may depend on the contributor's issue type.
    `default_target` (when set) applies to contributors whose type is
    not explicitly listed in `by_type`; if no default and the type
    isn't listed, no advance fires for that contributor.

    The simple shape `{released: shipped}` parses to a rule with
    `default_target="shipped"` and empty `by_type`. The per-type shape
    `{released: {experiment: measuring, "*": shipped}}` parses to
    `default_target="shipped"` and `by_type=(("experiment", "measuring"),)`.
    """

    collector_state: str
    default_target: str | None = None
    by_type: tuple[tuple[str, str], ...] = ()  # (contributor_type, target_state)

    def target_for(self, contributor_type: str | None) -> str | None:
        if contributor_type is not None:
            for t, tgt in self.by_type:
                if t == contributor_type:
                    return tgt
        return self.default_target

    def all_targets(self) -> tuple[str, ...]:
        """Every target state this rule can advance contributors to —
        the default plus every per-type override. Used by the validator
        and the emitter's collect-feedback-closing state computation."""
        seen: list[str] = []
        if self.default_target is not None and self.default_target not in seen:
            seen.append(self.default_target)
        for _t, tgt in self.by_type:
            if tgt not in seen:
                seen.append(tgt)
        return tuple(seen)


@dataclass(frozen=True)
class Collects:
    """Fan-in contract carried by a resting state — the inverse of `Spawn`.

    Issues created at the host state gather existing issues from another
    process as contributors. Used to model the release-train pattern:
    `release.cut` collects staged inner-loop work.

    Authoring lives on the **receiver** side (mirrors the existing
    handoff/spawn pattern). The framework queries the candidate set at
    `create-issue` time; it does NOT auto-create the collector — the
    human decides when to cut. `from_states` must be resting or closing state
    in the source process (collecting from a working state would
    conflict with that state's claim).

    `advance_on` is selective contributor-side feedback: when the
    collector reaches a listed state, every contributor (bearing the
    `collected-by:<collector>` marker) auto-advances to a target state
    on the source process. The target can be a single string (all
    contributor types advance to the same state) OR a per-type map (a
    contributor's target depends on its issue type, with `*` as the
    catch-all). Targets must be resting or closing state on the source
    process (no auto-enter into working states).
    """

    # Source process name. Optional in authoring — state names are
    # unique workflow-wide, so the validator can derive the source
    # process from `from_states`. Authored values must match the
    # derived value. Stored as `None` when omitted.
    process: str | None = None
    from_states: tuple[str, ...] = ()  # resting/closing states on the source process
    # Optional issue-type filter. When empty, every issue type accepted
    # by the source process is eligible. When set, candidates must
    # carry one of the listed types. Use this when one process has
    # multiple "flavors" of collectors targeting the same from_state
    # (e.g., a `cut` release collecting bug/feature/chore PRs and a
    # `hotfix_cut` release collecting only hotfix PRs).
    issue_types: tuple[str, ...] = ()
    advance_on: tuple[CollectAdvanceRule, ...] = ()
    # Collector states that **drop** the collection without moving the
    # contributors. The contributor's state is unchanged; the framework
    # clears the `collected-by:<collector>` label so the contributor is
    # eligible for future collectors. Use this when the collector
    # outcome (e.g., release abandoned) means "this collection didn't
    # happen — the items are still candidates."
    release_on: tuple[str, ...] = ()

    def contributor_next_state(
        self, collector_state: str, contributor_type: str | None = None
    ) -> str | None:
        for rule in self.advance_on:
            if rule.collector_state == collector_state:
                return rule.target_for(contributor_type)
        return None

    def releases_on(self, collector_state: str) -> bool:
        return collector_state in self.release_on


@dataclass(frozen=True)
class Closes:
    """Closing annotation on a resting state (per ADR-0002).

    Presence of `closes` makes a resting state a closing state — a sink that
    closes the tracker's issue on entry. `taxonomy` tags the outcome (per
    principle 8) and `reason` is the backend close reason (GitHub:
    "completed" / "not planned"). Replaces the former `closing state` state class.
    """

    taxonomy: ClosureTaxonomy
    reason: str


@dataclass(frozen=True)
class State:
    """A node on the workflow diagram.

    Reversibility is required for non-resting states that participate in HITL
    gates; the validator enforces this where it matters.

    `roles` lists every agent role that may occupy this working state.
    Only valid on working states — resting states are open queues, and the
    role-restriction lives on the working state they CLAIM into. Discovery
    queries (`workflow view-inbox`, `workflow search-issues --claim X`) traverse outgoing
    CLAIM transitions to find resting states whose downstream working
    state(s) include role X in `roles`.

    `issue_types` declares which issue types may occupy this state.
    Required on working AND resting; forbidden on closing state.
    - Working: types this state will do work on (claim semantics). The
      process's umbrella accepted-types set is derived as the union
      across all working states.
    - Resting: types that may sit waiting in this state (queue
      semantics). Must be a subset of the umbrella. Spawn-target resting
      states typically declare a single type; shared handoff states
      declare the full set that crosses the interface. The validator
      checks that any spawn's `issue_type` is in the target resting
      state's set.
    """

    name: str
    state_class: StateClass
    reversibility: ReversibilityClass | None = None
    roles: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()
    # Handover contract — `handoff: true` on a resting state declares it as
    # the interface between two processes. The same state name appears in
    # the other process(es)' state machines, also with `handoff: true`.
    # The registry resolves the other end(s) by name. Only valid on resting
    # states. Working / closing states cannot be handover interfaces.
    handoff: bool = False
    # External entry — `is_initial: true` on a resting state declares it as
    # an external entry point. New issues materialize here from outside
    # the workflow (manual `create-issue`, webhook, scheduled job).
    # `initial_label`, when set, names the trigger (e.g. "issue created",
    # "alert fires"). Only valid on resting states; mutually exclusive
    # with `collects` and with being a spawn target — issues either
    # arrive from outside, are gathered via collect, or are spawned.
    is_initial: bool = False
    initial_label: str | None = None
    # Subprocess / spawn contract. Only valid on working / resting /
    # closing states. See Spawn docstring for per-class semantics. A
    # state can declare multiple spawn rules — different kinds of work
    # to dispatch from the same state, fired ad-hoc by the agent. The
    # cascade's wait-for-all rule advances the parent only when every
    # active child issue is in its rule's `advance_on` trigger state.
    spawns: tuple[Spawn, ...] = ()
    # Fan-in contract — only valid on resting states. See Collects docstring.
    # When set, `create-issue --to <this state>` consults this to compute
    # candidate contributor issues from another process.
    collects: Collects | None = None
    # When True, advancing into this state flips the underlying PR from
    # draft to ready-for-review on the tracker (via `gh pr ready` on GitHub).
    # No-op when the issue isn't a pull request. Only valid on resting or
    # working states — closing states have already reached final form.
    mark_pr_ready: bool = False
    # Human inputs agents may invoke `request-input` on at this state.
    # Ids resolve against the shared `human-inputs.json`. Only valid on
    # working states. Empty / absent means `request-input` is forbidden
    # at this state — agents must release the issue if they're stuck.
    human_inputs: tuple[str, ...] = ()
    # Closing annotation (ADR-0002). When set, this resting state is a sink
    # that closes the issue on entry, carrying the outcome taxonomy and the
    # backend close reason (GitHub: "completed" / "not planned"). Cross-process
    # handoffs that keep the issue open use shared resting states, not closing
    # states. See `Closes` and `is_closing`.
    closes: Closes | None = None
    notes: list[str] = field(default_factory=list, hash=False, compare=False)

    @property
    def is_closing(self) -> bool:
        return self.closes is not None

    @property
    def is_irreversible(self) -> bool:
        return self.reversibility is ReversibilityClass.IRREVERSIBLE


@dataclass(frozen=True)
class Transition:
    """A directed edge from one state to another with a label.

    `gate_name` names the human-gate catalog entry this transition fires
    (when set). Presence of `gate_name` IS the HITL marker — the standalone
    `hitl` flag was removed. The validator cross-references the gate name
    against the human-gate catalog.

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
    independent spawns live on working / closing states (`spawns: {...}`).
    """

    source: str  # State name
    destination: str  # State name
    label: str  # The transition label
    transition_type: TransitionType = TransitionType.ADVANCE
    gate_name: str | None = None  # Human-gate catalog gate_name; presence = HITL-gated

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

    The human-gate catalog path is derived by convention from `name`:
    `<name>-human-gates.json`. There's no explicit field for it.

    Issue types live on working states (`State.issue_types`); the process's
    overall accepted set is derived as the union — see `accepted_issue_types`.
    """

    name: str
    description: str | None = None
    # Optional grouping hint for the process map. Processes sharing a
    # `group` value render inside the same Mermaid composite state
    # block (a bordered region). Pure layout sugar — has no semantic
    # effect on transitions, spawns, collects, or the cascade.
    group: str | None = None
    states: dict[str, State] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    gates_in_legend: dict[str, ReversibilityClass] = field(default_factory=dict)
    source_path: str | None = None

    @property
    def accepted_issue_types(self) -> list[str]:
        """Sorted union of every state's `issue_types` (working + resting).
        This is the process's umbrella — the set of types the process
        declares it handles at some point in its lifecycle. Most processes'
        umbrella equals the working union, since resting types are
        typically a subset of working. The exception is a process that
        accepts a type by handoff/collect without ever claiming it into
        a working state (e.g. release carrying dev tickets in `staged`
        until the train ships)."""
        seen: set[str] = set()
        for st in self.states.values():
            seen.update(st.issue_types)
        return sorted(seen)

    def state(self, name: str) -> State:
        if name not in self.states:
            raise KeyError(f"State {name!r} not found in workflow {self.name!r}")
        return self.states[name]

    def gated_transitions(self) -> list[Transition]:
        return [t for t in self.transitions if t.is_gated]

    def transitions_for_gate(self, gate_name: str) -> list[Transition]:
        """Every transition declaring this `gate_name` (1 for binary gate,
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
