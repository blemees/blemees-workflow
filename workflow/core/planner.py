"""Planner — translates a framework operation request into a `MarkerChange`.

The planner is where the framework's eleven-operation semantics live
(`hitl-principles.md` § 5). Each branch:

1. Validates preconditions against the current `IssueState` and the
   workflow/catalog/grants.
2. Produces the `MarkerChange` the backend should apply atomically.
3. Optionally surfaces the audit-comment text.

It raises `OperationError` on precondition failure; the controller surfaces
the error to the user without touching the backend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from workflow.backends.base import IssueState, MarkerChange
from workflow.core.model.human_gate import HumanGate, HumanGateCatalog
from workflow.core.model.state_machine import (
    ReversibilityClass,
    StateMachine,
    Transition,
    TransitionType,
)
from workflow.core.model.trust_grant import TrustGrant
from workflow.errors import OperationError

logger = logging.getLogger(__name__)


class Operation(Enum):
    """The eleven framework operations plus the three workflow operations.

    These names match `hitl-principles.md` principle 5 and the CLI sub-command
    names (with hyphens vs underscores normalized).
    """

    # StateMachine
    ADVANCE_ISSUE = "advance-issue"
    CLAIM_ISSUE = "claim-issue"
    RELEASE_ISSUE = "release-issue"
    # Catalogued — block-level gate
    AWAIT_SIGNAL = "await-signal"
    REVIEW_BLOCKED = "review-blocked"
    APPROVE_BLOCKED = "approve-blocked"
    REJECT_BLOCKED = "reject-blocked"
    # Catalogued — audit-level gate
    RECORD_ACTION = "record-action"
    REVIEW_AUDIT = "review-audit"
    APPROVE_AUDIT = "approve-audit"
    REJECT_AUDIT = "reject-audit"
    # Recognized — input request
    REQUEST_INPUT = "request-input"
    REVIEW_REQUEST = "review-request"
    RESPOND_REQUEST = "respond-request"
    # Creating — opens a new issue (no in-place mutation of issue_id)
    SPAWN_ISSUE = "spawn-issue"
    # Fan-in — marks a contributor as collected by a collector
    COLLECT_INTO = "collect-into"


@dataclass(frozen=True)
class OperationRequest:
    operation: Operation
    issue_id: str
    # Optional parameters used by various operations.
    transition_label: str | None = None
    gate: str | None = None
    destination: str | None = None
    role: str | None = None  # for claim
    actor: str | None = None  # who is invoking (role or handle)
    body_text: str | None = None  # comment body content (CLI reads --body/--body-from)
    topic: str | None = None  # for request-input
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CreationSpec:
    """Describes a new issue/PR a creating operation opens.

    The controller's create-then-apply path turns this into a
    `backend.create_issue` / `create_pull_request` call. `change` on the same
    plan (if non-empty) is then applied to the new issue; for spawn it is empty
    because the only marker — `parent-of:<parent>` — rides in `extra_labels`.
    """

    title: str
    body: str
    state: str
    entity: str = "issue"  # "issue" | "pull_request"
    issue_type: str | None = None  # framework type id (already encoded in extra_labels)
    github_issue_type: str | None = None  # native GitHub Issue Type name, when used
    extra_labels: tuple[str, ...] = ()
    head: str | None = None  # PR source branch
    base: str | None = None  # PR target branch
    draft: bool = False


@dataclass(frozen=True)
class OperationPlan:
    operation: Operation
    change: MarkerChange
    audit_comment: str
    packet_body: str | None = None  # additional comment, when applicable
    # Set by creating operations (spawn / create). When present the controller
    # opens a new issue from this spec instead of mutating `issue_id` in place.
    create: CreationSpec | None = None


def plan_operation(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None = None,
    grants: dict[str, TrustGrant] | None = None,
) -> OperationPlan:
    """Plan an operation against the current issue state.

    The agent-facing operations are `advance`, `claim`, `release`, and
    `request-input`. The planner's `advance` branch consults the catalog and
    dispatches internally to `await-signal` (block) or `record-action` (audit)
    semantics for gated transitions. The agent never invokes `await-signal` or
    `record-action` directly; those remain as internal primitives that
    `_plan_advance` calls into.

    Raises `OperationError` if preconditions are not met. Returns an
    `OperationPlan` whose `operation` field reflects the *resolved* primitive
    (e.g., a user-invoked `advance` on a block-gated transition returns a
    plan with operation=AWAIT_SIGNAL).
    """
    grants = grants or {}
    match request.operation:
        case Operation.ADVANCE_ISSUE:
            return _plan_advance(request, state, state_machine, catalog, grants)
        case Operation.CLAIM_ISSUE:
            return _plan_claim(request, state, state_machine)
        case Operation.RELEASE_ISSUE:
            return _plan_release(request, state, state_machine)
        case Operation.AWAIT_SIGNAL:
            # Internal primitive — exposed for tests/composition; not a CLI command.
            return _plan_await_signal(request, state, state_machine, catalog)
        case Operation.REVIEW_BLOCKED:
            return _plan_review(request, state)
        case Operation.APPROVE_BLOCKED:
            return _plan_approve(request, state, state_machine, catalog)
        case Operation.REJECT_BLOCKED:
            return _plan_reject(request, state, catalog)
        case Operation.RECORD_ACTION:
            # Internal primitive — exposed for tests/composition; not a CLI command.
            return _plan_record_action(request, state, state_machine, catalog)
        case Operation.REVIEW_AUDIT:
            return _plan_audit(request, state)
        case Operation.APPROVE_AUDIT:
            return _plan_confirm(request, state)
        case Operation.REJECT_AUDIT:
            return _plan_revoke(request, state, catalog)
        case Operation.SPAWN_ISSUE:
            return _plan_spawn(request, state)
        case Operation.COLLECT_INTO:
            return _plan_collect(request, state)
        case Operation.REQUEST_INPUT:
            return _plan_request_input(request, state, state_machine)
        case Operation.REVIEW_REQUEST:
            return _plan_advise(request, state)
        case Operation.RESPOND_REQUEST:
            return _plan_respond(request, state)
        case _:
            raise OperationError(f"Unknown operation: {request.operation!r}")


# ----- workflow operations -----


def _is_working_state(state_machine: StateMachine, name: str | None) -> bool:
    """Return True iff `name` is a WORKING state in the state machine."""
    if name is None:
        return False
    from workflow.core.model.state_machine import StateClass

    state = state_machine.states.get(name)
    return state is not None and state.state_class is StateClass.WORKING


def _closing_close_info(state_machine: StateMachine, name: str | None) -> tuple[bool, str | None]:
    """Whether advancing into `name` should close the tracker's issue.

    Returns `(close_issue, close_reason)`. The decision is driven by whether
    the destination is a closing state (`closes` annotation): present means
    close with `closes.reason`, absent means leave the issue open. Work that
    continues elsewhere uses a shared resting state, not a closing one.
    """
    if name is None or name == "[*]":
        return False, None
    state = state_machine.states.get(name)
    if state is None or state.closes is None:
        return False, None
    return True, state.closes.reason


def _require_role_match(
    actor: str | None,
    required: str,
    context: str,
) -> None:
    """Raise OperationError if actor's role doesn't match the required role.

    Both values are normalized (strip placeholder braces, lowercase). If
    `actor` is None, validation is skipped — the agent didn't declare a
    role and we can't check. The caller may pre-require the actor.
    """
    if actor is None:
        return
    actor_norm = actor.strip("{}").strip().lower()
    required_norm = required.strip("{}").strip().lower()
    if actor_norm != required_norm:
        raise OperationError(
            f"Role mismatch: agent is {actor!r}, but {context} requires {required!r}."
        )


def _require_role_in_set(
    actor: str | None,
    allowed: tuple[str, ...],
    context: str,
) -> None:
    """Raise OperationError if actor's role isn't in the allowed set.

    Same normalisation as `_require_role_match`. Empty `allowed` means
    "no role restriction" — the working state is open. None actor skips
    validation (agent didn't declare a role).
    """
    if actor is None or not allowed:
        return
    actor_norm = actor.strip("{}").strip().lower()
    allowed_norm = {r.strip("{}").strip().lower() for r in allowed}
    if actor_norm not in allowed_norm:
        raise OperationError(
            f"Role mismatch: agent is {actor!r}, but {context} only "
            f"permits roles {sorted(allowed_norm)}."
        )


def _find_transition(
    state_machine: StateMachine,
    source: str | None,
    label: str,
) -> Transition:
    candidates = [
        t
        for t in state_machine.transitions
        if t.label == label and (source is None or t.source == source)
    ]
    if not candidates:
        raise OperationError(
            f"No transition found with label {label!r}"
            + (f" from state {source!r}." if source else ".")
        )
    if len(candidates) > 1:
        raise OperationError(
            f"Ambiguous transition label {label!r}: matches {len(candidates)} edges."
        )
    return candidates[0]


def _find_transition_by_destination(
    state_machine: StateMachine,
    source: str | None,
    destination: str,
) -> Transition:
    """Look up a transition by (source, destination) instead of label.

    State names are normalized identifiers; transition labels are free-form
    prose. Looking up by destination is the cleaner UX for the agent.

    Raises OperationError if no matching transition exists, or if multiple
    edges share the same (source, destination) — that latter case is a
    workflow-design smell and is surfaced explicitly.
    """
    candidates = [
        t
        for t in state_machine.transitions
        if t.destination == destination and (source is None or t.source == source)
    ]
    if not candidates:
        outgoing = (
            [t.destination for t in state_machine.transitions if t.source == source]
            if source is not None
            else []
        )
        msg = f"No transition found from {source!r} to {destination!r}."
        if outgoing:
            msg += f" Outgoing destinations from {source!r}: {sorted(set(outgoing))}"
        raise OperationError(msg)
    if len(candidates) > 1:
        labels = sorted({t.label for t in candidates})
        raise OperationError(
            f"Multiple transitions match {source!r} → {destination!r}: {labels}. "
            "StateMachine has duplicate (source, destination) edges — clean up the diagram."
        )
    return candidates[0]


def _plan_advance(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
    grants: dict[str, TrustGrant],
) -> OperationPlan:
    """Plan an `advance` request.

    Consults the HumanGate catalog and the team's trust grants to determine whether
    the transition is HITL-gated. Dispatches to:

    - **Ungated:** direct state change.
    - **Block-gated:** apply the awaiting-gate marker; hold the agent's claim;
      do NOT change state. Returns a plan with operation=AWAIT_SIGNAL.
    - **Audit-gated:** change state atomically with the audit-pending marker.
      Returns a plan with operation=RECORD_ACTION.

    The agent never has to know which path applies; the tool reads the
    contract and chooses.
    """
    if not request.destination:
        raise OperationError("advance requires --to (destination state).")
    transition = _find_transition_by_destination(state_machine, state.state, request.destination)
    if state.state is not None and transition.source != state.state:
        raise OperationError(
            f"Cannot advance to {request.destination!r}: current state is "
            f"{state.state!r}, transition source is {transition.source!r}."
        )

    # Look up the HumanGate for this transition, if any.
    gate = _find_gate_for_transition(catalog, transition)

    if gate is None:
        # Ungated direct advance. If it's a claim transition, validate the
        # actor's role and the issue's type against the destination working
        # state's `roles` / `issue_types`.
        is_claim = transition.transition_type is TransitionType.CLAIM
        if is_claim:
            # Advancing over a CLAIM transition behaves exactly like a claim
            # (#11): the issue enters a working state, so per principle 1 it
            # must leave with exactly one owning role and an origin marker —
            # otherwise a later release fails with "no last-state marker".
            if not request.actor:
                raise OperationError(
                    "Advancing over a claim transition requires an acting role "
                    "(pass the agent role, or use claim-issue)."
                )
            if state.agent_claim and state.agent_claim != request.actor:
                raise OperationError(
                    f"Issue already claimed by {state.agent_claim!r}; "
                    f"cannot advance-claim as {request.actor!r}."
                )
            dest_state = state_machine.states.get(transition.destination)
            if dest_state is not None:
                if dest_state.roles:
                    _require_role_in_set(
                        actor=request.actor,
                        allowed=dest_state.roles,
                        context=f"claim transition into {transition.destination!r}",
                    )
                if dest_state.issue_types and state.issue_type is not None:
                    if state.issue_type not in dest_state.issue_types:
                        raise OperationError(
                            f"Issue type {state.issue_type!r} is not "
                            f"accepted by working state "
                            f"{transition.destination!r} "
                            f"(accepts: {sorted(dest_state.issue_types)})."
                        )

        # Per principle 1, leaving a working state clears the claim — the
        # role no longer "owns" the issue once it's resting/closing state.
        leaving_working = _is_working_state(state_machine, transition.source)
        close, reason = _closing_close_info(state_machine, transition.destination)
        dest = state_machine.states.get(transition.destination)
        change = MarkerChange(
            set_state=transition.destination,
            clear_awaiting_gate=True,
            clear_audit_pending=True,
            set_awaiting_input=False,
            clear_agent_claim=leaving_working,
            clear_last_state=leaving_working,
            # Claim semantics for CLAIM transitions (#11): ownership + origin.
            set_agent_claim=request.actor if is_claim else None,
            set_last_state=transition.source if is_claim else None,
            close_issue=close,
            close_reason=reason,
            set_pr_ready=bool(dest and dest.mark_pr_ready),
        )
        audit = (
            f"## state advance: {transition.source} → {transition.destination}\n\n"
            f"Invoked via transition {transition.label!r}."
        )
        return OperationPlan(
            operation=Operation.ADVANCE_ISSUE,
            change=change,
            audit_comment=audit,
        )

    # The transition is HITL-gated. Resolve the effective level.
    from workflow.core.model.human_gate import HumanGateLevel  # local import to avoid cycle

    grant = grants.get(gate.gate_name) if grants else None
    if grant is not None and grant.effective_today:
        effective_level = grant.current_level
    else:
        effective_level = gate.default_level

    # Validate no other gate is already in flight (principle 6).
    if state.awaiting_gate and state.awaiting_gate != gate.gate_name:
        raise OperationError(f"Another HITL gate is already in flight: {state.awaiting_gate!r}.")
    if state.audit_pending:
        raise OperationError(
            f"An audit is pending ({state.audit_pending!r}); cannot start a new gate."
        )

    # Role check: the agent firing the gate must be one of the gate's
    # triggering roles, derived from the source state's `roles` list.
    _require_role_in_set(
        actor=request.actor,
        allowed=state_machine.gate_triggering_roles(gate.gate_name),
        context=f"firing HumanGate gate {gate.gate_name!r}",
    )

    if effective_level is HumanGateLevel.BLOCK:
        return _advance_block_gated(request, state, transition, gate, state_machine)
    else:  # AUDIT
        return _advance_audit_gated(request, state, transition, gate, state_machine)


def _find_gate_for_transition(
    catalog: HumanGateCatalog | None, transition: Transition
) -> HumanGate | None:
    """Find the HumanGate for the transition by looking up the gate name.

    Returns None when there's no catalog, the transition isn't gated, or
    no matching catalog row exists. The validator surfaces mismatches; the
    planner is lenient at runtime — an unmarked transition with no row is
    just ungated.
    """
    if catalog is None or not transition.is_gated or transition.gate_name is None:
        return None
    return catalog.entries.get(transition.gate_name)


def _advance_block_gated(
    request: OperationRequest,
    state: IssueState,
    transition: Transition,
    gate: HumanGate,
    state_machine: StateMachine,
) -> OperationPlan:
    """Block-gated advance: apply awaiting marker, hold claim, no state change."""
    if not request.body_text:
        prepares = gate.agent_prepares_path or "(catalog: agent_prepares not set)"
        raise OperationError(
            f"Transition {transition.label!r} is gated at block level — provide "
            f"--body (or --body-from) with content matching {prepares}."
        )
    packet_body = request.body_text
    change = MarkerChange(set_awaiting_gate=gate.gate_name)
    triggering = ", ".join(state_machine.gate_triggering_roles(gate.gate_name)) or "?"
    destinations = ", ".join(state_machine.gate_destinations(gate.gate_name)) or "?"
    audit = (
        f"## await-signal: {gate.gate_name}\n\n"
        f"Triggered via `advance --transition {transition.label!r}`.\n"
        f"Triggering role(s): {triggering}\n"
        f"Destinations: {destinations}\n"
        f"Agent prepares: {gate.agent_prepares_path or 'n/a'}"
    )
    return OperationPlan(
        operation=Operation.AWAIT_SIGNAL,
        change=change,
        audit_comment=audit,
        packet_body=packet_body,
    )


def _advance_audit_gated(
    request: OperationRequest,
    state: IssueState,
    transition: Transition,
    gate: HumanGate,
    state_machine: StateMachine,
) -> OperationPlan:
    """Audit-gated advance: state changes + audit-pending marker, atomically."""
    if state_machine.gate_reversibility(gate.gate_name) is ReversibilityClass.IRREVERSIBLE:
        raise OperationError(
            f"Gate {gate.gate_name!r} is at audit level but destination is "
            "irreversible — invalid grant per hitl-principles.md#4."
        )
    notes = request.body_text
    # Audit-gated advance always leaves a working state (per principle 2,
    # role-action transitions originate in WORKING); clear the claim.
    close, reason = _closing_close_info(state_machine, transition.destination)
    dest = state_machine.states.get(transition.destination)
    change = MarkerChange(
        set_state=transition.destination,
        set_audit_pending=gate.gate_name,
        clear_agent_claim=True,
        clear_last_state=True,
        close_issue=close,
        close_reason=reason,
        set_pr_ready=bool(dest and dest.mark_pr_ready),
    )
    audit = (
        f"## record-action: {transition.source} → {transition.destination} "
        f"(gate {gate.gate_name!r})\n\n"
        f"Triggered via `advance --transition {transition.label!r}`. "
        "Audit pending under the catalog/grant cadence."
    )
    return OperationPlan(
        operation=Operation.RECORD_ACTION,
        change=change,
        audit_comment=audit,
        packet_body=notes,
    )


def _plan_claim(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
) -> OperationPlan:
    role = request.role or request.actor
    if not role:
        raise OperationError("claim requires an agent role.")
    if state.agent_claim and state.agent_claim != role:
        raise OperationError(
            f"Issue already claimed by {state.agent_claim!r}; cannot claim as {role!r}."
        )
    current_state = state_machine.states.get(state.state) if state.state is not None else None
    if state.state is None:
        raise OperationError("Cannot claim issue without a current workflow state.")
    if current_state is not None and current_state.is_closing:
        raise OperationError(
            f"Cannot claim closing state {state.state!r}; closing states are sinks."
        )
    # Role check is done once the destination is resolved (below), since
    # the role-restriction now lives on the destination working state.

    # Resolve the CLAIM transition. Per principle 3 a claim is always
    # resting → working, so a CLAIM transition out of the current state must
    # exist. If `destination` is explicit, look it up; otherwise auto-pick
    # when exactly one exists.
    new_state = state.state
    transition = None
    if state.state is not None:
        claim_options = [
            t
            for t in state_machine.transitions
            if t.source == state.state and t.transition_type is TransitionType.CLAIM
        ]
        if request.destination:
            matches = [t for t in claim_options if t.destination == request.destination]
            if not matches:
                outgoing = sorted({t.destination for t in claim_options})
                raise OperationError(
                    f"No CLAIM transition from {state.state!r} to {request.destination!r}. "
                    f"Available claim destinations: {outgoing or '(none)'}."
                )
            transition = matches[0]
        elif len(claim_options) == 1:
            transition = claim_options[0]
        elif len(claim_options) > 1:
            options = sorted({t.destination for t in claim_options})
            raise OperationError(
                f"Multiple CLAIM transitions from {state.state!r}: {options}. "
                f"Pass --to to disambiguate."
            )
        else:
            raise OperationError(
                f"No CLAIM transition from {state.state!r}; issue cannot be claimed "
                "from this state."
            )
        if transition is not None:
            new_state = transition.destination
            dest_state = state_machine.states.get(transition.destination)
            if dest_state is not None:
                # The destination working state may restrict to a role set.
                if dest_state.roles:
                    _require_role_in_set(
                        actor=role,
                        allowed=dest_state.roles,
                        context=f"claiming into {transition.destination!r}",
                    )
                # And it may restrict to a subset of issue types. Skip the
                # check when the issue's type isn't known (the backend
                # couldn't determine it — e.g., native encoding without a
                # native-type fetch).
                if dest_state.issue_types and state.issue_type is not None:
                    if state.issue_type not in dest_state.issue_types:
                        raise OperationError(
                            f"Issue type {state.issue_type!r} is not "
                            f"accepted by working state "
                            f"{transition.destination!r} "
                            f"(accepts: {sorted(dest_state.issue_types)})."
                        )

    # Record the origin so `release` can return the issue here.
    origin = state.state
    new_dest = state_machine.states.get(new_state) if new_state else None
    change = MarkerChange(
        set_state=new_state if new_state != state.state else None,
        set_agent_claim=role,
        set_last_state=origin,
        set_pr_ready=bool(new_dest and new_dest.mark_pr_ready),
    )
    audit = f"## claim: agent {role!r} claims issue" + (
        f" → {transition.destination!r}" if transition is not None else ""
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_release(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
) -> OperationPlan:
    if not state.agent_claim:
        raise OperationError("Cannot release: no agent claim is active.")
    if state.last_state is None:
        raise OperationError(
            "Cannot release: no last-state marker recorded. The issue must have "
            "been claimed via `workflow claim-issue` (which sets the origin) for "
            "release to know where to return it."
        )
    # Sanity: a CLAIM transition from last_state → current must exist. If not,
    # the marker has drifted and we'd put the issue in an invalid state.
    if state.state is not None:
        valid = any(
            t.source == state.last_state
            and t.destination == state.state
            and t.transition_type is TransitionType.CLAIM
            for t in state_machine.transitions
        )
        if not valid:
            raise OperationError(
                f"last-state marker {state.last_state!r} → {state.state!r} does "
                f"not match any CLAIM transition. The marker has drifted; "
                f"investigate before releasing."
            )
    change = MarkerChange(
        set_state=state.last_state,
        clear_agent_claim=True,
        clear_last_state=True,
    )
    audit = f"## release: agent {state.agent_claim!r} releases issue → {state.last_state!r}"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


# ----- catalogued block operations -----


def _require_gate(catalog: HumanGateCatalog | None, gate_name: str) -> HumanGate:
    if catalog is None:
        raise OperationError(f"No HumanGate catalog loaded; cannot validate gate {gate_name!r}.")
    if not catalog.has(gate_name):
        raise OperationError(
            f"Gate {gate_name!r} is not in the catalog for {catalog.process_name!r}."
        )
    return catalog.get(gate_name)


def _plan_await_signal(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
) -> OperationPlan:
    if not request.gate:
        raise OperationError("await-signal requires --gate.")
    gate = _require_gate(catalog, request.gate)
    gate_source = state_machine.gate_source(gate.gate_name)
    if state.state and gate_source and state.state != gate_source:
        raise OperationError(
            f"Cannot await-signal for gate {gate.gate_name!r}: "
            f"current state is {state.state!r}; gate fires from {gate_source!r}."
        )
    if state.awaiting_gate and state.awaiting_gate != gate.gate_name:
        # Principle 6 — one HITL gate in flight at a time.
        raise OperationError(f"Another HITL gate is already in flight: {state.awaiting_gate!r}.")
    if state.audit_pending:
        raise OperationError(
            f"An audit is pending ({state.audit_pending!r}); cannot start a new gate."
        )

    packet_body = request.body_text
    if packet_body is None and gate.agent_prepares_path:
        logger.info(
            "await-signal for %r: no packet body provided; catalog points at %s",
            gate.gate_name,
            gate.agent_prepares_path,
        )

    change = MarkerChange(set_awaiting_gate=gate.gate_name)
    triggering = ", ".join(state_machine.gate_triggering_roles(gate.gate_name)) or "?"
    destinations = ", ".join(state_machine.gate_destinations(gate.gate_name)) or "?"
    audit = (
        f"## await-signal: {gate.gate_name}\n\n"
        f"Triggering role(s): {triggering}\n"
        f"Destinations: {destinations}\n"
        f"Agent prepares: {gate.agent_prepares_path or 'n/a'}"
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
        packet_body=packet_body,
    )


def _plan_review(request: OperationRequest, state: IssueState) -> OperationPlan:
    if not state.awaiting_gate:
        raise OperationError("Cannot review: no catalogued gate is awaiting a signal.")
    if state.reviewing:
        raise OperationError("Review claim is already held.")
    if state.auditing or state.advising:
        raise OperationError("Another human-claim singleton is active (auditing/advising).")
    change = MarkerChange(set_reviewing=True)
    audit = f"## review: claim taken on gate {state.awaiting_gate!r}"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_approve(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
) -> OperationPlan:
    if not request.gate:
        raise OperationError("approve requires --gate.")
    gate = _require_gate(catalog, request.gate)
    if state.awaiting_gate != gate.gate_name:
        raise OperationError(
            f"Cannot approve {gate.gate_name!r}: current awaiting gate is {state.awaiting_gate!r}."
        )
    destination = request.destination
    gate_destinations = state_machine.gate_destinations(gate.gate_name)
    is_verdict_style = state_machine.gate_is_verdict_style(gate.gate_name)
    if not is_verdict_style:
        if not gate_destinations:
            raise OperationError(
                f"Gate {gate.gate_name!r} has no destinations declared on the state machine."
            )
        only = gate_destinations[0]
        if destination is None:
            destination = only
        if destination != only:
            raise OperationError(
                f"approve destination {destination!r} does not match the gate's "
                f"binary destination {only!r}."
            )
    else:
        if not destination:
            raise OperationError(f"Verdict-style gate {gate.gate_name!r} requires --destination.")
        if destination not in gate_destinations:
            raise OperationError(
                f"Destination {destination!r} is not among the gate's options "
                f"{gate_destinations!r}."
            )
    # Approve fires the gated transition; the agent who was holding the
    # working state is now done, so clear the claim. The original source
    # (the working state) was the agent's seat.
    close, reason = _closing_close_info(state_machine, destination)
    dest = state_machine.states.get(destination)
    change = MarkerChange(
        set_state=destination,
        clear_awaiting_gate=True,
        set_reviewing=False,
        record_approval=gate.gate_name,
        clear_agent_claim=True,
        clear_last_state=True,
        close_issue=close,
        close_reason=reason,
        set_pr_ready=bool(dest and dest.mark_pr_ready),
    )
    audit = (
        f"## approve: gate {gate.gate_name!r} → {destination}\n\nAuthorized via approve operation."
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_reject(
    request: OperationRequest,
    state: IssueState,
    catalog: HumanGateCatalog | None,
) -> OperationPlan:
    if not request.gate:
        raise OperationError("reject requires --gate.")
    gate = _require_gate(catalog, request.gate)
    if state.awaiting_gate != gate.gate_name:
        raise OperationError(
            f"Cannot reject {gate.gate_name!r}: current awaiting gate is {state.awaiting_gate!r}."
        )
    feedback = request.body_text
    # State unchanged; clear queue and review markers; agent retains its claim
    # (principle 7).
    change = MarkerChange(
        clear_awaiting_gate=True,
        set_reviewing=False,
        record_rejection=gate.gate_name,
    )
    audit = (
        f"## reject: gate {gate.gate_name!r}\n\n"
        "State unchanged; agent retains claim and iterates on the packet."
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
        packet_body=feedback,
    )


# ----- catalogued audit operations -----


def _plan_record_action(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
) -> OperationPlan:
    if not request.gate:
        raise OperationError("record-action requires --gate.")
    gate = _require_gate(catalog, request.gate)
    if state_machine.gate_reversibility(gate.gate_name) is ReversibilityClass.IRREVERSIBLE:
        raise OperationError(
            "record-action requires a reversible destination (hitl-principles.md#4)."
        )
    destination = request.destination
    gate_destinations = state_machine.gate_destinations(gate.gate_name)
    if not state_machine.gate_is_verdict_style(gate.gate_name):
        destination = destination or (gate_destinations[0] if gate_destinations else None)
    else:
        if not destination:
            raise OperationError(
                f"Verdict-style audit gate {gate.gate_name!r} requires --destination."
            )
        if destination not in gate_destinations:
            raise OperationError(f"Destination {destination!r} is not in {gate_destinations!r}.")
    if request.transition_label:
        transition = _find_transition(state_machine, state.state, request.transition_label)
        if transition.destination != destination:
            raise OperationError(
                f"transition destination {transition.destination!r} differs from "
                f"resolved gate destination {destination!r}."
            )
    if state.audit_pending and state.audit_pending != gate.gate_name:
        raise OperationError(f"Another audit is pending ({state.audit_pending!r}).")
    # Record-action leaves the working state (same shape as audit-gated advance).
    close, reason = _closing_close_info(state_machine, destination)
    change = MarkerChange(
        set_state=destination,
        set_audit_pending=gate.gate_name,
        clear_agent_claim=True,
        clear_last_state=True,
        close_issue=close,
        close_reason=reason,
    )
    audit = (
        f"## record-action: {state.state} → {destination} (gate {gate.gate_name!r})\n\n"
        "Agent acted; audit pending under cadence in the catalog/grant."
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_audit(request: OperationRequest, state: IssueState) -> OperationPlan:
    if not state.audit_pending:
        raise OperationError("Cannot audit: no audit-pending marker is active.")
    if state.auditing:
        raise OperationError("Audit claim is already held.")
    if state.reviewing or state.advising:
        raise OperationError("Another human-claim singleton is active (reviewing/advising).")
    change = MarkerChange(set_auditing=True)
    audit = f"## audit: claim taken on {state.audit_pending!r}"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_confirm(request: OperationRequest, state: IssueState) -> OperationPlan:
    if not request.gate:
        raise OperationError("check requires --gate.")
    if state.audit_pending != request.gate:
        raise OperationError(
            f"Cannot check {request.gate!r}: audit-pending is {state.audit_pending!r}."
        )
    change = MarkerChange(
        clear_audit_pending=True,
        set_auditing=False,
        record_confirm=request.gate,
    )
    audit = f"## check: {request.gate!r} confirmed post-hoc"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_revoke(
    request: OperationRequest,
    state: IssueState,
    catalog: HumanGateCatalog | None,
) -> OperationPlan:
    if not request.gate:
        raise OperationError("revoke requires --gate.")
    if state.audit_pending != request.gate:
        raise OperationError(
            f"Cannot revoke {request.gate!r}: audit-pending is {state.audit_pending!r}."
        )
    concern = request.body_text
    change = MarkerChange(
        clear_audit_pending=True,
        set_auditing=False,
        record_revoke=request.gate,
    )
    on_revoke = ""
    if catalog is not None and catalog.has(request.gate):
        gate = catalog.get(request.gate)
        if gate.rationale:
            on_revoke = "\n\nSee catalog rationale for on_revoke procedure."
    audit = (
        f"## revoke: {request.gate!r}\n\n"
        f"Audit-level action declared wrong; remediation procedure fires.{on_revoke}"
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
        packet_body=concern,
    )


# ----- recognized operations -----


def _plan_request_input(
    request: OperationRequest,
    state: IssueState,
    state_machine: StateMachine,
) -> OperationPlan:
    if state.awaiting_input:
        raise OperationError("Already awaiting input on this issue.")
    if state.awaiting_gate or state.audit_pending:
        raise OperationError(
            "A catalogued gate is in flight; resolve it before request-input "
            "(hitl-principles.md#6)."
        )
    if not request.body_text:
        raise OperationError("request-input requires --body or --body-from.")

    # The current state must be a working state that declares human_inputs.
    # The topic must be one of the declared ids — no free-form fallback.
    current = state_machine.states.get(state.state) if state.state else None
    if current is None or not current.human_inputs:
        raise OperationError(
            f"State {state.state!r} does not declare `human_inputs`; "
            f"`request-input` is not available here. Either declare topics "
            f"on the state (and one in the shared `human-inputs.json`) or "
            f"release the issue."
        )
    if not request.topic:
        raise OperationError(
            f"request-input requires --topic. Allowed topics at "
            f"{state.state!r}: {sorted(current.human_inputs)}."
        )
    if request.topic not in current.human_inputs:
        raise OperationError(
            f"Topic {request.topic!r} is not declared on state "
            f"{state.state!r}. Allowed: {sorted(current.human_inputs)}."
        )

    question = request.body_text
    change = MarkerChange(
        set_awaiting_input=True,
        set_human_input=request.topic,
    )
    audit = (
        f"## request-input: {request.topic}\n\n"
        f"Agent requests human input on topic `{request.topic}`."
    )
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
        packet_body=question,
    )


def _plan_advise(request: OperationRequest, state: IssueState) -> OperationPlan:
    if not state.awaiting_input:
        raise OperationError("Cannot advise: no awaiting-input marker active.")
    if state.advising:
        raise OperationError("Advise claim is already held.")
    if state.reviewing or state.auditing:
        raise OperationError("Another human-claim singleton is active (reviewing/auditing).")
    change = MarkerChange(set_advising=True)
    audit = "## advise: claim taken on recognized HITL moment"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
    )


def _plan_respond(request: OperationRequest, state: IssueState) -> OperationPlan:
    if not state.awaiting_input:
        raise OperationError("Cannot resolve: no awaiting-input marker active.")
    if not request.body_text:
        raise OperationError("respond requires --body or --body-from.")
    response = request.body_text
    change = MarkerChange(
        set_awaiting_input=False,
        set_advising=False,
        clear_human_input=True,
        record_response=True,
    )
    audit = "## resolve: recognized HITL moment closed"
    return OperationPlan(
        operation=request.operation,
        change=change,
        audit_comment=audit,
        packet_body=response,
    )


def _plan_spawn(request: OperationRequest, parent_state: IssueState) -> OperationPlan:
    """Plan a spawn — open a child issue/PR from the parent's `spawns` config.

    Pure: the impure cross-process resolution (which spawn rule, which target
    process, the issue-type entry / native GitHub type, the entity kind) happens
    at the operation boundary and arrives via `request.extras`:

    - `spawn`              — the chosen Spawn rule (issue_type, initial_state)
    - `parent_process`     — name of the parent's process (for the footer)
    - `entity`             — "issue" | "pull_request"
    - `github_issue_type`  — native GitHub Issue Type name, or None
    - `title` / `head` / `base` — optional CLI inputs

    The child carries only `parent-of:<parent>` (ADR-0003); the parent is left
    untouched (empty `change`).
    """
    if parent_state.state is None:
        raise OperationError("Parent issue has no state; cannot resolve spawn config.")
    extras = request.extras
    spawn = extras["spawn"]
    parent_process = extras.get("parent_process", "")
    entity = extras.get("entity", "issue")
    head = extras.get("head")
    base = extras.get("base")
    if entity == "pull_request" and not head:
        raise OperationError("--head is required for pr-typed spawns (PRs need a source branch).")

    parent_id = request.issue_id
    title = extras.get("title") or f"{parent_state.state} follow-up for #{parent_id}"
    footer = (
        f"\n\n---\n\n"
        f"**Spawned from**: state `{parent_state.state}` of process "
        f"`{parent_process}` (parent #{parent_id}).\n\n"
        f"Refs #{parent_id}\n"
    )
    body = (request.body_text or "").rstrip() + footer

    extra_labels = [f"parent-of:{parent_id}"]
    if entity != "pull_request":
        extra_labels.append(f"type:{spawn.issue_type}")

    spec = CreationSpec(
        title=title,
        body=body,
        state=spawn.initial_state,
        entity=entity,
        issue_type=spawn.issue_type,
        github_issue_type=extras.get("github_issue_type"),
        extra_labels=tuple(extra_labels),
        head=head,
        base=base,
        draft=entity == "pull_request" and spawn.initial_state == "draft",
    )
    audit = (
        f"## spawn: {spawn.issue_type} → {spawn.initial_state}\n\n"
        f"Child spawned from #{parent_id} (state `{parent_state.state}`)."
    )
    return OperationPlan(
        operation=Operation.SPAWN_ISSUE,
        change=MarkerChange(),
        audit_comment=audit,
        create=spec,
    )


def _plan_collect(request: OperationRequest, contributor_state: IssueState) -> OperationPlan:
    """Plan a collect — mark a contributor as gathered into a collector.

    Pure: the impure resolution (which collector, its `collects` config) happens
    at the operation boundary and arrives via `request.extras`:

    - `collector_id`  — the collector issue's id
    - `from_states`   — resting states the collector gathers from
    - `issue_types`   — types the collector accepts (empty = any)
    - `force`         — bypass eligibility checks

    The contributor (`request.issue_id`) is stamped `collected-by:<collector>`
    (ADR-0003); the collector is not touched.
    """
    extras = request.extras
    collector_id = extras["collector_id"]
    from_states = tuple(extras.get("from_states", ()))
    issue_types = tuple(extras.get("issue_types") or ())
    force = bool(extras.get("force", False))

    if contributor_state.state is None:
        raise OperationError(f"Contributor #{request.issue_id} has no state; cannot collect it.")
    if not force:
        if from_states and contributor_state.state not in from_states:
            raise OperationError(
                f"Contributor #{request.issue_id} is on {contributor_state.state!r}, "
                f"not one of the collector's from_states {list(from_states)}. "
                f"Pass --force to override."
            )
        if (
            issue_types
            and contributor_state.issue_type is not None
            and contributor_state.issue_type not in issue_types
        ):
            raise OperationError(
                f"Contributor #{request.issue_id} is type "
                f"{contributor_state.issue_type!r}, not one the collector accepts "
                f"{list(issue_types)}. Pass --force to override."
            )
        if contributor_state.collected_by is not None:
            raise OperationError(
                f"Contributor #{request.issue_id} is already collected by "
                f"#{contributor_state.collected_by}. Pass --force to override."
            )

    return OperationPlan(
        operation=Operation.COLLECT_INTO,
        change=MarkerChange(set_collected_by=collector_id),
        audit_comment=f"## collect: into #{collector_id}",
    )
