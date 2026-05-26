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
  "name": "refinement",
  "states": {
    "raw": {
      "class": "resting",
      "reversibility": "reversible-fast",
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
      "class": "terminal",
      "reversibility": "reversible-fast",
      "terminal_taxonomy": "abandoned",
      "close_reason": "not planned"
    }
  },
  "transitions": [
    {"source": "[*]", "destination": "raw", "type": "event", "label": "issue created"},
    {"source": "raw", "destination": "refining", "type": "claim", "label": "product-manager claims raw"},
    {"source": "refining", "destination": "ready_for_dev", "type": "advance",
     "label": "product-manager marks ready", "hitl": true},
    {"source": "refining", "destination": "wont_fix", "type": "advance",
     "label": "product-manager marks wont-fix", "hitl": true},
    {"source": "ready_for_dev", "destination": "refining", "type": "claim",
     "label": "product-manager claims (revision)"}
  ]
}
```

## Field reference

### Top-level
- `name` (string, optional): workflow name. Defaults to the file's stem with
  `-states` stripped. The HCP catalog path is derived from this by convention
  (`<name>-hcps.json`).
- `states` (object, required): map of state-id → state spec.
- `transitions` (list, required): ordered list of transition specs.

### States
- `class` (string, required): `"resting"` | `"working"` | `"terminal"`.
- `reversibility` (string, required on resting + terminal, forbidden on
  working): `"irreversible"` | `"reversible-fast"` | `"reversible-slow"`.
  Says how reversible *landing* in this state is. Working states are
  transient — only landings have a reversibility class.
- `terminal_taxonomy` (string, required when `class=terminal`):
  `"shipped"` | `"resolved"` | `"reverted"` | `"abandoned"` | `"deduplicated"` | `"superseded"`.
- `roles` (list of strings, required on working, forbidden elsewhere):
  role ids permitted to occupy this state. Non-empty. The role-restriction
  lives on the working state, not on the resting queue it's claimed from.
  Resting states are open queues; downstream working states declare who
  may pick items up.
- `issue_types` (list of strings, optional): subset of the process-level
  `issue_types` this working state accepts. Only valid on working states.
  Empty / absent = accepts any process-level type. The validator
  cross-checks that every entry is in the process-level set.
- `notes` (list of strings, optional): free prose for the emitter to render
  alongside the state. Not parsed for semantics — structured metadata goes
  in the typed fields above.

### Transitions
- `source` (string, required): state id or `"[*]"`.
- `destination` (string, required): state id or `"[*]"`.
- `type` (string, required): `"claim"` | `"advance"` | `"event"`.
- `label` (string, required): the human-readable transition label.
- `hitl` (bool, optional, default false): marks the transition as a HITL gate.
  Per `hitl-principles.md`, only claim and role-action transitions are
  typically gated; the validator enforces consistency with the HCP catalog.
The legend that mermaid uses (HITL gates block, cross-process interfaces
block) is **not** authored in JSON. Both legends are derived: HITL gates
from the union of `hitl=true` transitions and the destination state's
reversibility; cross-process from each state's `handoff` flag and
`spawns` field. The emitter regenerates them for visualization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.state_machine import (
    ReversibilityClass,
    Spawn,
    State,
    StateClass,
    StateMachine,
    TerminalTaxonomy,
    Transition,
    TransitionType,
)
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


_STATE_CLASS = {
    "resting": StateClass.RESTING,
    "working": StateClass.WORKING,
    "terminal": StateClass.TERMINAL,
}

_REVERSIBILITY = {
    "irreversible": ReversibilityClass.IRREVERSIBLE,
    "reversible-fast": ReversibilityClass.REVERSIBLE_FAST,
    "reversible-slow": ReversibilityClass.REVERSIBLE_SLOW,
}

_TERMINAL_TAX = {tax.value: tax for tax in TerminalTaxonomy}

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
            name = stem[: -len("-workflow")] if stem.endswith("-workflow") else stem
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

    declared_name = data.get("name")
    if declared_name is not None and not isinstance(declared_name, str):
        raise ParseError(
            f"`name` must be a string (got {type(declared_name).__name__})."
        )
    name = declared_name or name or "unnamed"

    # Process-level `issue_types` was removed — types now live exclusively
    # on working states (the umbrella is derivable as the union).
    if "issue_types" in data:
        raise ParseError(
            "Top-level `issue_types` was removed. Declare types on each "
            "working state via `issue_types: [...]`; the process's "
            "accepted set is derived as the union."
        )

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
    # Reversibility is REQUIRED on resting + terminal states (every state
    # an issue can "land" in declares how reversible the landing is).
    # Working states are transient — the field is FORBIDDEN there.
    if state_class is StateClass.WORKING and reversibility is not None:
        raise ParseError(
            f"State {state_id!r}: `reversibility` is not valid on working "
            f"states (working states are transient — only resting and "
            f"terminal landings have a reversibility class)."
        )
    if state_class is not StateClass.WORKING and reversibility is None:
        raise ParseError(
            f"State {state_id!r}: `reversibility` is required on "
            f"{state_class.value} states (one of "
            f"{sorted(_REVERSIBILITY.keys())})."
        )

    terminal_taxonomy: TerminalTaxonomy | None = None
    tax_raw = spec.get("terminal_taxonomy")
    if tax_raw is not None:
        if not isinstance(tax_raw, str) or tax_raw not in _TERMINAL_TAX:
            raise ParseError(
                f"State {state_id!r}: `terminal_taxonomy` must be one of "
                f"{sorted(_TERMINAL_TAX.keys())} (got {tax_raw!r})."
            )
        terminal_taxonomy = _TERMINAL_TAX[tax_raw]

    if state_class is StateClass.TERMINAL and terminal_taxonomy is None:
        raise ParseError(
            f"State {state_id!r}: terminal states require `terminal_taxonomy` "
            f"(one of {sorted(_TERMINAL_TAX.keys())})."
        )
    if state_class is not StateClass.TERMINAL and terminal_taxonomy is not None:
        raise ParseError(
            f"State {state_id!r}: `terminal_taxonomy` is only valid for "
            f"`class: terminal`."
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
    if state_issue_types_parsed and state_class is not StateClass.WORKING:
        raise ParseError(
            f"State {state_id!r}: `issue_types` is only valid on working "
            f"states (state class is {state_class.value!r})."
        )
    if state_class is StateClass.WORKING and not state_issue_types_parsed:
        raise ParseError(
            f"State {state_id!r}: `issue_types` is required on working "
            f"states (declare which issue types this state accepts)."
        )
    state_issue_types = tuple(state_issue_types_parsed)

    close_reason_raw = spec.get("close_reason")
    close_reason: str | None = None
    if close_reason_raw is not None:
        if not isinstance(close_reason_raw, str) or not close_reason_raw.strip():
            raise ParseError(
                f"State {state_id!r}: `close_reason` must be a non-empty string if present."
            )
        if state_class is not StateClass.TERMINAL:
            raise ParseError(
                f"State {state_id!r}: `close_reason` is only valid for `class: terminal`."
            )
        close_reason = close_reason_raw.strip()
    if state_class is StateClass.TERMINAL and close_reason is None:
        raise ParseError(
            f"State {state_id!r}: `close_reason` is required on terminal "
            f"states (every terminal closes the tracker's issue). For "
            f"GitHub, use 'completed' or 'not planned'."
        )

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
            f"working / terminal states aren't interfaces)."
        )

    spawns = _parse_spawns(state_id, state_class, spec.get("spawns"))

    mark_pr_ready_raw = spec.get("mark_pr_ready", False)
    if not isinstance(mark_pr_ready_raw, bool):
        raise ParseError(
            f"State {state_id!r}: `mark_pr_ready` must be a boolean if present "
            f"(got {type(mark_pr_ready_raw).__name__})."
        )
    if mark_pr_ready_raw and state_class is StateClass.TERMINAL:
        raise ParseError(
            f"State {state_id!r}: `mark_pr_ready` is not valid on terminal "
            f"states (terminals have already reached their final form)."
        )

    input_topics_raw = spec.get("input_topics", [])
    if not isinstance(input_topics_raw, list):
        raise ParseError(
            f"State {state_id!r}: `input_topics` must be a list of topic ids "
            f"(got {type(input_topics_raw).__name__})."
        )
    input_topics_parsed: list[str] = []
    for i, t in enumerate(input_topics_raw):
        if not isinstance(t, str) or not t.strip():
            raise ParseError(
                f"State {state_id!r}: `input_topics[{i}]` must be a non-empty "
                f"string (got {t!r})."
            )
        cleaned = t.strip()
        if cleaned in input_topics_parsed:
            raise ParseError(
                f"State {state_id!r}: duplicate topic {cleaned!r} in `input_topics`."
            )
        input_topics_parsed.append(cleaned)
    if input_topics_parsed and state_class is not StateClass.WORKING:
        raise ParseError(
            f"State {state_id!r}: `input_topics` is only valid on working "
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
        terminal_taxonomy=terminal_taxonomy,
        roles=roles,
        issue_types=state_issue_types,
        close_reason=close_reason,
        handoff=handoff_raw,
        spawns=spawns,
        mark_pr_ready=mark_pr_ready_raw,
        input_topics=tuple(input_topics_parsed),
        notes=notes,
    )


def _parse_spawns(
    state_id: str, state_class: StateClass, raw: Any
) -> Spawn | None:
    """Parse the optional `spawns` field on any non-`[*]` state.

    Working and resting states may declare `advance_on` (selective auto-
    advance on the listed child terminals; everything else keeps the
    parent put). Resting-state `advance_on` targets must be non-working
    states (event-style transition; no claim). Terminal states forbid
    `advance_on` (the parent is already closed). Cross-state-class
    validation of `advance_on` targets lives in the validator.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ParseError(
            f"State {state_id!r}: `spawns` must be an object "
            f"(got {type(raw).__name__})."
        )
    # Reject the legacy field outright so authors don't carry the field
    # forward with the now-different semantic.
    if "on_terminal" in raw:
        raise ParseError(
            f"State {state_id!r}: `spawns.on_terminal` was renamed to "
            f"`spawns.advance_on`, and the semantic changed: the map is "
            f"now SELECTIVE (advance parent only on these child terminals; "
            f"others keep the parent put), not exhaustive."
        )
    process = raw.get("process")
    if not isinstance(process, str) or not process.strip():
        raise ParseError(
            f"State {state_id!r}: `spawns.process` is required (target process name)."
        )
    issue_type = raw.get("issue_type")
    if not isinstance(issue_type, str) or not issue_type.strip():
        raise ParseError(
            f"State {state_id!r}: `spawns.issue_type` is required (child issue type)."
        )
    initial_state = raw.get("initial_state")
    if not isinstance(initial_state, str) or not initial_state.strip():
        raise ParseError(
            f"State {state_id!r}: `spawns.initial_state` is required "
            f"(child's starting state)."
        )

    advance_on_raw = raw.get("advance_on")
    advance_on: list[tuple[str, str]] = []
    if state_class is StateClass.TERMINAL:
        if advance_on_raw is not None:
            raise ParseError(
                f"State {state_id!r}: `spawns.advance_on` is not valid on "
                f"terminal-state spawns (the parent is already closed). "
                f"Remove the field for an independent spawn."
            )
    else:
        if advance_on_raw is not None:
            if not isinstance(advance_on_raw, dict):
                raise ParseError(
                    f"State {state_id!r}: `spawns.advance_on` must be an "
                    f"object mapping {{child-terminal: parent-next-state}} "
                    f"if present."
                )
            for k, v in advance_on_raw.items():
                if not isinstance(k, str) or not k.strip():
                    raise ParseError(
                        f"State {state_id!r}: `spawns.advance_on` keys must "
                        f"be child terminal state names (got {k!r})."
                    )
                if not isinstance(v, str) or not v.strip():
                    raise ParseError(
                        f"State {state_id!r}: `spawns.advance_on[{k!r}]` must "
                        f"be a parent state name (got {v!r})."
                    )
                advance_on.append((k.strip(), v.strip()))

    return Spawn(
        process=process.strip(),
        issue_type=issue_type.strip(),
        initial_state=initial_state.strip(),
        advance_on=tuple(advance_on),
    )


def _parse_transition(
    idx: int, spec: dict[str, Any], states: dict[str, State]
) -> Transition:
    source = _require_endpoint(spec, "source", idx)
    destination = _require_endpoint(spec, "destination", idx)
    if source != "[*]" and source not in states:
        raise ParseError(
            f"transitions[{idx}]: source {source!r} is not a declared state."
        )
    if destination != "[*]" and destination not in states:
        raise ParseError(
            f"transitions[{idx}]: destination {destination!r} is not a declared state."
        )

    type_raw = spec.get("type")
    if type_raw == "cross_process":
        raise ParseError(
            f"transitions[{idx}]: `cross_process` was removed. Shared "
            f"handovers use `handoff: true` on the resting state; "
            f"subprocess / independent spawns use `spawns: {{...}}` on "
            f"the working / terminal state."
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
            f"`spawns: {{...}}` on a working / terminal state for spawns."
        )

    # Label is optional for all types except external (which has no good
    # default — it describes the system event that fired). When absent for
    # other types, a structural label is generated below.
    label_raw = spec.get("label")
    if label_raw is not None and not isinstance(label_raw, str):
        raise ParseError(f"transitions[{idx}]: `label` must be a string if present.")
    label = label_raw.strip() if isinstance(label_raw, str) else ""

    # The standalone `hitl` flag was merged into `gate`: presence of `gate`
    # IS the HITL marker. Reject `hitl` outright so authors don't carry
    # the redundant field forward.
    if "hitl" in spec:
        raise ParseError(
            f"transitions[{idx}]: `hitl` was removed. The presence of "
            f"`gate` now marks a transition as HITL-gated — set `gate` "
            f"to the HCP catalog `gate_name`, omit it for ungated."
        )

    gate_name: str | None = None
    gate_raw = spec.get("gate")
    if gate_raw is not None:
        if not isinstance(gate_raw, str) or not gate_raw.strip():
            raise ParseError(
                f"transitions[{idx}]: `gate` must be a non-empty string if present."
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
