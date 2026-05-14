"""resolve — human provides input for a recognized HITL moment.

Per `hitl-principles.md` principles 5 and 11. Clears the awaiting-input
marker; the agent reads the response and decides the next action.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    work_item_id: str,
    response_from: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.RESOLVE,
        work_item_id=work_item_id,
        body_path=response_from,
        actor=actor,
    )
    return dispatch(controller, request)
