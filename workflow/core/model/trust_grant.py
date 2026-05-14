"""Trust grant model — per-team relaxations of catalogued HCPs.

Mirrors `trust-grant-schema.md`. Trust grants live outside the workflow repo
(in a team's own configuration). The YAML loader produces these dataclasses;
the validator and operations consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from workflow.core.model.hcp import HCPLevel


@dataclass
class Evidence:
    """One entry in a trust grant's evidence list.

    See trust-grant-schema.md § 5. At least one evidence entry is required for
    any non-default trust grant.
    """

    source: str  # "eval" | "production" | "manual"
    metric: str  # value or rationale
    window: str  # date range or "rationale-as-of <date>"
    detail: str  # pointer (filename, dashboard URL, rationale doc)


@dataclass
class TrustGrantParameters:
    """Level-specific parameters.

    Block-level fields: timeout, on_timeout, escalate_to.
    Audit-level fields: cadence, on_revoke.

    Mutually exclusive by level; the validator rejects a grant with parameters
    that don't match the declared `current_level`.
    """

    # Block-level
    timeout: str | None = None  # ISO-8601 duration or "none"
    on_timeout: str | None = None  # "abort" | "escalate"
    escalate_to: str | None = None

    # Audit-level
    cadence: str | None = None  # "per-event" | "daily" | "weekly" | "monthly"
    on_revoke: str | None = None  # Name of remediation procedure


@dataclass
class TrustGrant:
    """A per-team override of a catalogued HCP's default level / parameters.

    Mandatory fields per trust-grant-schema.md § 3. The validator enforces:

    - Irreversible destinations cannot relax `current_level` to `audit`.
    - `on_timeout` is `abort` or `escalate` only.
    - `expires_at` is required and in the future.
    - Evidence is non-empty.
    - `granted_by` is a human identity.
    """

    control_point: str  # Gate name (matches HCP.gate_name)
    workflow: str
    team: str
    current_level: HCPLevel
    parameters: TrustGrantParameters
    evidence: list[Evidence]
    granted_by: str  # Human identity (not an agent)
    granted_at: date
    expires_at: date
    review_cadence: str | None = None
    revocation_procedure: str | None = None
    revocation_authorized_revokers: list[str] = field(default_factory=list)
    source_path: str | None = None

    @property
    def is_expired(self) -> bool:
        from datetime import date as _date

        return self.expires_at < _date.today()

    @property
    def effective_today(self) -> bool:
        from datetime import date as _date

        today = _date.today()
        return self.granted_at <= today <= self.expires_at
