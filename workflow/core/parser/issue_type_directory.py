"""Issue-type directory parser — reads `issue-types.json` into an `IssueTypeDirectory`.

Shape:

```json
{
  "types": {
    "bug": {
      "name": "Bug",
      "description": "Defect in shipped behavior",
      "github_issue_type": "Bug"
    },
    "feature": {
      "name": "Feature",
      "description": "New user-facing capability"
    }
  }
}
```

`name` and `description` are required for every type. `github_issue_type`
is optional — when set, the GitHub backend can apply it to created issues
via `gh issue create --type`. Other backends may add similar optional
fields without disturbing the shape.

Missing file → empty directory + debug log (matches the pattern for
roles.json and HCP catalogs).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.issue_type import IssueType, IssueTypeDirectory
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_issue_type_directory(source: str | Path) -> IssueTypeDirectory:
    """Parse an issue-types file or JSON string into an `IssueTypeDirectory`.

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
            logger.debug("Issue type directory not found at %s; returning empty.", path)
            return IssueTypeDirectory(source_path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read issue type directory {path}: {exc}") from exc
        source_path = str(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Issue type directory{f' at {source_path}' if source_path else ''} "
            f"is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Issue type directory must be a JSON object at the top level "
            f"(got {type(data).__name__})."
        )

    types_raw = data.get("types", {})
    if not isinstance(types_raw, dict):
        raise ParseError(
            f"Issue type directory `types` must be an object "
            f"(got {type(types_raw).__name__})."
        )

    directory = IssueTypeDirectory(source_path=source_path)
    for type_id, entry in types_raw.items():
        if not isinstance(type_id, str) or not type_id:
            raise ParseError(f"Issue type id must be a non-empty string (got {type_id!r}).")
        if not isinstance(entry, dict):
            raise ParseError(
                f"Issue type {type_id!r}: entry must be an object "
                f"(got {type(entry).__name__})."
            )
        directory.types[type_id] = _parse_type(type_id, entry)

    return directory


def _parse_type(type_id: str, entry: dict[str, Any]) -> IssueType:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ParseError(
            f"Issue type {type_id!r}: `name` is required and must be a non-empty string."
        )

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ParseError(
            f"Issue type {type_id!r}: `description` is required and must be a non-empty string."
        )

    github_issue_type = entry.get("github_issue_type")
    if github_issue_type is not None:
        if not isinstance(github_issue_type, str) or not github_issue_type.strip():
            raise ParseError(
                f"Issue type {type_id!r}: `github_issue_type` must be a non-empty string if present."
            )
        github_issue_type = github_issue_type.strip()

    github_issue_type_color = entry.get("github_issue_type_color")
    if github_issue_type_color is not None:
        if not isinstance(github_issue_type_color, str) or not github_issue_type_color.strip():
            raise ParseError(
                f"Issue type {type_id!r}: `github_issue_type_color` must be a non-empty string if present."
            )
        github_issue_type_color = github_issue_type_color.strip()

    return IssueType(
        type_id=type_id,
        name=name.strip(),
        description=description.strip(),
        github_issue_type=github_issue_type,
        github_issue_type_color=github_issue_type_color,
    )
