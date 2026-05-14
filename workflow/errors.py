"""Exception hierarchy for the workflow tool.

All errors inherit from `WorkflowError`. Callers catch specific subclasses to
distinguish operation-level validation failures from backend mishaps from
configuration resolution problems.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for every workflow-tool error."""


class OperationError(WorkflowError):
    """Raised when an operation's preconditions are not met.

    Planner-level validation failures: wrong source state, missing claim,
    unknown gate, conflicting markers. The operation is rejected; no backend
    mutation occurs.
    """


class BackendError(WorkflowError):
    """Raised when a backend invocation fails.

    Examples: `gh` CLI returns a non-zero exit code, the API rejects the
    label change, the work item is unreadable. Backend-specific causes are
    wrapped in this exception.
    """


class ConfigError(WorkflowError):
    """Raised when artifact resolution or configuration loading fails.

    Examples: a lifecycle file referenced via CLI doesn't exist; the
    workflow repo root cannot be discovered; a trust grant directory is
    malformed at the directory level.
    """


class ParseError(WorkflowError):
    """Raised when a parser cannot make sense of its input.

    Distinct from `ConfigError`: the file was found but is malformed.
    """
