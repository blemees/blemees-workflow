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
    """The example workflow ships an issue-types.json with the work-type set
    plus the pre-defined pr type."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "examples" / "workflows" / "issue-types.json"
    directory = parse_issue_type_directory(path)
    assert {"bug", "feature", "hotfix", "chore", "spike", "experiment", "pr"} <= set(
        directory.types.keys()
    )
    assert directory.get("bug").github_issue_type == "Bug"
    assert directory.get("bug").github_entity == "issue"
    pr = directory.get("pr")
    assert pr.github_entity == "pull_request"
    assert pr.github_issue_type is None


def test_github_entity_defaults_to_issue() -> None:
    data = {"types": {"bug": {"name": "Bug", "description": "A defect."}}}
    directory = parse_issue_type_directory(json.dumps(data))
    assert directory.get("bug").github_entity == "issue"


def test_github_entity_pull_request_accepted() -> None:
    data = {
        "types": {
            "pr": {
                "name": "Pull Request",
                "description": "Proposed change.",
                "github_entity": "pull_request",
            }
        }
    }
    directory = parse_issue_type_directory(json.dumps(data))
    assert directory.get("pr").github_entity == "pull_request"


def test_github_entity_invalid_value_rejected() -> None:
    bad = {
        "types": {
            "x": {
                "name": "X",
                "description": "Y",
                "github_entity": "discussion",
            }
        }
    }
    with pytest.raises(ParseError, match="github_entity"):
        parse_issue_type_directory(json.dumps(bad))


def test_pull_request_with_github_issue_type_rejected() -> None:
    """PRs aren't a native GitHub Issue Type — combining the two is a misconfig."""
    bad = {
        "types": {
            "pr": {
                "name": "PR",
                "description": "x",
                "github_entity": "pull_request",
                "github_issue_type": "PR",
            }
        }
    }
    with pytest.raises(ParseError, match="pull_request"):
        parse_issue_type_directory(json.dumps(bad))
