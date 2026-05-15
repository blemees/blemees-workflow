"""HCP catalog parser tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.model.hcp import HCPLevel, HCPType
from workflow.core.model.state_machine import ReversibilityClass
from workflow.core.parser.hcp_catalog import parse_hcp_catalog
from workflow.errors import ParseError


def test_missing_file_yields_empty_catalog(
    refinement_hcp_catalog_path: Path,
) -> None:
    """Pre-HITL workflows have no hcps.json; parser must return empty rather than raise."""
    if refinement_hcp_catalog_path.exists():
        pytest.skip(
            "Catalog has been created post-migration; this test verifies pre-migration behavior."
        )
    catalog = parse_hcp_catalog(refinement_hcp_catalog_path)
    assert catalog.entries == {}
    assert catalog.source_path == str(refinement_hcp_catalog_path)


BINARY_HCP_CATALOG = {
    "process": "refinement",
    "hcps": [
        {
            "gate_name": "ready_for_dev",
            "transition": {
                "source": "refining",
                "destinations": ["ready_for_dev"],
            },
            "triggering_role": "pm",
            "type": "authority",
            "reversibility": "reversible-slow",
            "allowed_levels": ["block", "audit"],
            "default_level": "block",
            "agent_prepares": "dor.md",
            "rationale": "The DoR is the safety contract with inner-loop.",
        }
    ],
}


def test_parse_binary_hcp_catalog() -> None:
    catalog = parse_hcp_catalog(json.dumps(BINARY_HCP_CATALOG))
    assert "ready_for_dev" in catalog.entries

    hcp = catalog.entries["ready_for_dev"]
    assert hcp.gate_name == "ready_for_dev"
    assert hcp.source_state == "refining"
    assert hcp.destinations == ["ready_for_dev"]
    assert hcp.is_binary
    assert hcp.hcp_type is HCPType.AUTHORITY
    assert hcp.reversibility is ReversibilityClass.REVERSIBLE_SLOW
    assert HCPLevel.BLOCK in hcp.allowed_levels
    assert HCPLevel.AUDIT in hcp.allowed_levels
    assert hcp.default_level is HCPLevel.BLOCK
    assert hcp.agent_prepares_path == "dor.md"
    assert hcp.rationale == "The DoR is the safety contract with inner-loop."


VERDICT_HCP_CATALOG = {
    "process": "experimentation",
    "hcps": [
        {
            "gate_name": "experiment-verdict",
            "transition": {
                "source": "measurement_complete",
                "destinations": ["promoted", "killed", "iterated", "aborted"],
            },
            "triggering_role": "product-owner",
            "type": "authority",
            "reversibility": "irreversible",
            "allowed_levels": ["block"],
            "default_level": "block",
            "agent_prepares": "experiment-verdict-packet.md",
            "rationale": "Verdict is irreducibly the PO's call.",
        }
    ],
}


def test_parse_verdict_hcp_catalog() -> None:
    catalog = parse_hcp_catalog(json.dumps(VERDICT_HCP_CATALOG))
    assert "experiment-verdict" in catalog.entries
    hcp = catalog.entries["experiment-verdict"]
    assert hcp.is_verdict_style
    assert set(hcp.destinations) == {"promoted", "killed", "iterated", "aborted"}
    assert hcp.reversibility is ReversibilityClass.IRREVERSIBLE
    assert hcp.allowed_levels == [HCPLevel.BLOCK]


def test_truncated_json_fails_loudly() -> None:
    """The whole motivation for JSON: truncated files don't parse."""
    truncated = json.dumps(BINARY_HCP_CATALOG)[:-10]  # chop off the closing braces
    with pytest.raises(ParseError):
        parse_hcp_catalog(truncated)


def test_default_level_must_be_in_allowed_levels() -> None:
    bad = {
        "process": "refinement",
        "hcps": [
            {
                "gate_name": "x",
                "transition": {"source": "a", "destinations": ["b"]},
                "triggering_role": "pm",
                "type": "authority",
                "reversibility": "reversible-slow",
                "allowed_levels": ["block"],
                "default_level": "audit",
            }
        ],
    }
    with pytest.raises(ParseError, match="default_level"):
        parse_hcp_catalog(json.dumps(bad))


def test_unknown_type_rejected() -> None:
    bad = {
        "process": "refinement",
        "hcps": [
            {
                "gate_name": "x",
                "transition": {"source": "a", "destinations": ["b"]},
                "triggering_role": "pm",
                "type": "uncertainty",  # not in the four-type taxonomy
                "reversibility": "reversible-slow",
                "allowed_levels": ["block"],
                "default_level": "block",
            }
        ],
    }
    with pytest.raises(ParseError, match="type"):
        parse_hcp_catalog(json.dumps(bad))


def test_missing_required_field_rejected() -> None:
    bad = {
        "process": "refinement",
        "hcps": [
            {
                "gate_name": "x",
                # missing transition
                "triggering_role": "pm",
                "type": "authority",
                "reversibility": "reversible-slow",
                "allowed_levels": ["block"],
                "default_level": "block",
            }
        ],
    }
    with pytest.raises(ParseError):
        parse_hcp_catalog(json.dumps(bad))


def test_duplicate_gate_names_rejected() -> None:
    bad = {
        "process": "refinement",
        "hcps": [
            {
                "gate_name": "x",
                "transition": {"source": "a", "destinations": ["b"]},
                "triggering_role": "pm",
                "type": "authority",
                "reversibility": "reversible-slow",
                "allowed_levels": ["block"],
                "default_level": "block",
            },
            {
                "gate_name": "x",
                "transition": {"source": "c", "destinations": ["d"]},
                "triggering_role": "pm",
                "type": "authority",
                "reversibility": "reversible-slow",
                "allowed_levels": ["block"],
                "default_level": "block",
            },
        ],
    }
    with pytest.raises(ParseError, match="duplicate"):
        parse_hcp_catalog(json.dumps(bad))


def test_empty_catalog_returns_empty() -> None:
    catalog = parse_hcp_catalog(json.dumps({"process": "x", "hcps": []}))
    assert catalog.entries == {}
    assert catalog.process_name == "x"
