"""Role-directory parser tests (JSON)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.parser.role_directory import parse_role_directory
from workflow.errors import ParseError

SAMPLE_ROLES = {
    "roles": {
        "pm": {
            "name": "Product Manager",
            "responsibility": "Owns refinement",
            "processes": ["refinement (owner)"],
            "wakes_on": ["new raw issues"],
            "does_not": ["decide architecture"],
        },
        "developer": {
            "name": "Developer",
            "responsibility": "Implements changes end-to-end",
        },
    }
}


def test_parse_roles_directory() -> None:
    directory = parse_role_directory(json.dumps(SAMPLE_ROLES))
    assert "pm" in directory.roles
    pm = directory.roles["pm"]
    assert pm.name == "Product Manager"
    assert pm.responsibility == "Owns refinement"
    assert pm.processes == ["refinement (owner)"]
    assert pm.wakes_on == ["new raw issues"]
    assert pm.does_not == ["decide architecture"]
    # placeholder helper
    assert pm.placeholder == "{pm}"

    developer = directory.roles["developer"]
    # processes / wakes_on / does_not default to empty lists
    assert developer.processes == []
    assert developer.wakes_on == []
    assert developer.does_not == []


def test_parse_real_roles_file(roles_path: Path) -> None:
    directory = parse_role_directory(roles_path)
    # The example role directory ships the two roles its workflows reference.
    expected = {"pm", "developer"}
    assert expected <= set(directory.roles.keys())


def test_missing_required_field_rejected() -> None:
    bad = {"roles": {"pm": {"responsibility": "missing name"}}}
    with pytest.raises(ParseError, match="name"):
        parse_role_directory(json.dumps(bad))


def test_missing_responsibility_rejected() -> None:
    bad = {"roles": {"pm": {"name": "PM"}}}
    with pytest.raises(ParseError, match="responsibility"):
        parse_role_directory(json.dumps(bad))


def test_wrong_type_in_list_rejected() -> None:
    bad = {
        "roles": {
            "pm": {
                "name": "PM",
                "responsibility": "x",
                "processes": [123],  # not a string
            }
        }
    }
    with pytest.raises(ParseError, match="processes"):
        parse_role_directory(json.dumps(bad))


def test_truncated_roles_fails_loudly() -> None:
    body = json.dumps(SAMPLE_ROLES)[:-5]
    with pytest.raises(ParseError):
        parse_role_directory(body)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    directory = parse_role_directory(tmp_path / "does-not-exist.json")
    assert directory.roles == {}


def test_top_level_not_object_rejected() -> None:
    with pytest.raises(ParseError, match="object"):
        parse_role_directory("[]")
