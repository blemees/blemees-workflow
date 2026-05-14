"""check — human confirms an audit-level action post-hoc.

Per `hitl-principles.md` principle 11. Clears the audit queue for the work
item; no remediation triggered.
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
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.CHECK,
        work_item_id=work_item_id,
        gate=gate,
        actor=actor,
    )
    return dispatch(controller, request)
