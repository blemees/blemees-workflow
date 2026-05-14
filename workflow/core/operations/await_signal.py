"""await-signal — catalogued, block-level. Agent pauses for human signal.

Per `hitl-principles.md` principles 5 + 8. The agent applies the queue
marker for a catalogued gate; the human responds via `approve` or `reject`.
The agent's claim stays put (principle 7).
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
    packet_from: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.AWAIT_SIGNAL,
        work_item_id=work_item_id,
        gate=gate,
        body_path=packet_from,
        actor=actor,
    )
    return dispatch(controller, request)
