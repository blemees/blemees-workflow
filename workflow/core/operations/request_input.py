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
    issue_id: str,
    body: str,
    topic: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.REQUEST_INPUT,
        issue_id=issue_id,
        body_text=body,
        topic=topic,
        actor=actor,
    )
    return dispatch(controller, request)
