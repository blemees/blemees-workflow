"""HCP catalog parser — reads `<workflow>-hcps.json` into an `HCPCatalog`.

The catalog is structured data, not markdown. This parser fails loudly on
malformed input (per the rationale in the design discussion that motivated
the move away from markdown tables). JSON's strict syntax makes truncation
and partial-data bugs surface immediately.

## Expected JSON shape

```json
{
  "process": "refinement",
  "hcps": [
    {
      "gate_name": "ready_for_dev",
      "transition": {
        "source": "refining",
        "destinations": ["ready_for_dev"]
      },
      "triggering_role": "pm",
      "type": "judgment",
      "reversibility": "reversible-slow",
      "allowed_levels": ["block", "audit"],
      "default_level": "block",
      "agent_prepares": "ready-packet-template.md",
      "rationale": "ready_for_dev-rationale.md"
    }
  ]
}
```

For verdict-style HCPs, `transition.destinations` has multiple entries and the
catalog row's `reversibility` reflects the worst-case among them (per
`hitl-principles.md` principle 4).

The `rationale` field is a string. By convention it is either a one-line
inline rationale or a filename pointing at a sidecar markdown file (e.g.,
`<gate>-rationale.md`). The tool does not interpret the difference; both are
stored as-is for downstream readers.

If the file does not exist, the parser returns an empty `HCPCatalog` and logs
a debug message. This matches the pre-HITL state of every shipped workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
from workflow.core.model.lifecycle import ReversibilityClass
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_hcp_catalog(
    source: str | Path,
    process_name: str | None = None,
) -> HCPCatalog:
    """Parse an HCP catalog from a JSON file path or a JSON string.

    `process_name` defaults to the catalog's `process` field, then to the
    file stem with `-hcps` stripped.

    Returns an empty `HCPCatalog` if the source is a path that does not exist.
    Raises `ParseError` for malformed JSON or schema violations.
    """
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str)
        and "\n" not in source
        and not source.lstrip().startswith(("{", "["))
    ):
        path = Path(source)
        if not path.exists():
            logger.debug("HCP catalog not found at %s; returning empty catalog.", path)
            return HCPCatalog(
                process_name=process_name or _infer_process_name(path),
                source_path=str(path),
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read HCP catalog {path}: {exc}") from exc
        source_path = str(path)
        if process_name is None:
            process_name = _infer_process_name(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"HCP catalog{f' at {source_path}' if source_path else ''} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"HCP catalog must be a JSON object at the top level (got {type(data).__name__})."
        )

    declared_process = data.get("process")
    if declared_process is not None and not isinstance(declared_process, str):
        raise ParseError(
            f"HCP catalog `process` must be a string (got {type(declared_process).__name__})."
        )
    process_name = process_name or declared_process or "unnamed"

    hcps_raw = data.get("hcps", [])
    if not isinstance(hcps_raw, list):
        raise ParseError(f"HCP catalog `hcps` must be a list (got {type(hcps_raw).__name__}).")

    catalog = HCPCatalog(process_name=process_name, source_path=source_path)
    for idx, entry in enumerate(hcps_raw):
        if not isinstance(entry, dict):
            raise ParseError(
                f"HCP catalog entry #{idx} must be an object (got {type(entry).__name__})."
            )
        hcp = _parse_entry(entry, idx, source_path)
        if hcp.gate_name in catalog.entries:
            raise ParseError(
                f"HCP catalog has duplicate gate_name {hcp.gate_name!r} at entry #{idx}."
            )
        catalog.entries[hcp.gate_name] = hcp

    return catalog


def _parse_entry(entry: dict[str, Any], idx: int, source_path: str | None) -> HCP:
    gate_name = _require_str(entry, "gate_name", idx)

    transition = entry.get("transition")
    if not isinstance(transition, dict):
        raise ParseError(f"HCP {gate_name!r}: `transition` must be an object.")
    source_state = _require_str(transition, "source", idx, parent=f"hcps[{idx}].transition")
    destinations_raw = transition.get("destinations")
    if not isinstance(destinations_raw, list) or not destinations_raw:
        raise ParseError(f"HCP {gate_name!r}: `transition.destinations` must be a non-empty list.")
    destinations = [_require_str_item(d, idx, "destinations") for d in destinations_raw]

    triggering_role = _require_str(entry, "triggering_role", idx, allow_empty=False)
    hcp_type = _parse_type(_require_str(entry, "type", idx), gate_name=gate_name)
    reversibility = _parse_reversibility(
        _require_str(entry, "reversibility", idx), gate_name=gate_name
    )

    allowed_levels_raw = entry.get("allowed_levels")
    if not isinstance(allowed_levels_raw, list) or not allowed_levels_raw:
        raise ParseError(f"HCP {gate_name!r}: `allowed_levels` must be a non-empty list.")
    allowed_levels = [_parse_level(lvl, gate_name=gate_name) for lvl in allowed_levels_raw]

    default_level = _parse_level(_require_str(entry, "default_level", idx), gate_name=gate_name)
    if default_level not in allowed_levels:
        raise ParseError(
            f"HCP {gate_name!r}: `default_level` ({default_level.value}) is not in `allowed_levels`."
        )

    agent_prepares = entry.get("agent_prepares")
    if agent_prepares is not None and not isinstance(agent_prepares, str):
        raise ParseError(f"HCP {gate_name!r}: `agent_prepares` must be a string if present.")
    if isinstance(agent_prepares, str):
        agent_prepares = agent_prepares.strip() or None

    rationale = entry.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise ParseError(f"HCP {gate_name!r}: `rationale` must be a string if present.")
    if isinstance(rationale, str):
        rationale = rationale.strip() or None

    return HCP(
        gate_name=gate_name,
        source_state=source_state,
        destinations=destinations,
        triggering_role=triggering_role,
        hcp_type=hcp_type,
        reversibility=reversibility,
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
    parent: str = "hcps[idx]",
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


def _require_str_item(value: Any, idx: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParseError(f"hcps[{idx}].{field}: every item must be a non-empty string.")
    return value.strip()


def _parse_type(value: str, gate_name: str) -> HCPType:
    lowered = value.lower().strip()
    mapping = {
        "authority": HCPType.AUTHORITY,
        "knowledge": HCPType.KNOWLEDGE,
        "judgment": HCPType.JUDGMENT,
        "taste": HCPType.JUDGMENT,
        "reality": HCPType.REALITY,
    }
    if lowered not in mapping:
        raise ParseError(
            f"HCP {gate_name!r}: `type` must be one of {sorted(mapping.keys())} (got {value!r})."
        )
    return mapping[lowered]


def _parse_reversibility(value: str, gate_name: str) -> ReversibilityClass:
    lowered = value.lower().strip()
    mapping = {
        "irreversible": ReversibilityClass.IRREVERSIBLE,
        "reversible-fast": ReversibilityClass.REVERSIBLE_FAST,
        "reversible-slow": ReversibilityClass.REVERSIBLE_SLOW,
    }
    if lowered not in mapping:
        raise ParseError(
            f"HCP {gate_name!r}: `reversibility` must be one of {sorted(mapping.keys())} (got {value!r})."
        )
    return mapping[lowered]


def _parse_level(value: Any, gate_name: str) -> HCPLevel:
    if not isinstance(value, str):
        raise ParseError(
            f"HCP {gate_name!r}: level value must be a string (got {type(value).__name__})."
        )
    lowered = value.lower().strip()
    if lowered == "block":
        return HCPLevel.BLOCK
    if lowered == "audit":
        return HCPLevel.AUDIT
    raise ParseError(f"HCP {gate_name!r}: level must be 'block' or 'audit' (got {value!r}).")


def _infer_process_name(path: Path) -> str:
    """`refinement-hcps.json` → `refinement`."""
    stem = path.stem
    if stem.endswith("-hcps"):
        return stem[: -len("-hcps")]
    return stem
