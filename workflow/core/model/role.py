"""Role model — the role directory the framework refers to via `{placeholder}` form.

Roles are actor-agnostic (per hitl-principles.md intro). The framework's
role identifiers (e.g., `pm`, `developer`) map to backend handles at runtime via
a team-specific mapping file outside this tool's scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Role:
    """A single role entry from roles.json.

    `placeholder` is the curly-brace form skill prose uses, e.g., `{pm}`.
    `role_id` is the bare identifier (`pm`).
    """

    role_id: str
    name: str
    responsibility: str  # One-line description
    processes: list[str] = field(default_factory=list)
    wakes_on: list[str] = field(default_factory=list)
    does_not: list[str] = field(default_factory=list)

    @property
    def placeholder(self) -> str:
        return f"{{{self.role_id}}}"


@dataclass
class RoleDirectory:
    """The collection of roles parsed from roles.json."""

    roles: dict[str, Role] = field(default_factory=dict)  # role_id -> Role
    source_path: str | None = None

    def get(self, role_id: str) -> Role:
        # Accept both `pm` and `{pm}`; strip braces if present.
        normalized = role_id.strip("{}")
        if normalized not in self.roles:
            raise KeyError(f"Role {role_id!r} not found in role directory")
        return self.roles[normalized]

    def has(self, role_id: str) -> bool:
        return role_id.strip("{}") in self.roles
