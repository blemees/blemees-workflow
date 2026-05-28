"""Human-gate catalog parser — reads `<workflow>-human-gates.json` into a
`HumanGateCatalog`.

The catalog is structured data, not markdown. This parser fails loudly on
malformed input (per the rationale in the design discussion that motivated
the move away from markdown tables). JSON's strict syntax makes truncation
and partial-data bugs surface immediately.

## Expected JSON shape

```json
{
  "human_gates": [
    {
      "gate_name": "ready_for_dev",
      "type": "judgment",
      "allowed_levels": ["block", "audit"],
      "default_level": "block",
      "agent_prepares": "ready-packet-template.md",
      "rationale": "ready_for_dev-rationale.md"
    }
  ]
}
```

The gate carries only **policy** fields. Structural information —
source state, destinations, triggering roles, reversibility — is
derived from the paired state machine at lookup time via
`StateMachine.gate_*` helpers. The process name is derived from the
filename stem (`<process>-human-gates.json`).

The `rationale` field is a string. By convention it is either a one-line
inline rationale or a filename pointing at a sidecar markdown file (e.g.,
`<gate>-rationale.md`). The tool does not interpret the difference; both are
stored as-is for downstream readers.

If the file does not exist, the parser returns an empty `HumanGateCatalog`
and logs a debug message. This matches the pre-HITL state of every shipped
workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.human_gate import (
    HumanGate,
    HumanGateCatalog,
    HumanGateLevel,
    HumanGateType,
)
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_human_gate_catalog(
    source: str | Path,
    process_name: str | None = None,
) -> HumanGateCatalog:
    """Parse a human-gate catalog from a JSON file path or a JSON string.

    `process_name` defaults to the file stem with `-human-gates` stripped.

    Returns an empty `HumanGateCatalog` if the source is a path that does not
    exist. Raises `ParseError` for malformed JSON or schema violations.
    """
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str)
        and "\n" not in source
        and not source.lstrip().startswith(("{", "["))
    ):
        path = Path(source)
        if not path.exists():
            logger.debug(
                "Human-gate catalog not found at %s; returning empty catalog.", path
            )
            return HumanGateCatalog(
                process_name=process_name or _infer_process_name(path),
                source_path=str(path),
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read human-gate catalog {path}: {exc}") from exc
        source_path = str(path)
        if process_name is None:
            process_name = _infer_process_name(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Human-gate catalog{f' at {source_path}' if source_path else ''} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Human-gate catalog must be a JSON object at the top level (got {type(data).__name__})."
        )

    # The process name is derived from the filename stem
    # (`<process>-human-gates.json`).
    process_name = process_name or "unnamed"

    gates_raw = data.get("human_gates", [])
    if not isinstance(gates_raw, list):
        raise ParseError(
            f"Human-gate catalog `human_gates` must be a list (got {type(gates_raw).__name__})."
        )

    catalog = HumanGateCatalog(process_name=process_name, source_path=source_path)
    for idx, entry in enumerate(gates_raw):
        if not isinstance(entry, dict):
            raise ParseError(
                f"Human-gate catalog entry #{idx} must be an object (got {type(entry).__name__})."
            )
        gate = _parse_entry(entry, idx, source_path)
        if gate.gate_name in catalog.entries:
            raise ParseError(
                f"Human-gate catalog has duplicate gate_name {gate.gate_name!r} at entry #{idx}."
            )
        catalog.entries[gate.gate_name] = gate

    return catalog


def _parse_entry(entry: dict[str, Any], idx: int, source_path: str | None) -> HumanGate:
    gate_name = _require_str(entry, "gate_name", idx)
    gate_type = _parse_type(_require_str(entry, "type", idx), gate_name=gate_name)

    allowed_levels_raw = entry.get("allowed_levels")
    if not isinstance(allowed_levels_raw, list) or not allowed_levels_raw:
        raise ParseError(
            f"Human gate {gate_name!r}: `allowed_levels` must be a non-empty list."
        )
    allowed_levels = [_parse_level(lvl, gate_name=gate_name) for lvl in allowed_levels_raw]

    default_level = _parse_level(
        _require_str(entry, "default_level", idx), gate_name=gate_name
    )
    if default_level not in allowed_levels:
        raise ParseError(
            f"Human gate {gate_name!r}: `default_level` ({default_level.value}) is not in `allowed_levels`."
        )

    agent_prepares = entry.get("agent_prepares")
    if agent_prepares is not None and not isinstance(agent_prepares, str):
        raise ParseError(
            f"Human gate {gate_name!r}: `agent_prepares` must be a string if present."
        )
    if isinstance(agent_prepares, str):
        agent_prepares = agent_prepares.strip() or None

    rationale = entry.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise ParseError(
            f"Human gate {gate_name!r}: `rationale` must be a string if present."
        )
    if isinstance(rationale, str):
        rationale = rationale.strip() or None

    return HumanGate(
        gate_name=gate_name,
        gate_type=gate_type,
        allowed_levels=allowed_levels,
        default_level=default_level,
        agent_prepares_path=agent_prepares,
        rationale=rationale,
        source_doc=source_path,
    )


def _require_str(
    obj: dict[str, Any],
    key: str,
    idx: int,
    *,
    parent: str = "human_gates[idx]",
    allow_empty: bool = False,
) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ParseError(
            f"{parent.replace('idx', str(idx))}.{key}: required string field is missing or wrong type."
        )
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ParseError(f"{parent.replace('idx', str(idx))}.{key}: must not be empty.")
    return cleaned


def _parse_type(value: str, gate_name: str) -> HumanGateType:
    lowered = value.lower().strip()
    mapping = {
        "authority": HumanGateType.AUTHORITY,
        "knowledge": HumanGateType.KNOWLEDGE,
        "judgment": HumanGateType.JUDGMENT,
        "taste": HumanGateType.JUDGMENT,
        "reality": HumanGateType.REALITY,
    }
    if lowered not in mapping:
        raise ParseError(
            f"Human gate {gate_name!r}: `type` must be one of {sorted(mapping.keys())} (got {value!r})."
        )
    return mapping[lowered]


def _parse_level(value: Any, gate_name: str) -> HumanGateLevel:
    if not isinstance(value, str):
        raise ParseError(
            f"Human gate {gate_name!r}: level value must be a string (got {type(value).__name__})."
        )
    lowered = value.lower().strip()
    if lowered == "block":
        return HumanGateLevel.BLOCK
    if lowered == "audit":
        return HumanGateLevel.AUDIT
    raise ParseError(
        f"Human gate {gate_name!r}: level must be 'block' or 'audit' (got {value!r})."
    )


def _infer_process_name(path: Path) -> str:
    """`refinement-human-gates.json` → `refinement`."""
    stem = path.stem
    if stem.endswith("-human-gates"):
        return stem[: -len("-human-gates")]
    return stem
