"""Parser-layer invariant coverage (ADR-0004).

Each registered parser invariant gets a minimal spec that violates exactly that
constraint; parsing it must raise `ParseError`. A meta-test asserts the table
covers every registered parser invariant, so a new row without a trigger fails.
"""

from __future__ import annotations

import copy
import json

import pytest

import workflow.core.parser.invariants  # noqa: F401  (registers parser rows)
from workflow.core.invariants import invariants_for_layer
from workflow.core.parser.state_machine import parse_state_machine
from workflow.errors import ParseError


def _minimal() -> dict:
    return {
        "states": {
            "a": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "initial": "in",
                "issue_types": ["bug"],
            },
            "b": {
                "class": "working",
                "roles": ["product-manager"],
                "issue_types": ["bug"],
            },
            "c": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "closes": {"taxonomy": "shipped", "reason": "completed"},
            },
        },
        "transitions": [
            {"source": "a", "destination": "b", "type": "claim", "label": "pm claims a"},
            {"source": "b", "destination": "c", "type": "advance", "label": "pm ships"},
        ],
    }


def _collects_on_a(spec: dict, collects: dict) -> None:
    # `a` is resting; the parser allows `collects` there (initial+collects is a
    # validator concern, not a parse-shape one).
    del spec["states"]["a"]["initial"]
    spec["states"]["a"]["collects"] = collects


# id -> callable(spec) that breaks exactly that constraint.
VIOLATIONS = {
    "PARSE_TOP_LEVEL_TYPES": lambda s: s.update(description=123),
    "PARSE_STATES_PRESENT": lambda s: s.update(states={}),
    "PARSE_TRANSITIONS_LIST": lambda s: s.update(transitions="nope"),
    "PARSE_STATE_SPEC_OBJECT": lambda s: s["states"].__setitem__("a", "nope"),
    "PARSE_STATE_CLASS_VALID": lambda s: s["states"]["a"].__setitem__("class", "halfway"),
    "PARSE_REVERSIBILITY_PLACEMENT": lambda s: s["states"]["b"].__setitem__(
        "reversibility", "reversible-fast"
    ),
    "PARSE_ROLES_PLACEMENT": lambda s: s["states"]["b"].pop("roles"),
    "PARSE_LEGACY_CLAIM_ROLE_REJECTED": lambda s: s["states"]["b"].__setitem__(
        "claim_role", "developer"
    ),
    "PARSE_ISSUE_TYPES_PLACEMENT": lambda s: s["states"]["b"].pop("issue_types"),
    "PARSE_HUMAN_INPUTS_PLACEMENT": lambda s: s["states"]["a"].__setitem__(
        "human_inputs", ["general"]
    ),
    "PARSE_NOTES_LIST_OF_STRINGS": lambda s: s["states"]["a"].__setitem__("notes", "nope"),
    "PARSE_MARK_PR_READY_BOOLEAN": lambda s: s["states"]["a"].__setitem__("mark_pr_ready", "yes"),
    "PARSE_CLOSES_SHAPE": lambda s: s["states"]["c"].__setitem__("closes", {"reason": "x"}),
    "PARSE_INITIAL_PLACEMENT": lambda s: s["states"]["b"].__setitem__("initial", "in"),
    "PARSE_HANDOFF_PLACEMENT": lambda s: s["states"]["b"].__setitem__("handoff", True),
    "PARSE_SPAWNS_SHAPE": lambda s: s["states"]["a"].__setitem__("spawns", "nope"),
    "PARSE_SPAWN_FIELDS": lambda s: s["states"]["a"].__setitem__("spawns", {"initial_state": "x"}),
    "PARSE_SPAWN_ADVANCE_ON_SHAPE": lambda s: s["states"]["a"].__setitem__(
        "spawns", {"issue_type": "bug", "initial_state": "x", "advance_on": "nope"}
    ),
    "PARSE_COLLECTS_PLACEMENT": lambda s: s["states"]["b"].__setitem__(
        "collects", {"from_states": ["x"]}
    ),
    "PARSE_COLLECTS_FROM_STATES": lambda s: _collects_on_a(s, {}),
    "PARSE_COLLECTS_ADVANCE_ON_SHAPE": lambda s: _collects_on_a(
        s, {"from_states": ["x"], "advance_on": "nope"}
    ),
    "PARSE_COLLECTS_RELEASE_ON_LIST": lambda s: _collects_on_a(
        s, {"from_states": ["x"], "release_on": "nope"}
    ),
    "PARSE_COLLECTS_ADVANCE_RELEASE_DISJOINT": lambda s: _collects_on_a(
        s, {"from_states": ["x"], "advance_on": {"a": "b"}, "release_on": ["a"]}
    ),
    "PARSE_TRANSITION_ENDPOINTS": lambda s: s["transitions"][0].__setitem__("source", "ghost"),
    "PARSE_STAR_ENDPOINTS_FORBIDDEN": lambda s: s["transitions"].append(
        {"source": "[*]", "destination": "a", "type": "event"}
    ),
    "PARSE_LEGACY_TRANSITION_FIELDS_REJECTED": lambda s: s["transitions"][0].__setitem__(
        "type", "cross_process"
    ),
    "PARSE_TRANSITION_TYPE_VALID": lambda s: s["transitions"][0].__setitem__("type", "wibble"),
    "PARSE_TRANSITION_LABEL_STRING": lambda s: s["transitions"][0].__setitem__("label", 123),
}


@pytest.mark.parametrize("invariant_id", sorted(VIOLATIONS))
def test_parser_invariant_rejects_violation(invariant_id: str) -> None:
    spec = copy.deepcopy(_minimal())
    VIOLATIONS[invariant_id](spec)
    with pytest.raises(ParseError):
        parse_state_machine(json.dumps(spec))


def test_every_registered_parser_invariant_has_a_trigger() -> None:
    registered = {inv.id for inv in invariants_for_layer("parser")}
    assert set(VIOLATIONS) == registered, (
        "parser invariant registry and trigger table drifted: "
        f"unregistered={sorted(set(VIOLATIONS) - registered)} "
        f"untriggered={sorted(registered - set(VIOLATIONS))}"
    )
