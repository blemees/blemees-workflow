"""create-issue — open a new issue or pull request (optionally a collector).

A creating operation: the CLI resolves the process, issue-type encoding (native
vs label, via the capability cache), and contributor candidate set (the impure
work) and passes the result through `extras`; the pure `_plan_creation` assembles
the `CreationSpec`; the controller's create path opens the issue/PR and stamps
any gathered contributors `collected-by/` with the new id.
"""

from __future__ import annotations

from collections.abc import Sequence

from workflow.core.controller import Controller, OperationResult
from workflow.core.operations._base import dispatch
from workflow.core.planner import Operation, OperationRequest


def run(
    controller: Controller,
    *,
    title: str,
    state: str,
    body: str = "",
    entity: str = "issue",
    issue_type: str | None = None,
    github_issue_type: str | None = None,
    extra_labels: Sequence[str] = (),
    head: str | None = None,
    base: str | None = None,
    draft: bool = False,
    collect_contributors: Sequence[str] = (),
    actor: str | None = None,
) -> OperationResult:
    request = OperationRequest(
        operation=Operation.CREATE_ISSUE,
        issue_id="",  # no existing issue — create opens a new one
        actor=actor,
        extras={
            "title": title,
            "body": body,
            "state": state,
            "entity": entity,
            "issue_type": issue_type,
            "github_issue_type": github_issue_type,
            "extra_labels": tuple(extra_labels),
            "head": head,
            "base": base,
            "draft": draft,
            "collect_contributors": tuple(collect_contributors),
        },
    )
    return dispatch(controller, request)
