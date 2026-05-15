"""Backend implementations. Each backend translates the framework's abstract
operations and markers into concrete actions on a specific tracker.

Implementing a new backend means writing a class that satisfies the
`TrackerBackend` protocol in `base.py`. Nothing in `workflow.core` changes.
"""

from workflow.backends.base import (
    IssueFilters,
    IssueState,
    MarkerChange,
    TrackerBackend,
)
from workflow.backends.github import GitHubBackend

__all__ = [
    "MarkerChange",
    "TrackerBackend",
    "IssueFilters",
    "IssueState",
    "GitHubBackend",
]
