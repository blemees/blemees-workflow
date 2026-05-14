"""revoke — human declares an audit-level action wrong; remediation fires.

Per `hitl-principles.md` principle 11. The `on_revoke` procedure named in
the catalog row determines the remediation; this operation surfaces the
declaration and clears the audit-pending marker.
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
    concern_from: str,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.REVOKE,
        work_item_id=work_item_id,
        gate=gate,
        body_path=concern_from,
        actor=actor,
    )
    return dispatch(controller, request)
