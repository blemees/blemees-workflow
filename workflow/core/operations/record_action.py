"""record-action — agent acts and queues for retroactive review (audit-level).

Per `hitl-principles.md` principle 5 (audit subgroup). Atomic with the
transition: the state advances and the audit-pending marker is applied in
one backend transaction.

Refuses to operate if the destination is irreversible (principle 4 — audit
requires a reversible destination).
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
    transition_label: str | None = None,
    destination: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.RECORD_ACTION,
        issue_id=issue_id,
        gate=gate,
        transition_label=transition_label,
        destination=destination,
        actor=actor,
    )
    return dispatch(controller, request)
