"""request-input — agent recognizes an unanticipated HITL moment.

Per `hitl-principles.md` principle 10. State-orthogonal: the agent stays in
its current state with its claim intact (principle 7). The agent supplies
a structured question per principle 11.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    work_item_id: str,
    question_from: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.REQUEST_INPUT,
        work_item_id=work_item_id,
        body_path=question_from,
        actor=actor,
    )
    return dispatch(controller, request)
