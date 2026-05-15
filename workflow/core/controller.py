"""Controller — end-to-end orchestration of an operation.

Reads the current issue state via the backend, calls `plan_operation`,
applies the marker change atomically, and posts the packet body (if any) as
a follow-up comment.

The `dry_run` mode runs the planner but skips backend mutation; the result
describes the planned change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from workflow.backends.base import IssueState, TrackerBackend
from workflow.core.model.hcp import HCPCatalog
from workflow.core.model.state_machine import StateMachine
from workflow.core.model.trust_grant import TrustGrant
from workflow.core.planner import (
    Operation,
    OperationPlan,
    OperationRequest,
    plan_operation,
)
from workflow.core.validator import (
    ValidationFinding,
    validate_issue_markers,
)
from workflow.errors import OperationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationResult:
    operation: Operation
    issue_id: str
    dry_run: bool
    plan: OperationPlan
    pre_state: IssueState
    post_state: IssueState | None = None
    findings: list[ValidationFinding] = field(default_factory=list)


@dataclass
class Controller:
    backend: TrackerBackend
    state_machine: StateMachine
    catalog: HCPCatalog | None = None
    grants: dict[str, TrustGrant] = field(default_factory=dict)
    dry_run: bool = False

    def execute(self, request: OperationRequest) -> OperationResult:
        try:
            pre_state = self.backend.read_issue(request.issue_id)
        except Exception as exc:
            if self.dry_run:
                # Dry-run tolerance: the backend may be unavailable (no `gh`,
                # no network, etc.). Fall back to an empty state and log so
                # the user knows the plan is being computed against an
                # idealized "fresh" issue.
                logger.warning(
                    "[dry-run] backend read failed (%s); proceeding with empty state.",
                    exc,
                )
                pre_state = IssueState(
                    issue_id=str(request.issue_id),
                    state=None,
                    agent_claim=None,
                )
            else:
                raise
        # Runtime claim discipline (principle 6).
        findings = validate_issue_markers(pre_state)
        for finding in findings:
            if finding.severity.value == "error":
                raise OperationError(str(finding))

        plan = plan_operation(
            request=request,
            state=pre_state,
            state_machine=self.state_machine,
            catalog=self.catalog,
            grants=self.grants,
        )

        if self.dry_run:
            logger.info(
                "[dry-run] %s on %s: %s",
                request.operation.value,
                request.issue_id,
                plan.change,
            )
            return OperationResult(
                operation=request.operation,
                issue_id=request.issue_id,
                dry_run=True,
                plan=plan,
                pre_state=pre_state,
                post_state=None,
                findings=findings,
            )

        # Apply the marker change atomically and post the audit comment.
        self.backend.apply_marker_change(
            request.issue_id,
            plan.change,
            audit_comment=plan.audit_comment,
        )
        # Post the optional packet/question body as a follow-up comment.
        if plan.packet_body:
            self.backend.post_comment(request.issue_id, plan.packet_body)

        post_state = self.backend.read_issue(request.issue_id)
        return OperationResult(
            operation=request.operation,
            issue_id=request.issue_id,
            dry_run=False,
            plan=plan,
            pre_state=pre_state,
            post_state=post_state,
            findings=findings,
        )
