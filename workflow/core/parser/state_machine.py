"""StateMachine JSON parser — reads `<name>-states.json` into a `StateMachine`.

The workflow is structured data, not a presentation format. JSON is the
canonical source of truth; the mermaid file alongside it is generated for
visualization (see `workflow.core.emitter.mermaid`).

This parser is **strict**: every state's class is declared, every transition's
type is declared. The mermaid parser's text-inferred classification heuristics
are gone — the schema demands intent.

## Expected JSON shape

```json
{
  "states": {
    "raw": {
      "class": "resting",
      "reversibility": "reversible-fast",
      "initial": "issue created",
      "notes": ["optional prose for visualization"]
    },
    "refining": {
      "class": "working",
      "roles": ["product-manager"]
    },
    "ready_for_dev": {
      "class": "resting",
      "reversibility": "reversible-slow"
    },
    "wont_fix": {
      "class": "resting",
      "reversibility": "reversible-fast",
      "closes": {"taxonomy": "abandoned", "reason": "not planned"}
    }
  },
  "transitions": [
    {"source": "raw", "destination": "refining", "type": "claim", "label": "product-manager claims raw"},
    {"source": "refining", "destination": "ready_for_dev", "type": "advance",
     "label": "product-manager marks ready", "human_gate": "ready_for_dev"},
    {"source": "refining", "destination": "wont_fix", "type": "advance",
     "label": "product-manager marks wont-fix", "human_gate": "wont_fix"},
    {"source": "ready_for_dev", "destination": "refining", "type": "claim",
     "label": "product-manager claims (revision)"}
  ]
}
```

## Field reference

### Top-level
- `states` (object, required): map of state-id → state spec.
- `transitions` (list, required): ordered list of transition specs.

The process name is derived from the filename stem (`<process>-states.json`)
and is not authored in the JSON. The human-gate catalog path follows the
same convention: `<process>-human-gates.json`.

### States
- `class` (string, required): `"resting"` | `"working"`.
- `reversibility` (string, required on resting, forbidden on
  working): `"irreversible"` | `"reversible-fast"` | `"reversible-slow"`.
  Says how reversible *landing* in this state is. Working states are
  transient — only landings have a reversibility class.
- `closes` (object, optional, resting states only): marks a closing state —
  a sink that closes the issue on entry. Shape `{"taxonomy": <tag>,
  "reason": <close reason>}`, where `taxonomy` is one of `"shipped"` |
  `"resolved"` | `"reverted"` | `"abandoned"` | `"deduplicated"` |
  `"superseded"`. Mutually exclusive with `initial` / `collects` /
  `handoff` / `issue_types`; a closing state must have no outgoing
  transitions.
- `roles` (list of strings, required on working, forbidden elsewhere):
  role ids permitted to occupy this state. Non-empty. The role-restriction
  lives on the working state, not on the resting queue it's claimed from.
  Resting states are open queues; downstream working states declare who
  may pick items up.
- `issue_types` (list of strings): the issue types that may occupy this
  state. Required on working AND non-closing resting states; forbidden on
  closing states.
  - Working: types this state will actually do work on (claim semantics).
    The process's umbrella accepted-types set is derived as the union
    across all working states.
  - Resting: types that may sit waiting in this state (queue semantics).
    Must be a subset of the process's umbrella. Spawn-target resting
    states typically declare a single type; shared handoff states
    declare the full set that crosses the interface.
- `notes` (list of strings, optional): free prose for the emitter to render
  alongside the state. Not parsed for semantics — structured metadata goes
  in the typed fields above.

### Transitions
- `source` (string, required): state id. `"[*]"` is no longer authored —
  use the `initial` field on a resting state instead.
- `destination` (string, required): state id. Closing-state sinks are implicit;
  the emitter generates them from each closing state's `closes`.
- `type` (string, required): `"claim"` | `"advance"` | `"event"`.
- `label` (string, required): the human-readable transition label.
- `human_gate` (string, optional): the human-gate catalog's `gate_name` for
  this gate. Presence marks the transition as HITL-gated; absence means
  ungated. Per `hitl-principles.md`, only claim and role-action transitions
  are typically gated; the validator enforces consistency with the catalog.
The legend that mermaid uses (HITL gates block, cross-process interfaces
block) is **not** authored in JSON. Both legends are derived: HITL gates
from the union of transitions carrying `human_gate` and the destination
state's reversibility; cross-process from each state's `handoff` flag and
`spawns` field. The emitter regenerates them for visualization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.state_machine import (
    Closes,
    ClosureTaxonomy,
    CollectAdvanceRule,
    Collects,
    ReversibilityClass,
    Spawn,
    State,
    StateClass,
    StateMachine,
    Transition,
    TransitionType,
)
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


_STATE_CLASS = {
    "resting": StateClass.RESTING,
    "working": StateClass.WORKING,
}

_REVERSIBILITY = {
    "irreversible": ReversibilityClass.IRREVERSIBLE,
    "reversible-fast": ReversibilityClass.REVERSIBLE_FAST,
    "reversible-slow": ReversibilityClass.REVERSIBLE_SLOW,
}

_CLOSURE_TAX = {tax.value: tax for tax in ClosureTaxonomy}

_TRANSITION_TYPE = {
    "claim": TransitionType.CLAIM,
    "advance": TransitionType.ADVANCE,
    "event": TransitionType.EVENT,
}


def parse_state_machine(source: str | Path, name: str | None = None) -> StateMachine:
    """Parse a `<name>-states.json` file (path or raw text) into a `StateMachine`.

    `name` defaults to the JSON's `name` field, then to the filename stem
    with `-workflow` stripped.

    Raises `ParseError` on invalid JSON or schema violations.
    """
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str)
        and "\n" not in source
        and not source.lstrip().startswith(("{", "["))
    ):
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read workflow JSON {path}: {exc}") from exc
        source_path = str(path)
        if name is None:
            stem = path.stem
            # Canonical filename convention is `<process>-states.json`;
            # legacy `<process>-workflow.json` is supported for older trees.
            if stem.endswith("-states"):
                name = stem[: -len("-states")]
            elif stem.endswith("-workflow"):
                name = stem[: -len("-workflow")]
            else:
                name = stem
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"StateMachine JSON{f' at {source_path}' if source_path else ''} is not valid: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"StateMachine JSON must be a JSON object at the top level "
            f"(got {type(data).__name__})."
        )

    # Process name is always derived — from the `name` arg (filename stem
    # for file-loaded workflows) or "unnamed" for inline JSON without a
    # caller-supplied name. The JSON itself doesn't carry it.
    name = name or "unnamed"

    # Process-level `issue_types` was removed — types now live exclusively
    # on working states (the umbrella is derivable as the union).
    if "issue_types" in data:
        raise ParseError(
            "Top-level `issue_types` was removed. Declare types on each "
            "working state via `issue_types: [...]`; the process's "
            "accepted set is derived as the union."
        )

    description_raw = data.get("description")
    if description_raw is not None and not isinstance(description_raw, str):
        raise ParseError("Top-level `description` must be a string if present.")
    description = description_raw.strip() if isinstance(description_raw, str) else None
    if description == "":
        description = None

    group_raw = data.get("group")
    if group_raw is not None and not isinstance(group_raw, str):
        raise ParseError("Top-level `group` must be a string if present.")
    group = group_raw.strip() if isinstance(group_raw, str) else None
    if group == "":
        group = None

    states_raw = data.get("states")
    if not isinstance(states_raw, dict):
        raise ParseError("`states` must be a JSON object (id → state spec).")
    if not states_raw:
        raise ParseError("`states` must contain at least one state.")

    transitions_raw = data.get("transitions")
    if not isinstance(transitions_raw, list):
        raise ParseError("`transitions` must be a list.")

    # Parse states
    states: dict[str, State] = {}
    for state_id, spec in states_raw.items():
        if not isinstance(state_id, str) or not state_id:
            raise ParseError(f"State id must be a non-empty string (got {state_id!r}).")
        if not isinstance(spec, dict):
            raise ParseError(
                f"State {state_id!r}: spec must be an object "
                f"(got {type(spec).__name__})."
            )
        states[state_id] = _parse_state(state_id, spec)

    # Parse transitions
    transitions: list[Transition] = []
    for idx, spec in enumerate(transitions_raw):
        if not isinstance(spec, dict):
            raise ParseError(
                f"transitions[{idx}] must be an object (got {type(spec).__name__})."
            )
        transitions.append(_parse_transition(idx, spec, states))

    # Build the HITL gate legend (gate_name → destination reversibility).
    # Every hitl transition declares a `gate` field; the destination state's
    # reversibility is the gate's reversibility per principle 11.
    gates_in_legend: dict[str, ReversibilityClass] = {}
    for t in transitions:
        if not t.is_gated or t.gate_name is None:
            continue
        if t.destination == "[*]":
            continue
        dst = states.get(t.destination)
        if dst is None or dst.reversibility is None:
            continue
        gates_in_legend[t.gate_name] = dst.reversibility

    return StateMachine(
        name=name,
        description=description,
        group=group,
        states=states,
        transitions=transitions,
        gates_in_legend=gates_in_legend,
        source_path=source_path,
    )


def _parse_state(state_id: str, spec: dict[str, Any]) -> State:
    class_raw = spec.get("class")
    if not isinstance(class_raw, str) or class_raw not in _STATE_CLASS:
        raise ParseError(
            f"State {state_id!r}: `class` must be one of "
            f"{sorted(_STATE_CLASS.keys())} (got {class_raw!r})."
        )
    state_class = _STATE_CLASS[class_raw]

    reversibility: ReversibilityClass | None = None
    rev_raw = spec.get("reversibility")
    if rev_raw is not None:
        if not isinstance(rev_raw, str) or rev_raw not in _REVERSIBILITY:
            raise ParseError(
                f"State {state_id!r}: `reversibility` must be one of "
                f"{sorted(_REVERSIBILITY.keys())} (got {rev_raw!r})."
            )
        reversibility = _REVERSIBILITY[rev_raw]
    # Reversibility is REQUIRED on resting + closing states (every state
    # an issue can "land" in declares how reversible the landing is).
    # Working states are transient — the field is FORBIDDEN there.
    if state_class is StateClass.WORKING and reversibility is not None:
        raise ParseError(
            f"State {state_id!r}: `reversibility` is not valid on working "
            f"states (working states are transient — only resting and "
            f"closing state landings have a reversibility class)."
        )
    if state_class is not StateClass.WORKING and reversibility is None:
        raise ParseError(
            f"State {state_id!r}: `reversibility` is required on "
            f"{state_class.value} states (one of "
            f"{sorted(_REVERSIBILITY.keys())})."
        )


    roles_raw = spec.get("roles", [])
    if not isinstance(roles_raw, list):
        raise ParseError(
            f"State {state_id!r}: `roles` must be a list of role ids "
            f"(got {type(roles_raw).__name__})."
        )
    roles_parsed: list[str] = []
    for i, role in enumerate(roles_raw):
        if not isinstance(role, str) or not role.strip():
            raise ParseError(
                f"State {state_id!r}: `roles[{i}]` must be a non-empty "
                f"string (got {role!r})."
            )
        cleaned = role.strip().strip("{}").strip()
        if cleaned in roles_parsed:
            raise ParseError(
                f"State {state_id!r}: duplicate role {cleaned!r} in `roles`."
            )
        roles_parsed.append(cleaned)
    if roles_parsed and state_class is not StateClass.WORKING:
        raise ParseError(
            f"State {state_id!r}: `roles` is only valid on working states "
            f"(state class is {state_class.value!r})."
        )
    if state_class is StateClass.WORKING and not roles_parsed:
        raise ParseError(
            f"State {state_id!r}: `roles` is required on working states "
            f"(declare which role(s) may occupy this state)."
        )
    # Reject the legacy field outright so authors don't silently lose the
    # role declaration during migration.
    if "claim_role" in spec:
        raise ParseError(
            f"State {state_id!r}: `claim_role` was removed. Move the role "
            f"to `roles: [...]` on the working state(s) reached by CLAIM "
            f"transitions from this state."
        )
    roles = tuple(roles_parsed)

    closes = _parse_closes(state_id, state_class, spec.get("closes"))

    state_issue_types_raw = spec.get("issue_types", [])
    if not isinstance(state_issue_types_raw, list):
        raise ParseError(
            f"State {state_id!r}: `issue_types` must be a list of type ids "
            f"(got {type(state_issue_types_raw).__name__})."
        )
    state_issue_types_parsed: list[str] = []
    for i, t in enumerate(state_issue_types_raw):
        if not isinstance(t, str) or not t.strip():
            raise ParseError(
                f"State {state_id!r}: `issue_types[{i}]` must be a non-empty "
                f"string (got {t!r})."
            )
        cleaned = t.strip()
        if cleaned in state_issue_types_parsed:
            raise ParseError(
                f"State {state_id!r}: duplicate type {cleaned!r} in `issue_types`."
            )
        state_issue_types_parsed.append(cleaned)
    if state_class is StateClass.WORKING and not state_issue_types_parsed:
        raise ParseError(
            f"State {state_id!r}: `issue_types` is required on working "
            f"states (declare which issue types this state accepts)."
        )
    if (
        state_class is StateClass.RESTING
        and not state_issue_types_parsed
        and closes is None
    ):
        # Closing states (resting + `closes`) hold nothing, so they're exempt
        # from the resting `issue_types` requirement; the validator enforces
        # that `closes` and `issue_types` are mutually exclusive.
        raise ParseError(
            f"State {state_id!r}: `issue_types` is required on resting "
            f"states (declare which issue types may sit in this state). "
            f"Use the subset of the process's working-state types that "
            f"can pass through here."
        )
    state_issue_types = tuple(state_issue_types_parsed)

    handoff_raw = spec.get("handoff", False)
    if not isinstance(handoff_raw, bool):
        raise ParseError(
            f"State {state_id!r}: `handoff` must be a boolean if present "
            f"(got {type(handoff_raw).__name__})."
        )
    if handoff_raw and state_class is not StateClass.RESTING:
        raise ParseError(
            f"State {state_id!r}: `handoff` is only valid on resting "
            f"states (handovers are the interface between processes; "
            f"working / closing states aren't interfaces)."
        )

    spawns = _parse_spawns(state_id, state_class, spec.get("spawns"))
    collects = _parse_collects(state_id, state_class, spec.get("collects"))

    is_initial, initial_label = _parse_initial(
        state_id, state_class, spec.get("initial")
    )

    mark_pr_ready_raw = spec.get("mark_pr_ready", False)
    if not isinstance(mark_pr_ready_raw, bool):
        raise ParseError(
            f"State {state_id!r}: `mark_pr_ready` must be a boolean if present "
            f"(got {type(mark_pr_ready_raw).__name__})."
        )
    if mark_pr_ready_raw and closes is not None:
        raise ParseError(
            f"State {state_id!r}: `mark_pr_ready` is not valid on closing "
            f"states (a closing state has already reached its final form)."
        )

    human_inputs_raw = spec.get("human_inputs", [])
    if not isinstance(human_inputs_raw, list):
        raise ParseError(
            f"State {state_id!r}: `human_inputs` must be a list of topic ids "
            f"(got {type(human_inputs_raw).__name__})."
        )
    human_inputs_parsed: list[str] = []
    for i, t in enumerate(human_inputs_raw):
        if not isinstance(t, str) or not t.strip():
            raise ParseError(
                f"State {state_id!r}: `human_inputs[{i}]` must be a non-empty "
                f"string (got {t!r})."
            )
        cleaned = t.strip()
        if cleaned in human_inputs_parsed:
            raise ParseError(
                f"State {state_id!r}: duplicate topic {cleaned!r} in `human_inputs`."
            )
        human_inputs_parsed.append(cleaned)
    if human_inputs_parsed and state_class is not StateClass.WORKING:
        raise ParseError(
            f"State {state_id!r}: `human_inputs` is only valid on working "
            f"states (state class is {state_class.value!r})."
        )

    notes_raw = spec.get("notes", [])
    if notes_raw is None:
        notes_raw = []
    if not isinstance(notes_raw, list):
        raise ParseError(
            f"State {state_id!r}: `notes` must be a list of strings if present."
        )
    notes: list[str] = []
    for i, note in enumerate(notes_raw):
        if not isinstance(note, str):
            raise ParseError(
                f"State {state_id!r}: `notes[{i}]` must be a string "
                f"(got {type(note).__name__})."
            )
        notes.append(note)

    return State(
        name=state_id,
        state_class=state_class,
        reversibility=reversibility,
        roles=roles,
        issue_types=state_issue_types,
        handoff=handoff_raw,
        is_initial=is_initial,
        initial_label=initial_label,
        spawns=spawns,
        collects=collects,
        mark_pr_ready=mark_pr_ready_raw,
        human_inputs=tuple(human_inputs_parsed),
        closes=closes,
        notes=notes,
    )


def _parse_spawns(
    state_id: str, state_class: StateClass, raw: Any
) -> tuple[Spawn, ...]:
    """Parse the optional `spawns` field on any non-`[*]` state.

    Accepts either:
    - A single spawn object (the historical shape).
    - An array of spawn objects (multi-spawn — one rule per kind of
      work the agent can dispatch from this state).

    Working and resting states may declare `advance_on` (selective auto-
    advance on the listed child closing states; everything else keeps the
    parent put). Resting-state `advance_on` targets must be non-working
    states (event-style transition; no claim). Closing states forbid
    `advance_on` (the parent is already closed). Cross-state-class
    validation of `advance_on` targets lives in the validator.

    `process` is optional now — when omitted, the validator resolves it
    from `initial_state` via the registry's state-name uniqueness
    invariant. When supplied, the validator cross-checks the resolved
    process matches.
    """
    if raw is None:
        return ()
    if isinstance(raw, dict):
        return (_parse_one_spawn(state_id, state_class, raw, 0),)
    if not isinstance(raw, list):
        raise ParseError(
            f"State {state_id!r}: `spawns` must be an object or a list of "
            f"objects (got {type(raw).__name__})."
        )
    if not raw:
        raise ParseError(
            f"State {state_id!r}: `spawns` list is empty. Use omitted/null "
            f"to declare no spawns; an empty list is ambiguous."
        )
    return tuple(
        _parse_one_spawn(state_id, state_class, item, idx)
        for idx, item in enumerate(raw)
    )


def _parse_one_spawn(
    state_id: str, state_class: StateClass, raw: Any, idx: int
) -> Spawn:
    if not isinstance(raw, dict):
        raise ParseError(
            f"State {state_id!r}: `spawns[{idx}]` must be an object "
            f"(got {type(raw).__name__})."
        )
    process_raw = raw.get("process")
    process: str | None = None
    if process_raw is not None:
        if not isinstance(process_raw, str) or not process_raw.strip():
            raise ParseError(
                f"State {state_id!r}: `spawns[{idx}].process` must be a "
                f"non-empty string if present."
            )
        process = process_raw.strip()
    issue_type = raw.get("issue_type")
    if not isinstance(issue_type, str) or not issue_type.strip():
        raise ParseError(
            f"State {state_id!r}: `spawns[{idx}].issue_type` is required "
            f"(child issue type)."
        )
    initial_state = raw.get("initial_state")
    if not isinstance(initial_state, str) or not initial_state.strip():
        raise ParseError(
            f"State {state_id!r}: `spawns[{idx}].initial_state` is required "
            f"(child's starting state)."
        )

    # `advance_on` forbidden on closing-state spawns (the parent is already
    # closed) is enforced by the validator — the parser doesn't have the
    # `closes` annotation threaded into spawn parsing.
    advance_on_raw = raw.get("advance_on")
    advance_on: list[tuple[str, str]] = []
    if advance_on_raw is not None:
        if not isinstance(advance_on_raw, dict):
            raise ParseError(
                f"State {state_id!r}: `spawns[{idx}].advance_on` must be "
                f"an object mapping {{child-closing-state: parent-next-state}} "
                f"if present."
            )
        for k, v in advance_on_raw.items():
            if not isinstance(k, str) or not k.strip():
                raise ParseError(
                    f"State {state_id!r}: `spawns[{idx}].advance_on` keys "
                    f"must be child closing-state names (got {k!r})."
                )
            if not isinstance(v, str) or not v.strip():
                raise ParseError(
                    f"State {state_id!r}: `spawns[{idx}].advance_on[{k!r}]"
                    f"` must be a parent state name (got {v!r})."
                )
            advance_on.append((k.strip(), v.strip()))

    return Spawn(
        process=process,
        issue_type=issue_type.strip(),
        initial_state=initial_state.strip(),
        advance_on=tuple(advance_on),
    )


def _parse_collects(
    state_id: str, state_class: StateClass, raw: Any
) -> Collects | None:
    """Parse the optional `collects` field. Only valid on resting states.

    Cross-process validation (`process` resolves, `from_states` are
    resting/closing state in that process) lives in the validator — the parser
    doesn't have other workflows in scope here.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ParseError(
            f"State {state_id!r}: `collects` must be an object "
            f"(got {type(raw).__name__})."
        )
    if state_class is not StateClass.RESTING:
        raise ParseError(
            f"State {state_id!r}: `collects` is only valid on resting states "
            f"(state class is {state_class.value!r}). Move it to the resting "
            f"state where the collector issue is created."
        )

    # `process` is optional — state names are unique workflow-wide, so
    # the source process is derivable from `from_states`. The validator
    # resolves omitted process names; if authored it must match the
    # resolution.
    process_raw = raw.get("process")
    process: str | None
    if process_raw is None:
        process = None
    elif isinstance(process_raw, str) and process_raw.strip():
        process = process_raw.strip()
    else:
        raise ParseError(
            f"State {state_id!r}: `collects.process`, if present, must be a "
            f"non-empty string."
        )

    from_states_raw = raw.get("from_states")
    if not isinstance(from_states_raw, list) or not from_states_raw:
        raise ParseError(
            f"State {state_id!r}: `collects.from_states` must be a non-empty "
            f"list of state names."
        )
    from_states: list[str] = []
    for i, s in enumerate(from_states_raw):
        if not isinstance(s, str) or not s.strip():
            raise ParseError(
                f"State {state_id!r}: `collects.from_states[{i}]` must be a "
                f"non-empty string (got {s!r})."
            )
        cleaned = s.strip()
        if cleaned in from_states:
            raise ParseError(
                f"State {state_id!r}: duplicate state {cleaned!r} in "
                f"`collects.from_states`."
            )
        from_states.append(cleaned)

    issue_types_raw = raw.get("issue_types")
    issue_types: list[str] = []
    if issue_types_raw is not None:
        if not isinstance(issue_types_raw, list):
            raise ParseError(
                f"State {state_id!r}: `collects.issue_types` must be a list "
                f"of issue-type ids if present (got "
                f"{type(issue_types_raw).__name__})."
            )
        for i, t in enumerate(issue_types_raw):
            if not isinstance(t, str) or not t.strip():
                raise ParseError(
                    f"State {state_id!r}: `collects.issue_types[{i}]` must be "
                    f"a non-empty string (got {t!r})."
                )
            cleaned = t.strip()
            if cleaned in issue_types:
                raise ParseError(
                    f"State {state_id!r}: duplicate type {cleaned!r} in "
                    f"`collects.issue_types`."
                )
            issue_types.append(cleaned)

    advance_on_raw = raw.get("advance_on")
    advance_on: list[CollectAdvanceRule] = []
    if advance_on_raw is not None:
        if not isinstance(advance_on_raw, dict):
            raise ParseError(
                f"State {state_id!r}: `collects.advance_on` must be an object "
                f"mapping collector-state → target (got "
                f"{type(advance_on_raw).__name__})."
            )
        for k, v in advance_on_raw.items():
            if not isinstance(k, str) or not k.strip():
                raise ParseError(
                    f"State {state_id!r}: `collects.advance_on` keys must be "
                    f"collector-state names (got {k!r})."
                )
            collector_state = k.strip()
            if isinstance(v, str):
                if not v.strip():
                    raise ParseError(
                        f"State {state_id!r}: `collects.advance_on[{k!r}]` "
                        f"must be a non-empty target state name."
                    )
                advance_on.append(
                    CollectAdvanceRule(
                        collector_state=collector_state,
                        default_target=v.strip(),
                        by_type=(),
                    )
                )
            elif isinstance(v, dict):
                if not v:
                    raise ParseError(
                        f"State {state_id!r}: `collects.advance_on[{k!r}]` "
                        f"must declare at least one target."
                    )
                default_target: str | None = None
                by_type: list[tuple[str, str]] = []
                seen_types: set[str] = set()
                for tk, tv in v.items():
                    if not isinstance(tk, str) or not tk.strip():
                        raise ParseError(
                            f"State {state_id!r}: "
                            f"`collects.advance_on[{k!r}]` keys must be "
                            f"contributor type ids or '*' (got {tk!r})."
                        )
                    if not isinstance(tv, str) or not tv.strip():
                        raise ParseError(
                            f"State {state_id!r}: "
                            f"`collects.advance_on[{k!r}][{tk!r}]` must be a "
                            f"non-empty target state name."
                        )
                    type_key = tk.strip()
                    target_state = tv.strip()
                    if type_key in seen_types:
                        raise ParseError(
                            f"State {state_id!r}: "
                            f"`collects.advance_on[{k!r}]` has duplicate "
                            f"contributor type {type_key!r}."
                        )
                    seen_types.add(type_key)
                    if type_key == "*":
                        default_target = target_state
                    else:
                        by_type.append((type_key, target_state))
                advance_on.append(
                    CollectAdvanceRule(
                        collector_state=collector_state,
                        default_target=default_target,
                        by_type=tuple(by_type),
                    )
                )
            else:
                raise ParseError(
                    f"State {state_id!r}: `collects.advance_on[{k!r}]` must "
                    f"be a target state name (string) or a per-type map "
                    f"(object); got {type(v).__name__}."
                )

    release_on_raw = raw.get("release_on")
    release_on: list[str] = []
    if release_on_raw is not None:
        if not isinstance(release_on_raw, list):
            raise ParseError(
                f"State {state_id!r}: `collects.release_on` must be a list of "
                f"collector-state names (got {type(release_on_raw).__name__})."
            )
        for i, s in enumerate(release_on_raw):
            if not isinstance(s, str) or not s.strip():
                raise ParseError(
                    f"State {state_id!r}: `collects.release_on[{i}]` must be a "
                    f"non-empty string (got {s!r})."
                )
            cleaned = s.strip()
            if cleaned in release_on:
                raise ParseError(
                    f"State {state_id!r}: duplicate state {cleaned!r} in "
                    f"`collects.release_on`."
                )
            release_on.append(cleaned)

    overlap = {rule.collector_state for rule in advance_on} & set(release_on)
    if overlap:
        raise ParseError(
            f"State {state_id!r}: `collects.advance_on` and `collects.release_on` "
            f"share state(s) {sorted(overlap)}. A collector state either moves "
            f"contributors (advance_on) or releases them in place (release_on); "
            f"choose one per state."
        )

    return Collects(
        process=process,
        from_states=tuple(from_states),
        issue_types=tuple(issue_types),
        advance_on=tuple(advance_on),
        release_on=tuple(release_on),
    )


def _parse_closes(
    state_id: str, state_class: StateClass, raw: Any
) -> Closes | None:
    """Parse the optional `closes` annotation (ADR-0002).

    Shape: `{ "taxonomy": <closure tag>, "reason": <close reason> }`. Only
    valid on resting states — a closing state is an unowned sink, so it can't
    be a working state. Mutual exclusion with `is_initial` / `collects` /
    `handoff` / `issue_types` is enforced by the validator.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ParseError(
            f"State {state_id!r}: `closes` must be an object "
            f"`{{taxonomy, reason}}` (got {type(raw).__name__})."
        )
    if state_class is not StateClass.RESTING:
        raise ParseError(
            f"State {state_id!r}: `closes` is only valid on resting states "
            f"(state class is {state_class.value!r}); a closing state is an "
            f"unowned sink."
        )
    tax_raw = raw.get("taxonomy")
    if not isinstance(tax_raw, str) or tax_raw not in _CLOSURE_TAX:
        raise ParseError(
            f"State {state_id!r}: `closes.taxonomy` must be one of "
            f"{sorted(_CLOSURE_TAX.keys())} (got {tax_raw!r})."
        )
    reason_raw = raw.get("reason")
    if not isinstance(reason_raw, str) or not reason_raw.strip():
        raise ParseError(
            f"State {state_id!r}: `closes.reason` is required and must be a "
            f"non-empty string (for GitHub, 'completed' or 'not planned')."
        )
    return Closes(taxonomy=_CLOSURE_TAX[tax_raw], reason=reason_raw.strip())


def _parse_initial(
    state_id: str, state_class: StateClass, raw: Any
) -> tuple[bool, str | None]:
    """Parse the optional `initial` field.

    Accepts:
      - absent / null / False  → (False, None)
      - True                   → (True, None)
      - non-empty string       → (True, <label>)

    Only valid on resting states (parser-enforced). Mutual exclusion
    with `collects` and inbound-spawn targets is checked by the
    validator (it needs the sibling-machines map).
    """
    if raw is None or raw is False:
        return False, None
    if raw is True:
        if state_class is not StateClass.RESTING:
            raise ParseError(
                f"State {state_id!r}: `initial` is only valid on resting "
                f"states (state class is {state_class.value!r})."
            )
        return True, None
    if isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            raise ParseError(
                f"State {state_id!r}: `initial` must be a non-empty string "
                f"or boolean (got empty string)."
            )
        if state_class is not StateClass.RESTING:
            raise ParseError(
                f"State {state_id!r}: `initial` is only valid on resting "
                f"states (state class is {state_class.value!r})."
            )
        # `initial` becomes part of the mermaid `[*] --> state: <label>`
        # description; the v2 parser rejects a second `:` and bare `;` /
        # newlines inside.
        bad_chars = [c for c in (":", ";", "\n") if c in cleaned]
        if bad_chars:
            raise ParseError(
                f"State {state_id!r}: `initial` label {cleaned!r} contains a "
                f"character ({bad_chars!r}) that mermaid's stateDiagram-v2 "
                f"parser rejects. Rephrase — use parentheses or an em-dash "
                f"instead of `:`; comma or new sentence instead of `;`."
            )
        return True, cleaned
    raise ParseError(
        f"State {state_id!r}: `initial` must be a boolean or non-empty "
        f"string (got {type(raw).__name__})."
    )


def _parse_transition(
    idx: int, spec: dict[str, Any], states: dict[str, State]
) -> Transition:
    source = _require_endpoint(spec, "source", idx)
    destination = _require_endpoint(spec, "destination", idx)
    if source == "[*]":
        raise ParseError(
            f"transitions[{idx}]: `[*]→state` transitions are no longer "
            f"authored. Mark the destination state with `\"initial\": true` "
            f"(or `\"initial\": \"<label>\"`) instead. Example: move "
            f"`{{\"source\": \"[*]\", \"destination\": {destination!r}, "
            f"\"type\": \"event\", \"label\": \"<label>\"}}` to the "
            f"{destination!r} state spec as `\"initial\": \"<label>\"`."
        )
    if destination == "[*]":
        raise ParseError(
            f"transitions[{idx}]: `state→[*]` transitions are implicit; the "
            f"emitter generates closing-state sinks from each closing state's "
            f"`closes`. Remove this transition."
        )
    if source not in states:
        raise ParseError(
            f"transitions[{idx}]: source {source!r} is not a declared state."
        )
    if destination not in states:
        raise ParseError(
            f"transitions[{idx}]: destination {destination!r} is not a declared state."
        )

    type_raw = spec.get("type")
    if type_raw == "cross_process":
        raise ParseError(
            f"transitions[{idx}]: `cross_process` was removed. Shared "
            f"handovers use `handoff: true` on the resting state; "
            f"subprocess / independent spawns use `spawns: {{...}}` on "
            f"the working / closing state."
        )
    if not isinstance(type_raw, str) or type_raw not in _TRANSITION_TYPE:
        raise ParseError(
            f"transitions[{idx}]: `type` must be one of "
            f"{sorted(_TRANSITION_TYPE.keys())} (got {type_raw!r})."
        )
    transition_type = _TRANSITION_TYPE[type_raw]

    # `kind` and `process` were cross_process-only metadata. Reject them
    # outright so authors can't leave them around after migration.
    if "kind" in spec or "process" in spec:
        raise ParseError(
            f"transitions[{idx}]: `kind` and `process` fields were "
            f"removed along with the cross_process type. Use `handoff: "
            f"true` on a resting state for shared handovers, or "
            f"`spawns: {{...}}` on a working / closing state for spawns."
        )

    # Label is optional for all types except external (which has no good
    # default — it describes the system event that fired). When absent for
    # other types, a structural label is generated below.
    label_raw = spec.get("label")
    if label_raw is not None and not isinstance(label_raw, str):
        raise ParseError(f"transitions[{idx}]: `label` must be a string if present.")
    label = label_raw.strip() if isinstance(label_raw, str) else ""
    # stateDiagram-v2 treats the first `:` after the arrow as the label
    # separator and rejects a second one inside the description. The
    # same applies to `;` (statement terminator) and newlines. Reject
    # these characters at parse time so the emitter can't produce
    # malformed mermaid that fails to render.
    bad_chars = [c for c in (":", ";", "\n") if c in label]
    if bad_chars:
        raise ParseError(
            f"transitions[{idx}]: label {label!r} contains a character "
            f"({bad_chars!r}) that mermaid's stateDiagram-v2 parser rejects "
            f"inside transition descriptions. Rephrase the label — use "
            f"parentheses or an em-dash instead of `:`; use a comma or new "
            f"sentence instead of `;`."
        )

    # The standalone `hitl` flag was merged into `human_gate`: presence of
    # `human_gate` IS the HITL marker. Reject `hitl` outright so authors
    # don't carry the redundant field forward.
    if "hitl" in spec:
        raise ParseError(
            f"transitions[{idx}]: `hitl` was removed. The presence of "
            f"`human_gate` now marks a transition as HITL-gated — set "
            f"`human_gate` to the human-gate catalog `gate_name`, omit it for ungated."
        )

    gate_name: str | None = None
    gate_raw = spec.get("human_gate")
    if gate_raw is not None:
        if not isinstance(gate_raw, str) or not gate_raw.strip():
            raise ParseError(
                f"transitions[{idx}]: `human_gate` must be a non-empty string if present."
            )
        gate_name = gate_raw.strip()

    # Auto-generate the label when absent (except for event, which has
    # no good default and stays required).
    if not label:
        if transition_type is TransitionType.EVENT:
            raise ParseError(
                f"transitions[{idx}]: `label` is required on event "
                f"transitions (describe the system event that fires it)."
            )
        label = _generate_label(
            transition_type,
            source,
            destination,
            states.get(destination),
            states.get(source),
        )

    return Transition(
        source=source,
        destination=destination,
        label=label,
        transition_type=transition_type,
        gate_name=gate_name,
    )


def _generate_label(
    transition_type: TransitionType,
    source: str,
    destination: str,
    dest_state: State | None,
    source_state: State | None,
) -> str:
    """Structural label for a transition when the author omitted one.

    - CLAIM: `{role(s)} claim {source}` (drawn from destination's roles)
    - ADVANCE: `{role(s)} → {destination}` (drawn from source's roles)

    EVENT transitions require an authored label and never reach this path.
    """
    if transition_type is TransitionType.CLAIM:
        if dest_state and dest_state.roles:
            roles_str = ", ".join(dest_state.roles)
            verb = "claims" if len(dest_state.roles) == 1 else "claim"
            return f"{roles_str} {verb} {source}"
        return f"claim into {destination}"
    if transition_type is TransitionType.ADVANCE:
        if source_state and source_state.roles:
            roles_str = ", ".join(source_state.roles)
            return f"{roles_str} → {destination}"
        return f"→ {destination}"
    return ""


def _require_endpoint(spec: dict[str, Any], field: str, idx: int) -> str:
    value = spec.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ParseError(
            f"transitions[{idx}]: `{field}` must be a non-empty string "
            f"(state id or `[*]`)."
        )
    return value.strip()
