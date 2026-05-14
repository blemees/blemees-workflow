"""Backend protocol — what every backend implementation must provide.

The framework's eleven operations (defined in hitl-principles.md § 5) are
implemented in `workflow.core.operations` against this protocol. Concrete
backends (GitHub, GitLab, Jira, Linear, ...) implement this interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkItemState:
    """A snapshot of a work item's framework-relevant markers, read from the backend.

    The structure is abstract — concrete backends map it from labels (GitHub),
    custom fields (Jira), tags (Linear), etc. The core never sees the backend's
    raw representation; it only reads `WorkItemState`.
    """

    work_item_id: str
    state: str | None  # current lifecycle state name
    agent_claim: str | None  # role_id holding the agent claim, if any
    # Catalogued-block markers
    awaiting_gate: str | None = None  # gate_name currently awaiting signal
    reviewing: bool = False  # singleton: a human has claimed pre-action review
    # Catalogued-audit markers
    audit_pending: str | None = None  # gate_name pending retroactive review
    auditing: bool = False  # singleton: a human has claimed post-action audit
    # Recognized markers
    awaiting_input: bool = False  # generic queue marker for recognized HCPs
    advising: bool = False  # singleton: a human is advising
    # Misc
    extras: dict[str, str] = field(default_factory=dict)  # backend-specific extras


@dataclass(frozen=True)
class MarkerChange:
    """A planned change to the work item's marker set.

    The planner produces a list of these; the controller executes them
    atomically via the backend. The backend translates abstract names to
    concrete encodings.
    """

    set_state: str | None = None  # new state name, if state advances
    set_agent_claim: str | None = None  # new agent role, or empty to clear
    clear_agent_claim: bool = False
    set_awaiting_gate: str | None = None
    clear_awaiting_gate: bool = False
    set_reviewing: bool | None = None
    set_audit_pending: str | None = None
    clear_audit_pending: bool = False
    set_auditing: bool | None = None
    set_awaiting_input: bool | None = None
    set_advising: bool | None = None
    # Outcome markers (audit-trace labels in GitHub encoding; signal events elsewhere)
    record_approval: str | None = None  # destination approved
    record_rejection: str | None = None  # gate rejected
    record_check: str | None = None  # destination checked post-hoc
    record_revoke: str | None = None  # destination revoked
    record_resolution: bool = False  # recognized HCP resolved


@dataclass(frozen=True)
class WorkItemFilters:
    """Filters for `list_work_items`.

    Each filter is optional; backends apply them as AND constraints. The
    semantics are framework-level (state, claim, gate markers); each backend
    translates them to its tracker's query syntax.
    """

    state: str | None = None
    claim_role: str | None = None
    awaiting_gate: str | None = None  # specific gate name; pass "*" for any
    audit_pending: str | None = None  # specific gate name; pass "*" for any
    awaiting_input: bool | None = None  # True / False / None (don't filter)
    limit: int = 50


@runtime_checkable
class WorkflowBackend(Protocol):
    """The contract a backend implementation satisfies.

    Operations in `workflow.core.operations` call these methods. Each backend
    provides the nine operations below; some backends may add tracker-specific
    affordances, but the nine are the framework's required surface.
    """

    name: str  # short identifier, e.g., "github"

    def create_work_item(
        self,
        title: str,
        body: str,
        state: str,
        extra_labels: list[str] | None = None,
    ) -> str:
        """Create a new work item in the given initial state.

        Returns the new work item's id (a string — issue number for github,
        whatever the backend uses elsewhere). The backend MUST attach the
        framework's `state:<name>` marker atomically with creation so the
        item never exists without a state, and SHOULD attach any
        `extra_labels` the caller passed (e.g., a `wip:<role>` to claim
        the item at intake).
        """
        ...

    def read_work_item(self, work_item_id: str) -> WorkItemState:
        """Fetch the work item's current framework-relevant markers."""
        ...

    def list_work_items(self, filters: WorkItemFilters) -> list[WorkItemState]:
        """List work items matching the filters.

        Filters compose with AND. Empty / None filters match everything. Each
        backend translates the abstract filters to its native query syntax.
        """
        ...

    def apply_marker_change(
        self,
        work_item_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
        """Atomically apply a marker change and post the audit comment.

        The backend MUST honor the atomicity guarantee in
        `backends/<name>-encoding.md` — readers never see partial state.
        """
        ...

    def post_comment(self, work_item_id: str, body: str) -> None:
        """Post a comment without changing markers. Used for packets and
        question bodies that accompany await-signal / request-input."""
        ...

    def read_comments(self, work_item_id: str, since: str | None = None) -> list[dict]:
        """Read comments on the work item. Each comment is a dict with at
        least `author`, `body`, `created_at` keys."""
        ...

    def resolve_role(self, role_id: str) -> str | None:
        """Resolve a framework role placeholder (e.g., `pm`) to a concrete
        backend handle (e.g., a GitHub username). Returns None if unmapped."""
        ...

    def assignee(self, work_item_id: str) -> str | None:
        """Return the current assignee handle, or None if unassigned."""
        ...

    def assign(self, work_item_id: str, handle: str) -> None:
        """Assign the work item to the named handle. Used by `claim`."""
        ...

    def unassign(self, work_item_id: str) -> None:
        """Clear the work item's assignment. Used by `release`."""
        ...

    def list_labels(self) -> list[str]:
        """Return the names of every label currently defined on the backend repo.

        Used by `setup-labels` to skip labels that already exist, and by any
        future audit that wants to compare the framework's required set
        against the repo's actual set.
        """
        ...

    def ensure_label(self, name: str, color: str | None = None) -> bool:
        """Create the named label on the repo if it does not already exist.

        Returns True if a new label was created, False if it already existed.
        Never overwrites an existing label's color or description — used by
        `setup-labels` for one-shot provisioning, where the user's
        customizations on existing labels must be preserved.

        If `color` is None, the backend selects a default color appropriate
        for the label's namespace (state, wip, hitl, ...).
        """
        ...
