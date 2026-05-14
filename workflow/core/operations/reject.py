"""reject — human refuses a catalogued HCP packet; agent iterates.

Per `hitl-principles.md` principles 5, 7, and 11. The state never changes;
the agent's claim is retained. The agent reads the feedback comment and
re-prepares.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    work_item_id: str,
    gate: str,
    feedback_from: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.REJECT,
        work_item_id=work_item_id,
        gate=gate,
        body_path=feedback_from,
        actor=actor,
    )
    return dispatch(controller, request)
