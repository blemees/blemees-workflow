"""spawn-issue — open a child issue/PR from the parent state's `spawns` config.

A creating operation: the CLI resolves the spawn rule and target process (the
impure cross-process lookup) and passes the result through `extras`; the pure
`_plan_spawn` assembles the `CreationSpec`; the controller's create path opens
the child and runs the cascade. The child carries only `child-of/<parent>`
(ADR-0003) — the parent is left untouched.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.model.state_machine import Spawn
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    issue_id: str,
    spawn: Spawn,
    parent_process: str,
    entity: str = "issue",
    github_issue_type: str | None = None,
    title: str | None = None,
    body: str | None = None,
    head: str | None = None,
    base: str | None = None,
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.SPAWN_ISSUE,
        issue_id=issue_id,
        body_text=body,
        actor=actor,
        extras={
            "spawn": spawn,
            "parent_process": parent_process,
            "entity": entity,
            "github_issue_type": github_issue_type,
            "title": title,
            "head": head,
            "base": base,
        },
    )
    return dispatch(controller, request)
