"""review — human claims pre-action review of an awaiting gate.

Singleton claim operation (per `hitl-principles.md` principle 6). Mutually
exclusive with `audit` and `advise` across the work item.
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
        operation=Operation.REVIEW,
        work_item_id=work_item_id,
        actor=actor,
    )
    return dispatch(controller, request)
