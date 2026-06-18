"""Trust-grant JSON parser.

Reads a single trust-grant JSON file into a `TrustGrant`. Loads a team's
grant directory into a `dict[control_point, TrustGrant]`.

Enforces only the hard structural rules from `trust-grant-schema.md` § 7
that can be checked from the file alone (presence of fields, valid
on_timeout, etc.). Cross-artifact rules (control_point matches a real
human gate, level in the gate's allowed_levels, evidence currency vs
catalog) are the validator's job.

JSON was chosen over YAML for strictness: a truncated file fails to parse
loudly rather than degrading silently into a partial structure. See the
design discussion in the repo's docs for full rationale.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from workflow.core.model.human_gate import HumanGateLevel
from workflow.core.model.trust_grant import (
    Evidence,
    TrustGrant,
    TrustGrantParameters,
)
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


_REQUIRED_FIELDS = (
    "control_point",
    "workflow",
    "team",
    "current_level",
    "evidence",
    "granted_by",
    "granted_at",
    "expires_at",
)


def parse_trust_grant(source: str | Path) -> TrustGrant:
    """Parse a single trust-grant JSON file or JSON string."""
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str)
        and "\n" not in source
        and not source.lstrip().startswith(("{", "["))
    ):
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read trust grant {path}: {exc}") from exc
        source_path = str(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Trust grant{f' at {source_path}' if source_path else ''} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Trust grant must be a JSON object at the top level (got {type(data).__name__})."
        )

    missing = [k for k in _REQUIRED_FIELDS if k not in data]
    if missing:
        raise ParseError("Trust grant is missing required fields: " + ", ".join(missing))

    level_raw = str(data["current_level"]).strip().lower()
    try:
        current_level = HumanGateLevel(level_raw)
    except ValueError as exc:
        raise ParseError(
            f"Invalid current_level {level_raw!r}; must be 'block' or 'audit'"
        ) from exc

    raw_params = data.get("parameters") or {}
    if not isinstance(raw_params, dict):
        raise ParseError("Trust grant `parameters` must be an object")
    parameters = TrustGrantParameters(
        timeout=_optional_str(raw_params.get("timeout")),
        on_timeout=_optional_str(raw_params.get("on_timeout")),
        escalate_to=_optional_str(raw_params.get("escalate_to")),
        cadence=_optional_str(raw_params.get("cadence")),
        on_revoke=_optional_str(raw_params.get("on_revoke")),
    )

    # Hard-rule § 7.3: on_timeout, if set, must be `abort` or `escalate`.
    if parameters.on_timeout is not None and parameters.on_timeout not in (
        "abort",
        "escalate",
    ):
        raise ParseError(f"on_timeout must be 'abort' or 'escalate'; got {parameters.on_timeout!r}")

    evidence_raw = data.get("evidence") or []
    if not isinstance(evidence_raw, list) or not evidence_raw:
        # Hard-rule § 7.5
        raise ParseError("Trust grant must have at least one evidence entry")
    evidence: list[Evidence] = []
    for idx, entry in enumerate(evidence_raw):
        if not isinstance(entry, dict):
            raise ParseError(f"Evidence entry #{idx} must be an object")
        evidence.append(
            Evidence(
                source=str(entry.get("source", "")).strip(),
                metric=str(entry.get("metric", "")).strip(),
                window=str(entry.get("window", "")).strip(),
                detail=str(entry.get("detail", "")).strip(),
            )
        )

    granted_at = _parse_date(data["granted_at"], "granted_at")
    expires_at = _parse_date(data["expires_at"], "expires_at")
    if expires_at <= granted_at:
        raise ParseError(
            f"expires_at ({expires_at}) must be strictly after granted_at ({granted_at})"
        )

    revocation = data.get("revocation") or {}
    if not isinstance(revocation, dict):
        revocation = {}
    revokers = revocation.get("authorized_revokers") or []
    if isinstance(revokers, str):
        revokers = [revokers]
    if not isinstance(revokers, list):
        raise ParseError("`revocation.authorized_revokers` must be a list of strings")

    return TrustGrant(
        control_point=str(data["control_point"]).strip(),
        workflow=str(data["workflow"]).strip(),
        team=str(data["team"]).strip(),
        current_level=current_level,
        parameters=parameters,
        evidence=evidence,
        granted_by=str(data["granted_by"]).strip(),
        granted_at=granted_at,
        expires_at=expires_at,
        review_cadence=_optional_str(data.get("review_cadence")),
        revocation_procedure=_optional_str(revocation.get("procedure")),
        revocation_authorized_revokers=[str(r) for r in revokers],
        source_path=source_path,
    )


def load_team_grants(team_dir: str | Path, workflow: str | None = None) -> dict[str, TrustGrant]:
    """Load every `*.json` under a team's grant directory.

    Returns a `{control_point: TrustGrant}` map. Files that fail to parse
    are logged at WARN and skipped — one bad grant does not poison the rest.
    Subdirectories (e.g., `trust-grants/<workflow>/<gate>.json`) are walked.

    When `workflow` is given, only grants whose `workflow` field matches are
    loaded — a gate name is unique only *within* a process, so without this
    filter a same-named gate's relaxation would leak across processes (#19).
    Grants are keyed by `control_point`, which is unambiguous once filtered.
    """
    path = Path(team_dir)
    if not path.exists() or not path.is_dir():
        return {}

    grants: dict[str, TrustGrant] = {}
    for json_path in sorted(path.rglob("*.json")):
        try:
            grant = parse_trust_grant(json_path)
        except ParseError as exc:
            logger.warning("Skipping trust grant %s: %s", json_path, exc)
            continue
        if workflow is not None and grant.workflow != workflow:
            continue
        if grant.control_point in grants:
            logger.warning(
                "Duplicate trust grant for control_point %r (workflow %r) in %s; keeping first.",
                grant.control_point,
                grant.workflow,
                team_dir,
            )
            continue
        grants[grant.control_point] = grant
    return grants


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, date):
        return value
    s = str(value).strip()
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise ParseError(f"{field_name} {s!r} is not a valid ISO-8601 date") from exc
