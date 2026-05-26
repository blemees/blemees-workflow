"""Input-topic directory parser — reads `input-topics.json`.

Shape:

```json
{
  "topics": {
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

from workflow.core.model.input_topic import InputTopic, InputTopicDirectory
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_input_topic_directory(source: str | Path) -> InputTopicDirectory:
    """Parse an input-topics file (or JSON string) into an `InputTopicDirectory`.

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
            logger.debug("Input topics directory not found at %s; returning empty.", path)
            return InputTopicDirectory(source_path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read input topics directory {path}: {exc}") from exc
        source_path = str(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Input topics directory{f' at {source_path}' if source_path else ''} "
            f"is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Input topics directory must be a JSON object at the top level "
            f"(got {type(data).__name__})."
        )

    topics_raw = data.get("topics", {})
    if not isinstance(topics_raw, dict):
        raise ParseError(
            f"Input topics directory `topics` must be an object "
            f"(got {type(topics_raw).__name__})."
        )

    directory = InputTopicDirectory(source_path=source_path)
    for topic_id, entry in topics_raw.items():
        if not isinstance(topic_id, str) or not topic_id:
            raise ParseError(
                f"Input topic id must be a non-empty string (got {topic_id!r})."
            )
        if not isinstance(entry, dict):
            raise ParseError(
                f"Input topic {topic_id!r}: entry must be an object "
                f"(got {type(entry).__name__})."
            )
        directory.topics[topic_id] = _parse_topic(topic_id, entry)

    return directory


def _parse_topic(topic_id: str, entry: dict[str, Any]) -> InputTopic:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ParseError(
            f"Input topic {topic_id!r}: `name` is required and must be non-empty."
        )

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ParseError(
            f"Input topic {topic_id!r}: `description` is required and must be non-empty."
        )

    agent_prepares = entry.get("agent_prepares")
    if agent_prepares is not None and (
        not isinstance(agent_prepares, str) or not agent_prepares.strip()
    ):
        raise ParseError(
            f"Input topic {topic_id!r}: `agent_prepares` must be a non-empty string if present."
        )
    if isinstance(agent_prepares, str):
        agent_prepares = agent_prepares.strip()

    rationale = entry.get("rationale")
    if rationale is not None and (
        not isinstance(rationale, str) or not rationale.strip()
    ):
        raise ParseError(
            f"Input topic {topic_id!r}: `rationale` must be a non-empty string if present."
        )
    if isinstance(rationale, str):
        rationale = rationale.strip()

    return InputTopic(
        topic_id=topic_id,
        name=name.strip(),
        description=description.strip(),
        agent_prepares=agent_prepares,
        rationale=rationale,
    )
