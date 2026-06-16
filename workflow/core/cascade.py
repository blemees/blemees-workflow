"""Cascade-advance — recursively trigger downstream `advance_on` rules.

When an issue's state changes, two kinds of cross-process auto-advance can fire:

1. **Spawn parent feedback.** If this issue was spawned by a parent
   (it carries `parent-of:<parent>`), and the new state matches a key
   in the parent's `spawns.advance_on`, the parent advances to the
   mapped state.

2. **Collector contributor advance / release.** If this issue is a
   collector (its state declares `collects`), and the new state
   matches a key in `collects.advance_on` (or appears in
   `collects.release_on`), every contributor (issues with
   `collected-by:<this>`) advances or has its collection cleared.

Each cascade-applied state change can itself trigger further cascades.
A BFS queue with a visited set keeps the chain bounded — every (issue,
state) pair is processed at most once, so cycles can't deadlock the
runtime.

This module is invoked by `Controller.execute` after every successful
state-changing operation; it walks the chain and applies all derived
changes via the same backend the controller uses. Each cascade
application records an audit comment so the chain is visible in the
issue history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workflow.backends.base import IssueState, MarkerChange, TrackerBackend
from workflow.core.model.state_machine import StateMachine
from workflow.errors import BackendError

if TYPE_CHECKING:
    from workflow.config import Workflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CascadeApplication:
    """One state change applied by the cascade machinery.

    `kind` is one of `"spawn_parent"`, `"collect_advance"`, `"collect_release"`.
    `source_issue` is the issue whose state change triggered the cascade.
    `affected_issue` is the issue this application changed.
    `from_state` / `to_state` describe the affected issue's transition
    (or "(label-only)" when the cascade only cleared a marker).
    """

    kind: str
    source_issue: str
    affected_issue: str
    from_state: str | None
    to_state: str | None


def _destination_lifecycle_change(
    state_machine: StateMachine,
    destination: str | None,
) -> tuple[bool, str | None, bool]:
    """Return tracker lifecycle effects for an auto-advance destination."""
    if destination is None or destination == "[*]":
        return False, None, False
    state = state_machine.states.get(destination)
    if state is None:
        return False, None, False
    close_issue = state.closes is not None
    close_reason = state.closes.reason if state.closes is not None else None
    return close_issue, close_reason, state.mark_pr_ready


def cascade_after_state_change(
    registry: Workflow,
    backend: TrackerBackend,
    issue_id: str,
    post_state: IssueState,
    actor: str | None = None,
) -> list[CascadeApplication]:
    """Walk the cascade chain rooted at `issue_id` and apply all derived
    changes via `backend.apply_marker_change`.

    Returns the ordered list of applications (in the order they fired)
    for audit / telemetry purposes.
    """
    applications: list[CascadeApplication] = []
    queue: list[tuple[str, IssueState]] = [(issue_id, post_state)]
    visited: set[tuple[str, str | None]] = set()

    while queue:
        current_id, current = queue.pop(0)
        key = (current_id, current.state)
        if key in visited:
            continue
        visited.add(key)

        # (1) Spawn parent feedback.
        parent_change = _apply_spawn_parent_cascade(
            registry, backend, current_id, current, applications, actor
        )
        if parent_change is not None:
            queue.append(parent_change)

        # (2) Collector contributor advance / release. This issue might
        #     itself be a collector — if its state matches an entry in
        #     its own `collects.advance_on` or `collects.release_on`,
        #     every contributor needs updating.
        contributor_changes = _apply_collector_cascade(
            registry, backend, current_id, current, applications, actor
        )
        queue.extend(contributor_changes)

    return applications


def _apply_spawn_parent_cascade(
    registry: Workflow,
    backend: TrackerBackend,
    child_id: str,
    child_state: IssueState,
    applications: list[CascadeApplication],
    actor: str | None,
) -> tuple[str, IssueState] | None:
    """Wait-for-all spawn-parent advance.

    When `child_id`'s new state matches *its* spawn rule's `advance_on`,
    the parent advances ONLY if every other active child on the same
    parent also satisfies its respective spawn rule's advance_on. With
    a single spawn rule and a single child this reduces to the previous
    behavior. With multiple rules / children, the parent waits for the
    full set.

    Returns the parent's (id, post_state) for further cascading, or
    None if no advance fired this turn.
    """
    if child_state.parent_of is None or child_state.state is None:
        return None
    parent_id = child_state.parent_of
    try:
        parent_state = backend.read_issue(parent_id)
    except BackendError as exc:
        logger.warning("cascade: cannot read parent %s of %s: %s", parent_id, child_id, exc)
        return None
    if parent_state.state is None:
        return None
    parent_process_name = registry.find_process_for_state(parent_state.state)
    if parent_process_name is None:
        return None
    try:
        parent_process = registry.get_process(parent_process_name)
    except Exception as exc:  # pragma: no cover - registry surface
        logger.warning("cascade: cannot load parent process %s: %s", parent_process_name, exc)
        return None
    parent_state_def = parent_process.state_machine.states.get(parent_state.state)
    if parent_state_def is None or not parent_state_def.spawns:
        return None

    # Match this child to its spawn rule. Primary key is issue_type;
    # if the child's IssueState has no issue_type (e.g. test mocks or
    # backends that don't surface it), fall back to all rules on the
    # state and pick by advance_on containing the child's closing state.
    if child_state.issue_type is not None:
        candidate_rules = [
            sp for sp in parent_state_def.spawns if sp.issue_type == child_state.issue_type
        ]
        if not candidate_rules:
            return None
    else:
        candidate_rules = list(parent_state_def.spawns)
    triggering_rule = None
    for sp in candidate_rules:
        if any(term == child_state.state for term, _ in sp.advance_on):
            triggering_rule = sp
            break
    if triggering_rule is None:
        return None

    # Wait-for-all: every active child on the parent must satisfy its
    # rule. Gather subprocess children from labels.
    target_state: str | None = None
    for sibling_id in parent_state.subprocess_children:
        try:
            sibling = backend.read_issue(sibling_id)
        except BackendError as exc:
            logger.warning(
                "cascade: cannot read sibling %s of parent %s: %s",
                sibling_id,
                parent_id,
                exc,
            )
            # Treat unreadable siblings as not-satisfied (conservative).
            return None
        if sibling.state is None:
            return None
        if sibling.issue_type is not None:
            sibling_rules = [
                sp for sp in parent_state_def.spawns if sp.issue_type == sibling.issue_type
            ]
            if not sibling_rules:
                # Sibling out-of-band (no matching rule) — treat as
                # not-satisfied. The responder will need to close manually.
                return None
        else:
            # No issue_type on the sibling — consider all rules.
            sibling_rules = list(parent_state_def.spawns)
        matched: tuple[str, str] | None = None
        for sp in sibling_rules:
            for term, parent_next in sp.advance_on:
                if term == sibling.state:
                    matched = (term, parent_next)
                    break
            if matched is not None:
                break
        if matched is None:
            return None
        _, parent_next = matched
        if target_state is None:
            target_state = parent_next
        elif target_state != parent_next:
            logger.warning(
                "cascade: parent %s spawn rules disagree on target "
                "(%s vs %s) — skipping auto-advance.",
                parent_id,
                target_state,
                parent_next,
            )
            return None

    if target_state is None:
        return None

    close_issue, close_reason, set_pr_ready = _destination_lifecycle_change(
        parent_process.state_machine,
        target_state,
    )
    change = MarkerChange(
        set_state=target_state,
        clear_agent_claim=parent_state.agent_claim is not None,
        clear_last_state=parent_state.last_state is not None,
        close_issue=close_issue,
        close_reason=close_reason,
        set_pr_ready=set_pr_ready,
    )
    audit = (
        f"## cascade: spawn-parent advance\n\n"
        f"Child #{child_id} reached `{child_state.state}`; parent "
        f"#{parent_id} advances {parent_state.state!r} → {target_state!r} "
        f"(all spawned children satisfy their `advance_on` triggers)."
    )
    try:
        backend.apply_marker_change(parent_id, change, audit_comment=audit)
    except BackendError as exc:
        logger.error(
            "cascade: failed to advance parent %s to %r: %s",
            parent_id,
            target_state,
            exc,
        )
        return None
    applications.append(
        CascadeApplication(
            kind="spawn_parent",
            source_issue=child_id,
            affected_issue=parent_id,
            from_state=parent_state.state,
            to_state=target_state,
        )
    )
    try:
        post = backend.read_issue(parent_id)
    except BackendError:
        return None
    return (parent_id, post)


def _apply_collector_cascade(
    registry: Workflow,
    backend: TrackerBackend,
    collector_id: str,
    collector_state: IssueState,
    applications: list[CascadeApplication],
    actor: str | None,
) -> list[tuple[str, IssueState]]:
    """If `collector_id` is a collector and its new state matches its own
    `collects.advance_on` or `collects.release_on`, propagate to every
    contributor. Returns the list of contributors that need further
    cascading (post-state)."""
    if collector_state.state is None:
        return []
    process_name = registry.find_process_for_state(collector_state.state)
    if process_name is None:
        return []
    try:
        process = registry.get_process(process_name)
    except Exception as exc:  # pragma: no cover
        logger.warning("cascade: cannot load collector process %s: %s", process_name, exc)
        return []
    state_def = process.state_machine.states.get(collector_state.state)
    if state_def is None or state_def.collects is None:
        return []
    collects = state_def.collects

    # Determine which contributor action applies, if any.
    matching_rule = None
    is_release = False
    for rule in collects.advance_on:
        if rule.collector_state == collector_state.state:
            matching_rule = rule
            break
    if matching_rule is None and collector_state.state in collects.release_on:
        is_release = True

    if matching_rule is None and not is_release:
        return []

    contributors = list(collector_state.collects_contributors)
    if not contributors:
        return []

    next_queue: list[tuple[str, IssueState]] = []
    for contributor_id in contributors:
        try:
            contributor = backend.read_issue(contributor_id)
        except BackendError as exc:
            logger.warning(
                "cascade: cannot read contributor %s of %s: %s",
                contributor_id,
                collector_id,
                exc,
            )
            continue
        if contributor.state is None:
            continue
        # Resolve the contributor's target — depends on its issue_type
        # when the rule declares per-type targets.
        contributor_target: str | None = None
        if matching_rule is not None:
            contributor_target = matching_rule.target_for(contributor.issue_type)
        if contributor_target is not None:
            contributor_process_name = registry.find_process_for_state(contributor_target)
            if contributor_process_name is not None:
                try:
                    contributor_process = registry.get_process(contributor_process_name)
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "cascade: cannot load contributor process %s: %s",
                        contributor_process_name,
                        exc,
                    )
                    contributor_process = None
            else:
                contributor_process = None
            if contributor_process is None:
                close_issue, close_reason, set_pr_ready = False, None, False
            else:
                close_issue, close_reason, set_pr_ready = _destination_lifecycle_change(
                    contributor_process.state_machine,
                    contributor_target,
                )
            change = MarkerChange(
                set_state=contributor_target,
                clear_collected_by=True,
                close_issue=close_issue,
                close_reason=close_reason,
                set_pr_ready=set_pr_ready,
            )
            audit = (
                f"## cascade: collector advance\n\n"
                f"Collector #{collector_id} reached "
                f"`{collector_state.state}`; contributor "
                f"#{contributor_id} advances "
                f"{contributor.state!r} → {contributor_target!r} "
                f"per `{process_name}.{collector_state.state}.collects.advance_on`."
            )
            try:
                backend.apply_marker_change(contributor_id, change, audit_comment=audit)
            except BackendError as exc:
                logger.error(
                    "cascade: failed to advance contributor %s: %s",
                    contributor_id,
                    exc,
                )
                continue
            applications.append(
                CascadeApplication(
                    kind="collect_advance",
                    source_issue=collector_id,
                    affected_issue=contributor_id,
                    from_state=contributor.state,
                    to_state=contributor_target,
                )
            )
            try:
                post = backend.read_issue(contributor_id)
            except BackendError:
                continue
            next_queue.append((contributor_id, post))
        elif is_release:
            # Release-only: clear the collected-by label, no state change.
            change = MarkerChange(clear_collected_by=True)
            audit = (
                f"## cascade: collector release\n\n"
                f"Collector #{collector_id} reached `{collector_state.state}`; "
                f"contributor #{contributor_id} released from collection "
                f"per `{process_name}.{collector_state.state}.collects.release_on`."
            )
            try:
                backend.apply_marker_change(contributor_id, change, audit_comment=audit)
            except BackendError as exc:
                logger.error(
                    "cascade: failed to release contributor %s: %s",
                    contributor_id,
                    exc,
                )
                continue
            applications.append(
                CascadeApplication(
                    kind="collect_release",
                    source_issue=collector_id,
                    affected_issue=contributor_id,
                    from_state=contributor.state,
                    to_state=None,
                )
            )
            # Released contributors don't change state, so no further
            # cascading from them.
        # else: matching_rule exists but the contributor's type has no
        # mapped target and no default — leave the contributor in place.
    return next_queue
