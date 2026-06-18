"""Controller create-path tests — creating operations (spawn) open a new
issue rather than mutating `issue_id` in place."""

from __future__ import annotations

from dataclasses import dataclass, field

from workflow.backends.base import IssueFilters, IssueState, MarkerChange
from workflow.core.controller import Controller
from workflow.core.model.state_machine import Spawn, StateMachine
from workflow.core.operations import collect_into as collect_into_op
from workflow.core.operations import create_issue as create_issue_op
from workflow.core.operations import spawn_issue as spawn_issue_op


def _parent_from(labels: list[str] | None) -> str | None:
    for label in labels or []:
        if label.startswith("child-of:"):
            return label[len("child-of:") :]
    return None


@dataclass
class _CreateMockBackend:
    name: str = "mock"
    issues: dict[str, IssueState] = field(default_factory=dict)
    created_issues: list[tuple[str, str, tuple[str, ...], str | None]] = field(default_factory=list)
    created_prs: list[tuple[str, str | None, bool, tuple[str, ...]]] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)
    applied: list[tuple[str, MarkerChange]] = field(default_factory=list)
    _next: int = 200

    def read_issue(self, issue_id: str) -> IssueState:
        return self.issues[issue_id]

    def create_issue(self, title, body, state, extra_labels=None, issue_type=None) -> str:
        new_id = str(self._next)
        self._next += 1
        self.created_issues.append((new_id, state, tuple(extra_labels or ()), issue_type))
        self.issues[new_id] = IssueState(
            issue_id=new_id, state=state, agent_claim=None, child_of=_parent_from(extra_labels)
        )
        return new_id

    def create_pull_request(
        self, title, body, state, head=None, base=None, draft=False, extra_labels=None
    ) -> str:
        new_id = str(self._next)
        self._next += 1
        self.created_prs.append((new_id, head, draft, tuple(extra_labels or ())))
        self.issues[new_id] = IssueState(
            issue_id=new_id, state=state, agent_claim=None, child_of=_parent_from(extra_labels)
        )
        return new_id

    def apply_marker_change(self, issue_id, change, audit_comment=None) -> None:
        # Records every applied change. Spawn applies none (empty change is
        # skipped by the controller); collect applies one per contributor.
        self.mutations.append(issue_id)
        self.applied.append((issue_id, change))

    def list_issues(self, filters: IssueFilters) -> list[IssueState]:
        return []


def _controller(backend: _CreateMockBackend) -> Controller:
    # registry=None → cascade is skipped (the fresh child triggers nothing here).
    return Controller(backend=backend, state_machine=StateMachine(name="parent"), registry=None)


def test_controller_spawn_creates_child_and_leaves_parent_untouched() -> None:
    backend = _CreateMockBackend()
    backend.issues["100"] = IssueState(issue_id="100", state="mitigating", agent_claim="ic")
    spawn = Spawn(
        process="inner-loop", issue_type="hotfix", initial_state="ready_for_dev", advance_on=()
    )

    result = spawn_issue_op.run(
        _controller(backend),
        issue_id="100",
        spawn=spawn,
        parent_process="incident-response",
        entity="issue",
        github_issue_type="Hotfix",
        body="fix it",
    )

    assert result.created_issue_id == "200"
    new_id, state, labels, gh_type = backend.created_issues[0]
    assert state == "ready_for_dev"
    assert "child-of:100" in labels and "type:hotfix" in labels
    assert gh_type == "Hotfix"
    # The parent (100) was read but never mutated.
    assert backend.mutations == []
    assert backend.issues["100"].state == "mitigating"


def test_controller_spawn_pr_uses_create_pull_request() -> None:
    backend = _CreateMockBackend()
    backend.issues["5"] = IssueState(issue_id="5", state="implementing", agent_claim="dev")
    spawn = Spawn(process="pr", issue_type="pr", initial_state="draft", advance_on=())

    result = spawn_issue_op.run(
        _controller(backend),
        issue_id="5",
        spawn=spawn,
        parent_process="inner-loop",
        entity="pull_request",
        head="feat/x",
        body="impl",
    )

    assert result.created_issue_id == "200"
    assert not backend.created_issues  # went through the PR path
    new_id, head, draft, labels = backend.created_prs[0]
    assert head == "feat/x"
    assert draft is True
    assert labels == ("child-of:5",)
    assert backend.mutations == []


def test_controller_spawn_dry_run_creates_nothing() -> None:
    backend = _CreateMockBackend()
    backend.issues["100"] = IssueState(issue_id="100", state="mitigating", agent_claim="ic")
    spawn = Spawn(
        process="inner-loop", issue_type="hotfix", initial_state="ready_for_dev", advance_on=()
    )
    controller = Controller(
        backend=backend, state_machine=StateMachine(name="parent"), registry=None, dry_run=True
    )

    result = spawn_issue_op.run(
        controller,
        issue_id="100",
        spawn=spawn,
        parent_process="incident-response",
        entity="issue",
    )

    assert result.dry_run is True
    assert result.created_issue_id is None
    assert backend.created_issues == [] and backend.created_prs == []
    assert result.plan.create is not None and result.plan.create.state == "ready_for_dev"


def test_controller_collect_marks_contributor_only() -> None:
    backend = _CreateMockBackend()
    backend.issues["7"] = IssueState(issue_id="7", state="staged", agent_claim=None)

    result = collect_into_op.run(
        _controller(backend),
        issue_id="7",
        collector_id="REL-1",
        from_states=("staged",),
    )

    assert result.operation.value == "collect-into"
    # Exactly one mutation — on the contributor — carrying set_collected_by.
    assert [iid for iid, _ in backend.applied] == ["7"]
    _, change = backend.applied[0]
    assert change.set_collected_by == "REL-1"


def test_controller_create_with_collect_marks_contributors() -> None:
    backend = _CreateMockBackend()

    result = create_issue_op.run(
        _controller(backend),
        title="release cut",
        state="cut",
        collect_contributors=("7", "8"),
    )

    assert result.created_issue_id == "200"
    # The new collector's contributors are stamped collected-by:<new-id>.
    marked = {iid: change.set_collected_by for iid, change in backend.applied}
    assert marked == {"7": "200", "8": "200"}


def test_controller_skips_cascade_when_state_unchanged() -> None:
    """A state-orthogonal op (e.g. review-blocked) must not trigger the cascade —
    only an actual state change does (#18)."""
    from workflow.core.operations import review_blocked as review_blocked_op

    consulted: list[str] = []

    class _RecordingRegistry:
        def find_process_for_state(self, state_name):  # noqa: ANN001
            consulted.append(state_name)
            return None

        def get_process(self, name):  # noqa: ANN001
            raise AssertionError("cascade should not run on a no-state-change op")

    backend = _CreateMockBackend()
    backend.issues["1"] = IssueState(
        issue_id="1", state="refining", agent_claim="pm", awaiting_gate="g"
    )
    controller = Controller(
        backend=backend, state_machine=StateMachine(name="t"), registry=_RecordingRegistry()
    )

    result = review_blocked_op.run(controller, issue_id="1")

    # review-blocked sets the reviewing singleton; state is unchanged → no cascade.
    assert result.post_state is not None and result.post_state.state == "refining"
    assert result.cascade_applications == []
    assert consulted == []  # the registry was never consulted


def test_controller_resolves_native_issue_type() -> None:
    from workflow.core.model.issue_type import IssueType, IssueTypeDirectory

    directory = IssueTypeDirectory(
        types={
            "bug": IssueType(type_id="bug", name="Bug", description="d", github_issue_type="Bug")
        }
    )
    controller = Controller(
        backend=_CreateMockBackend(),
        state_machine=StateMachine(name="t"),
        registry=None,
        issue_type_directory=directory,
    )
    # Native encoding (no type: label) → mapped to the framework id.
    native = IssueState(issue_id="1", state="raw", agent_claim=None, native_issue_type="Bug")
    assert controller._resolve_native_type(native).issue_type == "bug"
    # Label encoding already resolved the id → left untouched.
    labelled = IssueState(
        issue_id="2", state="raw", agent_claim=None, issue_type="feature", native_issue_type="Bug"
    )
    assert controller._resolve_native_type(labelled).issue_type == "feature"
