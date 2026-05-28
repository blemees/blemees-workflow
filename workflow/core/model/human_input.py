"""Human-input model — the catalog of inputs agents can request from a human.

Working states reference inputs by id via `State.human_inputs`. The shared
`human-inputs.json` defines each entry's display name, description, and
optional packet template. Cataloguing matches the pattern used for
issue types and roles.

Routing target is the **human operator** — these are escalations from
the agent loop, not requests directed at a specific framework role.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HumanInput:
    """A single entry from human-inputs.json.

    `agent_prepares` (optional) names a packet template path the agent
    fills before invoking `request-input` with this id — mirrors the
    human-gate catalog's same-named field.
    """

    human_input_id: str
    name: str
    description: str
    agent_prepares: str | None = None
    rationale: str | None = None


@dataclass
class HumanInputDirectory:
    """The collection of human-input entries parsed from human-inputs.json."""

    entries: dict[str, HumanInput] = field(default_factory=dict)
    source_path: str | None = None

    def get(self, human_input_id: str) -> HumanInput:
        if human_input_id not in self.entries:
            raise KeyError(f"Human input {human_input_id!r} not found in directory")
        return self.entries[human_input_id]

    def has(self, human_input_id: str) -> bool:
        return human_input_id in self.entries
