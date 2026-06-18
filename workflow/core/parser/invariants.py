"""Parser-layer invariants — shape and single-artifact rules enforced while
parsing a state machine (`parse_state_machine`).

Parser rules *raise* `ParseError` rather than returning findings, so they can't
self-register via the `@invariant` decorator. Instead each conceptual constraint
is registered here as a data row (ADR-0004, parser layer = hard stop). The raise
sites in the parser modules enforce them; `tests/test_parser_invariants.py`
exercises each one and asserts the registry is fully covered.

Covers all six parsers: the state machine plus the shared directories/catalog/
trust-grant parsers (roles, issue-types, human-inputs, human-gates, trust-grants).
"""

from __future__ import annotations

from workflow.core.invariants import Invariant, Severity, register_invariant

_SYMBOL = "workflow.core.parser.state_machine.parse_state_machine"
_ROLE_SYMBOL = "workflow.core.parser.role_directory.parse_role_directory"
_TYPE_SYMBOL = "workflow.core.parser.issue_type_directory.parse_issue_type_directory"
_INPUT_SYMBOL = "workflow.core.parser.human_input_directory.parse_human_input_directory"
_GATE_SYMBOL = "workflow.core.parser.human_gate_catalog.parse_human_gate_catalog"
_GRANT_SYMBOL = "workflow.core.parser.trust_grant.parse_trust_grant"


def _row(id: str, statement: str, principle: str, symbol: str = _SYMBOL) -> Invariant:
    return Invariant(
        id=id,
        statement=statement,
        severity=Severity.ERROR,  # parse failures are always hard stops
        layer="parser",
        principle=principle,
        enforcing_symbol=symbol,
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


# Directory / catalog / trust-grant parsers (shared roles, issue-types,
# human-inputs, human-gates, trust-grants).
DIRECTORY_PARSER_INVARIANTS: tuple[Invariant, ...] = (
    # --- roles.json ---
    _row(
        "PARSE_ROLE_DIRECTORY_SHAPE",
        "Role directory is a top-level object whose `roles` is an object of "
        "non-empty id → object entry.",
        "ADR-0001",
        _ROLE_SYMBOL,
    ),
    _row(
        "PARSE_ROLE_NAME_REQUIRED",
        "Each role declares a non-empty `name`.",
        "ADR-0001",
        _ROLE_SYMBOL,
    ),
    _row(
        "PARSE_ROLE_RESPONSIBILITY_REQUIRED",
        "Each role declares a non-empty `responsibility`.",
        "ADR-0001",
        _ROLE_SYMBOL,
    ),
    _row(
        "PARSE_ROLE_LEGACY_FIELDS_REJECTED",
        "The removed `processes` / `wakes_on` role fields are rejected.",
        "ADR-0001",
        _ROLE_SYMBOL,
    ),
    _row(
        "PARSE_ROLE_STRING_LIST_FIELDS",
        "A role's `does_not`, if present, is a list of strings.",
        "ADR-0001",
        _ROLE_SYMBOL,
    ),
    # --- issue-types.json ---
    _row(
        "PARSE_TYPE_DIRECTORY_SHAPE",
        "Issue-type directory is a top-level object whose `types` is an object "
        "of non-empty id → object entry.",
        "state-machine-principles.md#1",
        _TYPE_SYMBOL,
    ),
    _row(
        "PARSE_TYPE_NAME_REQUIRED",
        "Each issue type declares a non-empty `name`.",
        "state-machine-principles.md#1",
        _TYPE_SYMBOL,
    ),
    _row(
        "PARSE_TYPE_DESCRIPTION_REQUIRED",
        "Each issue type declares a non-empty `description`.",
        "state-machine-principles.md#1",
        _TYPE_SYMBOL,
    ),
    _row(
        "PARSE_TYPE_GITHUB_FIELDS_TYPED",
        "An issue type's `github_issue_type` / `github_issue_type_color` are "
        "non-empty strings if present and `github_entity` is issue | pull_request.",
        "state-machine-principles.md#1",
        _TYPE_SYMBOL,
    ),
    _row(
        "PARSE_TYPE_PR_NO_NATIVE_TYPE",
        "`github_issue_type` is forbidden when `github_entity` is pull_request.",
        "state-machine-principles.md#1",
        _TYPE_SYMBOL,
    ),
    # --- human-inputs.json ---
    _row(
        "PARSE_HUMAN_INPUT_DIRECTORY_SHAPE",
        "Human-input directory is a top-level object whose `human_inputs` is an "
        "object of non-empty id → object entry.",
        "hitl-principles.md#7",
        _INPUT_SYMBOL,
    ),
    _row(
        "PARSE_HUMAN_INPUT_NAME_REQUIRED",
        "Each human input declares a non-empty `name`.",
        "hitl-principles.md#7",
        _INPUT_SYMBOL,
    ),
    _row(
        "PARSE_HUMAN_INPUT_DESCRIPTION_REQUIRED",
        "Each human input declares a non-empty `description`.",
        "hitl-principles.md#7",
        _INPUT_SYMBOL,
    ),
    _row(
        "PARSE_HUMAN_INPUT_OPTIONAL_FIELDS_TYPED",
        "A human input's `agent_prepares` / `rationale` are non-empty strings if present.",
        "hitl-principles.md#7",
        _INPUT_SYMBOL,
    ),
    # --- <process>-human-gates.json ---
    _row(
        "PARSE_GATE_CATALOG_SHAPE",
        "Human-gate catalog is a top-level object whose `human_gates` is a list of object entries.",
        "hitl-principles.md#6",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_NAME_REQUIRED",
        "Each gate declares a non-empty `gate_name`.",
        "hitl-principles.md#6",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_TYPE_VALID",
        "Each gate's `type` is one of authority | knowledge | judgment | reality.",
        "hitl-principles.md#6",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_ALLOWED_LEVELS",
        "A gate's `allowed_levels` is a non-empty list of block | audit.",
        "hitl-principles.md#4",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_DEFAULT_LEVEL_IN_ALLOWED",
        "A gate's `default_level` is valid and present in `allowed_levels`.",
        "hitl-principles.md#4",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_OPTIONAL_FIELDS_TYPED",
        "A gate's `agent_prepares` / `rationale` are strings if present.",
        "hitl-principles.md#8",
        _GATE_SYMBOL,
    ),
    _row(
        "PARSE_GATE_NAME_UNIQUE",
        "Gate names are unique within a catalog.",
        "hitl-principles.md#6",
        _GATE_SYMBOL,
    ),
    # --- trust-grants/<process>/<gate>.json ---
    _row(
        "PARSE_GRANT_SHAPE",
        "A trust grant is a top-level JSON object.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_REQUIRED_FIELDS",
        "A trust grant declares control_point, workflow, team, current_level, "
        "evidence, granted_by, granted_at, expires_at.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_LEVEL_VALID",
        "A trust grant's `current_level` is block | audit.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_PARAMETERS_OBJECT",
        "A trust grant's `parameters`, if present, is an object.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_ON_TIMEOUT_VALID",
        "A trust grant's `parameters.on_timeout`, if set, is abort | escalate.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_EVIDENCE_REQUIRED",
        "A trust grant has at least one evidence entry, each an object.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_DATES_VALID",
        "A trust grant's dates are ISO-8601 and `expires_at` is after `granted_at`.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
    _row(
        "PARSE_GRANT_REVOKERS_LIST",
        "A trust grant's `revocation.authorized_revokers`, if present, is a list.",
        "trust-grant-schema.md#7",
        _GRANT_SYMBOL,
    ),
)


def register_parser_invariants() -> None:
    """Register every parser-layer invariant (idempotent per process via the
    registry's duplicate guard — call once)."""
    for inv in (*PARSER_INVARIANTS, *DIRECTORY_PARSER_INVARIANTS):
        register_invariant(inv)


register_parser_invariants()
