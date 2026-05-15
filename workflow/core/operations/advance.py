"""advance — move an issue to a new state via a known transition.

The user-facing entry point for state changes. The planner consults the HCP
catalog and team trust grants and dispatches internally:

- Ungated transition → straightforward state change.
- Block-gated → applies awaiting marker, holds the agent's claim, no state
  change. Requires `body_text` pointing at a packet matching the catalog's
  `agent_prepares` template.
- Audit-gated → state change atomically with the audit-pending marker.
  `body_text`, if provided, becomes the audit-comment body.

The agent never has to know which path applies; it just calls `advance`.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    issue_id: str,
    destination: str,
    body_text: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.ADVANCE,
        issue_id=issue_id,
        destination=destination,
        body_text=body_text,
        actor=actor,
    )
    return dispatch(controller, request)
