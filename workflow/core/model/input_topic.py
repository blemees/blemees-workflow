"""Input-topic model — the catalog of topics agents can request input on.

Working states reference topics by id via `State.input_topics`. The shared
`input-topics.json` defines each topic's display name, description, and
optional packet template. Cataloguing matches the pattern used for
issue types and roles.

Routing target is the **human operator** — these are escalations from
the agent loop, not requests directed at a specific framework role.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InputTopic:
    """A single entry from input-topics.json.

    `agent_prepares` (optional) names a packet template path the agent
    fills before invoking `request-input` with this topic — mirrors the
    HCP catalog's same-named field.
    """

    topic_id: str
    name: str
    description: str
    agent_prepares: str | None = None
    rationale: str | None = None


@dataclass
class InputTopicDirectory:
    """The collection of input topics parsed from input-topics.json."""

    topics: dict[str, InputTopic] = field(default_factory=dict)
    source_path: str | None = None

    def get(self, topic_id: str) -> InputTopic:
        if topic_id not in self.topics:
            raise KeyError(f"Input topic {topic_id!r} not found in directory")
        return self.topics[topic_id]

    def has(self, topic_id: str) -> bool:
        return topic_id in self.topics
