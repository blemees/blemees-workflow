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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from workflow.backends.base import IssueState, MarkerChange, TrackerBackend
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
    from workflow.core.model.issue_type import IssueTypeDirectory

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
    # Set by creating operations (spawn / create) to the id of the new issue.
    # None for in-place operations and for dry-runs.
    created_issue_id: str | None = None


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
    registry: Workflow | None = None
    # Optional issue-type directory, used to map an issue read under native
    # encoding (GitHub Issue Type name, no `type:` label) back to its framework
    # type id before planning — so claim-time type-restriction checks aren't
    # silently skipped (#12).
    issue_type_directory: IssueTypeDirectory | None = None

    def _resolve_native_type(self, state: IssueState) -> IssueState:
        if (
            state.issue_type is None
            and state.native_issue_type is not None
            and self.issue_type_directory is not None
        ):
            framework_id = self.issue_type_directory.by_github_type(state.native_issue_type)
            if framework_id is not None:
                return replace(state, issue_type=framework_id)
        return state

    def execute(self, request: OperationRequest) -> OperationResult:
        # create-issue opens a brand-new issue — there is no existing issue to
        # read. Every other operation (including spawn, which targets the
        # parent) reads its subject's pre-state first.
        if request.operation is Operation.CREATE_ISSUE:
            pre_state = IssueState(issue_id="", state=None, agent_claim=None)
            plan = plan_operation(
                request=request,
                state=pre_state,
                state_machine=self.state_machine,
                catalog=self.catalog,
                grants=self.grants,
            )
            return self._execute_create(request, plan, pre_state, findings=[])
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
        # Resolve a native-encoded issue type to its framework id so claim /
        # advance type checks aren't silently skipped (#12).
        pre_state = self._resolve_native_type(pre_state)
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

        # Creating operations (spawn / create) open a new issue rather than
        # mutating `issue_id` in place — separate path.
        if plan.create is not None:
            return self._execute_create(request, plan, pre_state, findings)

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

        # Cascade pass — propagate cross-process advance_on chains. Edge-, not
        # level-triggered: only fire when this operation actually changed the
        # state. Otherwise a state-orthogonal op (review-blocked, respond, …) on
        # an issue sitting at a collector trigger state would re-fire the rule
        # and yank contributors that have moved on (#18). Needs the registry to
        # look up sibling-process spawn / collect definitions.
        cascade_applications: list[CascadeApplication] = []
        state_changed = post_state.state != pre_state.state
        if self.registry is not None and state_changed:
            cascade_applications = cascade_after_state_change(
                self.registry,
                self.backend,
                request.issue_id,
                post_state,
                actor=request.actor,
            )
        elif self.registry is None:
            logger.debug("controller: no registry attached; skipping cross-process cascade pass.")

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

    def _execute_create(
        self,
        request: OperationRequest,
        plan: OperationPlan,
        pre_state: IssueState,
        findings: list[ValidationFinding],
    ) -> OperationResult:
        """Create-then-apply path for creating operations (spawn / create).

        Opens a new issue/PR from `plan.create`, applies the plan's primary
        marker change to the new issue if non-empty (empty for spawn — the
        `child-of:` marker rides in `extra_labels`), then runs the cascade
        against the new issue.
        """
        spec = plan.create
        assert spec is not None
        if self.dry_run:
            logger.info(
                "[dry-run] %s would create a %s in state %s",
                request.operation.value,
                spec.entity,
                spec.state,
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

        if spec.entity == "pull_request":
            new_id = self.backend.create_pull_request(
                title=spec.title,
                body=spec.body,
                state=spec.state,
                head=spec.head,
                base=spec.base,
                draft=spec.draft,
                extra_labels=list(spec.extra_labels),
            )
        else:
            new_id = self.backend.create_issue(
                title=spec.title,
                body=spec.body,
                state=spec.state,
                extra_labels=list(spec.extra_labels),
                issue_type=spec.github_issue_type,
            )

        if plan.change != MarkerChange():
            self.backend.apply_marker_change(new_id, plan.change, audit_comment=plan.audit_comment)

        # Stamp gathered contributors `collected-by:<new-id>` — the new id only
        # exists now, so this is the create-with-collect secondary effect.
        for contributor_id in spec.collect_contributors:
            self.backend.apply_marker_change(
                contributor_id,
                MarkerChange(set_collected_by=new_id),
                audit_comment=f"Collected into #{new_id}.",
            )

        child_state = self.backend.read_issue(new_id)

        cascade_applications: list[CascadeApplication] = []
        if self.registry is not None:
            cascade_applications = cascade_after_state_change(
                self.registry,
                self.backend,
                new_id,
                child_state,
                actor=request.actor,
            )

        return OperationResult(
            operation=request.operation,
            issue_id=request.issue_id,
            dry_run=False,
            plan=plan,
            pre_state=pre_state,
            post_state=child_state,
            findings=findings,
            cascade_applications=cascade_applications,
            created_issue_id=new_id,
        )
