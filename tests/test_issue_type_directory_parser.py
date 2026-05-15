"""Issue-type directory parser tests."""

from __future__ import annotations

import json

import pytest

from workflow.core.parser.issue_type_directory import parse_issue_type_directory
from workflow.errors import ParseError


def test_parses_minimal_directory() -> None:
    data = {
        "types": {
            "bug": {"name": "Bug", "description": "A defect."},
        }
    }
    directory = parse_issue_type_directory(json.dumps(data))
    assert directory.has("bug")
    bug = directory.get("bug")
    assert bug.name == "Bug"
    assert bug.description == "A defect."
    assert bug.github_issue_type is None


def test_parses_github_issue_type_mapping() -> None:
    data = {
        "types": {
            "feature": {
                "name": "Feature",
                "description": "New capability.",
                "github_issue_type": "Feature",
            }
        }
    }
    directory = parse_issue_type_directory(json.dumps(data))
    assert directory.get("feature").github_issue_type == "Feature"


def test_missing_name_rejected() -> None:
    bad = {"types": {"bug": {"description": "no name"}}}
    with pytest.raises(ParseError, match="name"):
        parse_issue_type_directory(json.dumps(bad))


def test_missing_description_rejected() -> None:
    bad = {"types": {"bug": {"name": "Bug"}}}
    with pytest.raises(ParseError, match="description"):
        parse_issue_type_directory(json.dumps(bad))


def test_non_object_types_rejected() -> None:
    bad = {"types": "not an object"}
    with pytest.raises(ParseError, match="object"):
        parse_issue_type_directory(json.dumps(bad))


def test_empty_id_rejected() -> None:
    bad = {"types": {"": {"name": "X", "description": "Y"}}}
    with pytest.raises(ParseError, match="non-empty"):
        parse_issue_type_directory(json.dumps(bad))


def test_parses_real_example_directory() -> None:
    """The example workflow ships an issue-types.json with bug, feature, task."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "examples" / ".workflow" / "workflows" / "issue-types.json"
    directory = parse_issue_type_directory(path)
    assert {"bug", "feature", "task"} <= set(directory.types.keys())
    assert directory.get("bug").github_issue_type == "Bug"
