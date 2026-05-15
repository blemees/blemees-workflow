"""Issue-type model — the directory of types the framework refers to via type ids.

Processes declare which types they accept on their state-machine JSON
(`"issue_types": ["bug", "feature"]`). Each type's definition lives in a
shared `issue-types.json` alongside `roles.json`. Same authoring pattern.

Mapping to a specific backend's type system (GitHub Issue Types, Jira issue
types, etc.) lives on the per-type entry as an optional field — the
framework's id is canonical, the backend mapping is hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IssueType:
    """A single type entry from issue-types.json.

    `github_issue_type` (optional) names the corresponding GitHub Issue
    Type (now a first-class field on GitHub issues). `github_issue_type_color`
    is an optional color hint used when `setup-github` provisions the type
    at the org (GitHub accepts gray/blue/green/yellow/orange/red/pink/purple).
    Other backends may add similar fields; the framework's `type_id` is
    the canonical name.
    """

    type_id: str
    name: str
    description: str
    github_issue_type: str | None = None
    github_issue_type_color: str | None = None


@dataclass
class IssueTypeDirectory:
    """The collection of issue types parsed from issue-types.json."""

    types: dict[str, IssueType] = field(default_factory=dict)
    source_path: str | None = None

    def get(self, type_id: str) -> IssueType:
        if type_id not in self.types:
            raise KeyError(f"Issue type {type_id!r} not found in directory")
        return self.types[type_id]

    def has(self, type_id: str) -> bool:
        return type_id in self.types
