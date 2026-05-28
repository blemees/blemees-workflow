"""claim — agent takes responsibility for a resting issue.

Per `state-machine-principles.md` principle 3: an agent must claim before
acting. The claim transition is a state change resting → working; the
backend records the agent's role on the issue.

When the current resting state has exactly one outgoing CLAIM transition,
`destination` is optional and the planner picks it. When multiple CLAIM
transitions are possible (e.g., the state forks into different working
roles), `destination` is required to disambiguate.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    issue_id: str,
    role: str,
    destination: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.CLAIM_ISSUE,
        issue_id=issue_id,
        role=role,
        destination=destination,
        actor=actor or role,
    )
    return dispatch(controller, request)
