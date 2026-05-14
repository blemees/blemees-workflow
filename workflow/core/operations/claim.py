"""claim — agent takes responsibility for a resting work item.

Per `state-machine-principles.md` principle 3: an agent must claim before
acting. The claim transition is a state change resting → working; the
backend records the agent's role on the work item.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    work_item_id: str,
    role: str,
    transition_label: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.CLAIM,
        work_item_id=work_item_id,
        role=role,
        transition_label=transition_label,
        actor=actor or role,
    )
    return dispatch(controller, request)
