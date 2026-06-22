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

# NOTE: concrete backends (e.g. GitHubBackend) are intentionally NOT imported
# here. Importing this package — e.g. `from workflow.backends import
# github_labels` in core code — must stay lightweight and not pull in a backend
# implementation and its dependencies. Import concrete backends from their
# submodule: `from workflow.backends.github import GitHubBackend`.

__all__ = [
    "MarkerChange",
    "TrackerBackend",
    "IssueFilters",
    "IssueState",
]
