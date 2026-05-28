"""reject — human refuses a catalogued HumanGate packet; agent iterates.

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
    issue_id: str,
    gate: str,
    body: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.REJECT_BLOCKED,
        issue_id=issue_id,
        gate=gate,
        body_text=body,
        actor=actor,
    )
    return dispatch(controller, request)
