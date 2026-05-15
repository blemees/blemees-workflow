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
  "canonical_catalog_path": "refinement-hcps.json",
  "states": {
    "raw": {
      "class": "resting",
      "claim_role": "pm",
      "notes": ["optional prose for visualization"]
    },
    "refining": {"class": "working"},
    "ready_for_dev": {
      "class": "resting",
      "reversibility": "reversible-slow"
    },
    "wont_fix": {
      "class": "terminal",
      "reversibility": "reversible-fast",
      "terminal_taxonomy": "abandoned"
    }
  },
  "transitions": [
    {"source": "[*]", "destination": "raw", "type": "external", "label": "issue created"},
    {"source": "raw", "destination": "refining", "type": "claim", "label": "PM claims raw"},
    {"source": "refining", "destination": "ready_for_dev", "type": "role_action",
     "label": "PM marks ready", "hitl": true},
    {"source": "refining", "destination": "wont_fix", "type": "role_action",
     "label": "PM marks wont-fix", "hitl": true},
    {"source": "ready_for_dev", "destination": "[*]", "type": "cross_process",
     "label": "to process inner-loop", "kind": "shared", "process": "inner-loop"}
  ]
}
```

## Field reference

### Top-level
- `name` (string, optional): workflow name. Defaults to the file's stem with
  `-workflow` stripped.
- `canonical_catalog_path` (string, optional): pointer to the HCP catalog
  file or process-doc section. Carried through to `StateMachine.canonical_catalog_path`.
- `states` (object, required): map of state-id → state spec.
- `transitions` (list, required): ordered list of transition specs.

### States
- `class` (string, required): `"resting"` | `"working"` | `"terminal"`.
- `reversibility` (string, optional): `"irreversible"` | `"reversible-fast"` |
  `"reversible-slow"`.
- `terminal_taxonomy` (string, required when `class=terminal`):
  `"shipped"` | `"reverted"` | `"abandoned"` | `"deduplicated"` | `"iterated"` |
  `"aborted"` | `"stabilized"` | `"resolved"`.
- `claim_role` (string, optional): role id that claims this state.
- `notes` (list of strings, optional): free prose for the emitter to render
  alongside the state. Not parsed for semantics — structured metadata goes
  in the typed fields above.

### Transitions
- `source` (string, required): state id or `"[*]"`.
- `destination` (string, required): state id or `"[*]"`.
- `type` (string, required): `"claim"` | `"role_action"` | `"external"` |
  `"cross_process"`.
- `label` (string, required): the human-readable transition label.
- `hitl` (bool, optional, default false): marks the transition as a HITL gate.
  Per `hitl-principles.md`, only claim and role-action transitions are
  typically gated; the validator enforces consistency with the HCP catalog.
- `kind` (string, required when `type=cross_process`): `"shared"` (same issue
  continues on the other process) or `"spawn"` (new issue starts there).
- `process` (string, required when `type=cross_process`): name of the other
  process.

The legend that mermaid uses (HITL gates block, cross-process interfaces
block) is **not** authored in JSON. Both legends are derived: HITL gates
from the union of `hitl=true` transitions and the destination state's
reversibility; cross-process from the cross_process transitions. The
emitter regenerates them for visualization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.state_machine import (
    ReversibilityClass,
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
    "role_action": TransitionType.ROLE_ACTION,
    "external": TransitionType.EXTERNAL,
    "cross_process": TransitionType.CROSS_PROCESS,
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

    canonical_catalog_path = data.get("canonical_catalog_path")
    if canonical_catalog_path is not None and not isinstance(canonical_catalog_path, str):
        raise ParseError("`canonical_catalog_path` must be a string if present.")

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
        canonical_catalog_path=canonical_catalog_path,
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

    claim_role_raw = spec.get("claim_role")
    claim_role: str | None = None
    if claim_role_raw is not None:
        if not isinstance(claim_role_raw, str) or not claim_role_raw.strip():
            raise ParseError(
                f"State {state_id!r}: `claim_role` must be a non-empty string if present."
            )
        claim_role = claim_role_raw.strip().strip("{}").strip()

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
        claim_role=claim_role,
        close_reason=close_reason,
        notes=notes,
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
    if not isinstance(type_raw, str) or type_raw not in _TRANSITION_TYPE:
        raise ParseError(
            f"transitions[{idx}]: `type` must be one of "
            f"{sorted(_TRANSITION_TYPE.keys())} (got {type_raw!r})."
        )
    transition_type = _TRANSITION_TYPE[type_raw]

    label_raw = spec.get("label")
    if not isinstance(label_raw, str):
        raise ParseError(f"transitions[{idx}]: `label` must be a string.")
    label = label_raw.strip()

    hitl_raw = spec.get("hitl", False)
    if not isinstance(hitl_raw, bool):
        raise ParseError(
            f"transitions[{idx}]: `hitl` must be a boolean if present "
            f"(got {type(hitl_raw).__name__})."
        )

    gate_name: str | None = None
    gate_raw = spec.get("gate")
    if gate_raw is not None:
        if not isinstance(gate_raw, str) or not gate_raw.strip():
            raise ParseError(
                f"transitions[{idx}]: `gate` must be a non-empty string if present."
            )
        gate_name = gate_raw.strip()
    if hitl_raw and gate_name is None:
        raise ParseError(
            f"transitions[{idx}]: hitl transitions must declare a `gate` field "
            f"(the HCP catalog gate_name)."
        )
    if not hitl_raw and gate_name is not None:
        raise ParseError(
            f"transitions[{idx}]: `gate` is only valid on hitl transitions."
        )

    cross_process_kind: str | None = None
    cross_process_other: str | None = None
    if transition_type is TransitionType.CROSS_PROCESS:
        kind = spec.get("kind")
        if kind not in ("shared", "spawn"):
            raise ParseError(
                f"transitions[{idx}]: cross_process transitions require "
                f"`kind` set to 'shared' or 'spawn' (got {kind!r})."
            )
        process = spec.get("process")
        if not isinstance(process, str) or not process.strip():
            raise ParseError(
                f"transitions[{idx}]: cross_process transitions require "
                f"`process` (the name of the other process)."
            )
        cross_process_kind = kind
        cross_process_other = process.strip()

    return Transition(
        source=source,
        destination=destination,
        label=label,
        is_gated=hitl_raw,
        transition_type=transition_type,
        gate_name=gate_name,
        cross_process_kind=cross_process_kind,
        cross_process_other=cross_process_other,
    )


def _require_endpoint(spec: dict[str, Any], field: str, idx: int) -> str:
    value = spec.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ParseError(
            f"transitions[{idx}]: `{field}` must be a non-empty string "
            f"(state id or `[*]`)."
        )
    return value.strip()
