"""release — agent gives up the claim on an issue.

Symmetric to `claim`. Used when the agent voluntarily steps off the item;
the resting state is preserved (release is not an advance).
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    issue_id: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.RELEASE,
        issue_id=issue_id,
        actor=actor,
    )
    return dispatch(controller, request)
