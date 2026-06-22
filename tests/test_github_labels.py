"""github_labels grammar tests — encode, parse, colours (ADR-0005)."""

from __future__ import annotations

import pytest

from workflow.backends import github_labels as gh


@pytest.mark.parametrize(
    "label, kind, value",
    [
        ("state/raw", gh.STATE, "raw"),
        ("claimed/product-manager", gh.CLAIM, "product-manager"),
        ("last-state/refining", gh.LAST_STATE, "refining"),
        ("type/bug", gh.TYPE, "bug"),
        ("child-of/100", gh.CHILD_OF, "100"),
        ("collected-by/42", gh.COLLECTED_BY, "42"),
        ("hitl-blocked/ready_for_dev", gh.HITL_BLOCKED, "ready_for_dev"),
        ("hitl-audit/ship", gh.HITL_AUDIT, "ship"),
        ("hitl-input/scope", gh.HITL_INPUT, "scope"),
        ("hitl-claim/reviewing", gh.HITL_CLAIM, "reviewing"),
        ("hitl-signal/approved", gh.HITL_SIGNAL, "approved"),
    ],
)
def test_parse_grammar(label: str, kind: str, value: str) -> None:
    parsed = gh.parse_label(label)
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.value == value


def test_child_of_value_can_contain_slashes() -> None:
    """A cross-repo id (`owner/repo#1`) survives the first-`/` partition."""
    parsed = gh.parse_label("child-of/owner/repo#1")
    assert parsed is not None
    assert parsed.kind == gh.CHILD_OF
    assert parsed.value == "owner/repo#1"


@pytest.mark.parametrize(
    "label",
    ["", "  ", "needs-triage", "priority/P1", "documentation", "state:raw", "wip:dev"],
)
def test_non_marker_labels_parse_to_none(label: str) -> None:
    """Labels outside the grammar — including the pre-ADR-0005 `:` form — are
    not framework markers."""
    assert gh.parse_label(label) is None


def test_encoders_round_trip_through_parse() -> None:
    cases = [
        (gh.state_label("raw"), gh.STATE, "raw"),
        (gh.claim_label("dev"), gh.CLAIM, "dev"),
        (gh.last_state_label("raw"), gh.LAST_STATE, "raw"),
        (gh.type_label("bug"), gh.TYPE, "bug"),
        (gh.child_of_label("9"), gh.CHILD_OF, "9"),
        (gh.collected_by_label("9"), gh.COLLECTED_BY, "9"),
        (gh.hitl_blocked_label("g"), gh.HITL_BLOCKED, "g"),
        (gh.hitl_audit_label("g"), gh.HITL_AUDIT, "g"),
        (gh.hitl_input_label("t"), gh.HITL_INPUT, "t"),
        (gh.hitl_claim_label(gh.CLAIM_REVIEWING), gh.HITL_CLAIM, "reviewing"),
        (gh.hitl_signal_label(gh.SIGNAL_RESOLVED), gh.HITL_SIGNAL, "resolved"),
    ]
    for label, kind, value in cases:
        assert "/" in label
        parsed = gh.parse_label(label)
        assert parsed == gh.ParsedLabel(kind, value)


def test_color_for() -> None:
    assert gh.color_for("state/raw") == "1f6feb"
    assert gh.color_for("claimed/dev") == "fbca04"
    assert gh.color_for("hitl-blocked/g") == "8957e5"
    # Unknown classifier → neutral grey.
    assert gh.color_for("needs-triage") == "ededed"


def test_classifier_of() -> None:
    assert gh.classifier_of("state/raw") == "state"
    assert gh.classifier_of("hitl-blocked/g") == "hitl-blocked"
