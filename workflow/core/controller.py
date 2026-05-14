"""Controller — end-to-end orchestration of an operation.

Reads the current work-item state via the backend, calls `plan_operation`,
applies the marker change atomically, and posts the packet body (if any) as
a follow-up comment.

The `dry_run` mode runs the planner but skips backend mutation; the result
describes the planned change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from workflow.backends.base import WorkflowBackend, WorkItemState
from workflow.core.model.hcp import HCPCatalog
from workflow.core.model.lifecycle import Lifecycle
from workflow.core.model.trust_grant import TrustGrant
from workflow.core.planner import (
    Operation,
    OperationPlan,
    OperationRequest,
    plan_operation,
)
from workflow.core.validator import (
    ValidationFinding,
    validate_work_item_markers,
)
from workflow.errors import OperationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationResult:
    operation: Operation
    work_item_id: str
    dry_run: bool
    plan: OperationPlan
    pre_state: WorkItemState
    post_state: WorkItemState | None = None
    findings: list[ValidationFinding] = field(default_factory=list)


@dataclass
class Controller:
    backend: WorkflowBackend
    lifecycle: Lifecycle
    catalog: HCPCatalog | None = None
    grants: dict[str, TrustGrant] = field(default_factory=dict)
    dry_run: bool = False

    def execute(self, request: OperationRequest) -> OperationResult:
        try:
            pre_state = self.backend.read_work_item(request.work_item_id)
        except Exception as exc:
            if self.dry_run:
                # Dry-run tolerance: the backend may be unavailable (no `gh`,
                # no network, etc.). Fall back to an empty state and log so
                # the user knows the plan is being computed against an
                # idealized "fresh" work item.
                logger.warning(
                    "[dry-run] backend read failed (%s); proceeding with empty state.",
                    exc,
                )
                pre_state = WorkItemState(
                    work_item_id=str(request.work_item_id),
                    state=None,
                    agent_claim=None,
                )
            else:
                raise
        # Runtime claim discipline (principle 6).
        findings = validate_work_item_markers(pre_state)
        for finding in findings:
            if finding.severity.value == "error":
                raise OperationError(str(finding))

        plan = plan_operation(
            request=request,
            state=pre_state,
            lifecycle=self.lifecycle,
            catalog=self.catalog,
            grants=self.grants,
        )

        if self.dry_run:
            logger.info(
                "[dry-run] %s on %s: %s",
                request.operation.value,
                request.work_item_id,
                plan.change,
            )
            return OperationResult(
                operation=request.operation,
                work_item_id=request.work_item_id,
                dry_run=True,
                plan=plan,
                pre_state=pre_state,
                post_state=None,
                findings=findings,
            )

        # Apply the marker change atomically and post the audit comment.
        self.backend.apply_marker_change(
            request.work_item_id,
            plan.change,
            audit_comment=plan.audit_comment,
        )
        # Post the optional packet/question body as a follow-up comment.
        if plan.packet_body:
            self.backend.post_comment(request.work_item_id, plan.packet_body)

        post_state = self.backend.read_work_item(request.work_item_id)
        return OperationResult(
            operation=request.operation,
            work_item_id=request.work_item_id,
            dry_run=False,
            plan=plan,
            pre_state=pre_state,
            post_state=post_state,
            findings=findings,
        )
