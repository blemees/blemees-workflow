"""Trust-grant parser tests (JSON)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from workflow.core.model.human_gate import HumanGateLevel
from workflow.core.parser.trust_grant import (
    load_team_grants,
    parse_trust_grant,
)
from workflow.errors import ParseError


def _grant_dict(**overrides) -> dict:
    today = date.today()
    in_future = today + timedelta(days=30)
    yesterday = today - timedelta(days=1)
    defaults = {
        "control_point": "ready_for_dev",
        "workflow": "refinement",
        "team": "acme-eng",
        "current_level": "audit",
        "parameters": {
            "cadence": "daily",
            "on_revoke": "rebound_to_refinement",
        },
        "evidence": [
            {
                "source": "eval",
                "metric": "match_rate=0.97 over 156 runs",
                "window": "2026-03-15 / 2026-05-01",
                "detail": "skills/refinement/.workspace/baselines/2026-05-01/control-point-evals.json",
            }
        ],
        "granted_by": "jana@example.com",
        "granted_at": yesterday.isoformat(),
        "expires_at": in_future.isoformat(),
        "review_cadence": "60d",
        "revocation": {
            "procedure": "Edit this file.",
            "authorized_revokers": ["{pm}"],
        },
    }
    defaults.update(overrides)
    return defaults


def _grant_json(**overrides) -> str:
    return json.dumps(_grant_dict(**overrides))


def test_parse_audit_level_grant() -> None:
    grant = parse_trust_grant(_grant_json())
    assert grant.control_point == "ready_for_dev"
    assert grant.workflow == "refinement"
    assert grant.team == "acme-eng"
    assert grant.current_level is HumanGateLevel.AUDIT
    assert grant.parameters.cadence == "daily"
    assert grant.parameters.on_revoke == "rebound_to_refinement"
    assert grant.evidence
    assert grant.evidence[0].source == "eval"
    assert grant.granted_at < grant.expires_at
    assert "{pm}" in grant.revocation_authorized_revokers


def test_invalid_on_timeout_rejected() -> None:
    body = json.dumps(
        {
            "control_point": "staged",
            "workflow": "inner-loop",
            "team": "acme",
            "current_level": "block",
            "parameters": {
                "timeout": "24h",
                "on_timeout": "proceed",
                "escalate_to": "{architect}",
            },
            "evidence": [
                {
                    "source": "manual",
                    "metric": "rationale",
                    "window": "2026-05-01",
                    "detail": "rationale.md",
                }
            ],
            "granted_by": "vargha@example.com",
            "granted_at": "2026-05-09",
            "expires_at": "2026-08-07",
        }
    )
    with pytest.raises(ParseError, match="on_timeout"):
        parse_trust_grant(body)


def test_missing_required_field_rejected() -> None:
    body = json.dumps(
        {
            "control_point": "ready_for_dev",
            "workflow": "refinement",
            "team": "acme",
            "current_level": "audit",
            "evidence": [
                {
                    "source": "manual",
                    "metric": "x",
                    "window": "2026-05-01",
                    "detail": "x",
                }
            ],
            "granted_by": "jana@example.com",
            "granted_at": "2026-05-09",
            # missing expires_at
        }
    )
    with pytest.raises(ParseError, match="missing required fields"):
        parse_trust_grant(body)


def test_empty_evidence_rejected() -> None:
    body = json.dumps(
        {
            "control_point": "ready_for_dev",
            "workflow": "refinement",
            "team": "acme",
            "current_level": "audit",
            "parameters": {"cadence": "daily"},
            "evidence": [],
            "granted_by": "jana@example.com",
            "granted_at": "2026-05-09",
            "expires_at": "2026-08-07",
        }
    )
    with pytest.raises(ParseError, match="at least one evidence"):
        parse_trust_grant(body)


def test_truncated_grant_fails_loudly() -> None:
    body = _grant_json()
    truncated = body[:-8]  # chop off the closing braces
    with pytest.raises(ParseError):
        parse_trust_grant(truncated)


def test_load_team_grants_directory(tmp_path: Path) -> None:
    team_dir = tmp_path / "refinement"
    team_dir.mkdir()

    (team_dir / "ready_for_dev.json").write_text(_grant_json(), encoding="utf-8")
    # A malformed one — should be skipped without poisoning the rest.
    (team_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")

    grants = load_team_grants(team_dir)
    assert "ready_for_dev" in grants
    assert grants["ready_for_dev"].current_level is HumanGateLevel.AUDIT


def test_load_team_grants_nonexistent_returns_empty(tmp_path: Path) -> None:
    grants = load_team_grants(tmp_path / "does-not-exist")
    assert grants == {}


def test_load_team_grants_filters_by_workflow(tmp_path: Path) -> None:
    """A same-named gate in two processes must not leak its relaxation (#19).
    `workflow=` filters to the requested process; without it both are returned
    (keyed by control_point, last-wins) — which is exactly the leak."""
    team_dir = tmp_path / "acme"
    team_dir.mkdir()
    (team_dir / "refinement.json").write_text(
        _grant_json(workflow="refinement", control_point="shared_gate"), encoding="utf-8"
    )
    (team_dir / "release.json").write_text(
        _grant_json(workflow="release", control_point="shared_gate"), encoding="utf-8"
    )

    refinement = load_team_grants(team_dir, workflow="refinement")
    assert set(refinement) == {"shared_gate"}
    assert refinement["shared_gate"].workflow == "refinement"

    release = load_team_grants(team_dir, workflow="release")
    assert release["shared_gate"].workflow == "release"


def test_expires_before_granted_rejected() -> None:
    body = _grant_json(
        granted_at="2026-06-01",
        expires_at="2026-05-01",  # before granted_at — invalid
    )
    with pytest.raises(ParseError, match="strictly after"):
        parse_trust_grant(body)
