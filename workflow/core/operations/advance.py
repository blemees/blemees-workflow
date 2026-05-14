"""advance — move a work item to a new state via a known transition.

The user-facing entry point for state changes. The planner consults the HCP
catalog and team trust grants and dispatches internally:

- Ungated transition → straightforward state change.
- Block-gated → applies awaiting marker, holds the agent's claim, no state
  change. Requires `body_path` pointing at a packet matching the catalog's
  `agent_prepares` template.
- Audit-gated → state change atomically with the audit-pending marker.
  `body_path`, if provided, becomes the audit-comment body.

The agent never has to know which path applies; it just calls `advance`.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    work_item_id: str,
    destination: str,
    body_path: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.ADVANCE,
        work_item_id=work_item_id,
        destination=destination,
        body_path=body_path,
        actor=actor,
    )
    return dispatch(controller, request)
