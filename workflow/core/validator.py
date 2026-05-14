"""Validator — static cross-artifact checks against the framework principles.

Produces a list of `ValidationFinding` objects rather than throwing. Callers
(the `validate` CLI command, the controller's pre-flight check) decide how
to surface them.

Each finding cites the source principle so that drift reports stay legible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum

from workflow.backends.base import WorkItemState
from workflow.core.model.hcp import HCPCatalog, HCPLevel
from workflow.core.model.lifecycle import (
    Lifecycle,
    ReversibilityClass,
    StateClass,
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


def validate_workflow(
    lifecycle: Lifecycle,
    catalog: HCPCatalog | None,
    grants: dict[str, TrustGrant] | None = None,
) -> list[ValidationFinding]:
    """Run every static cross-artifact check; return findings."""
    grants = grants or {}
    findings: list[ValidationFinding] = []

    findings.extend(_check_irreversible_destinations_gated(lifecycle))
    findings.extend(_check_terminal_taxonomy(lifecycle))
    findings.extend(_check_reversibility_declared_on_legend_states(lifecycle))

    if catalog is not None:
        findings.extend(_check_legend_catalog_sync(lifecycle, catalog))
        findings.extend(_check_audit_irreversible(catalog))
        findings.extend(_check_block_on_timeout(catalog, grants))
        findings.extend(_check_agent_prepares_present(catalog))

    findings.extend(_check_trust_grants(catalog, grants))

    return findings


def validate_work_item_markers(
    state: WorkItemState,
) -> list[ValidationFinding]:
    """Runtime claim-discipline check: at most one HITL gate is in flight on
    a single work item at a time (`hitl-principles.md` principle 6)."""
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
                    "Multiple HITL queue markers active on the same work item: "
                    + ", ".join(in_flight)
                ),
                location=state.work_item_id,
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
                location=state.work_item_id,
            )
        )
    return findings


# ----- check helpers -----


def _check_irreversible_destinations_gated(
    lifecycle: Lifecycle,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for t in lifecycle.transitions:
        dst_state = lifecycle.states.get(t.destination)
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
                    location=lifecycle.source_path,
                )
            )
    return findings


def _check_terminal_taxonomy(lifecycle: Lifecycle) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for state in lifecycle.states.values():
        if state.state_class is StateClass.TERMINAL and state.terminal_taxonomy is None:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="state-machine-principles.md#8",
                    message=(
                        f"Terminal state {state.name!r} has no taxonomy tag. "
                        "Add `terminal (<tag>)` to the sink transition or a note."
                    ),
                    location=lifecycle.source_path,
                )
            )
    return findings


def _check_reversibility_declared_on_legend_states(
    lifecycle: Lifecycle,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    # Every gate in the legend that resolves to a state should have that
    # state's reversibility declared (either on the state or in the legend).
    for gate, _rev in lifecycle.gates_in_legend.items():
        state = lifecycle.states.get(gate)
        if state is not None and state.reversibility is None:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    principle_cite="hitl-principles.md#4",
                    message=(
                        f"Legend names gate {gate!r} but the state has no "
                        "reversibility class declared on the diagram."
                    ),
                    location=lifecycle.source_path,
                )
            )
    return findings


def _check_legend_catalog_sync(
    lifecycle: Lifecycle, catalog: HCPCatalog
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    legend_gates = set(lifecycle.gates_in_legend.keys())
    catalog_gates = set(catalog.entries.keys())
    marker_gates = {
        t.destination if "→" not in t.label else t.destination
        for t in lifecycle.transitions
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
                    location=catalog.source_path or lifecycle.source_path,
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
