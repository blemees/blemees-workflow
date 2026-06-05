"""Validator — static cross-artifact checks against the framework principles.

Produces a list of `ValidationFinding` objects rather than throwing. Callers
(the `validate-workflow` CLI command, the controller's pre-flight check) decide how
to surface them.

Each finding cites the source principle so that drift reports stay legible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from workflow.backends.base import IssueState
from workflow.core.model.human_gate import HumanGateCatalog, HumanGateLevel
from workflow.core.model.human_input import HumanInputDirectory
from workflow.core.model.issue_type import IssueTypeDirectory
from workflow.core.model.state_machine import (
    ReversibilityClass,
    StateClass,
    StateMachine,
    TransitionType,
)
from workflow.core.model.trust_grant import TrustGrant

logger = logging.getLogger(__name__)


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ValidationFinding:
    severity: Severity
    principle_cite: str  # e.g., "state-machine-principles.md#11"
    message: str
    location: str | None = None  # filename or `state:name`, when known

    def __str__(self) -> str:
        loc = f" [{self.location}]" if self.location else ""
        return f"{self.severity.value.upper()}{loc} {self.principle_cite}: {self.message}"


def validate_state_machine(
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
    grants: dict[str, TrustGrant] | None = None,
    issue_type_directory: IssueTypeDirectory | None = None,
    human_input_directory: HumanInputDirectory | None = None,
    handoff_index: dict[str, set[str]] | None = None,
    sibling_machines: dict[str, StateMachine] | None = None,
) -> list[ValidationFinding]:
    """Run every static cross-artifact check; return findings.

    `handoff_index`, when provided, maps `state_name → {process_name, ...}` —
    the set of processes that declare each `handoff: true` state. The
    validator checks every `handoff: true` state in this state machine
    has at least one OTHER process declaring the same state (handovers
    are interfaces and require two parties).
    """
    grants = grants or {}
    findings: list[ValidationFinding] = []

    findings.extend(_check_irreversible_destinations_gated(state_machine))
    findings.extend(_check_closing_states_are_sinks(state_machine))
    findings.extend(_check_closes_exclusivity(state_machine))
    findings.extend(_check_reversibility_declared_on_legend_states(state_machine))
    findings.extend(_check_transition_type_compatibility(state_machine))
    findings.extend(_check_working_states_are_claim_destinations(state_machine))
    findings.extend(_check_level_keywords_not_on_diagram(state_machine))
    findings.extend(_check_issue_types_resolved(state_machine, issue_type_directory))
    findings.extend(_check_human_inputs_resolved(state_machine, human_input_directory))
    findings.extend(_check_gates_have_unique_source(state_machine))
    if handoff_index is not None:
        findings.extend(_check_handoffs_have_partners(state_machine, handoff_index))
    if sibling_machines is not None:
        findings.extend(_check_spawns(state_machine, sibling_machines))
        findings.extend(_check_collects(state_machine, sibling_machines))
        findings.extend(_check_entry_not_also_target(state_machine, sibling_machines))
        findings.extend(_check_process_reachable(state_machine, sibling_machines))

    if catalog is not None:
        findings.extend(_check_legend_catalog_sync(state_machine, catalog))
        findings.extend(_check_audit_irreversible(state_machine, catalog))
        findings.extend(_check_block_on_timeout(catalog, grants))
        findings.extend(_check_agent_prepares_present(catalog))

    findings.extend(_check_trust_grants(state_machine, catalog, grants))

    return findings


def validate_issue_markers(
    state: IssueState,
) -> list[ValidationFinding]:
    """Runtime claim-discipline check: at most one HITL gate is in flight on
    a single issue at a time (`hitl-principles.md` principle 6)."""
    findings: list[ValidationFinding] = []
    in_flight: list[str] = []
    if state.awaiting_gate:
        in_flight.append(f"awaiting:{state.awaiting_gate}")
    if state.audit_pending:
        in_flight.append(f"audit-pending:{state.audit_pending}")
    if len(in_flight) > 1:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                principle_cite="hitl-principles.md#6",
                message=(
                    "Multiple HITL queue markers active on the same issue: "
                    + ", ".join(in_flight)
                ),
                location=state.issue_id,
            )
        )
    # Claim singletons — at most one of reviewing/auditing/advising.
    singletons = [
        ("reviewing", state.reviewing),
        ("auditing", state.auditing),
        ("advising", state.advising),
    ]
    active = [name for name, flag in singletons if flag]
    if len(active) > 1:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                principle_cite="hitl-principles.md#6",
                message=("Multiple human-claim singletons active: " + ", ".join(active)),
                location=state.issue_id,
            )
        )
    return findings


# ----- check helpers -----


def _check_irreversible_destinations_gated(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for t in state_machine.transitions:
        dst_state = state_machine.states.get(t.destination)
        if dst_state is None:
            continue
        if dst_state.reversibility is ReversibilityClass.IRREVERSIBLE and not t.is_gated:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="state-machine-principles.md#11",
                    message=(
                        f"Transition {t.source!r} → {t.destination!r} ({t.label!r}) "
                        "lands in an irreversible state without [hitl]."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_closing_states_are_sinks(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    """A closing state (`closes`) is a sink — it must have no outgoing
    transitions (ADR-0002). Previously implicit (no transition type accepted a
    terminal source); explicit now that closing states are `resting` and an
    EVENT is resting → resting."""
    findings: list[ValidationFinding] = []
    for t in state_machine.transitions:
        source = state_machine.states.get(t.source)
        if source is not None and source.closes is not None:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#1",
                    message=(
                        f"Closing state {t.source!r} has an outgoing transition "
                        f"({t.label!r}); closing states are sinks (no further "
                        "transitions)."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_closes_exclusivity(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    """A closing state (`closes`) is an unowned sink, so it cannot also be an
    external entry, a collector, a handoff interface, or hold `issue_types`;
    and a spawn on a closing state cannot carry `advance_on` (ADR-0002)."""
    findings: list[ValidationFinding] = []
    cite = "state-machine-principles.md#1"
    for state in state_machine.states.values():
        if state.closes is None:
            continue
        conflicts: list[str] = []
        if state.is_initial:
            conflicts.append("is_initial")
        if state.collects is not None:
            conflicts.append("collects")
        if state.handoff:
            conflicts.append("handoff")
        if state.issue_types:
            conflicts.append("issue_types")
        for field_name in conflicts:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite=cite,
                    message=(
                        f"State {state.name!r} has both `closes` and "
                        f"`{field_name}` — a closing state is an unowned sink; "
                        "these are mutually exclusive."
                    ),
                    location=state_machine.source_path,
                )
            )
        if any(sp.advance_on for sp in state.spawns):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite=cite,
                    message=(
                        f"State {state.name!r}: a spawn on a closing state "
                        "cannot carry `advance_on` (the parent is already "
                        "closed)."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_reversibility_declared_on_legend_states(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    # Every gate in the legend that resolves to a state should have that
    # state's reversibility declared (either on the state or in the legend).
    for gate, _rev in state_machine.gates_in_legend.items():
        state = state_machine.states.get(gate)
        if state is not None and state.reversibility is None:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"Legend names gate {gate!r} but the state has no "
                        "reversibility class declared on the diagram."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_legend_catalog_sync(
    state_machine: StateMachine, catalog: HumanGateCatalog
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    legend_gates = set(state_machine.gates_in_legend.keys())
    catalog_gates = set(catalog.entries.keys())
    marker_gates = {
        t.destination if "→" not in t.label else t.destination
        for t in state_machine.transitions
        if t.is_gated
    }

    # The strict requirement: legend ↔ catalog ↔ markers all agree
    # (hitl-principles.md#5/#9). When pre-HITL artifacts have none of the
    # three, we don't flag — only flag drift between sets that have content.
    if legend_gates or catalog_gates or marker_gates:
        if legend_gates ^ catalog_gates:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="hitl-principles.md#5",
                    message=(
                        "Legend gates and catalog gates differ. "
                        f"Legend-only: {sorted(legend_gates - catalog_gates)}, "
                        f"Catalog-only: {sorted(catalog_gates - legend_gates)}"
                    ),
                    location=catalog.source_path or state_machine.source_path,
                )
            )
    return findings


def _check_audit_irreversible(
    state_machine: StateMachine, catalog: HumanGateCatalog
) -> list[ValidationFinding]:
    """Audit-level on an irreversible destination is forbidden (principle 4).
    Reversibility is derived from the gate's destination state(s)."""
    findings: list[ValidationFinding] = []
    for gate in catalog.entries.values():
        rev = state_machine.gate_reversibility(gate.gate_name)
        if rev is not ReversibilityClass.IRREVERSIBLE:
            continue
        if gate.default_level is HumanGateLevel.AUDIT:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"HumanGate {gate.gate_name!r} declares default_level=audit "
                        "but its destination is irreversible (derived). "
                        "Irreversible destinations require block."
                    ),
                    location=catalog.source_path,
                )
            )
        if HumanGateLevel.AUDIT in gate.allowed_levels:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"HumanGate {gate.gate_name!r} lists audit as an allowed "
                        "level but the destination is irreversible (derived)."
                    ),
                    location=catalog.source_path,
                )
            )
    return findings


def _check_block_on_timeout(
    catalog: HumanGateCatalog, grants: dict[str, TrustGrant]
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    # We can only check the trust-grant side — block defaults at the catalog
    # level have no timeout (timeout: none); the principle bites only when a
    # team has relaxed the timeout.
    for gate, grant in grants.items():
        if grant.current_level is HumanGateLevel.BLOCK:
            on_timeout = grant.parameters.on_timeout
            if on_timeout is None:
                continue
            if on_timeout not in ("abort", "escalate"):
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="hitl-principles.md#4",
                        message=(
                            f"Trust grant for {gate!r} has on_timeout={on_timeout!r}. "
                            "block-level on_timeout must be 'abort' or 'escalate'."
                        ),
                        location=grant.source_path,
                    )
                )
    return findings


def _check_agent_prepares_present(catalog: HumanGateCatalog) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for gate in catalog.entries.values():
        if not gate.agent_prepares_path:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="hitl-principles.md#8",
                    message=(
                        f"HumanGate {gate.gate_name!r} has no 'Agent prepares' pointer. "
                        "Add a reference to the artifact-template file."
                    ),
                    location=catalog.source_path,
                )
            )
    return findings


def _check_trust_grants(
    state_machine: StateMachine,
    catalog: HumanGateCatalog | None,
    grants: dict[str, TrustGrant],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    today = date.today()
    for gate, grant in grants.items():
        if grant.expires_at < today:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="trust-grant-schema.md#7",
                    message=(
                        f"Trust grant for {gate!r} expired on {grant.expires_at}. "
                        "Default level resumes until re-justified."
                    ),
                    location=grant.source_path,
                )
            )
        if catalog is not None and gate in catalog.entries:
            gate = catalog.entries[gate]
            if grant.current_level not in gate.allowed_levels:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="trust-grant-schema.md#7",
                        message=(
                            f"Trust grant for {gate!r} requests level "
                            f"{grant.current_level.value!r} but HumanGate allows "
                            f"{[lvl.value for lvl in gate.allowed_levels]}."
                        ),
                        location=grant.source_path,
                    )
                )
            if (
                grant.current_level is HumanGateLevel.AUDIT
                and state_machine.gate_reversibility(gate)
                is ReversibilityClass.IRREVERSIBLE
            ):
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="trust-grant-schema.md#7",
                        message=(
                            f"Trust grant for {gate!r} relaxes to audit but "
                            "destination is irreversible."
                        ),
                        location=grant.source_path,
                    )
                )
        if not grant.evidence:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="trust-grant-schema.md#7",
                    message=(f"Trust grant for {gate!r} has no evidence entries."),
                    location=grant.source_path,
                )
            )
    return findings


def _check_transition_type_compatibility(state_machine: StateMachine) -> list[ValidationFinding]:
    """Per state-machine-principles.md #2, each transition type has strict
    source/destination state-class rules:

    - CLAIM: resting → working
    - ADVANCE: working → resting | terminal
    - EVENT: resting → resting | terminal

    `[*]` endpoints are no longer authored as transition endpoints —
    entries are declared via `State.is_initial` and terminal sinks are
    implicit. Source/destination are real state names here. ERROR-level
    — these are hard structural rules. Cross-process relationships are
    not transitions (see `State.handoff` and `State.spawns` for the data
    model).
    """
    findings: list[ValidationFinding] = []
    for t in state_machine.transitions:
        src_state = state_machine.states.get(t.source)
        dst_state = state_machine.states.get(t.destination)

        # CLAIM: resting → working
        if t.transition_type is TransitionType.CLAIM:
            if src_state is not None and src_state.state_class is not StateClass.RESTING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Claim transition {t.source!r} → {t.destination!r} ({t.label!r}) "
                            f"must originate in a RESTING state; source is "
                            f"{src_state.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )
            if dst_state is not None and dst_state.state_class is not StateClass.WORKING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Claim transition {t.source!r} → {t.destination!r} ({t.label!r}) "
                            f"must land in a WORKING state; destination is "
                            f"{dst_state.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )

        # ADVANCE: working → resting | terminal
        elif t.transition_type is TransitionType.ADVANCE:
            if src_state is not None and src_state.state_class is not StateClass.WORKING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Role-action transition {t.source!r} → {t.destination!r} "
                            f"({t.label!r}) must originate in a WORKING state; source is "
                            f"{src_state.state_class.value}. The agent must claim before "
                            f"acting (principle 3)."
                        ),
                        location=state_machine.source_path,
                    )
                )
            if dst_state is not None and dst_state.state_class is StateClass.WORKING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Role-action transition {t.source!r} → {t.destination!r} "
                            f"({t.label!r}) must land in a RESTING or TERMINAL state; "
                            f"destination is {dst_state.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )

        # EVENT: resting → resting | terminal (system / time trigger).
        elif t.transition_type is TransitionType.EVENT:
            if src_state is not None and src_state.state_class is not StateClass.RESTING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Event transition {t.source!r} → {t.destination!r} "
                            f"({t.label!r}) must originate in a RESTING state; "
                            f"source is {src_state.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )
            if dst_state is not None and dst_state.state_class is StateClass.WORKING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Event transition {t.source!r} → {t.destination!r} "
                            f"({t.label!r}) must land in a RESTING or TERMINAL state; "
                            f"destination is {dst_state.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )

    return findings


def _check_working_states_are_claim_destinations(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    """Per state-machine-principles.md #3, agents must claim before working.

    Every WORKING state must be the destination of at least one CLAIM
    transition. A working state with no incoming claim is unreachable via
    the documented protocol — either the agent skips the claim (anti-pattern)
    or the state's purpose is misclassified.

    ERROR-level. Empty / skeletal workflows with no transitions yet are
    exempt (the validator can't reason about future structure).
    """
    findings: list[ValidationFinding] = []
    if not state_machine.transitions:
        return findings
    for state in state_machine.states.values():
        if state.state_class is not StateClass.WORKING:
            continue
        claim_incoming = [
            t
            for t in state_machine.transitions
            if t.destination == state.name and t.transition_type is TransitionType.CLAIM
        ]
        if not claim_incoming:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#3",
                    message=(
                        f"Working state {state.name!r} has no CLAIM transition into it. "
                        f"Every working state must be entered via a `{{role}} claims …` "
                        f"transition; the bounce-at-the-gate anti-pattern is forbidden."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


# Matches HITL level keywords used as standalone tokens (e.g.,
# `level=block`, `block-level`, `audit cadence`). The leading boundary is
# `\b`; the trailing context excludes false positives like the word `block`
# inside larger identifiers. Case-insensitive.
_LEVEL_KEYWORD_RE = re.compile(r"\b(block|audit)\b(?!\w)", re.IGNORECASE)


def _check_level_keywords_not_on_diagram(state_machine: StateMachine) -> list[ValidationFinding]:
    """Per hitl-principles.md #11.4 (and state-machine-principles.md #11),
    HITL level information (`block`, `audit`) is a runtime property
    declared in trust grants, never on the diagram. Notes that leak level
    keywords couple the diagram to one team's policy.

    Scans every state's note lines for standalone `block` or `audit`
    tokens. WARNING-level — these may be incidental word choice (e.g., a
    state literally named `block`), but the principle is strict so we
    surface every match for human review.
    """
    findings: list[ValidationFinding] = []
    for state in state_machine.states.values():
        for line in state.notes:
            for match in _LEVEL_KEYWORD_RE.finditer(line):
                keyword = match.group(0).lower()
                findings.append(
                    ValidationFinding(
                        severity=Severity.WARNING,
                        principle_cite="state-machine-principles.md#11",
                        message=(
                            f"Note on state {state.name!r} mentions HITL level keyword "
                            f"{keyword!r}: {line!r}. Level information belongs in trust "
                            f"grants, not on the diagram."
                        ),
                        location=state_machine.source_path,
                    )
                )
                break  # one finding per note line is enough
    return findings


def _check_issue_types_resolved(
    state_machine: StateMachine,
    issue_type_directory: IssueTypeDirectory | None,
) -> list[ValidationFinding]:
    """Every issue type referenced by any state must resolve in the
    issue-types directory.

    Severities:
    - Any state declares `issue_types` but no directory loaded: WARNING.
    - State references an unknown type id: ERROR.

    There is intentionally no "resting must be a subset of working"
    rule: a process can legitimately accept a type via handoff or
    collect without ever claiming it into a working state (e.g. release
    carrying dev tickets in `staged` until the train ships).
    """
    findings: list[ValidationFinding] = []
    state_types: set[str] = set()
    for st in state_machine.states.values():
        state_types.update(st.issue_types)

    if not state_types:
        return findings

    if issue_type_directory is None:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                principle_cite="state-machine-principles.md#1",
                message=(
                    f"Process working states reference issue_types "
                    f"{sorted(state_types)} but no issue-types.json was "
                    f"found. Types cannot be resolved to backend identifiers."
                ),
                location=state_machine.source_path,
            )
        )
        return findings

    for type_id in sorted(state_types):
        if not issue_type_directory.has(type_id):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#1",
                    message=(
                        f"References issue type {type_id!r} but it is not "
                        f"defined in {issue_type_directory.source_path or 'issue-types.json'}."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_handoffs_have_partners(
    state_machine: StateMachine,
    handoff_index: dict[str, set[str]],
) -> list[ValidationFinding]:
    """Every `handoff: true` resting state must be declared in at least one
    OTHER process. A handover with no partner is misconfigured.
    """
    findings: list[ValidationFinding] = []
    for state in state_machine.states.values():
        if not state.handoff:
            continue
        partners = handoff_index.get(state.name, set()) - {state_machine.name}
        if not partners:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#9",
                    message=(
                        f"State {state.name!r} is marked `handoff: true` "
                        f"but no other process declares the same state. A "
                        f"handover requires at least two parties — either "
                        f"add the state (with `handoff: true`) to the "
                        f"receiving / sending process, or remove the flag "
                        f"if this isn't actually a cross-process interface."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_spawns(
    state_machine: StateMachine,
    sibling_machines: dict[str, StateMachine],
) -> list[ValidationFinding]:
    """Validate every state's `spawns` declarations against target processes.

    A state can declare one or many spawn rules. For each:

    - `process` may be authored or omitted; if omitted, it is resolved
      from `initial_state` (every state belongs to exactly one process).
      If authored and the resolution disagrees, ERROR.
    - issue_type must be in the target's accepted_issue_types.
    - initial_state must exist on the target and be RESTING.
    - Working / resting-state spawns: each advance_on key must be a
      terminal on the target; each value must be a state on this
      process. Resting-state advance_on values must be non-working.
    - Within a single state, no two spawns may share
      (issue_type, initial_state) — that pair is the runtime
      disambiguator the CLI uses at spawn time.
    - Across all spawns on a single state, the set of advance_on
      target states must be a singleton (every rule that fires
      advances the parent to the same target). The cascade's
      wait-for-all rule depends on this.

    The validator now mutates each `Spawn` in place to fill in the
    resolved process — no, frozen dataclass, can't mutate. Instead the
    validator updates the state machine's state to replace the spawn
    tuple with resolved-process copies. (Run before any code that
    relies on `spawn.process` being populated.)
    """
    findings: list[ValidationFinding] = []
    # Build a state-name → process-name index once.
    state_to_process: dict[str, str] = {}
    for proc_name, machine in sibling_machines.items():
        for state_name in machine.states:
            state_to_process[state_name] = proc_name

    for state in state_machine.states.values():
        if not state.spawns:
            continue
        # Within-state uniqueness on (issue_type, initial_state).
        seen_keys: set[tuple[str, str]] = set()
        for sp in state.spawns:
            key = (sp.issue_type, sp.initial_state)
            if key in seen_keys:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: two `spawns` entries share "
                            f"(issue_type={sp.issue_type!r}, "
                            f"initial_state={sp.initial_state!r}) — the CLI "
                            f"disambiguator can't distinguish them at spawn time."
                        ),
                        location=state_machine.source_path,
                    )
                )
            seen_keys.add(key)

        # Cross-spawn advance_on targets must be a singleton.
        targets_seen: set[str] = set()
        for sp in state.spawns:
            for _term, parent_next in sp.advance_on:
                targets_seen.add(parent_next)
        if len(targets_seen) > 1:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#9",
                    message=(
                        f"State {state.name!r}: spawn rules disagree on the "
                        f"advance_on target — found {sorted(targets_seen)}. "
                        f"The cascade's wait-for-all advance can only fire "
                        f"if every rule's advance_on value points at the same "
                        f"parent state."
                    ),
                    location=state_machine.source_path,
                )
            )

        for sp in state.spawns:
            # Resolve process if omitted.
            resolved_process: str | None = sp.process
            if resolved_process is None:
                resolved_process = state_to_process.get(sp.initial_state)
                if resolved_process is None:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: `spawns.initial_state` "
                                f"{sp.initial_state!r} does not exist on any "
                                f"known process. Author `process` explicitly or "
                                f"check the state name."
                            ),
                            location=state_machine.source_path,
                        )
                    )
                    continue
            elif sp.process is not None:
                # Authored process must match resolution.
                resolved_from_initial = state_to_process.get(sp.initial_state)
                if (
                    resolved_from_initial is not None
                    and resolved_from_initial != sp.process
                ):
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: `spawns.process` "
                                f"{sp.process!r} disagrees with the process "
                                f"that owns `initial_state` "
                                f"{sp.initial_state!r} ({resolved_from_initial!r}). "
                                f"Drop `process` (it's derived) or fix the mismatch."
                            ),
                            location=state_machine.source_path,
                        )
                    )

            target = sibling_machines.get(resolved_process)
            if target is None:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: spawns target process "
                            f"{resolved_process!r} which is not a known process."
                        ),
                        location=state_machine.source_path,
                    )
                )
                continue
            if sp.issue_type not in target.accepted_issue_types:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `spawns.issue_type` "
                            f"{sp.issue_type!r} is not accepted by process "
                            f"{resolved_process!r} (accepts: "
                            f"{target.accepted_issue_types})."
                        ),
                        location=state_machine.source_path,
                    )
                )
            target_initial = target.states.get(sp.initial_state)
            if target_initial is None:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `spawns.initial_state` "
                            f"{sp.initial_state!r} is not declared on process "
                            f"{resolved_process!r}."
                        ),
                        location=state_machine.source_path,
                    )
                )
            elif target_initial.state_class is not StateClass.RESTING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `spawns.initial_state` "
                            f"{sp.initial_state!r} on process "
                            f"{resolved_process!r} must be resting, not "
                            f"{target_initial.state_class.value}."
                        ),
                        location=state_machine.source_path,
                    )
                )
            elif (
                target_initial.issue_types
                and sp.issue_type not in target_initial.issue_types
            ):
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `spawns.issue_type` "
                            f"{sp.issue_type!r} is not in the target resting "
                            f"state's `issue_types` "
                            f"({list(target_initial.issue_types)} on "
                            f"{resolved_process!r}.{sp.initial_state!r}). "
                            f"Update one side so the spawn lands in a state "
                            f"that accepts this type."
                        ),
                        location=state_machine.source_path,
                    )
                )

            # advance_on checks per rule. A spawned child's lifecycle
            # can cross processes via handoff, so the terminal can live
            # on any sibling process — not necessarily the spawn target.
            # Example: mitigation spawns a hotfix into inner-loop, but
            # the hotfix's terminal (`shipped`) is in release because
            # `inner-loop.staged` is a handoff to release.
            if not state.is_closing and sp.advance_on:
                global_closing_states: set[str] = set()
                for sibling in sibling_machines.values():
                    for s in sibling.states.values():
                        if s.is_closing:
                            global_closing_states.add(s.name)
                declared_closing_states = {k for k, _ in sp.advance_on}
                unknown = declared_closing_states - global_closing_states
                if unknown:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: `spawns.advance_on` "
                                f"references state(s) {sorted(unknown)} that "
                                f"aren't closing states on any known process."
                            ),
                            location=state_machine.source_path,
                        )
                    )
                for child_term, parent_next in sp.advance_on:
                    parent_next_state = state_machine.states.get(parent_next)
                    if parent_next_state is None:
                        findings.append(
                            ValidationFinding(
                                severity=Severity.ERROR,
                                principle_cite="state-machine-principles.md#9",
                                message=(
                                    f"State {state.name!r}: `spawns.advance_on"
                                    f"[{child_term!r}]` → {parent_next!r} is "
                                    f"not a state on this process."
                                ),
                                location=state_machine.source_path,
                            )
                        )
                        continue
                    if (
                        state.state_class is StateClass.RESTING
                        and parent_next_state.state_class is StateClass.WORKING
                    ):
                        findings.append(
                            ValidationFinding(
                                severity=Severity.ERROR,
                                principle_cite="state-machine-principles.md#3",
                                message=(
                                    f"State {state.name!r}: resting-state "
                                    f"`spawns.advance_on[{child_term!r}]` → "
                                    f"{parent_next!r} would auto-advance into a "
                                    f"working state, bypassing the "
                                    f"claim-before-working invariant. "
                                    f"Auto-advance must land on a resting or "
                                    f"terminal state."
                                ),
                                location=state_machine.source_path,
                            )
                        )
    return findings


def _check_collects(
    state_machine: StateMachine,
    sibling_machines: dict[str, StateMachine],
) -> list[ValidationFinding]:
    """Validate every state's `collects` declaration against the source process.

    - Only valid on resting states (parser already enforces; reasserted
      here for cross-artifact consistency).
    - Source process must exist in the sibling machines map.
    - Every `from_states` entry must exist on the source process AND be
      resting or terminal (collecting from a working state would conflict
      with that state's claim).
    """
    findings: list[ValidationFinding] = []
    # Build a state-name → process-name index so we can resolve omitted
    # `collects.process` from `from_states[0]` (state names are unique
    # across the workflow).
    state_to_process: dict[str, str] = {}
    for proc_name, machine in sibling_machines.items():
        for st_name in machine.states:
            state_to_process[st_name] = proc_name

    for state in state_machine.states.values():
        collects = state.collects
        if collects is None:
            continue
        if state.state_class is not StateClass.RESTING:
            # Parser catches this; redundant guard for downstream callers.
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#9",
                    message=(
                        f"State {state.name!r}: `collects` is only valid on "
                        f"resting states (got {state.state_class.value!r})."
                    ),
                    location=state_machine.source_path,
                )
            )
            continue

        # Resolve the source process: derive from from_states[0] when
        # not authored; cross-check authored values against derivation.
        resolved_process: str | None = collects.process
        if resolved_process is None:
            if collects.from_states:
                resolved_process = state_to_process.get(collects.from_states[0])
            if resolved_process is None:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.from_states` "
                            f"references state(s) not declared on any known "
                            f"process; cannot derive `collects.process`."
                        ),
                        location=state_machine.source_path,
                    )
                )
                continue
        else:
            for fs in collects.from_states:
                derived = state_to_process.get(fs)
                if derived is not None and derived != resolved_process:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: authored "
                                f"`collects.process` {resolved_process!r} "
                                f"disagrees with the process that owns "
                                f"`from_states` entry {fs!r} ({derived!r}). "
                                f"Drop `process` (it's derived) or fix the "
                                f"mismatch."
                            ),
                            location=state_machine.source_path,
                        )
                    )

        target = sibling_machines.get(resolved_process)
        if target is None:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#9",
                    message=(
                        f"State {state.name!r}: `collects.process` "
                        f"{resolved_process!r} is not a known process."
                    ),
                    location=state_machine.source_path,
                )
            )
            continue
        for from_state_name in collects.from_states:
            from_state = target.states.get(from_state_name)
            if from_state is None:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.from_states` "
                            f"entry {from_state_name!r} is not declared on "
                            f"process {resolved_process!r}."
                        ),
                        location=state_machine.source_path,
                    )
                )
                continue
            if from_state.state_class is StateClass.WORKING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.from_states` "
                            f"entry {from_state_name!r} is a working state "
                            f"on {resolved_process!r}. Collect only from "
                            f"resting or terminal states (working-state "
                            f"items are already claimed)."
                        ),
                        location=state_machine.source_path,
                    )
                )

        # `issue_types` entries must be a subset of the source process's
        # accepted_issue_types (derived from its working states).
        if collects.issue_types:
            source_types = set(target.accepted_issue_types)
            unknown = set(collects.issue_types) - source_types
            if unknown:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.issue_types` "
                            f"contains {sorted(unknown)} which are not "
                            f"accepted by process {resolved_process!r} "
                            f"(accepts: {sorted(source_types)})."
                        ),
                        location=state_machine.source_path,
                    )
                )

        # `advance_on` keys must be declared states on THIS process (the
        # collector); values must be declared resting or terminal states
        # on the source process (no auto-enter into working).
        for rule in collects.advance_on:
            if rule.collector_state not in state_machine.states:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.advance_on` key "
                            f"{rule.collector_state!r} is not a declared state "
                            f"on this process."
                        ),
                        location=state_machine.source_path,
                    )
                )
            # Validate every target (default + per-type) — each must
            # exist on the source process and be resting/terminal. Also
            # validate per-type keys are real issue types accepted by
            # the source process.
            targets: list[tuple[str, str]] = []  # (label, target_state)
            if rule.default_target is not None:
                targets.append(("*", rule.default_target))
            for type_key, target_state in rule.by_type:
                targets.append((type_key, target_state))
                if type_key not in target.accepted_issue_types:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: "
                                f"`collects.advance_on[{rule.collector_state!r}]"
                                f"[{type_key!r}]` references contributor type "
                                f"{type_key!r} which is not accepted by "
                                f"process {resolved_process!r} (accepts: "
                                f"{target.accepted_issue_types})."
                            ),
                            location=state_machine.source_path,
                        )
                    )
            for label, target_state in targets:
                contributor_state = target.states.get(target_state)
                if contributor_state is None:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#9",
                            message=(
                                f"State {state.name!r}: `collects.advance_on"
                                f"[{rule.collector_state!r}]"
                                f"[{label!r}]` → {target_state!r} is not a "
                                f"declared state on process "
                                f"{resolved_process!r}."
                            ),
                            location=state_machine.source_path,
                        )
                    )
                    continue
                if contributor_state.state_class is StateClass.WORKING:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#3",
                            message=(
                                f"State {state.name!r}: `collects.advance_on"
                                f"[{rule.collector_state!r}]"
                                f"[{label!r}]` → {target_state!r} on "
                                f"{resolved_process!r} must be resting or "
                                f"terminal — auto-advancing contributors into "
                                f"a working state would bypass the "
                                f"claim-before-working invariant."
                            ),
                            location=state_machine.source_path,
                        )
                    )

        # `release_on` entries must be declared states on THIS process.
        # The label-clearing applies regardless of where the contributor
        # currently is, so there's no need to validate against the source
        # process; the trigger condition is just "collector enters this
        # state".
        for collector_state in collects.release_on:
            if collector_state not in state_machine.states:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#9",
                        message=(
                            f"State {state.name!r}: `collects.release_on` "
                            f"entry {collector_state!r} is not a declared "
                            f"state on this process."
                        ),
                        location=state_machine.source_path,
                    )
                )
    return findings


def _check_entry_not_also_target(
    state_machine: StateMachine,
    sibling_machines: dict[str, StateMachine],
) -> list[ValidationFinding]:
    """External entry, spawn target, and collector are mutually exclusive.

    `is_initial: true` declares "issues materialize at this state from
    outside the workflow" (manual `create-issue`, webhook, scheduler).
    A spawn target lands here because some upstream process created it.
    A `collects` state is reached by gathering existing items from
    another process. Each describes a different entry path; combining
    them implies an issue can arrive via two paths simultaneously, which
    is ambiguous about provenance. Author the one that describes the
    true origin.
    """
    findings: list[ValidationFinding] = []
    entry_states = {s.name for s in state_machine.states.values() if s.is_initial}
    if not entry_states:
        return findings

    # collects on the same state: contradiction.
    for state_name in entry_states:
        state = state_machine.states.get(state_name)
        if state is not None and state.collects is not None:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#2",
                    message=(
                        f"State {state_name!r} declares `initial` AND "
                        f"`collects`. These describe contradictory entry "
                        f"paths (external creation vs. gathering existing "
                        f"items). Pick one: drop `initial`, or drop "
                        f"`collects`."
                    ),
                    location=state_machine.source_path,
                )
            )

    # The state is the initial_state of a spawn from any sibling.
    inbound_spawn_targets: set[str] = set()
    # Build the state-to-process map once to resolve spawn.process when
    # the author omits it.
    state_to_process: dict[str, str] = {}
    for proc_name, machine in sibling_machines.items():
        for state_name in machine.states:
            state_to_process[state_name] = proc_name
    for other in sibling_machines.values():
        if other.name == state_machine.name:
            continue
        for s in other.states.values():
            for sp in s.spawns:
                resolved = sp.process or state_to_process.get(sp.initial_state)
                if resolved == state_machine.name:
                    inbound_spawn_targets.add(sp.initial_state)
    for state_name in entry_states & inbound_spawn_targets:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                principle_cite="state-machine-principles.md#2",
                message=(
                    f"State {state_name!r} declares `initial` AND is a "
                    f"spawn target from another process. An issue here "
                    f"either materializes from outside or is created by "
                    f"spawn — pick one."
                ),
                location=state_machine.source_path,
            )
        )
    return findings


def _check_process_reachable(
    state_machine: StateMachine,
    sibling_machines: dict[str, StateMachine],
) -> list[ValidationFinding]:
    """Warn if the process has no way for issues to arrive.

    A process is reachable if at least one of the following is true:
    - it has a state with `is_initial: true` (an external entry point);
    - it has a state declaring `collects` (the human creates collectors
      here via `create-issue --to <state>` — a legitimate entry path);
    - it is the target of a `spawns.process` field on some sibling's state;
    - it shares a `handoff: true` resting state with at least one sibling
      (the issue arrives via the cross-process handover).

    Truly orphan processes (none of the above) are almost certainly an
    authoring oversight — they're loaded but unreachable. The warning
    surfaces this so the author can either add `initial`, a `collects`
    declaration, a spawn from somewhere, or remove the process.
    """
    findings: list[ValidationFinding] = []

    has_initial = any(s.is_initial for s in state_machine.states.values())
    if has_initial:
        return findings  # External entry — already reachable.

    # Any state declares `collects`? Creating a collector at that state
    # IS the entry path for this process.
    has_collects = any(s.collects is not None for s in state_machine.states.values())
    if has_collects:
        return findings

    # Is some other process spawning into us?
    # Build the state-to-process map once so spawns omitting `process`
    # can still resolve to a target.
    state_to_process: dict[str, str] = {}
    for proc_name, machine in sibling_machines.items():
        for state_name in machine.states:
            state_to_process[state_name] = proc_name
    has_inbound_spawn = False
    for other in sibling_machines.values():
        if other.name == state_machine.name:
            continue
        for s in other.states.values():
            for sp in s.spawns:
                resolved = sp.process or state_to_process.get(sp.initial_state)
                if resolved == state_machine.name:
                    has_inbound_spawn = True
                    break
            if has_inbound_spawn:
                break
        if has_inbound_spawn:
            break
    if has_inbound_spawn:
        return findings

    # Do we share a handoff state with anyone?
    our_handoffs = {s.name for s in state_machine.states.values() if s.handoff}
    has_handoff_partner = False
    for other in sibling_machines.values():
        if other.name == state_machine.name:
            continue
        for s in other.states.values():
            if s.handoff and s.name in our_handoffs:
                has_handoff_partner = True
                break
        if has_handoff_partner:
            break
    if has_handoff_partner:
        return findings

    findings.append(
        ValidationFinding(
            severity=Severity.WARNING,
            principle_cite="state-machine-principles.md#2",
            message=(
                f"Process {state_machine.name!r} has no `initial` state, "
                f"no `collects` declaration, no inbound spawn from any "
                f"other process, and no shared handoff state. New issues "
                f"cannot reach it. Mark an entry state with `\"initial\": "
                f"true` (or `\"initial\": \"<label>\"`), declare "
                f"`collects`, wire it as a spawn target, or share a "
                f"handoff state."
            ),
            location=state_machine.source_path,
        )
    )
    return findings


def _check_gates_have_unique_source(
    state_machine: StateMachine,
) -> list[ValidationFinding]:
    """A gate fires from exactly one source state.

    Multiple transitions can share a gate name only when they're verdict-style
    (same source, different destinations — the human picks the destination
    on approve). Sharing a gate across different source states is forbidden:
    the gate's `hitl:awaiting-<gate>` label would be ambiguous about which
    transition fires.
    """
    findings: list[ValidationFinding] = []
    sources_per_gate: dict[str, set[str]] = {}
    for t in state_machine.transitions:
        if t.gate_name is None:
            continue
        sources_per_gate.setdefault(t.gate_name, set()).add(t.source)
    for gate, sources in sources_per_gate.items():
        if len(sources) > 1:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#6",
                    message=(
                        f"Gate {gate!r} fires from multiple source states "
                        f"{sorted(sources)}. A gate must originate from "
                        f"exactly one source state — pick one or rename the "
                        f"gates so each has a single origin."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings


def _check_human_inputs_resolved(
    state_machine: StateMachine,
    directory: HumanInputDirectory | None,
) -> list[ValidationFinding]:
    """Every topic id referenced by a working state must resolve in the
    shared `human-inputs.json`. Missing directory + referenced topics is
    a WARNING; declared id absent from directory is an ERROR."""
    findings: list[ValidationFinding] = []
    referenced: set[str] = set()
    for st in state_machine.states.values():
        referenced.update(st.human_inputs)
    if not referenced:
        return findings

    if directory is None:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                principle_cite="hitl-principles.md#7",
                message=(
                    f"Process working states reference human_inputs "
                    f"{sorted(referenced)} but no human-inputs.json was "
                    f"found. Topics cannot be resolved."
                ),
                location=state_machine.source_path,
            )
        )
        return findings

    for human_input_id in sorted(referenced):
        if not directory.has(human_input_id):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#7",
                    message=(
                        f"References human input {human_input_id!r} but it is not "
                        f"defined in "
                        f"{directory.source_path or 'human-inputs.json'}."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings
