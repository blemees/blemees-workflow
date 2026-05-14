"""Eleven framework operations + the three lifecycle helpers.

Each submodule exposes a `run(request, controller) -> OperationResult`
function that constructs the planner request (if needed) and dispatches to
the controller. The CLI imports each module's `run` and wires it to the
corresponding click sub-command.
"""

from workflow.core.operations import (
    advance,
    advise,
    approve,
    audit,
    await_signal,
    check,
    claim,
    record_action,
    reject,
    release,
    request_input,
    resolve,
    review,
    revoke,
)

__all__ = [
    "advance",
    "approve",
    "audit",
    "advise",
    "await_signal",
    "check",
    "claim",
    "record_action",
    "reject",
    "release",
    "request_input",
    "resolve",
    "review",
    "revoke",
]
