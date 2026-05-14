"""Backend implementations. Each backend translates the framework's abstract
operations and markers into concrete actions on a specific tracker.

Implementing a new backend means writing a class that satisfies the
`WorkflowBackend` protocol in `base.py`. Nothing in `workflow.core` changes.
"""

from workflow.backends.base import (
    MarkerChange,
    WorkflowBackend,
    WorkItemFilters,
    WorkItemState,
)
from workflow.backends.github import GitHubBackend

__all__ = [
    "MarkerChange",
    "WorkflowBackend",
    "WorkItemFilters",
    "WorkItemState",
    "GitHubBackend",
]
