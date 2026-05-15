"""Validator — static cross-artifact checks against the framework principles.

Produces a list of `ValidationFinding` objects rather than throwing. Callers
(the `validate` CLI command, the controller's pre-flight check) decide how
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
from workflow.core.model.hcp import HCPCatalog, HCPLevel
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
    catalog: HCPCatalog | None,
    grants: dict[str, TrustGrant] | None = None,
    issue_type_directory: IssueTypeDirectory | None = None,
) -> list[ValidationFinding]:
    """Run every static cross-artifact check; return findings."""
    grants = grants or {}
    findings: list[ValidationFinding] = []

    findings.extend(_check_irreversible_destinations_gated(state_machine))
    findings.extend(_check_terminal_taxonomy(state_machine))
    findings.extend(_check_reversibility_declared_on_legend_states(state_machine))
    findings.extend(_check_transition_type_compatibility(state_machine))
    findings.extend(_check_working_states_are_claim_destinations(state_machine))
    findings.extend(_check_level_keywords_not_on_diagram(state_machine))
    findings.extend(_check_issue_types_resolved(state_machine, issue_type_directory))

    if catalog is not None:
        findings.extend(_check_legend_catalog_sync(state_machine, catalog))
        findings.extend(_check_audit_irreversible(catalog))
        findings.extend(_check_block_on_timeout(catalog, grants))
        findings.extend(_check_agent_prepares_present(catalog))

    findings.extend(_check_trust_grants(catalog, grants))

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


def _check_terminal_taxonomy(state_machine: StateMachine) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for state in state_machine.states.values():
        if state.state_class is StateClass.TERMINAL and state.terminal_taxonomy is None:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="state-machine-principles.md#8",
                    message=(
                        f"Terminal state {state.name!r} has no taxonomy tag. "
                        "Add `terminal (<tag>)` to the sink transition or a note."
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
    state_machine: StateMachine, catalog: HCPCatalog
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


def _check_audit_irreversible(catalog: HCPCatalog) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for hcp in catalog.entries.values():
        if (
            hcp.default_level is HCPLevel.AUDIT
            and hcp.reversibility is ReversibilityClass.IRREVERSIBLE
        ):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"HCP {hcp.gate_name!r} declares default_level=audit "
                        "but its destination is irreversible. Irreversible "
                        "destinations require block."
                    ),
                    location=catalog.source_path,
                )
            )
        if (
            HCPLevel.AUDIT in hcp.allowed_levels
            and hcp.reversibility is ReversibilityClass.IRREVERSIBLE
        ):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"HCP {hcp.gate_name!r} lists audit as an allowed "
                        "level but the destination is irreversible."
                    ),
                    location=catalog.source_path,
                )
            )
    return findings


def _check_block_on_timeout(
    catalog: HCPCatalog, grants: dict[str, TrustGrant]
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    # We can only check the trust-grant side — block defaults at the catalog
    # level have no timeout (timeout: none); the principle bites only when a
    # team has relaxed the timeout.
    for gate, grant in grants.items():
        if grant.current_level is HCPLevel.BLOCK:
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


def _check_agent_prepares_present(catalog: HCPCatalog) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for hcp in catalog.entries.values():
        if not hcp.agent_prepares_path:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="hitl-principles.md#8",
                    message=(
                        f"HCP {hcp.gate_name!r} has no 'Agent prepares' pointer. "
                        "Add a reference to the artifact-template file."
                    ),
                    location=catalog.source_path,
                )
            )
    return findings


def _check_trust_grants(
    catalog: HCPCatalog | None, grants: dict[str, TrustGrant]
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
            hcp = catalog.entries[gate]
            if grant.current_level not in hcp.allowed_levels:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="trust-grant-schema.md#7",
                        message=(
                            f"Trust grant for {gate!r} requests level "
                            f"{grant.current_level.value!r} but HCP allows "
                            f"{[lvl.value for lvl in hcp.allowed_levels]}."
                        ),
                        location=grant.source_path,
                    )
                )
            if (
                grant.current_level is HCPLevel.AUDIT
                and hcp.reversibility is ReversibilityClass.IRREVERSIBLE
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
    - ROLE_ACTION: working → resting | terminal
    - EXTERNAL: resting → resting | terminal
    - CROSS_PROCESS: resting → [*] | [*] → resting (handoffs between
      processes, both endpoints are resting; one side is always [*])

    `[*]` endpoints are sentinels, not states, so source/dest checks are
    skipped when an endpoint is `[*]`. ERROR-level — these are hard
    structural rules.
    """
    findings: list[ValidationFinding] = []
    for t in state_machine.transitions:
        src_state = state_machine.states.get(t.source) if t.source != "[*]" else None
        dst_state = state_machine.states.get(t.destination) if t.destination != "[*]" else None

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

        # ROLE_ACTION: working → resting | terminal
        elif t.transition_type is TransitionType.ROLE_ACTION:
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

        # EXTERNAL: resting → resting | terminal.
        # Note: `terminal_state → [*]` is the conventional sink marker for a
        # terminal — visual, not a real transition. Skip class checks when the
        # destination is `[*]` (the sink). When the source is `[*]` (entry),
        # the destination must be RESTING.
        elif t.transition_type is TransitionType.EXTERNAL:
            if t.destination == "[*]":
                pass  # sink marker; no class rule applies
            else:
                if src_state is not None and src_state.state_class is not StateClass.RESTING:
                    findings.append(
                        ValidationFinding(
                            severity=Severity.ERROR,
                            principle_cite="state-machine-principles.md#2",
                            message=(
                                f"External transition {t.source!r} → {t.destination!r} "
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
                                f"External transition {t.source!r} → {t.destination!r} "
                                f"({t.label!r}) must land in a RESTING or TERMINAL state; "
                                f"destination is {dst_state.state_class.value}."
                            ),
                            location=state_machine.source_path,
                        )
                    )

        # CROSS_PROCESS: one endpoint is [*]; the non-sentinel endpoint must be resting.
        elif t.transition_type is TransitionType.CROSS_PROCESS:
            non_sentinel = src_state if src_state is not None else dst_state
            if non_sentinel is not None and non_sentinel.state_class is not StateClass.RESTING:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        principle_cite="state-machine-principles.md#2",
                        message=(
                            f"Cross-process transition {t.source!r} → {t.destination!r} "
                            f"({t.label!r}) must touch a RESTING state on the non-[*] "
                            f"side; got {non_sentinel.state_class.value}."
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
    """Every issue type a process declares must exist in the issue-types directory.

    If the process declares `issue_types` but no directory was loaded, that's
    a WARNING (directory missing or malformed). If the directory exists but
    one of the referenced ids is absent, that's an ERROR (dangling reference).
    """
    findings: list[ValidationFinding] = []
    if not state_machine.issue_types:
        return findings
    if issue_type_directory is None:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                principle_cite="state-machine-principles.md#1",
                message=(
                    f"Process declares issue_types {sorted(state_machine.issue_types)} "
                    f"but no issue-types.json was found. Types cannot be resolved "
                    f"to backend type identifiers."
                ),
                location=state_machine.source_path,
            )
        )
        return findings
    for type_id in state_machine.issue_types:
        if not issue_type_directory.has(type_id):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    principle_cite="state-machine-principles.md#1",
                    message=(
                        f"Process references issue type {type_id!r} but it is not "
                        f"defined in {issue_type_directory.source_path or 'issue-types.json'}."
                    ),
                    location=state_machine.source_path,
                )
            )
    return findings
