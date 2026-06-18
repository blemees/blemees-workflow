"""Parser-layer invariant coverage (ADR-0004).

Each registered parser invariant gets a minimal input that violates exactly that
constraint; parsing it must raise `ParseError`. A meta-test asserts the trigger
tables cover every registered parser invariant across all six parsers, so a new
row without a trigger fails.

A mutator edits the minimal spec in place and returns `None` (the mutated spec
is used), or returns a replacement payload to serialize instead (for the
top-level-not-an-object cases). In-place mutators use `__setitem__` / `__delitem__`
so they evaluate to `None`.
"""

from __future__ import annotations

import copy
import json

import pytest

import workflow.core.parser.invariants  # noqa: F401  (registers parser rows)
from workflow.core.invariants import invariants_for_layer
from workflow.core.parser.human_gate_catalog import parse_human_gate_catalog
from workflow.core.parser.human_input_directory import parse_human_input_directory
from workflow.core.parser.issue_type_directory import parse_issue_type_directory
from workflow.core.parser.role_directory import parse_role_directory
from workflow.core.parser.state_machine import parse_state_machine
from workflow.core.parser.trust_grant import parse_trust_grant
from workflow.errors import ParseError

# --------------------------------------------------------------------------- #
# state machine


def _minimal_sm() -> dict:
    return {
        "states": {
            "a": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "initial": "in",
                "issue_types": ["bug"],
            },
            "b": {"class": "working", "roles": ["product-manager"], "issue_types": ["bug"]},
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
    del spec["states"]["a"]["initial"]
    spec["states"]["a"]["collects"] = collects


SM_VIOLATIONS = {
    "PARSE_TOP_LEVEL_TYPES": lambda s: s.update(description=123),
    "PARSE_STATES_PRESENT": lambda s: s.update(states={}),
    "PARSE_TRANSITIONS_LIST": lambda s: s.update(transitions="nope"),
    "PARSE_STATE_SPEC_OBJECT": lambda s: s["states"].__setitem__("a", "nope"),
    "PARSE_STATE_CLASS_VALID": lambda s: s["states"]["a"].__setitem__("class", "halfway"),
    "PARSE_REVERSIBILITY_PLACEMENT": lambda s: s["states"]["b"].__setitem__(
        "reversibility", "reversible-fast"
    ),
    "PARSE_ROLES_PLACEMENT": lambda s: s["states"]["b"].__delitem__("roles"),
    "PARSE_LEGACY_CLAIM_ROLE_REJECTED": lambda s: s["states"]["b"].__setitem__(
        "claim_role", "developer"
    ),
    "PARSE_ISSUE_TYPES_PLACEMENT": lambda s: s["states"]["b"].__delitem__("issue_types"),
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


# --------------------------------------------------------------------------- #
# roles.json


def _minimal_role() -> dict:
    return {"roles": {"developer": {"name": "Developer", "responsibility": "writes code"}}}


ROLE_VIOLATIONS = {
    "PARSE_ROLE_DIRECTORY_SHAPE": lambda s: s.__setitem__("roles", "nope"),
    "PARSE_ROLE_NAME_REQUIRED": lambda s: s["roles"]["developer"].__delitem__("name"),
    "PARSE_ROLE_RESPONSIBILITY_REQUIRED": lambda s: s["roles"]["developer"].__delitem__(
        "responsibility"
    ),
    "PARSE_ROLE_LEGACY_FIELDS_REJECTED": lambda s: s["roles"]["developer"].__setitem__(
        "processes", []
    ),
    "PARSE_ROLE_STRING_LIST_FIELDS": lambda s: s["roles"]["developer"].__setitem__(
        "does_not", "nope"
    ),
}


# --------------------------------------------------------------------------- #
# issue-types.json


def _minimal_type() -> dict:
    return {"types": {"bug": {"name": "Bug", "description": "a bug"}}}


TYPE_VIOLATIONS = {
    "PARSE_TYPE_DIRECTORY_SHAPE": lambda s: s.__setitem__("types", "nope"),
    "PARSE_TYPE_NAME_REQUIRED": lambda s: s["types"]["bug"].__delitem__("name"),
    "PARSE_TYPE_DESCRIPTION_REQUIRED": lambda s: s["types"]["bug"].__delitem__("description"),
    "PARSE_TYPE_GITHUB_FIELDS_TYPED": lambda s: s["types"]["bug"].__setitem__(
        "github_entity", "widget"
    ),
    "PARSE_TYPE_PR_NO_NATIVE_TYPE": lambda s: s["types"]["bug"].update(
        {"github_entity": "pull_request", "github_issue_type": "Bug"}
    ),
}


# --------------------------------------------------------------------------- #
# human-inputs.json


def _minimal_input() -> dict:
    return {"human_inputs": {"general": {"name": "General", "description": "ask the operator"}}}


INPUT_VIOLATIONS = {
    "PARSE_HUMAN_INPUT_DIRECTORY_SHAPE": lambda s: s.__setitem__("human_inputs", "nope"),
    "PARSE_HUMAN_INPUT_NAME_REQUIRED": lambda s: s["human_inputs"]["general"].__delitem__("name"),
    "PARSE_HUMAN_INPUT_DESCRIPTION_REQUIRED": lambda s: s["human_inputs"]["general"].__delitem__(
        "description"
    ),
    "PARSE_HUMAN_INPUT_OPTIONAL_FIELDS_TYPED": lambda s: s["human_inputs"]["general"].__setitem__(
        "agent_prepares", 123
    ),
}


# --------------------------------------------------------------------------- #
# <process>-human-gates.json


def _minimal_gate() -> dict:
    return {
        "human_gates": [
            {
                "gate_name": "g",
                "type": "judgment",
                "allowed_levels": ["block"],
                "default_level": "block",
            }
        ]
    }


GATE_VIOLATIONS = {
    "PARSE_GATE_CATALOG_SHAPE": lambda s: s.__setitem__("human_gates", "nope"),
    "PARSE_GATE_NAME_REQUIRED": lambda s: s["human_gates"][0].__delitem__("gate_name"),
    "PARSE_GATE_TYPE_VALID": lambda s: s["human_gates"][0].__setitem__("type", "bogus"),
    "PARSE_GATE_ALLOWED_LEVELS": lambda s: s["human_gates"][0].__setitem__("allowed_levels", []),
    "PARSE_GATE_DEFAULT_LEVEL_IN_ALLOWED": lambda s: s["human_gates"][0].__setitem__(
        "default_level", "audit"
    ),
    "PARSE_GATE_OPTIONAL_FIELDS_TYPED": lambda s: s["human_gates"][0].__setitem__(
        "agent_prepares", 123
    ),
    "PARSE_GATE_NAME_UNIQUE": lambda s: s["human_gates"].append(
        {
            "gate_name": "g",
            "type": "judgment",
            "allowed_levels": ["block"],
            "default_level": "block",
        }
    ),
}


# --------------------------------------------------------------------------- #
# trust-grants/<process>/<gate>.json


def _minimal_grant() -> dict:
    return {
        "control_point": "g",
        "workflow": "w",
        "team": "t",
        "current_level": "audit",
        "evidence": [{"source": "m", "metric": "x", "window": "2026", "detail": "ok"}],
        "granted_by": "lead@example.com",
        "granted_at": "2026-01-01",
        "expires_at": "2026-12-31",
    }


GRANT_VIOLATIONS = {
    "PARSE_GRANT_SHAPE": lambda s: [1, 2, 3],  # top-level not an object
    "PARSE_GRANT_REQUIRED_FIELDS": lambda s: s.__delitem__("team"),
    "PARSE_GRANT_LEVEL_VALID": lambda s: s.__setitem__("current_level", "bogus"),
    "PARSE_GRANT_PARAMETERS_OBJECT": lambda s: s.__setitem__("parameters", "nope"),
    "PARSE_GRANT_ON_TIMEOUT_VALID": lambda s: s.__setitem__("parameters", {"on_timeout": "ignore"}),
    "PARSE_GRANT_EVIDENCE_REQUIRED": lambda s: s.__setitem__("evidence", []),
    "PARSE_GRANT_DATES_VALID": lambda s: s.__setitem__("expires_at", "2025-01-01"),
    "PARSE_GRANT_REVOKERS_LIST": lambda s: s.__setitem__(
        "revocation", {"authorized_revokers": 123}
    ),
}


_SUITES = (
    (parse_state_machine, _minimal_sm, SM_VIOLATIONS),
    (parse_role_directory, _minimal_role, ROLE_VIOLATIONS),
    (parse_issue_type_directory, _minimal_type, TYPE_VIOLATIONS),
    (parse_human_input_directory, _minimal_input, INPUT_VIOLATIONS),
    (parse_human_gate_catalog, _minimal_gate, GATE_VIOLATIONS),
    (parse_trust_grant, _minimal_grant, GRANT_VIOLATIONS),
)

_CASES = [
    (parse_fn, minimal, inv_id, mutator)
    for parse_fn, minimal, table in _SUITES
    for inv_id, mutator in table.items()
]


@pytest.mark.parametrize(
    "parse_fn,minimal,invariant_id,mutator", _CASES, ids=[c[2] for c in _CASES]
)
def test_parser_invariant_rejects_violation(parse_fn, minimal, invariant_id, mutator) -> None:
    spec = copy.deepcopy(minimal())
    payload = mutator(spec)
    if payload is None:
        payload = spec
    with pytest.raises(ParseError):
        parse_fn(json.dumps(payload))


def test_every_registered_parser_invariant_has_a_trigger() -> None:
    covered: set[str] = set()
    for _, _, table in _SUITES:
        covered |= set(table)
    registered = {inv.id for inv in invariants_for_layer("parser")}
    assert covered == registered, (
        "parser invariant registry and trigger tables drifted: "
        f"unregistered={sorted(covered - registered)} "
        f"untriggered={sorted(registered - covered)}"
    )
