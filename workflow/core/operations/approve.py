"""approve — human authorizes a catalogued HCP; transition fires atomically.

Per `hitl-principles.md` principle 5. For binary HCPs, the destination is
implicit (the gate's single destination). For verdict-style HCPs, the human
names the destination.
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
    destination: str | None = None,
    comment_from: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.APPROVE,
        work_item_id=work_item_id,
        gate=gate,
        destination=destination,
        body_path=comment_from,
        actor=actor,
    )
    return dispatch(controller, request)
