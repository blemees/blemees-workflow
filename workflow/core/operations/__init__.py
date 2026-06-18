"""Eleven framework operations + the three workflow helpers.

Each submodule exposes a `run(request, controller) -> OperationResult`
function that constructs the planner request (if needed) and dispatches to
the controller. The CLI imports each module's `run` and wires it to the
corresponding click sub-command.
"""

from workflow.core.operations import (
    advance_issue,
    approve_audit,
    approve_blocked,
    await_signal,
    claim_issue,
    collect_into,
    record_action,
    reject_audit,
    reject_blocked,
    release_issue,
    request_input,
    respond_request,
    review_audit,
    review_blocked,
    review_request,
    spawn_issue,
)

__all__ = [
    "advance_issue",
    "approve_audit",
    "approve_blocked",
    "await_signal",
    "claim_issue",
    "collect_into",
    "record_action",
    "reject_audit",
    "reject_blocked",
    "release_issue",
    "request_input",
    "respond_request",
    "review_audit",
    "review_blocked",
    "review_request",
    "spawn_issue",
]
