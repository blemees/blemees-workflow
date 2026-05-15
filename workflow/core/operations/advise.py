"""advise — human claims the recognition response role.

Singleton claim per `hitl-principles.md` principle 6. Distinct from `review`
and `audit` because the human is providing input rather than reviewing
prepared work.
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
        operation=Operation.ADVISE,
        issue_id=issue_id,
        actor=actor,
    )
    return dispatch(controller, request)
