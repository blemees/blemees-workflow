"""audit — human claims post-action audit.

Singleton claim operation per `hitl-principles.md` principle 6. Mutually
exclusive with `review` and `advise`.
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
        operation=Operation.REVIEW_AUDIT,
        issue_id=issue_id,
        actor=actor,
    )
    return dispatch(controller, request)
