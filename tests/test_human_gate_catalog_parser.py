"""HumanGate catalog parser tests — schema is policy-only; structural info
(source state / destinations / triggering roles / reversibility) is
derived from the paired state machine via `StateMachine.gate_*`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.model.human_gate import HumanGateLevel, HumanGateType
from workflow.core.parser.human_gate_catalog import parse_human_gate_catalog
from workflow.errors import ParseError


def test_missing_file_yields_empty_catalog(
    refinement_hcp_catalog_path: Path,
) -> None:
    """Pre-HITL workflows have no gates.json; parser must return empty rather than raise."""
    if refinement_hcp_catalog_path.exists():
        pytest.skip(
            "Catalog has been created post-migration; this test verifies pre-migration behavior."
        )
    catalog = parse_human_gate_catalog(refinement_hcp_catalog_path)
    assert catalog.entries == {}
    assert catalog.source_path == str(refinement_hcp_catalog_path)


BINARY_HCP_CATALOG = {
    "human_gates": [
        {
            "gate_name": "ready_for_dev",
            "type": "authority",
            "allowed_levels": ["block", "audit"],
            "default_level": "block",
            "agent_prepares": "dor.md",
            "rationale": "The DoR is the safety contract with inner-loop.",
        }
    ],
}


def test_parse_binary_hcp_catalog() -> None:
    catalog = parse_human_gate_catalog(json.dumps(BINARY_HCP_CATALOG))
    assert "ready_for_dev" in catalog.entries

    gate = catalog.entries["ready_for_dev"]
    assert gate.gate_name == "ready_for_dev"
    assert gate.gate_type is HumanGateType.AUTHORITY
    assert HumanGateLevel.BLOCK in gate.allowed_levels
    assert HumanGateLevel.AUDIT in gate.allowed_levels
    assert gate.default_level is HumanGateLevel.BLOCK
    assert gate.agent_prepares_path == "dor.md"
    assert gate.rationale == "The DoR is the safety contract with inner-loop."


VERDICT_HCP_CATALOG = {
    "human_gates": [
        {
            "gate_name": "experiment-verdict",
            "type": "authority",
            "allowed_levels": ["block"],
            "default_level": "block",
            "agent_prepares": "experiment-verdict-packet.md",
            "rationale": "Verdict is irreducibly the PO's call.",
        }
    ],
}


def test_parse_verdict_hcp_catalog() -> None:
    catalog = parse_human_gate_catalog(json.dumps(VERDICT_HCP_CATALOG))
    assert "experiment-verdict" in catalog.entries
    gate = catalog.entries["experiment-verdict"]
    assert gate.allowed_levels == [HumanGateLevel.BLOCK]


def test_truncated_json_fails_loudly() -> None:
    """The whole motivation for JSON: truncated files don't parse."""
    truncated = json.dumps(BINARY_HCP_CATALOG)[:-10]  # chop off the closing braces
    with pytest.raises(ParseError):
        parse_human_gate_catalog(truncated)


def test_default_level_must_be_in_allowed_levels() -> None:
    bad = {
        "human_gates": [
            {
                "gate_name": "x",
                "type": "authority",
                "allowed_levels": ["block"],
                "default_level": "audit",
            }
        ],
    }
    with pytest.raises(ParseError, match="default_level"):
        parse_human_gate_catalog(json.dumps(bad))


def test_unknown_type_rejected() -> None:
    bad = {
        "human_gates": [
            {
                "gate_name": "x",
                "type": "uncertainty",  # not in the four-type taxonomy
                "allowed_levels": ["block"],
                "default_level": "block",
            }
        ],
    }
    with pytest.raises(ParseError, match="type"):
        parse_human_gate_catalog(json.dumps(bad))


def test_missing_required_field_rejected() -> None:
    bad = {
        "human_gates": [
            {
                "gate_name": "x",
                # missing type
                "allowed_levels": ["block"],
                "default_level": "block",
            }
        ],
    }
    with pytest.raises(ParseError):
        parse_human_gate_catalog(json.dumps(bad))


def test_duplicate_gate_names_rejected() -> None:
    bad = {
        "human_gates": [
            {
                "gate_name": "x",
                "type": "authority",
                "allowed_levels": ["block"],
                "default_level": "block",
            },
            {
                "gate_name": "x",
                "type": "authority",
                "allowed_levels": ["block"],
                "default_level": "block",
            },
        ],
    }
    with pytest.raises(ParseError, match="duplicate"):
        parse_human_gate_catalog(json.dumps(bad))


def test_empty_catalog_returns_empty() -> None:
    # The top-level `process` field was removed — process name now derives
    # from the filename stem when loading from disk. From a raw string,
    # the default is "unnamed".
    catalog = parse_human_gate_catalog(json.dumps({"human_gates": []}))
    assert catalog.entries == {}
    assert catalog.process_name == "unnamed"
