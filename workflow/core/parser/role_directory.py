"""Role directory parser — reads `roles.json` into a `RoleDirectory`.

The role directory is structured data, not markdown. JSON's strict syntax
makes truncation surface as a parse error rather than as silent corruption.

## Expected JSON shape

```json
{
  "roles": {
    "product-manager": {
      "name": "Product Manager",
      "responsibility": "Owns refinement; turns raw issues into ready tickets",
      "does_not": ["decide architecture", "implement tickets"]
    },
    "developer": {
      "name": "Developer",
      "responsibility": "Implements changes end-to-end"
    }
  }
}
```

`name` and `responsibility` are required. `does_not` is optional. The
processes a role participates in are derived at doc-generation time from
the state machines (any working state whose `roles` includes the role).

If the file does not exist, returns an empty `RoleDirectory` with a debug log.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflow.core.model.role import Role, RoleDirectory
from workflow.errors import ParseError

logger = logging.getLogger(__name__)


def parse_role_directory(source: str | Path) -> RoleDirectory:
    """Parse a roles file or JSON string into a `RoleDirectory`.

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
            logger.debug("Role directory not found at %s; returning empty directory.", path)
            return RoleDirectory(source_path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read role directory {path}: {exc}") from exc
        source_path = str(path)
    else:
        text = str(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Role directory{f' at {source_path}' if source_path else ''} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Role directory must be a JSON object at the top level (got {type(data).__name__})."
        )

    roles_raw = data.get("roles", {})
    if not isinstance(roles_raw, dict):
        raise ParseError(
            f"Role directory `roles` must be an object (got {type(roles_raw).__name__})."
        )

    directory = RoleDirectory(source_path=source_path)
    for role_id, entry in roles_raw.items():
        if not isinstance(role_id, str) or not role_id:
            raise ParseError(f"Role id must be a non-empty string (got {role_id!r}).")
        if not isinstance(entry, dict):
            raise ParseError(
                f"Role {role_id!r}: entry must be an object (got {type(entry).__name__})."
            )
        directory.roles[role_id] = _parse_role(role_id, entry)

    return directory


def _parse_role(role_id: str, entry: dict[str, Any]) -> Role:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ParseError(f"Role {role_id!r}: `name` is required and must be a non-empty string.")

    responsibility = entry.get("responsibility")
    if not isinstance(responsibility, str) or not responsibility.strip():
        raise ParseError(
            f"Role {role_id!r}: `responsibility` is required and must be a non-empty string."
        )

    # The `processes` and `wakes_on` fields were removed — the framework
    # derives that information from the state machines at doc-gen time.
    # Reject if present so authors don't carry dead data forward.
    for legacy in ("processes", "wakes_on"):
        if legacy in entry:
            raise ParseError(
                f"Role {role_id!r}: `{legacy}` was removed. The framework "
                f"derives this from the state machines (roles on working "
                f"states). If there's important context, fold it into "
                f"`responsibility`."
            )

    does_not = _parse_string_list(entry.get("does_not"), role_id, "does_not")

    return Role(
        role_id=role_id,
        name=name.strip(),
        responsibility=responsibility.strip(),
        does_not=does_not,
    )


def _parse_string_list(value: Any, role_id: str, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ParseError(
            f"Role {role_id!r}: `{field}` must be a list of strings (got {type(value).__name__})."
        )
    items: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ParseError(
                f"Role {role_id!r}: `{field}[{i}]` must be a string (got {type(item).__name__})."
            )
        cleaned = item.strip()
        if cleaned:
            items.append(cleaned)
    return items
