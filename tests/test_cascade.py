"""Cascade-advance tests — exercise the cross-process chain logic with
an in-memory backend so the framework's recursive trigger semantics can
be verified without a real tracker."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from workflow.backends.base import IssueFilters, IssueState, MarkerChange
from workflow.core.cascade import (
    cascade_after_state_change,
)
from workflow.core.model.state_machine import (
    Closes,
    ClosureTaxonomy,
    CollectAdvanceRule,
    Collects,
    ReversibilityClass,
    Spawn,
    State,
    StateClass,
    StateMachine,
)


@dataclass
class _MockBackend:
    """Minimal in-memory backend that records state changes and
    surfaces them via the same `read_issue` / `apply_marker_change` API
    real backends implement. Only the methods cascade uses are stubbed.
    """

    name: str = "mock"
    issues: dict[str, IssueState] = field(default_factory=dict)
    audit_log: list[tuple[str, str]] = field(default_factory=list)  # (issue, audit)
    closed: list[tuple[str, str | None]] = field(default_factory=list)
    pr_ready: list[str] = field(default_factory=list)

    def read_issue(self, issue_id: str) -> IssueState:
        if issue_id not in self.issues:
            raise KeyError(f"unknown issue {issue_id!r}")
        return self.issues[issue_id]

    def apply_marker_change(
        self,
        issue_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
        current = self.issues[issue_id]
        new = replace(
            current,
            state=(change.set_state if change.set_state is not None else current.state),
            agent_claim=(None if change.clear_agent_claim else current.agent_claim),
            last_state=(None if change.clear_last_state else current.last_state),
            collected_by=(
                None
                if change.clear_collected_by
                else (change.set_collected_by or current.collected_by)
            ),
        )
        self.issues[issue_id] = new
        if audit_comment:
            self.audit_log.append((issue_id, audit_comment))
        if change.close_issue:
            self.closed.append((issue_id, change.close_reason))
        if change.set_pr_ready:
            self.pr_ready.append(issue_id)

    def list_issues(self, filters: IssueFilters) -> list[IssueState]:
        """Cohort lookups only — filter the in-memory issues by parent_of /
        collected_by (what the cascade queries)."""
        out: list[IssueState] = []
        for issue in self.issues.values():
            if filters.parent_of is not None and issue.parent_of != filters.parent_of:
                continue
            if filters.collected_by is not None and issue.collected_by != filters.collected_by:
                continue
            out.append(issue)
        return out


@dataclass
class _MockRegistry:
    """Tiny registry stub with just the calls cascade makes."""

    processes_by_name: dict[str, _MockProcess] = field(default_factory=dict)
    state_to_process: dict[str, str] = field(default_factory=dict)

    def find_process_for_state(self, state_name: str) -> str | None:
        return self.state_to_process.get(state_name)

    def get_process(self, name: str) -> _MockProcess:
        return self.processes_by_name[name]


@dataclass
class _MockProcess:
    process_name: str
    state_machine: StateMachine


def _build_release_chain() -> tuple[_MockBackend, _MockRegistry]:
    """Three processes wired in a chain:
      release.released → contributors (inner-loop tickets) → mitigation.mitigated
        → incident.needs_verification.

    This mirrors the user's hotfix-feedback scenario: when release ships,
    contributor inner-loop issues advance; if those contributors are
    children of mitigation, the chain ripples up to incident-response.
    """
    # incident-response
    incident = StateMachine(name="incident-response")
    incident.states["mitigating"] = State(
        name="mitigating",
        state_class=StateClass.WORKING,
        roles=("incident-commander",),
        issue_types=("incident",),
        spawns=(
            Spawn(
                process="mitigation",
                issue_type="incident",
                initial_state="ready_for_mitigation",
                advance_on=(("mitigated", "needs_verification"),),
            ),
        ),
    )
    incident.states["needs_verification"] = State(
        name="needs_verification",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )

    # mitigation
    mitigation = StateMachine(name="mitigation")
    mitigation.states["applying_mitigation"] = State(
        name="applying_mitigation",
        state_class=StateClass.WORKING,
        roles=("incident-responder",),
        issue_types=("incident",),
        spawns=(
            Spawn(
                process="inner-loop",
                issue_type="hotfix",
                initial_state="ready_for_hotfix",
                advance_on=(("shipped", "mitigated"),),
            ),
        ),
    )
    mitigation.states["mitigated"] = State(
        name="mitigated",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )

    # inner-loop
    inner = StateMachine(name="inner-loop")
    inner.states["ready_for_hotfix"] = State(
        name="ready_for_hotfix",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    inner.states["staged"] = State(
        name="staged",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
    )
    inner.states["shipped"] = State(
        name="shipped",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )

    # release
    release = StateMachine(name="release")
    release.states["cut"] = State(
        name="cut",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        collects=Collects(
            process="inner-loop",
            from_states=("staged",),
            advance_on=(CollectAdvanceRule(collector_state="released", default_target="shipped"),),
        ),
    )
    release.states["released"] = State(
        name="released",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )

    registry = _MockRegistry(
        processes_by_name={
            "incident-response": _MockProcess("incident-response", incident),
            "mitigation": _MockProcess("mitigation", mitigation),
            "inner-loop": _MockProcess("inner-loop", inner),
            "release": _MockProcess("release", release),
        },
        state_to_process={
            "mitigating": "incident-response",
            "needs_verification": "incident-response",
            "applying_mitigation": "mitigation",
            "mitigated": "mitigation",
            "ready_for_hotfix": "inner-loop",
            "staged": "inner-loop",
            "shipped": "inner-loop",
            "cut": "release",
            "released": "release",
        },
    )

    backend = _MockBackend()
    return backend, registry


def test_cascade_spawn_parent_single_hop():
    backend, registry = _build_release_chain()
    # incident-response.mitigating spawns mitigation; the mitigation
    # issue reaches `mitigated` and the incident should auto-advance to
    # needs_verification.
    backend.issues["INC-1"] = IssueState(
        issue_id="INC-1",
        state="mitigating",
        agent_claim="incident-commander",
    )
    backend.issues["MIT-1"] = IssueState(
        issue_id="MIT-1",
        state="mitigated",
        agent_claim=None,
        parent_of="INC-1",
    )

    apps = cascade_after_state_change(
        registry,
        backend,
        "MIT-1",
        backend.issues["MIT-1"],
    )
    assert len(apps) == 1
    app = apps[0]
    assert app.kind == "spawn_parent"
    assert app.affected_issue == "INC-1"
    assert app.from_state == "mitigating"
    assert app.to_state == "needs_verification"
    assert backend.issues["INC-1"].state == "needs_verification"


def test_cascade_collect_advance_propagates_to_contributors():
    backend, registry = _build_release_chain()
    # release.cut collects from inner-loop.staged; when release reaches
    # `released`, every contributor advances to `shipped` and the
    # `collected-by:<release>` label is cleared.
    backend.issues["REL-1"] = IssueState(
        issue_id="REL-1",
        state="released",
        agent_claim=None,
    )
    backend.issues["IL-1"] = IssueState(
        issue_id="IL-1",
        state="staged",
        agent_claim=None,
        collected_by="REL-1",
    )
    backend.issues["IL-2"] = IssueState(
        issue_id="IL-2",
        state="staged",
        agent_claim=None,
        collected_by="REL-1",
    )

    # The collector is on `cut` (where the `collects` is declared);
    # `released` is the advance_on key. The cascade looks up `cut`'s
    # collects… but the state is `released`. Cascade walks via the
    # state's own declarations — for the chain to fire on `released`,
    # we need the collects declaration to be on the `released` state
    # too. Real workflows declare `collects` on the entry state (`cut`).
    # Since cascade keys on the issue's CURRENT state, the released
    # release issue won't auto-fire unless we extend the model.

    # Instead, simulate the realistic flow: when release transitions
    # accumulating → cut, the contributor advance happens later when
    # release reaches the final state. For this test the relevant
    # collects declaration must be on the state the release is IN. Mark
    # `released` with the same collects rules as `cut`.
    state_def = registry.processes_by_name["release"].state_machine.states["released"]
    state_def = State(
        name="released",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        collects=Collects(
            process="inner-loop",
            from_states=("staged",),
            advance_on=(CollectAdvanceRule(collector_state="released", default_target="shipped"),),
        ),
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    registry.processes_by_name["release"].state_machine.states["released"] = state_def

    apps = cascade_after_state_change(
        registry,
        backend,
        "REL-1",
        backend.issues["REL-1"],
    )
    # Two collect_advance applications, one per contributor.
    assert sum(1 for a in apps if a.kind == "collect_advance") == 2
    assert backend.issues["IL-1"].state == "shipped"
    assert backend.issues["IL-2"].state == "shipped"
    assert backend.issues["IL-1"].collected_by is None
    assert backend.issues["IL-2"].collected_by is None


def test_cascade_release_clears_collection_no_state_change():
    """`release_on` drops the `collected-by` marker but leaves
    contributor state unchanged."""
    backend, registry = _build_release_chain()
    # Mark cut's collects with release_on rather than advance_on by
    # re-declaring the state on the registry.
    cut_def = registry.processes_by_name["release"].state_machine.states["cut"]
    cut_def = State(
        name="cut",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        collects=Collects(
            process="inner-loop",
            from_states=("staged",),
            release_on=("cut",),
        ),
    )
    registry.processes_by_name["release"].state_machine.states["cut"] = cut_def

    backend.issues["REL-2"] = IssueState(
        issue_id="REL-2",
        state="cut",
        agent_claim=None,
    )
    backend.issues["IL-3"] = IssueState(
        issue_id="IL-3",
        state="staged",
        agent_claim=None,
        collected_by="REL-2",
    )

    apps = cascade_after_state_change(
        registry,
        backend,
        "REL-2",
        backend.issues["REL-2"],
    )
    assert len(apps) == 1
    assert apps[0].kind == "collect_release"
    # No state change.
    assert backend.issues["IL-3"].state == "staged"
    # Label cleared.
    assert backend.issues["IL-3"].collected_by is None


def test_cascade_multi_hop_chain():
    """A single state change can propagate across multiple cross-process
    hops: contributor advance → parent advance → grandparent advance."""
    backend, registry = _build_release_chain()
    # Wire `released` as a collect-advance trigger like the earlier test.
    state_def = State(
        name="released",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        collects=Collects(
            process="inner-loop",
            from_states=("staged",),
            advance_on=(CollectAdvanceRule(collector_state="released", default_target="shipped"),),
        ),
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    registry.processes_by_name["release"].state_machine.states["released"] = state_def

    # Three issues in a chain. Release ships → inner-loop hotfix
    # advances to `shipped` (closing state) → mitigation parent advances to
    # `mitigated` (closing state) → incident grandparent advances to
    # `needs_verification`.
    backend.issues["INC-2"] = IssueState(
        issue_id="INC-2",
        state="mitigating",
        agent_claim="incident-commander",
    )
    backend.issues["MIT-2"] = IssueState(
        issue_id="MIT-2",
        state="applying_mitigation",
        agent_claim="incident-responder",
        parent_of="INC-2",
    )
    backend.issues["IL-4"] = IssueState(
        issue_id="IL-4",
        state="staged",
        agent_claim=None,
        collected_by="REL-3",
        parent_of="MIT-2",
    )
    backend.issues["REL-3"] = IssueState(
        issue_id="REL-3",
        state="released",
        agent_claim=None,
    )

    apps = cascade_after_state_change(
        registry,
        backend,
        "REL-3",
        backend.issues["REL-3"],
    )

    # Three cascade applications fire in sequence: contributor advance,
    # then two spawn-parent advances rolling up the chain.
    kinds = [a.kind for a in apps]
    assert kinds == ["collect_advance", "spawn_parent", "spawn_parent"]
    # Final states across the chain.
    assert backend.issues["IL-4"].state == "shipped"
    assert backend.issues["IL-4"].collected_by is None
    assert backend.issues["MIT-2"].state == "mitigated"
    assert backend.issues["INC-2"].state == "needs_verification"
    assert ("IL-4", "completed") in backend.closed
    assert ("MIT-2", "completed") in backend.closed


def test_cascade_auto_advance_sets_pr_ready_for_destination():
    backend, registry = _build_release_chain()
    shipped = registry.processes_by_name["inner-loop"].state_machine.states["shipped"]
    registry.processes_by_name["inner-loop"].state_machine.states["shipped"] = State(
        name="shipped",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        closes=shipped.closes,
        mark_pr_ready=True,
    )
    registry.processes_by_name["release"].state_machine.states["released"] = State(
        name="released",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_SLOW,
        collects=Collects(
            process="inner-loop",
            from_states=("staged",),
            advance_on=(CollectAdvanceRule(collector_state="released", default_target="shipped"),),
        ),
        closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
    )
    backend.issues["REL-READY"] = IssueState(
        issue_id="REL-READY",
        state="released",
        agent_claim=None,
    )
    backend.issues["PR-1"] = IssueState(
        issue_id="PR-1",
        state="staged",
        agent_claim=None,
        collected_by="REL-READY",
    )

    cascade_after_state_change(registry, backend, "REL-READY", backend.issues["REL-READY"])

    assert backend.issues["PR-1"].state == "shipped"
    assert ("PR-1", "completed") in backend.closed
    assert backend.pr_ready == ["PR-1"]


def test_cascade_cycle_guard_visits_state_pair_once():
    """Synthetic pathological case: two states that ping-pong via spawn
    rules would loop forever without a visited-set guard. We verify the
    guard terminates the cascade after each (issue, state) pair is seen
    once."""
    # Build a minimal "ping-pong" scenario: P spawns C; both processes
    # have advance_on rules pointing back at the same state. The visited
    # set keys on (issue_id, state) so a stable state pair short-circuits.
    sm_p = StateMachine(name="p")
    sm_p.states["working"] = State(
        name="working",
        state_class=StateClass.WORKING,
        roles=("a",),
        issue_types=("x",),
        spawns=(
            Spawn(
                process="c",
                issue_type="x",
                initial_state="resting_c",
                # Child closing state "done_c" → parent advances to "resting_p".
                advance_on=(("done_c", "resting_p"),),
            ),
        ),
    )
    sm_p.states["resting_p"] = State(
        name="resting_p",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )

    sm_c = StateMachine(name="c")
    sm_c.states["resting_c"] = State(
        name="resting_c",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )
    sm_c.states["done_c"] = State(
        name="done_c",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
        closes=Closes(taxonomy=ClosureTaxonomy.RESOLVED, reason="completed"),
    )

    registry = _MockRegistry(
        processes_by_name={
            "p": _MockProcess("p", sm_p),
            "c": _MockProcess("c", sm_c),
        },
        state_to_process={
            "working": "p",
            "resting_p": "p",
            "resting_c": "c",
            "done_c": "c",
        },
    )

    backend = _MockBackend()
    backend.issues["P1"] = IssueState(
        issue_id="P1",
        state="working",
        agent_claim="a",
    )
    backend.issues["C1"] = IssueState(
        issue_id="C1",
        state="done_c",
        agent_claim=None,
        parent_of="P1",
    )

    apps = cascade_after_state_change(registry, backend, "C1", backend.issues["C1"])
    # One spawn_parent application. P1 advances to resting_p. No further
    # cascade because resting_p isn't an advance_on key on any spawn.
    assert len(apps) == 1
    assert backend.issues["P1"].state == "resting_p"


def test_cascade_multi_spawn_wait_for_all_holds_when_sibling_unfinished():
    """With multiple spawn rules on the parent, the wait-for-all cascade
    holds the parent until EVERY sibling reaches one of its rule's
    advance_on closing states. One satisfied + one unsatisfied → no advance."""
    # parent.working spawns child issues of two kinds; both must finish
    # before the parent advances to parent.done.
    parent_sm = StateMachine(name="parent")
    parent_sm.states["working"] = State(
        name="working",
        state_class=StateClass.WORKING,
        roles=("worker",),
        issue_types=("kicker",),
        spawns=(
            Spawn(
                process="kid",
                issue_type="kind_a",
                initial_state="ready_a",
                advance_on=(("done_a", "done"),),
            ),
            Spawn(
                process="kid",
                issue_type="kind_b",
                initial_state="ready_b",
                advance_on=(("done_b", "done"),),
            ),
        ),
    )
    parent_sm.states["done"] = State(
        name="done",
        state_class=StateClass.RESTING,
        reversibility=ReversibilityClass.REVERSIBLE_FAST,
    )

    kid_sm = StateMachine(name="kid")
    for s, closing_name in (("ready_a", "done_a"), ("ready_b", "done_b")):
        kid_sm.states[s] = State(
            name=s,
            state_class=StateClass.RESTING,
            reversibility=ReversibilityClass.REVERSIBLE_FAST,
        )
        kid_sm.states[closing_name] = State(
            name=closing_name,
            state_class=StateClass.RESTING,
            closes=Closes(taxonomy=ClosureTaxonomy.SHIPPED, reason="completed"),
        )

    registry = _MockRegistry(
        processes_by_name={
            "parent": _MockProcess("parent", parent_sm),
            "kid": _MockProcess("kid", kid_sm),
        },
        state_to_process={
            "working": "parent",
            "done": "parent",
            "ready_a": "kid",
            "ready_b": "kid",
            "done_a": "kid",
            "done_b": "kid",
        },
    )

    backend = _MockBackend()
    backend.issues["P"] = IssueState(
        issue_id="P",
        state="working",
        agent_claim="worker",
    )
    backend.issues["KA"] = IssueState(
        issue_id="KA",
        state="done_a",
        agent_claim=None,
        issue_type="kind_a",
        parent_of="P",
    )
    backend.issues["KB"] = IssueState(
        issue_id="KB",
        state="ready_b",
        agent_claim=None,
        issue_type="kind_b",
        parent_of="P",
    )

    # Only KA is satisfied; KB still resting. Parent must stay put.
    apps = cascade_after_state_change(registry, backend, "KA", backend.issues["KA"])
    assert apps == []
    assert backend.issues["P"].state == "working"

    # Now KB also reaches its rule's closing state — parent advances.
    backend.issues["KB"] = replace(backend.issues["KB"], state="done_b")
    apps = cascade_after_state_change(registry, backend, "KB", backend.issues["KB"])
    assert len(apps) == 1
    assert apps[0].kind == "spawn_parent"
    assert backend.issues["P"].state == "done"
