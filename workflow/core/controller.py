"""Controller — end-to-end orchestration of an operation.

Reads the current issue state via the backend, calls `plan_operation`,
applies the marker change atomically, and posts the packet body (if any) as
a follow-up comment.

The `dry_run` mode runs the planner but skips backend mutation; the result
describes the planned change.

After a successful state change the controller invokes the cascade
machinery (`workflow.core.cascade.cascade_after_state_change`) to walk
cross-process `advance_on` chains: child closing states trigger parent
advances, collector advances propagate to contributors, etc. The
cascade requires a `registry` (the full `Workflow` registry) so it
can look up sibling-process spawn / collect definitions; controllers
constructed without one still execute the primary operation but skip
the cascade pass with a debug log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from workflow.backends.base import IssueState, TrackerBackend
from workflow.core.cascade import CascadeApplication, cascade_after_state_change
from workflow.core.model.human_gate import HumanGateCatalog
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

if TYPE_CHECKING:
    from workflow.config import Workflow

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
    # Cross-process cascades fired by this operation's state change.
    # Empty for dry-runs and for operations whose change didn't trigger
    # any sibling advance_on / collects rule.
    cascade_applications: list[CascadeApplication] = field(default_factory=list)


@dataclass
class Controller:
    backend: TrackerBackend
    state_machine: StateMachine
    catalog: HumanGateCatalog | None = None
    grants: dict[str, TrustGrant] = field(default_factory=dict)
    dry_run: bool = False
    # Optional full-registry handle. When set, the controller invokes
    # cascade-advance after each successful state change so cross-process
    # `advance_on` chains propagate. Without it, the primary operation
    # still runs but cascades are skipped (the user sees a debug log).
    registry: "Workflow | None" = None

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

        # Cascade pass — propagate cross-process advance_on chains. Only
        # runs when the registry is available (the runtime needs it to
        # look up sibling-process spawn / collect definitions).
        cascade_applications: list[CascadeApplication] = []
        if self.registry is not None:
            cascade_applications = cascade_after_state_change(
                self.registry,
                self.backend,
                request.issue_id,
                post_state,
                actor=request.actor,
            )
        else:
            logger.debug(
                "controller: no registry attached; skipping cross-process cascade pass."
            )

        return OperationResult(
            operation=request.operation,
            issue_id=request.issue_id,
            dry_run=False,
            plan=plan,
            pre_state=pre_state,
            post_state=post_state,
            findings=findings,
            cascade_applications=cascade_applications,
        )
