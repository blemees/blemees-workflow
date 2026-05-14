"""release — agent gives up the claim on a work item.

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
    work_item_id: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.RELEASE,
        work_item_id=work_item_id,
        actor=actor,
    )
    return dispatch(controller, request)
