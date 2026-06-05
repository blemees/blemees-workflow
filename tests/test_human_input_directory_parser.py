"""Human-input directory parser tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.parser.human_input_directory import parse_human_input_directory
from workflow.errors import ParseError


def test_parses_minimal_directory() -> None:
    data = {
        "human_inputs": {
            "general": {"name": "General", "description": "Catch-all."},
        }
    }
    directory = parse_human_input_directory(json.dumps(data))
    assert directory.has("general")
    t = directory.get("general")
    assert t.name == "General"
    assert t.description == "Catch-all."
    assert t.agent_prepares is None
    assert t.rationale is None


def test_parses_full_topic() -> None:
    data = {
        "human_inputs": {
            "needs-arch-review": {
                "name": "Needs architecture review",
                "description": "Cross-module choice.",
                "agent_prepares": "arch-packet.md",
                "rationale": "Architect routes here.",
            }
        }
    }
    directory = parse_human_input_directory(json.dumps(data))
    t = directory.get("needs-arch-review")
    assert t.agent_prepares == "arch-packet.md"
    assert t.rationale == "Architect routes here."


def test_missing_name_rejected() -> None:
    bad = {"human_inputs": {"x": {"description": "no name"}}}
    with pytest.raises(ParseError, match="name"):
        parse_human_input_directory(json.dumps(bad))


def test_missing_description_rejected() -> None:
    bad = {"human_inputs": {"x": {"name": "X"}}}
    with pytest.raises(ParseError, match="description"):
        parse_human_input_directory(json.dumps(bad))


def test_parses_real_example_directory() -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "workflows" / "human-inputs.json"
    directory = parse_human_input_directory(path)
    assert {"general", "clarify-scope", "needs-arch-review"} <= set(directory.entries.keys())


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    directory = parse_human_input_directory(tmp_path / "does-not-exist.json")
    assert directory.entries == {}
