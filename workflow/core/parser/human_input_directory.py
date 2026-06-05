"""Human-input directory parser — reads `human-inputs.json`.

Shape:

```json
{
  "human_inputs": {
    "general": {
      "name": "General",
      "description": "Catch-all for off-catalog questions."
    },
    "clarify-scope": {
      "name": "Clarify scope",
      "description": "Boundaries / out-of-scope confirmation.",
      "agent_prepares": "scope-clarification-template.md"
    }
  }
}
```

`name` and `description` are required. `agent_prepares` and `rationale`
are optional. Missing file → empty directory + debug log (matches the
issue-types pattern).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.human_input import HumanInput, HumanInputDirectory
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_human_input_directory(source: str | Path) -> HumanInputDirectory:
    """Parse a human-inputs file (or JSON string) into a `HumanInputDirectory`.

    Returns an empty directory if `source` is a path that does not exist.
    Raises `ParseError` on malformed JSON or schema violations.
    """
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str)
        and "\n" not in source
        and not source.lstrip().startswith(("{", "["))
    ):
        path = Path(source)
        if not path.exists():
            logger.debug("Human-input directory not found at %s; returning empty.", path)
            return HumanInputDirectory(source_path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read human-input directory {path}: {exc}") from exc
        source_path = str(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Human-input directory{f' at {source_path}' if source_path else ''} "
            f"is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Human-input directory must be a JSON object at the top level "
            f"(got {type(data).__name__})."
        )

    entries_raw = data.get("human_inputs", {})
    if not isinstance(entries_raw, dict):
        raise ParseError(
            f"Human-input directory `human_inputs` must be an object "
            f"(got {type(entries_raw).__name__})."
        )

    directory = HumanInputDirectory(source_path=source_path)
    for human_input_id, entry in entries_raw.items():
        if not isinstance(human_input_id, str) or not human_input_id:
            raise ParseError(f"Human-input id must be a non-empty string (got {human_input_id!r}).")
        if not isinstance(entry, dict):
            raise ParseError(
                f"Human input {human_input_id!r}: entry must be an object "
                f"(got {type(entry).__name__})."
            )
        directory.entries[human_input_id] = _parse_entry(human_input_id, entry)

    return directory


def _parse_entry(human_input_id: str, entry: dict[str, Any]) -> HumanInput:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ParseError(
            f"Human input {human_input_id!r}: `name` is required and must be non-empty."
        )

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ParseError(
            f"Human input {human_input_id!r}: `description` is required and must be non-empty."
        )

    agent_prepares = entry.get("agent_prepares")
    if agent_prepares is not None and (
        not isinstance(agent_prepares, str) or not agent_prepares.strip()
    ):
        raise ParseError(
            f"Human input {human_input_id!r}: `agent_prepares` must be a non-empty string if present."
        )
    if isinstance(agent_prepares, str):
        agent_prepares = agent_prepares.strip()

    rationale = entry.get("rationale")
    if rationale is not None and (not isinstance(rationale, str) or not rationale.strip()):
        raise ParseError(
            f"Human input {human_input_id!r}: `rationale` must be a non-empty string if present."
        )
    if isinstance(rationale, str):
        rationale = rationale.strip()

    return HumanInput(
        human_input_id=human_input_id,
        name=name.strip(),
        description=description.strip(),
        agent_prepares=agent_prepares,
        rationale=rationale,
    )
