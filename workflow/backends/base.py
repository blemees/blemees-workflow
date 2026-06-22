"""Backend protocol — what every backend implementation must provide.

The framework's eleven operations (defined in hitl-principles.md § 5) are
implemented in `workflow.core.operations` against this protocol. Concrete
backends (GitHub, GitLab, Jira, Linear, ...) implement this interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class IssueState:
    """A snapshot of an issue's framework-relevant markers, read from the backend.

    The structure is abstract — concrete backends map it from labels (GitHub),
    custom fields (Jira), tags (Linear), etc. The core never sees the backend's
    raw representation; it only reads `IssueState`.
    """

    issue_id: str
    state: str | None  # current workflow state name
    agent_claim: str | None  # role_id holding the agent claim, if any
    # Origin marker — the resting state we claimed from. Recorded on `claim`
    # so `release` knows where to return the issue to. Eliminates the need
    # for the user to specify a destination on release.
    last_state: str | None = None
    # Framework-canonical issue type id (e.g., "bug", "experiment"). Set
    # at issue creation; immutable. Backends extract this from the type
    # encoding they were configured with — label encoding reads `type/<id>`
    # labels; native encoding (GitHub Issue Types) reads the entity's
    # native type field. None means the backend couldn't determine it;
    # the planner treats that as "skip type-restriction checks."
    issue_type: str | None = None
    # The backend's native type name when read under native encoding (GitHub
    # Issue Type, e.g. "Bug") and no `type/` label is present. The framework id
    # isn't derivable without the issue-type directory, so the controller maps
    # this back to `issue_type` before planning (otherwise type-restriction
    # checks silently no-op under native encoding).
    native_issue_type: str | None = None
    # Catalogued-block markers
    awaiting_gate: str | None = None  # gate_name currently awaiting signal
    reviewing: bool = False  # singleton: a human has claimed pre-action review
    # Catalogued-audit markers
    audit_pending: str | None = None  # gate_name pending retroactive review
    auditing: bool = False  # singleton: a human has claimed post-action audit
    # Recognized markers
    awaiting_input: bool = False  # generic queue marker for recognized input requests
    human_input: str | None = None  # topic the agent is awaiting input on
    advising: bool = False  # singleton: a human is advising
    # Fan-in marker — set when this issue has been gathered into a
    # collector issue on another process via `collects`. Backends populate
    # this from a `collected-by/<collector-id>` label on GitHub. None
    # means the issue has not yet been collected and is a candidate for
    # future collectors that target its state.
    collected_by: str | None = None
    # The inverse — a collector's contributors — is NOT stored as a marker.
    # The cohort is discovered on demand by querying the `collected-by/` label
    # (`list_issues(collected_by=...)`); there is no collector-side registry
    # (ADR-0003).
    # Spawn parent — when this issue was created by another issue via
    # `spawns`, the parent's id is recorded here (read from a
    # `child-of/<parent-id>` label on GitHub). None means this issue
    # was not spawned by anyone. Used by the cascade-advance logic to
    # walk back up the spawn chain when a child terminates.
    # The inverse — a parent's children — is NOT stored as a marker. The
    # cohort is discovered on demand by querying the `child-of/` label
    # (`list_issues(child_of=...)`); there is no parent-side registry
    # (ADR-0003).
    child_of: str | None = None
    # Misc
    extras: dict[str, str] = field(default_factory=dict)  # backend-specific extras


@dataclass(frozen=True)
class MarkerChange:
    """A planned change to the issue's marker set.

    The planner produces a list of these; the controller executes them
    atomically via the backend. The backend translates abstract names to
    concrete encodings.
    """

    set_state: str | None = None  # new state name, if state advances
    set_agent_claim: str | None = None  # new agent role, or empty to clear
    clear_agent_claim: bool = False
    set_last_state: str | None = None  # origin resting state, set on claim
    clear_last_state: bool = False  # cleared on release / advance out of working
    set_awaiting_gate: str | None = None
    clear_awaiting_gate: bool = False
    set_reviewing: bool | None = None
    set_audit_pending: str | None = None
    clear_audit_pending: bool = False
    set_auditing: bool | None = None
    set_awaiting_input: bool | None = None
    # Companion to set_awaiting_input — records the topic the agent is
    # awaiting input on. On GitHub the two are encoded as one merged
    # `hitl-input/<topic>` label. Cleared via `clear_human_input` when
    # respond fires.
    set_human_input: str | None = None
    clear_human_input: bool = False
    set_advising: bool | None = None
    # Fan-in label mutation — `collected-by/<collector>` is set on each
    # contributor (the sole record of the relationship; ADR-0003). There is no
    # collector-side `collects:` write — the cohort is a `collected-by/` query.
    set_collected_by: str | None = None
    clear_collected_by: bool = False
    # Outcome markers (audit-trace labels in GitHub encoding; signal events elsewhere)
    record_approval: str | None = None  # gate approved (destination is captured via set_state)
    record_rejection: str | None = None  # gate rejected
    record_confirm: str | None = None  # destination checked post-hoc
    record_revoke: str | None = None  # destination revoked
    record_response: bool = False  # recognized HumanGate resolved
    # Issue lifecycle on the tracker. Set when advancing into a closing state
    # state whose taxonomy means "done" (not `iterated`, which is a
    # cross-process handoff). The tracker closes the issue with the
    # provided reason ("completed" / "not planned" for GitHub).
    close_issue: bool = False
    close_reason: str | None = None
    # Set when advancing into a state with `mark_pr_ready: true`. The
    # GitHub backend interprets this as `gh pr ready <id>` — flipping the
    # PR from draft to ready-for-review. No-op when the issue isn't a
    # pull request.
    set_pr_ready: bool = False


@dataclass(frozen=True)
class IssueFilters:
    """Filters for `list_issues`.

    Each filter is optional; backends apply them as AND constraints. The
    semantics are framework-level (state, claim, gate markers); each backend
    translates them to its tracker's query syntax.
    """

    state: str | None = None
    claim_role: str | None = None
    awaiting_gate: str | None = None  # specific gate name; pass "*" for any
    audit_pending: str | None = None  # specific gate name; pass "*" for any
    awaiting_input: bool | None = None  # True / False / None (don't filter)
    child_of: str | None = None  # cohort: children spawned from this parent id
    collected_by: str | None = None  # cohort: contributors gathered by this collector id
    limit: int = 50


@runtime_checkable
class TrackerBackend(Protocol):
    """The contract a backend implementation satisfies.

    The framework's operations in `workflow.core.operations` call these methods.
    Each backend provides every method defined below — they are the framework's
    required surface — and may add tracker-specific affordances on top.
    """

    name: str  # short identifier, e.g., "github"

    def create_issue(
        self,
        title: str,
        body: str,
        state: str,
        extra_labels: list[str] | None = None,
        issue_type: str | None = None,
    ) -> str:
        """Create a new issue in the given initial state.

        Returns the new issue's id (a string — issue number for github,
        whatever the backend uses elsewhere). The backend MUST attach the
        framework's `state/<name>` marker atomically with creation so the
        item never exists without a state, and SHOULD attach any
        `extra_labels` the caller passed (e.g., a `claimed/<role>` to claim
        the item at intake).

        `issue_type`, if provided, is the backend-specific type identifier
        (GitHub Issue Type name, Jira issue type, etc.). The CLI resolves
        the framework's type id to this backend-specific string via the
        `IssueTypeDirectory`. Backends that don't support typed issues
        ignore the argument.
        """
        ...

    def create_pull_request(
        self,
        title: str,
        body: str,
        state: str,
        head: str,
        base: str | None = None,
        draft: bool = False,
        extra_labels: list[str] | None = None,
    ) -> str:
        """Create a new pull request in the given initial state.

        Distinct from `create_issue` because the backend dispatches to a
        different tracker entity (GitHub PRs aren't Issues; some trackers
        merge the two but the creation path still differs). The framework's
        `state/<name>` marker is attached atomically with creation.

        `head` is the source branch; `base` is the target branch (None
        means the backend chooses the repo default). `draft` opens the PR
        in the tracker's draft mode — typically aligned with the initial
        framework state being `draft`, but they're independent.

        Returns the new PR's id (issue/PR number for GitHub).
        """
        ...

    def read_issue(self, issue_id: str) -> IssueState:
        """Fetch the issue's current framework-relevant markers."""
        ...

    def list_issues(self, filters: IssueFilters) -> list[IssueState]:
        """List issues matching the filters.

        Filters compose with AND. Empty / None filters match everything. Each
        backend translates the abstract filters to its native query syntax.
        """
        ...

    def apply_marker_change(
        self,
        issue_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
        """Apply a marker change and post the audit comment.

        The state change itself (the marker/label swap) MUST be atomic — readers
        never see a torn state marker. Side effects that a tracker can't bundle
        into that swap (assignment, close, pr-ready, the audit comment) are
        applied as a best-effort sequence *after* it; on failure the backend
        MUST raise rather than leave a silent inconsistency, and SHOULD name the
        repair. The audit comment is posted last so it never records a
        transition that didn't happen.
        """
        ...

    def post_comment(self, issue_id: str, body: str) -> None:
        """Post a comment without changing markers. Used for packets and
        question bodies that accompany await-signal / request-input."""
        ...

    def read_comments(self, issue_id: str, since: str | None = None) -> list[dict]:
        """Read comments on the issue. Each comment is a dict with at
        least `author`, `body`, `created_at` keys."""
        ...

    def resolve_role(self, role_id: str) -> str | None:
        """Resolve a framework role placeholder (e.g., `pm`) to a concrete
        backend handle (e.g., a GitHub username). Returns None if unmapped."""
        ...

    def assignee(self, issue_id: str) -> str | None:
        """Return the current assignee handle, or None if unassigned."""
        ...

    def assign(self, issue_id: str, handle: str) -> None:
        """Assign the issue to the named handle. Used by `claim`."""
        ...

    def unassign(self, issue_id: str) -> None:
        """Clear the issue's assignment. Used by `release`."""
        ...

    def edit_issue(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        """Edit the issue's title and/or body on the tracker.

        Independent of workflow state — neither labels nor markers change.
        At least one of `title` or `body` must be provided; both is allowed.
        The CLI's `edit` command surfaces this for typo fixes and scope
        adjustments that don't fit into a state transition.
        """
        ...

    def close_issue(self, issue_id: str, reason: str | None = None) -> None:
        """Close the issue on the tracker.

        Called when an operation advances into a closing state whose
        taxonomy means "done" (everything except `iterated`, which is a
        cross-process handoff). `reason` is a backend-specific hint —
        GitHub accepts `completed` or `not planned`; other backends may
        ignore the argument.
        """
        ...

    def list_labels(self) -> list[str]:
        """Return the names of every label currently defined on the backend repo.

        Used by `setup-github` to skip labels that already exist, and by any
        future audit that wants to compare the framework's required set
        against the repo's actual set.
        """
        ...

    def list_issue_types(self, org: str) -> list[str] | None:
        """Return existing issue type names at the org, or None if unavailable.

        `None` means the backend cannot read issue types in this org for any
        reason — feature not enabled, user lacks permission, server doesn't
        support the feature, etc. The CLI treats `None` as "encode as labels"
        rather than trying to distinguish causes.

        An empty list `[]` means the feature is supported and accessible but
        no types have been defined yet. The CLI also treats this as "encode
        as labels" (no native types to attach), until someone runs
        `setup-github --setup-org` to create them.
        """
        ...

    def ensure_issue_type(
        self,
        org: str,
        name: str,
        description: str,
        color: str | None = None,
    ) -> bool:
        """Create the named issue type at the org if it doesn't exist.

        Returns True if a new type was created, False if it already existed.
        Backends that don't support issue types should raise `BackendError`
        rather than silently succeeding — the CLI's `--setup-org` path
        wants loud failures so the admin can fix permissions or feature flags.
        """
        ...

    def ensure_label(self, name: str, color: str | None = None) -> bool:
        """Create the named label on the repo if it does not already exist.

        Returns True if a new label was created, False if it already existed.
        Never overwrites an existing label's color or description — used by
        `setup-github` for one-shot provisioning, where the user's
        customizations on existing labels must be preserved.

        If `color` is None, the backend selects a default color appropriate
        for the label's classifier (state, claimed, hitl-*, ...).
        """
        ...
