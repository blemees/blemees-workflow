"""Role-directory parser tests (JSON)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.parser.role_directory import parse_role_directory
from workflow.errors import ParseError

SAMPLE_ROLES = {
    "roles": {
        "product-manager": {
            "name": "Product Manager",
            "responsibility": "Owns refinement",
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
    assert "product-manager" in directory.roles
    pm = directory.roles["product-manager"]
    assert pm.name == "Product Manager"
    assert pm.responsibility == "Owns refinement"
    assert pm.does_not == ["decide architecture"]
    # placeholder helper
    assert pm.placeholder == "{product-manager}"

    developer = directory.roles["developer"]
    # does_not defaults to empty list when absent
    assert developer.does_not == []


def test_parse_real_roles_file(roles_path: Path) -> None:
    directory = parse_role_directory(roles_path)
    # The example role directory ships the two roles its workflows reference.
    expected = {"product-manager", "developer"}
    assert expected <= set(directory.roles.keys())


def test_missing_required_field_rejected() -> None:
    bad = {"roles": {"product-manager": {"responsibility": "missing name"}}}
    with pytest.raises(ParseError, match="name"):
        parse_role_directory(json.dumps(bad))


def test_missing_responsibility_rejected() -> None:
    bad = {"roles": {"product-manager": {"name": "PM"}}}
    with pytest.raises(ParseError, match="responsibility"):
        parse_role_directory(json.dumps(bad))


def test_wrong_type_in_list_rejected() -> None:
    bad = {
        "roles": {
            "product-manager": {
                "name": "PM",
                "responsibility": "x",
                "does_not": [123],  # not a string
            }
        }
    }
    with pytest.raises(ParseError, match="does_not"):
        parse_role_directory(json.dumps(bad))


def test_legacy_processes_field_rejected() -> None:
    bad = {
        "roles": {
            "product-manager": {
                "name": "PM",
                "responsibility": "owns refinement",
                "processes": ["refinement (owner)"],
            }
        }
    }
    with pytest.raises(ParseError, match="processes.*was removed"):
        parse_role_directory(json.dumps(bad))


def test_legacy_wakes_on_field_rejected() -> None:
    bad = {
        "roles": {
            "product-manager": {
                "name": "PM",
                "responsibility": "owns refinement",
                "wakes_on": ["new raw issues"],
            }
        }
    }
    with pytest.raises(ParseError, match="wakes_on.*was removed"):
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
