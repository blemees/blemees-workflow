"""Parser-layer invariants — shape and single-artifact rules enforced while
parsing a state machine (`parse_state_machine`).

Parser rules *raise* `ParseError` rather than returning findings, so they can't
self-register via the `@invariant` decorator. Instead each conceptual constraint
is registered here as a data row (ADR-0004, parser layer = hard stop). The raise
sites in `state_machine.py` enforce them; `tests/test_parser_invariants.py`
exercises each one and asserts the registry is fully covered.

Scope: the state-machine parser. The directory/catalog/trust-grant parsers
register their own rows in a follow-up.
"""

from __future__ import annotations

from workflow.core.invariants import Invariant, Severity, register_invariant

_SYMBOL = "workflow.core.parser.state_machine.parse_state_machine"


def _row(id: str, statement: str, principle: str) -> Invariant:
    return Invariant(
        id=id,
        statement=statement,
        severity=Severity.ERROR,  # parse failures are always hard stops
        layer="parser",
        principle=principle,
        enforcing_symbol=_SYMBOL,
    )


# One row per conceptual constraint (grouping the ~89 raise sites by concern).
PARSER_INVARIANTS: tuple[Invariant, ...] = (
    _row(
        "PARSE_TOP_LEVEL_TYPES",
        "Top-level `description` and `group`, if present, are strings.",
        "ADR-0001",
    ),
    _row(
        "PARSE_STATES_PRESENT",
        "`states` is a non-empty JSON object of id → spec.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_TRANSITIONS_LIST",
        "`transitions` is a list.",
        "state-machine-principles.md#2",
    ),
    _row(
        "PARSE_STATE_SPEC_OBJECT",
        "Each state id is a non-empty string mapping to an object spec.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_STATE_CLASS_VALID",
        "Each state declares `class` as one of resting | working.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_REVERSIBILITY_PLACEMENT",
        "`reversibility` is required on resting/closing states and forbidden on working.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_ROLES_PLACEMENT",
        "`roles` is a duplicate-free list, required on working states and forbidden elsewhere.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_LEGACY_CLAIM_ROLE_REJECTED",
        "The removed `claim_role` field is rejected (roles live on the working state).",
        "ADR-0001",
    ),
    _row(
        "PARSE_ISSUE_TYPES_PLACEMENT",
        "`issue_types` is a duplicate-free list, required on working/resting and forbidden on "
        "closing states.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_HUMAN_INPUTS_PLACEMENT",
        "`human_inputs` is a duplicate-free list, valid only on working states.",
        "hitl-principles.md#7",
    ),
    _row(
        "PARSE_NOTES_LIST_OF_STRINGS",
        "`notes`, if present, is a list of strings.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_MARK_PR_READY_BOOLEAN",
        "`mark_pr_ready`, if present, is a boolean and is forbidden on closing states.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_CLOSES_SHAPE",
        "`closes` is an object with a valid `taxonomy` and non-empty `reason`, only on resting "
        "states.",
        "state-machine-principles.md#8",
    ),
    _row(
        "PARSE_INITIAL_PLACEMENT",
        "`initial` is valid only on resting states and its label is mermaid-safe.",
        "state-machine-principles.md#1",
    ),
    _row(
        "PARSE_HANDOFF_PLACEMENT",
        "`handoff`, if present, is a boolean valid only on resting states.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_SPAWNS_SHAPE",
        "`spawns` is an object or non-empty list of spawn rules.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_SPAWN_FIELDS",
        "Each spawn rule has a non-empty `issue_type` and `initial_state` (and string `process` "
        "if present).",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_SPAWN_ADVANCE_ON_SHAPE",
        "A spawn rule's `advance_on`, if present, is a dict of closing-state → parent-state.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_COLLECTS_PLACEMENT",
        "`collects` is an object valid only on resting states.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_COLLECTS_FROM_STATES",
        "A `collects` declares a non-empty `from_states` list.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_COLLECTS_ADVANCE_ON_SHAPE",
        "A `collects` `advance_on` is a dict of collector-state → target (or per-type map).",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_COLLECTS_RELEASE_ON_LIST",
        "A `collects` `release_on`, if present, is a list of collector-state names.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_COLLECTS_ADVANCE_RELEASE_DISJOINT",
        "A `collects` keeps `advance_on` keys and `release_on` entries disjoint.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_TRANSITION_ENDPOINTS",
        "Each transition's `source` and `destination` are declared states.",
        "state-machine-principles.md#2",
    ),
    _row(
        "PARSE_STAR_ENDPOINTS_FORBIDDEN",
        "`[*]` transition endpoints are not authored — entry is `is_initial`, exit is `closes`.",
        "ADR-0002",
    ),
    _row(
        "PARSE_LEGACY_TRANSITION_FIELDS_REJECTED",
        "The removed `cross_process` type and `kind`/`process` transition fields are rejected.",
        "state-machine-principles.md#9",
    ),
    _row(
        "PARSE_TRANSITION_TYPE_VALID",
        "Each transition declares `type` as one of claim | advance | event.",
        "state-machine-principles.md#2",
    ),
    _row(
        "PARSE_TRANSITION_LABEL_STRING",
        "A transition `label`, if present, is a string.",
        "state-machine-principles.md#4",
    ),
)


def register_parser_invariants() -> None:
    """Register every parser-layer invariant (idempotent per process via the
    registry's duplicate guard — call once)."""
    for inv in PARSER_INVARIANTS:
        register_invariant(inv)


register_parser_invariants()
