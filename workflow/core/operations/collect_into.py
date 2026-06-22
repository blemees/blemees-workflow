"""collect-into — mark a contributor as gathered into a collector.

A fan-in mutation: the CLI resolves the collector's `collects` config (the
impure lookup) and passes it via `extras`; the pure `_plan_collect` validates
the contributor's eligibility and produces the `set_collected_by` marker change;
the controller's in-place path applies it to the contributor. The collector is
never touched — the relationship lives solely on the contributor's
`collected-by/<collector>` label (ADR-0003).
"""

from __future__ import annotations

from collections.abc import Sequence

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    issue_id: str,
    collector_id: str,
    from_states: Sequence[str] = (),
    issue_types: Sequence[str] | None = None,
    force: bool = False,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.COLLECT_INTO,
        issue_id=issue_id,
        actor=actor,
        extras={
            "collector_id": collector_id,
            "from_states": tuple(from_states),
            "issue_types": tuple(issue_types or ()),
            "force": force,
        },
    )
    return dispatch(controller, request)
