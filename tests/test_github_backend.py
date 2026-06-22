"""GitHub backend tests — mock subprocess.run; verify constructed `gh` commands."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from workflow.backends.base import IssueFilters, IssueState, MarkerChange
from workflow.backends.github import GitHubBackend
from workflow.errors import BackendError, OperationError


def _fake_run_factory(responses: list[mock.Mock]):
    """Return a side-effect function returning successive `subprocess.run` mocks."""
    iterator = iter(responses)

    def _fn(*args, **kwargs):
        return next(iterator)

    return _fn


def _proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> mock.Mock:
    m = mock.Mock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def test_create_issue_invokes_gh_issue_create_with_state_label() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # gh issue create prints the URL of the new issue on stdout.
    new_issue_url = "https://github.com/owner/repo/issues/42\n"
    # First call is ensure_label (state/raw); second is gh issue create.
    responses = [
        _proc(stdout=""),  # label create (state/raw)
        _proc(stdout=new_issue_url),  # gh issue create
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        new_id = backend.create_issue(
            title="Fix the login bug",
            body="Steps to reproduce: ...",
            state="raw",
        )

    assert new_id == "42"
    calls = [args.args[0] for args in patched.call_args_list]
    create_cmd = [c for c in calls if "issue" in c and "create" in c][0]
    # Title (=form so a leading-dash title isn't parsed as a flag), --body-file,
    # and one --label=state/raw flag all present.
    assert "--title=Fix the login bug" in create_cmd
    assert "--body-file" in create_cmd
    assert "--label=state/raw" in create_cmd


def test_create_issue_with_claim_adds_claimed_label() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # Two ensure_label calls (state/raw, claimed/product-manager) then gh issue create.
    responses = [
        _proc(stdout=""),  # ensure state/raw
        _proc(stdout=""),  # ensure claimed/product-manager
        _proc(stdout="https://github.com/owner/repo/issues/7\n"),
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        new_id = backend.create_issue(
            title="New thing",
            body="",
            state="raw",
            extra_labels=["claimed/product-manager"],
        )

    assert new_id == "7"
    create_cmd = [c.args[0] for c in patched.call_args_list if "create" in c.args[0]][-1]
    # One --label flag per label (=form), not a comma-joined value.
    assert "--label=state/raw" in create_cmd
    assert "--label=claimed/product-manager" in create_cmd


def test_create_issue_raises_on_unexpected_gh_output() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # gh succeeded but returned nothing parseable.
    responses = [
        _proc(stdout=""),  # ensure_label
        _proc(stdout="(some non-URL response)\n"),
    ]
    with (
        mock.patch(
            "workflow.backends.github.subprocess.run",
            side_effect=_fake_run_factory(responses),
        ),
        pytest.raises(BackendError),
    ):
        backend.create_issue(title="X", body="", state="raw")


def test_read_issue_translates_labels() -> None:
    backend = GitHubBackend(repo="owner/repo")
    issue_payload = {
        "number": 1,
        "labels": [
            {"name": "state/refining"},
            {"name": "claimed/product-manager"},
            {"name": "hitl-blocked/ready_for_dev"},
            {"name": "type/feat"},
        ],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
    }
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(issue_payload)),
    ) as patched:
        state = backend.read_issue("1")

    assert state.state == "refining"
    assert state.agent_claim == "product-manager"
    assert state.awaiting_gate == "ready_for_dev"
    # The actual call was a single `gh issue view`.
    cmd = patched.call_args[0][0]
    assert cmd[0] == "gh"
    assert "view" in cmd
    assert "--repo" in cmd
    assert "owner/repo" in cmd


def test_read_issue_parses_full_marker_set() -> None:
    """read_issue resolves every classifier into the IssueState fields (#68)."""
    backend = GitHubBackend(repo="owner/repo")
    payload = {
        "number": 5,
        "labels": [
            {"name": "state/refining"},
            {"name": "claimed/product-manager"},
            {"name": "hitl-blocked/ready_for_dev"},
            {"name": "hitl-audit/ship"},
            {"name": "hitl-input/scope"},
            {"name": "type/bug"},
            {"name": "child-of/100"},
        ],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
    }
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(payload)),
    ):
        state = backend.read_issue("5")

    assert state.state == "refining"
    assert state.agent_claim == "product-manager"
    assert state.awaiting_gate == "ready_for_dev"
    assert state.audit_pending == "ship"
    assert state.awaiting_input is True
    assert state.human_input == "scope"
    assert state.issue_type == "bug"
    assert state.child_of == "100"


def _apply_run_factory(pre_labels: list[str]):
    """side_effect for apply_marker_change: pre-state for any view, "" otherwise."""
    pre = {
        "number": 1,
        "labels": [{"name": n} for n in pre_labels],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
    }

    def _run(*args, **kwargs):
        cmd = args[0]
        if "view" in cmd:
            return _proc(stdout=json.dumps(pre))
        return _proc(stdout="")

    return _run


def test_request_input_emits_single_merged_hitl_input_label() -> None:
    """request-input writes one `hitl-input/<topic>` carrying both the queue
    marker and the topic — not a separate awaiting-input + topic pair (#68)."""
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_apply_run_factory(["state/refining"]),
    ) as patched:
        backend.apply_marker_change(
            "1",
            MarkerChange(set_awaiting_input=True, set_human_input="scope"),
            audit_comment="## request-input: scope",
        )
    edit_cmd = [
        c.args[0] for c in patched.call_args_list if "edit" in c.args[0] and "issue" in c.args[0]
    ][0]
    assert "--add-label=hitl-input/scope" in edit_cmd
    # Exactly one hitl-input label — the queue marker and topic are merged.
    add_input = [a for a in edit_cmd if a.startswith("--add-label=hitl-input/")]
    assert add_input == ["--add-label=hitl-input/scope"]


def test_respond_removes_merged_hitl_input_label() -> None:
    """respond clears the merged `hitl-input/<topic>` and the advising claim,
    and drops a `hitl-signal/resolved` breadcrumb (#68)."""
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_apply_run_factory(
            ["state/refining", "hitl-input/scope", "hitl-claim/advising"]
        ),
    ) as patched:
        backend.apply_marker_change(
            "1",
            MarkerChange(
                set_awaiting_input=False,
                set_advising=False,
                clear_human_input=True,
                record_response=True,
            ),
            audit_comment="## resolve",
        )
    edit_cmd = [
        c.args[0] for c in patched.call_args_list if "edit" in c.args[0] and "issue" in c.args[0]
    ][0]
    assert "--remove-label=hitl-input/scope" in edit_cmd
    assert "--remove-label=hitl-claim/advising" in edit_cmd
    assert "--add-label=hitl-signal/resolved" in edit_cmd


def test_transition_swaps_state_label() -> None:
    """Advancing swaps the single state label: add the new, remove the old."""
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_apply_run_factory(["state/refining", "claimed/product-manager"]),
    ) as patched:
        backend.apply_marker_change("1", MarkerChange(set_state="ready_for_dev"))
    edit_cmd = [
        c.args[0] for c in patched.call_args_list if "edit" in c.args[0] and "issue" in c.args[0]
    ][0]
    assert "--add-label=state/ready_for_dev" in edit_cmd
    assert "--remove-label=state/refining" in edit_cmd
    # The untouched claim is left alone.
    assert not any("--remove-label=claimed/product-manager" in a for a in edit_cmd)


def test_apply_marker_change_constructs_add_remove_labels() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # Pre-state: state/refining + claimed/product-manager.
    pre = {
        "labels": [
            {"name": "state/refining"},
            {"name": "claimed/product-manager"},
        ],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
        "number": 1,
    }
    # apply_marker_change reads pre-state, then writes labels.
    # Sequence of calls:
    #   1. gh issue view (read pre)
    #   2. gh label create (lazy; one per label being added — we'll get 1)
    #   3. gh issue comment (audit comment)
    #   4. gh issue edit (add/remove labels)
    responses = [
        _proc(stdout=json.dumps(pre)),  # read pre-state
        _proc(stdout=""),  # label create (state/ready_for_dev)
        _proc(stdout=""),  # gh issue comment
        _proc(stdout=""),  # gh issue edit
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        backend.apply_marker_change(
            "1",
            MarkerChange(set_state="ready_for_dev"),
            audit_comment="## advance: refining → ready_for_dev",
        )

    # Inspect every shell call.
    calls = [args.args[0] for args in patched.call_args_list]
    assert any(c[0] == "gh" and "view" in c for c in calls)
    assert any(c[0] == "gh" and "label" in c and "create" in c for c in calls)
    assert any(c[0] == "gh" and "comment" in c for c in calls)
    edit_calls = [c for c in calls if c[0] == "gh" and "edit" in c and "issue" in c]
    assert edit_calls
    edit_cmd = edit_calls[0]
    # Expected add/remove flags (=form, one per label): the state label swaps,
    # so the issue ends with exactly one state label.
    assert "--add-label=state/ready_for_dev" in edit_cmd
    assert "--remove-label=state/refining" in edit_cmd


def test_apply_marker_change_singletons_and_audit() -> None:
    backend = GitHubBackend(repo="owner/repo")
    pre = {
        "labels": [
            {"name": "state/refining"},
            {"name": "claimed/product-manager"},
            {"name": "hitl-blocked/ready_for_dev"},
        ],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
        "number": 1,
    }
    # approve: set_state, clear_awaiting_gate, set_reviewing=False, record_approval
    responses = [
        _proc(stdout=json.dumps(pre)),  # read pre-state
        _proc(stdout=""),  # label create (state/ready_for_dev)
        _proc(stdout=""),  # label create (hitl-signal/approved)
        _proc(stdout=""),  # comment
        _proc(stdout=""),  # edit
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        backend.apply_marker_change(
            "1",
            MarkerChange(
                set_state="ready_for_dev",
                clear_awaiting_gate=True,
                record_approval="ready_for_dev",
            ),
            audit_comment="## approve",
        )

    calls = [args.args[0] for args in patched.call_args_list]
    edit_cmd = [c for c in calls if "edit" in c and "issue" in c][0]
    assert "--add-label=state/ready_for_dev" in edit_cmd
    assert "--add-label=hitl-signal/approved" in edit_cmd
    assert "--remove-label=state/refining" in edit_cmd
    assert "--remove-label=hitl-blocked/ready_for_dev" in edit_cmd


def test_apply_marker_change_clear_claim_does_not_unassign_without_mapping() -> None:
    backend = GitHubBackend(repo="owner/repo")
    pre = {
        "labels": [
            {"name": "state/refining"},
            {"name": "claimed/product-manager"},
        ],
        "assignees": [{"login": "alice"}],
        "state": "OPEN",
        "comments": [],
        "number": 1,
    }
    responses = [
        _proc(stdout=json.dumps(pre)),  # read pre-state
        _proc(stdout=""),  # edit labels
        _proc(stdout=json.dumps({"assignees": [{"login": "alice"}]})),
        _proc(stdout=""),  # old buggy remove-assignee call
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        backend.apply_marker_change("1", MarkerChange(clear_agent_claim=True))

    calls = [args.args[0] for args in patched.call_args_list]
    assert any("--remove-label=claimed/product-manager" in c for c in calls)
    assert not any("--remove-assignee" in c for c in calls)


def test_post_comment_uses_body_file() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=""),
    ) as patched:
        backend.post_comment("1", "body content")

    cmd = patched.call_args[0][0]
    assert "comment" in cmd
    assert "--body-file" in cmd
    # The body-file value is a temp path created by the backend.


def test_assign_and_unassign_without_mapping() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # assign:
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=""),
    ) as patched:
        backend.assign("1", "alice")
    cmd = patched.call_args[0][0]
    assert "--add-assignee" in cmd and "alice" in cmd

    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=""),
    ) as patched:
        backend.unassign("1")
    patched.assert_not_called()


def test_resolve_role_returns_none_by_default() -> None:
    backend = GitHubBackend(repo="owner/repo")
    assert backend.resolve_role("product-manager") is None


def test_list_issues_translates_filters_to_label_flags() -> None:
    backend = GitHubBackend(repo="owner/repo")
    fake_response = [
        {
            "number": 7,
            "title": "Some issue",
            "labels": [
                {"name": "state/refining"},
                {"name": "claimed/product-manager"},
            ],
        }
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(fake_response)),
    ) as patched:
        results = backend.list_issues(
            IssueFilters(state="refining", claim_role="product-manager", limit=20)
        )

    # Both `gh issue list` and `gh pr list` are queried and merged.
    calls = [c.args[0] for c in patched.call_args_list]
    issue_cmd = next(c for c in calls if "issue" in c and "list" in c)
    pr_cmd = next(c for c in calls if "pr" in c and "list" in c)
    assert issue_cmd[0] == "gh"
    # Both query every state so closed issues / PRs are visible.
    assert issue_cmd[issue_cmd.index("--state") + 1] == "all"
    assert pr_cmd[pr_cmd.index("--state") + 1] == "all"
    # Filters become --label entries (identical on both queries).
    label_indices = [i for i, x in enumerate(issue_cmd) if x == "--label"]
    label_values = [issue_cmd[i + 1] for i in label_indices]
    assert "state/refining" in label_values
    assert "claimed/product-manager" in label_values
    # Limit is respected.
    assert "--limit" in issue_cmd
    assert issue_cmd[issue_cmd.index("--limit") + 1] == "20"
    # Result is parsed into IssueState with the title in extras (deduped to one).
    assert len(results) == 1
    assert results[0].issue_id == "7"
    assert results[0].state == "refining"
    assert results[0].agent_claim == "product-manager"
    assert results[0].extras.get("title") == "Some issue"


def test_list_issues_wildcard_awaiting_post_filters() -> None:
    """`--awaiting-gate '*'` can't be expressed in a single gh label filter;
    the backend fetches and post-filters in Python."""
    backend = GitHubBackend(repo="owner/repo")
    fake_response = [
        {
            "number": 1,
            "title": "has awaiting",
            "labels": [
                {"name": "state/refining"},
                {"name": "hitl-blocked/ready_for_dev"},
            ],
        },
        {
            "number": 2,
            "title": "no awaiting",
            "labels": [{"name": "state/refining"}],
        },
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(fake_response)),
    ):
        results = backend.list_issues(IssueFilters(awaiting_gate="*"))

    # Only the one with an awaiting marker comes through.
    assert len(results) == 1
    assert results[0].issue_id == "1"
    assert results[0].awaiting_gate == "ready_for_dev"


def test_list_issues_returns_empty_for_no_matches() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout="[]"),
    ):
        results = backend.list_issues(IssueFilters(state="refining"))
    assert results == []


def test_list_issues_merges_prs_and_queries_all_states() -> None:
    """list_issues queries `gh issue list` and `gh pr list`, both with
    --state all, and merges — so closed issues and PRs are both visible."""
    backend = GitHubBackend(repo="owner/repo")
    issue_response = [
        {"number": 1, "title": "a closed issue", "labels": [{"name": "state/shipped"}]},
    ]
    pr_response = [
        {"number": 2, "title": "a merged PR", "labels": [{"name": "state/merged"}]},
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(
            [
                _proc(stdout=json.dumps(issue_response)),  # gh issue list
                _proc(stdout=json.dumps(pr_response)),  # gh pr list
            ]
        ),
    ) as patched:
        results = backend.list_issues(IssueFilters(limit=50))

    # Both the (closed) issue and the (merged) PR come through.
    assert sorted(r.issue_id for r in results) == ["1", "2"]
    calls = [c.args[0] for c in patched.call_args_list]
    assert any(c[0] == "gh" and "issue" in c and "list" in c for c in calls)
    assert any(c[0] == "gh" and "pr" in c and "list" in c for c in calls)
    # Every list query asks for all states (open + closed + merged).
    for c in calls:
        assert c[c.index("--state") + 1] == "all"


def test_list_issues_cohort_query_by_child_of_returns_closed_and_pr_children() -> None:
    """A cohort query (`child-of/<id>`) finds children regardless of whether
    they are closed issues or PRs — wait-for-all depends on this (ADR-0003)."""
    backend = GitHubBackend(repo="owner/repo")
    closed_child = [
        {"number": 11, "title": "closed child", "labels": [{"name": "child-of/100"}]},
    ]
    pr_child = [
        {"number": 12, "title": "PR child", "labels": [{"name": "child-of/100"}]},
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(
            [
                _proc(stdout=json.dumps(closed_child)),  # gh issue list
                _proc(stdout=json.dumps(pr_child)),  # gh pr list
            ]
        ),
    ) as patched:
        results = backend.list_issues(IssueFilters(child_of="100"))

    assert sorted(r.issue_id for r in results) == ["11", "12"]
    # The cohort label is passed to both queries.
    for c in [c.args[0] for c in patched.call_args_list]:
        label_vals = [c[i + 1] for i, x in enumerate(c) if x == "--label"]
        assert "child-of/100" in label_vals


# --------------------------------------------------------------------------- #
# inspector.inbox_for_role tests (registry-aware read-only query; mocking
# subprocess.run lets us exercise it end-to-end against the real backend).


def _fake_registry(workflow, name="test"):
    """Build a mock registry that returns the given workflow from get_process."""
    registry = mock.Mock()
    registry.discovered_processes.return_value = [name]
    registry.get_process.return_value = mock.Mock(state_machine=workflow, catalog=None)
    return registry


def test_list_for_role_returns_inbox_and_actionable_wip() -> None:
    from workflow.core.inspector import inbox_for_role
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        # 'raw' is product-manager's inbox because the CLAIM transition from
        # it lands on 'refining', whose `roles` includes product-manager.
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]

    backend = GitHubBackend(repo="owner/repo")
    # The helper queries the backend two ways for role "product-manager":
    #   1. Inbox: state=raw (then filter to unclaimed)
    #   2. Actionable wip: claim_role=product-manager label (then filter to no
    #      awaiting markers)
    inbox_response = [
        {"number": 10, "title": "raw unclaimed", "labels": [{"name": "state/raw"}]},
        # This one is already claimed by someone else — must be excluded.
        {
            "number": 11,
            "title": "raw claimed",
            "labels": [{"name": "state/raw"}, {"name": "claimed/peer-reviewer"}],
        },
    ]
    wip_response = [
        # Actionable: claimed/product-manager with no HITL markers.
        {
            "number": 20,
            "title": "wip actionable",
            "labels": [{"name": "state/refining"}, {"name": "claimed/product-manager"}],
        },
        # Blocked: claimed/product-manager with hitl-blocked/ready_for_dev — must be excluded.
        {
            "number": 21,
            "title": "wip blocked",
            "labels": [
                {"name": "state/refining"},
                {"name": "claimed/product-manager"},
                {"name": "hitl-blocked/ready_for_dev"},
            ],
        },
    ]

    # Pass the fake registry straight into the inspector query.
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(
            [
                # list_issues(state=raw): gh issue list, then gh pr list.
                _proc(stdout=json.dumps(inbox_response)),
                _proc(stdout="[]"),
                # list_issues(claim_role=...): gh issue list, then gh pr list.
                _proc(stdout=json.dumps(wip_response)),
                _proc(stdout="[]"),
            ]
        ),
    ):
        results = inbox_for_role(
            _fake_registry(workflow),
            backend,
            "product-manager",
            50,
        )

    ids = sorted(item.issue_id for item in results)
    # 10 (raw, unclaimed): in inbox.
    # 20 (claimed/product-manager, no HITL): actionable wip.
    # 11 (claimed/peer-reviewer): excluded — wrong claim role.
    # 21 (claimed/product-manager, awaiting gate): excluded — blocked.
    assert ids == ["10", "20"]


def test_list_for_role_no_inbox_states_when_no_working_state_accepts_role() -> None:
    """If no working state's `roles` includes the queried role, the role has
    no inbox in this workflow — only the wip filter contributes."""
    from workflow.core.inspector import inbox_for_role
    from workflow.core.model.state_machine import (
        State,
        StateClass,
        StateMachine,
        Transition,
        TransitionType,
    )

    workflow = StateMachine(name="t")
    workflow.states = {
        # The only working state accepts product-manager, not developer.
        "raw": State(name="raw", state_class=StateClass.RESTING),
        "refining": State(
            name="refining",
            state_class=StateClass.WORKING,
            roles=("product-manager",),
        ),
    }
    workflow.transitions = [
        Transition(
            source="raw",
            destination="refining",
            label="pm claims raw",
            transition_type=TransitionType.CLAIM,
        ),
    ]

    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        # Only one subprocess.run call: the claimed/developer filter
        # (no inbox states means no per-state backend calls).
        return_value=_proc(stdout=json.dumps([])),
    ):
        results = inbox_for_role(
            _fake_registry(workflow),
            backend,
            "developer",
            50,
        )

    assert results == []


def test_registry_discovers_all_workflows(workflow_dir) -> None:
    """The registry finds every `*-states.json` in the workflows dir."""
    from workflow.config import build_registry

    r = build_registry(workflow_dir=workflow_dir)
    assert r is not None
    names = r.discovered_processes()
    # The example ships refinement + inner-loop.
    assert "refinement" in names
    assert "inner-loop" in names


def test_gh_invocation_passes_through_host_as_env_var() -> None:
    """When the backend has a host configured (GHES), every `gh` subprocess
    runs with GH_HOST=<host> in its env."""
    backend = GitHubBackend(repo="owner/repo", host="ghe.example.com")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps({"number": 1, "labels": []})),
    ) as patched:
        backend.read_issue("1")

    _, kwargs = patched.call_args
    env = kwargs.get("env")
    assert env is not None, "host should cause subprocess.run to receive an env dict"
    assert env.get("GH_HOST") == "ghe.example.com"
    # Other env vars (PATH, HOME, etc.) are preserved.
    assert "PATH" in env


def test_gh_invocation_inherits_env_when_no_host_set() -> None:
    """Without a configured host, the backend doesn't touch the env — gh
    inherits whatever the parent process has, so the user's exported
    GH_HOST or gh auth config wins."""
    backend = GitHubBackend(repo="owner/repo")  # no host
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps({"number": 1, "labels": []})),
    ) as patched:
        backend.read_issue("1")

    _, kwargs = patched.call_args
    # env=None means the subprocess inherits the parent's environment intact.
    assert kwargs.get("env") is None


def test_parse_git_remote_url_https() -> None:
    from workflow.backends.github import parse_git_remote_url

    assert parse_git_remote_url("https://github.com/owner/repo.git") == (
        "github.com",
        "owner/repo",
    )
    assert parse_git_remote_url("https://github.com/owner/repo") == (
        "github.com",
        "owner/repo",
    )
    # GHES via HTTPS, with embedded credentials.
    assert parse_git_remote_url("https://user@ghe.acme.com/myorg/widget.git") == (
        "ghe.acme.com",
        "myorg/widget",
    )


def test_parse_git_remote_url_ssh_scp_style() -> None:
    from workflow.backends.github import parse_git_remote_url

    assert parse_git_remote_url("git@github.com:owner/repo.git") == (
        "github.com",
        "owner/repo",
    )
    # GHES via SSH shorthand.
    assert parse_git_remote_url("git@ghe.acme.com:myorg/widget.git") == (
        "ghe.acme.com",
        "myorg/widget",
    )
    # Trailing slash, no .git suffix.
    assert parse_git_remote_url("git@github.com:owner/repo/") == (
        "github.com",
        "owner/repo",
    )


def test_parse_git_remote_url_ssh_with_scheme_and_port() -> None:
    from workflow.backends.github import parse_git_remote_url

    assert parse_git_remote_url("ssh://git@ghe.acme.com:22/myorg/widget.git") == (
        "ghe.acme.com",
        "myorg/widget",
    )
    assert parse_git_remote_url("git://github.com/owner/repo.git") == (
        "github.com",
        "owner/repo",
    )


def test_parse_git_remote_url_rejects_nonsense() -> None:
    from workflow.backends.github import parse_git_remote_url

    assert parse_git_remote_url("") is None
    assert parse_git_remote_url("not a url") is None
    assert parse_git_remote_url("https://github.com/just-one-segment") is None


def test_discover_remote_from_git_parses_origin() -> None:
    from workflow.backends.github import discover_remote_from_git

    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout="git@ghe.acme.com:myorg/widget.git\n"),
    ) as patched:
        host, slug = discover_remote_from_git()

    assert host == "ghe.acme.com"
    assert slug == "myorg/widget"
    # Confirms we asked git, not gh.
    cmd = patched.call_args[0][0]
    assert cmd[0] == "git"
    assert cmd[1:] == ["remote", "get-url", "origin"]


def test_discover_remote_from_git_returns_none_when_not_a_repo() -> None:
    """git exits non-zero when cwd isn't a repo or origin doesn't exist."""
    from workflow.backends.github import discover_remote_from_git

    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(
            returncode=128,
            stderr="fatal: not a git repository",
        ),
    ):
        assert discover_remote_from_git() == (None, None)


def test_discover_remote_from_git_returns_none_when_git_missing() -> None:
    from workflow.backends.github import discover_remote_from_git

    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    ):
        assert discover_remote_from_git() == (None, None)


def test_discover_remote_from_git_returns_none_for_unparseable_url() -> None:
    """A non-github-shaped remote URL returns (None, None) silently."""
    from workflow.backends.github import discover_remote_from_git

    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout="file:///some/local/path\n"),
    ):
        assert discover_remote_from_git() == (None, None)


def test_list_labels_returns_names_and_seeds_cache() -> None:
    backend = GitHubBackend(repo="owner/repo")
    fake_response = [
        {"name": "state/raw"},
        {"name": "claimed/product-manager"},
        {"name": "hitl-claim/reviewing"},
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(fake_response)),
    ) as patched:
        names = backend.list_labels()

    cmd = patched.call_args[0][0]
    assert cmd[0] == "gh"
    assert "label" in cmd and "list" in cmd
    assert "--repo" in cmd and "owner/repo" in cmd
    assert sorted(names) == ["claimed/product-manager", "hitl-claim/reviewing", "state/raw"]
    # Cache was seeded so subsequent ensure_label calls are no-ops.
    assert "state/raw" in backend._known_labels
    assert "claimed/product-manager" in backend._known_labels


def test_ensure_label_creates_missing() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=""),
    ) as patched:
        created = backend.ensure_label("state/new_state")

    assert created is True
    cmd = patched.call_args[0][0]
    assert cmd[0] == "gh"
    assert "label" in cmd and "create" in cmd
    assert "state/new_state" in cmd
    assert "--color" in cmd
    # Color came from the state namespace default (blue).
    color_idx = cmd.index("--color") + 1
    assert cmd[color_idx] == "1f6feb"
    # No --force: setup-github preserves user customizations on existing labels.
    assert "--force" not in cmd


def test_ensure_label_is_idempotent_via_cache() -> None:
    backend = GitHubBackend(repo="owner/repo")
    backend._known_labels.add("state/raw")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
    ) as patched:
        created = backend.ensure_label("state/raw")

    assert created is False
    assert not patched.called  # cache hit, no subprocess call


def test_ensure_label_handles_already_exists_from_gh() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(
            returncode=1,
            stderr="HTTP 422: Validation failed (label already exists)",
        ),
    ):
        created = backend.ensure_label("state/raw")

    # `gh` reported "already exists"; the method treats that as benign.
    assert created is False
    assert "state/raw" in backend._known_labels


def test_registry_find_workflow_for_state(workflow_dir) -> None:
    """find_process_for_state resolves a state to its owning workflow.

    The framework's state-name uniqueness invariant means each state belongs
    to exactly one workflow."""
    from workflow.config import build_registry

    r = build_registry(workflow_dir=workflow_dir)
    assert r is not None
    # 'refining' is a refinement-workflow state.
    found = r.find_process_for_state("refining")
    assert found == "refinement", f"expected refinement, got {found!r}"


def test_registry_rejects_non_handoff_state_name_collision(tmp_path) -> None:
    from workflow.config import build_registry
    from workflow.errors import ConfigError

    workflow_a = {
        "states": {
            "shared": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "issue_types": ["bug"],
            },
            "working_a": {
                "class": "working",
                "roles": ["developer"],
                "issue_types": ["bug"],
            },
        },
        "transitions": [
            {
                "source": "shared",
                "destination": "working_a",
                "type": "claim",
                "label": "developer claims shared",
            }
        ],
    }
    workflow_b = {
        "states": {
            "shared": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "issue_types": ["bug"],
            },
            "working_b": {
                "class": "working",
                "roles": ["developer"],
                "issue_types": ["bug"],
            },
        },
        "transitions": [
            {
                "source": "shared",
                "destination": "working_b",
                "type": "claim",
                "label": "developer claims shared",
            }
        ],
    }
    (tmp_path / "a-states.json").write_text(json.dumps(workflow_a), encoding="utf-8")
    (tmp_path / "b-states.json").write_text(json.dumps(workflow_b), encoding="utf-8")

    registry = build_registry(workflow_dir=tmp_path)
    assert registry is not None
    registry.get_process("a")
    with pytest.raises(
        ConfigError, match="Duplicate state names|Duplicate state name|declared by both"
    ):
        registry.get_process("b")


def test_discover_workflows_dir_uses_agent_home(tmp_path) -> None:
    """Discovery returns `<agent-home>/.workflow/workflows/` when it exists."""
    from workflow.config import discover_workflows_dir

    agent_home = tmp_path / "agent"
    agent_workflows = agent_home / ".workflow" / "workflows"
    agent_workflows.mkdir(parents=True)
    (agent_workflows / "stub-states.mermaid").write_text("stateDiagram-v2\n")

    found = discover_workflows_dir(agent_home=agent_home)
    assert found == agent_workflows


def test_discover_workflows_dir_returns_none_without_agent_or_env(tmp_path, monkeypatch) -> None:
    """Without WORKFLOW_DIR env and without an agent-home/.workflow/workflows/
    directory, discovery returns None. There's no cwd walk-up fallback —
    workflows are agent-scoped, not checkout-scoped."""
    from workflow.config import discover_workflows_dir

    monkeypatch.delenv("WORKFLOW_DIR", raising=False)

    # Agent home with no workflows/ subdir.
    agent_home = tmp_path / "agent"
    (agent_home / ".workflow").mkdir(parents=True)

    assert discover_workflows_dir(agent_home=agent_home) is None
    # Also None when no agent_home is passed at all.
    assert discover_workflows_dir() is None


def test_discover_workflows_dir_honors_config_key(tmp_path, monkeypatch) -> None:
    """The `workflow-dir` key in agent config wins over the default
    `<agent-home>/.workflow/workflows/` location."""
    from workflow.config import discover_workflows_dir

    monkeypatch.delenv("WORKFLOW_DIR", raising=False)

    agent_home = tmp_path / "agent"
    # The default location also exists (would be picked up otherwise).
    (agent_home / ".workflow" / "workflows").mkdir(parents=True)

    # But config points elsewhere.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = {"workflow-dir": str(elsewhere)}

    found = discover_workflows_dir(agent_home=agent_home, agent_config=config)
    assert found == elsewhere


def test_discover_workflows_dir_resolves_relative_config_path(tmp_path, monkeypatch) -> None:
    """Relative paths in the config key are anchored to the agent home."""
    from workflow.config import discover_workflows_dir

    monkeypatch.delenv("WORKFLOW_DIR", raising=False)

    agent_home = tmp_path / "agent"
    target = agent_home / "shared" / "workflows"
    target.mkdir(parents=True)
    config = {"workflow-dir": "shared/workflows"}

    found = discover_workflows_dir(agent_home=agent_home, agent_config=config)
    assert found == target.resolve()


def test_discover_grants_dir_honors_config_key(tmp_path, monkeypatch) -> None:
    """The `grants-dir` key in agent config wins over the default location."""
    from workflow.config import discover_grants_dir

    monkeypatch.delenv("GRANTS_DIR", raising=False)

    agent_home = tmp_path / "agent"
    (agent_home / ".workflow" / "trust-grants").mkdir(parents=True)

    elsewhere = tmp_path / "shared-grants"
    elsewhere.mkdir()
    config = {"grants-dir": str(elsewhere)}

    found = discover_grants_dir(agent_home=agent_home, agent_config=config)
    assert found == elsewhere


def test_discover_grants_dir_env_var_wins_over_config(tmp_path, monkeypatch) -> None:
    """GRANTS_DIR env beats the agent config key."""
    from workflow.config import discover_grants_dir

    env_dir = tmp_path / "env-grants"
    env_dir.mkdir()
    monkeypatch.setenv("GRANTS_DIR", str(env_dir))

    config_dir = tmp_path / "config-grants"
    config_dir.mkdir()
    agent_home = tmp_path / "agent"
    agent_home.mkdir()
    config = {"grants-dir": str(config_dir)}

    found = discover_grants_dir(agent_home=agent_home, agent_config=config)
    assert found == env_dir.resolve()


def test_discover_workflows_dir_respects_env_var(tmp_path, monkeypatch) -> None:
    """WORKFLOW_DIR env var beats agent-home discovery."""
    from workflow.config import discover_workflows_dir

    # Set up an agent-home workflows dir (would be discovered if env unset).
    agent_home = tmp_path / "agent"
    (agent_home / ".workflow" / "workflows").mkdir(parents=True)

    # And a separate env-pointed dir.
    env_dir = tmp_path / "elsewhere"
    env_dir.mkdir()
    monkeypatch.setenv("WORKFLOW_DIR", str(env_dir))

    found = discover_workflows_dir(agent_home=agent_home)
    assert found == env_dir.resolve()


def test_apply_marker_change_partial_failure_raises_repair_error() -> None:
    """If a follow-up step fails after the label swap, the issue is partially
    applied — the backend raises a clear repair error, not a bare one (#20)."""
    backend = GitHubBackend(repo="owner/repo")
    pre = {
        "labels": [{"name": "state/implementing"}],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
        "number": 1,
    }

    def _run(*args, **kwargs):
        cmd = args[0]
        if "view" in cmd:
            return _proc(stdout=json.dumps(pre))
        return _proc(stdout="")  # label create / edit all succeed

    with (
        mock.patch("workflow.backends.github.subprocess.run", side_effect=_run),
        mock.patch.object(GitHubBackend, "close_issue", side_effect=BackendError("close boom")),
        pytest.raises(BackendError, match="partially applied"),
    ):
        backend.apply_marker_change(
            "1",
            MarkerChange(set_state="shipped", close_issue=True, close_reason="completed"),
            audit_comment="## advance",
        )


def test_close_issue_rejects_unsupported_reason() -> None:
    """An unrecognised close reason is rejected loudly, not silently dropped (#27)."""
    backend = GitHubBackend(repo="owner/repo")
    with pytest.raises(BackendError, match="Unsupported close reason"):
        backend.close_issue("1", reason="iterated")


def test_list_issue_types_paginates() -> None:
    """list_issue_types follows pagination so >30 org types aren't truncated (#27)."""
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps([{"name": "Bug"}, {"name": "Feature"}])),
    ) as patched:
        result = backend.list_issue_types("acme")
    assert result == ["Bug", "Feature"]
    assert "--paginate" in patched.call_args[0][0]


def test_create_issue_dash_title_not_parsed_as_flag() -> None:
    """A title starting with '-' is passed as --title=<value>, not a bare arg (#27)."""
    backend = GitHubBackend(repo="owner/repo")
    responses = [
        _proc(stdout=""),  # ensure state/raw
        _proc(stdout="https://github.com/owner/repo/issues/9\n"),
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        backend.create_issue(title="--oops looks like a flag", body="", state="raw")
    create_cmd = [c.args[0] for c in patched.call_args_list if "create" in c.args[0]][-1]
    assert "--title=--oops looks like a flag" in create_cmd


def test_list_issues_warns_when_post_filter_hits_limit(caplog) -> None:
    """A wildcard gate filter is applied after the `--limit` cap; if the raw
    fetch filled the cap, matches beyond it were missed — warn (#26)."""
    import logging

    backend = GitHubBackend(repo="owner/repo")
    # Two issues, both awaiting some gate, returned at limit=2.
    issues = [
        {"number": 1, "labels": [{"name": "hitl-blocked/g1"}], "title": "a", "state": "OPEN"},
        {"number": 2, "labels": [{"name": "hitl-blocked/g2"}], "title": "b", "state": "OPEN"},
    ]

    def _run(*args, **kwargs):
        cmd = args[0]
        if "issue" in cmd and "list" in cmd:
            return _proc(stdout=json.dumps(issues))
        return _proc(stdout=json.dumps([]))  # pr list

    with (
        mock.patch("workflow.backends.github.subprocess.run", side_effect=_run),
        caplog.at_level(logging.WARNING),
    ):
        results = backend.list_issues(IssueFilters(awaiting_gate="*", limit=2))

    assert len(results) == 2
    assert any("may be missed" in r.message for r in caplog.records)


def test_list_issues_no_warning_without_post_filter(caplog) -> None:
    """Hitting the limit with no post-filter is the expected top-N behavior — no
    truncation warning (#26)."""
    import logging

    backend = GitHubBackend(repo="owner/repo")
    issues = [
        {"number": 1, "labels": [{"name": "state/raw"}], "title": "a", "state": "OPEN"},
        {"number": 2, "labels": [{"name": "state/raw"}], "title": "b", "state": "OPEN"},
    ]

    def _run(*args, **kwargs):
        cmd = args[0]
        if "issue" in cmd and "list" in cmd:
            return _proc(stdout=json.dumps(issues))
        return _proc(stdout=json.dumps([]))

    with (
        mock.patch("workflow.backends.github.subprocess.run", side_effect=_run),
        caplog.at_level(logging.WARNING),
    ):
        backend.list_issues(IssueFilters(state="raw", limit=2))

    assert not any("may be missed" in r.message for r in caplog.records)


def _claim_run_factory(verify_labels: list[str], pre_labels: list[str] | None = None):
    """side_effect for a claim apply: read_issue vs the post-write verify fetch
    are distinguished by the `--json` field set (`labels` only = the verify)."""
    pre = {
        "number": 1,
        "labels": [{"name": lbl} for lbl in (pre_labels or ["state/raw"])],
        "assignees": [],
        "state": "OPEN",
    }

    def _run(*args, **kwargs):
        cmd = args[0]
        if "view" in cmd:
            json_fields = cmd[cmd.index("--json") + 1]
            if json_fields == "labels":
                return _proc(stdout=json.dumps({"labels": [{"name": x} for x in verify_labels]}))
            return _proc(stdout=json.dumps(pre))
        return _proc(stdout="")

    return _run


def test_claim_race_lost_self_reverts_and_raises() -> None:
    """A concurrent claim lands a second claim label; the loser sees both, removes
    its own, and raises OperationError instead of believing it won (#21)."""
    backend = GitHubBackend(repo="owner/repo")
    # The live labels show two claims (a concurrent agent landed one); the claim
    # parser counts both as contention.
    run = _claim_run_factory(verify_labels=["claimed/product-manager", "claimed/developer"])
    with (
        mock.patch("workflow.backends.github.subprocess.run", side_effect=run) as patched,
        pytest.raises(OperationError, match="Lost claim race"),
    ):
        backend.apply_marker_change(
            "1", MarkerChange(set_state="refining", set_agent_claim="product-manager")
        )
    # Our own claim label was removed in the self-revert (and nobody else's).
    edits = [c.args[0] for c in patched.call_args_list if "edit" in c.args[0]]
    assert any("--remove-label=claimed/product-manager" in e for e in edits)
    assert not any("--remove-label=claimed/developer" in e for e in edits)


def test_claim_clean_win_no_revert() -> None:
    """When ours is the only claim label after the write, the claim stands and
    no self-revert fires (#21)."""
    backend = GitHubBackend(repo="owner/repo")
    run = _claim_run_factory(verify_labels=["claimed/product-manager"])
    with mock.patch("workflow.backends.github.subprocess.run", side_effect=run) as patched:
        backend.apply_marker_change(
            "1", MarkerChange(set_state="refining", set_agent_claim="product-manager")
        )
    edits = [c.args[0] for c in patched.call_args_list if "edit" in c.args[0]]
    assert not any(any(a.startswith("--remove-label=claimed/") for a in e) for e in edits)


# --------------------------------------------------------------------------- #
# Native GraphQL path (ADR-0005 native tier, #71)


def test_graphql_pipes_body_and_returns_data() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps({"data": {"x": 1}})),
    ) as patched:
        data = backend._graphql("query($n:String!){__type(name:$n){name}}", {"n": "Foo"})

    assert data == {"x": 1}
    _, kwargs = patched.call_args
    # The request body is piped on stdin as {query, variables}.
    body = json.loads(kwargs["input"])
    assert body["variables"] == {"n": "Foo"}
    assert "query" in body
    cmd = patched.call_args[0][0]
    assert cmd[:4] == ["gh", "api", "graphql", "--input"]


def test_graphql_raises_on_errors() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with (
        mock.patch(
            "workflow.backends.github.subprocess.run",
            return_value=_proc(stdout=json.dumps({"errors": [{"message": "boom"}]})),
        ),
        pytest.raises(BackendError, match="GraphQL errors"),
    ):
        backend._graphql("query{viewer{login}}")


def test_org_node_id() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps({"data": {"organization": {"id": "O_abc"}}})),
    ):
        assert backend.org_node_id("blemees") == "O_abc"


def test_list_issue_fields_parses_union_nodes() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    payload = {
        "data": {
            "organization": {
                "issueFields": {
                    "nodes": [
                        {"__typename": "IssueFieldSingleSelect", "name": "Priority"},
                        {"__typename": "IssueFieldText", "name": "Collected By"},
                    ]
                }
            }
        }
    }
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(payload)),
    ):
        assert backend.list_issue_fields("blemees") == ["Priority", "Collected By"]


def test_list_issue_fields_returns_none_on_error() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps({"errors": [{"message": "nope"}]})),
    ):
        assert backend.list_issue_fields("blemees") is None


def test_ensure_issue_field_skips_existing() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    existing = {"data": {"organization": {"issueFields": {"nodes": [{"name": "Agent"}]}}}}
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=json.dumps(existing)),
    ) as patched:
        created = backend.ensure_issue_field("blemees", "Agent", "SINGLE_SELECT", options=["dev"])
    assert created is False
    # Only the list query ran — no org-id lookup, no create mutation.
    assert len(patched.call_args_list) == 1


def test_ensure_issue_field_creates_single_select_with_options() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    responses = [
        _proc(stdout=json.dumps({"data": {"organization": {"issueFields": {"nodes": []}}}})),
        _proc(stdout=json.dumps({"data": {"organization": {"id": "O_1"}}})),
        _proc(stdout=json.dumps({"data": {"createIssueField": {"issueField": {}}}})),
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        created = backend.ensure_issue_field(
            "blemees", "Workflow State", "SINGLE_SELECT", options=["raw", "refining"]
        )
    assert created is True
    create_body = json.loads(patched.call_args_list[-1].kwargs["input"])
    inp = create_body["variables"]["input"]
    assert inp["ownerId"] == "O_1"
    assert inp["name"] == "Workflow State"
    assert inp["dataType"] == "SINGLE_SELECT"
    assert [o["name"] for o in inp["options"]] == ["raw", "refining"]
    assert inp["options"][0]["priority"] == 0 and inp["options"][1]["priority"] == 1


def test_ensure_issue_field_creates_text_without_options() -> None:
    backend = GitHubBackend(repo="blemees/repo")
    responses = [
        _proc(stdout=json.dumps({"data": {"organization": {"issueFields": {"nodes": []}}}})),
        _proc(stdout=json.dumps({"data": {"organization": {"id": "O_1"}}})),
        _proc(stdout=json.dumps({"data": {"createIssueField": {"issueField": {}}}})),
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        created = backend.ensure_issue_field("blemees", "Collected By", "TEXT")
    assert created is True
    inp = json.loads(patched.call_args_list[-1].kwargs["input"])["variables"]["input"]
    assert inp["dataType"] == "TEXT"
    assert "options" not in inp


def test_ensure_issue_field_rejects_empty_single_select() -> None:
    """A single-select with no options can't be created — fail fast (#71 review)."""
    backend = GitHubBackend(repo="blemees/repo")
    with (
        mock.patch(
            "workflow.backends.github.subprocess.run",
            return_value=_proc(
                stdout=json.dumps({"data": {"organization": {"issueFields": {"nodes": []}}}})
            ),
        ),
        pytest.raises(BackendError, match="no options"),
    ):
        backend.ensure_issue_field("blemees", "HITL Blocked", "SINGLE_SELECT", options=[])


def test_list_issue_fields_paginates() -> None:
    """>100 fields: the connection is followed via pageInfo/endCursor (#71 review)."""
    backend = GitHubBackend(repo="blemees/repo")
    page1 = {
        "data": {
            "organization": {
                "issueFields": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                    "nodes": [{"__typename": "IssueFieldText", "name": "A"}],
                }
            }
        }
    }
    page2 = {
        "data": {
            "organization": {
                "issueFields": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"__typename": "IssueFieldText", "name": "B"}],
                }
            }
        }
    }
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(
            [_proc(stdout=json.dumps(page1)), _proc(stdout=json.dumps(page2))]
        ),
    ) as patched:
        assert backend.list_issue_fields("blemees") == ["A", "B"]
    # Second request carried the cursor.
    body2 = json.loads(patched.call_args_list[1].kwargs["input"])
    assert body2["variables"]["after"] == "C1"


# --------------------------------------------------------------------------- #
# Native tier: read/write marker values as Issue Fields (#72/#73)


def _native_backend() -> GitHubBackend:
    """A native-tier backend with field metadata pre-seeded (no load query)."""
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    backend._field_meta = {
        "Workflow State": {"id": "FS", "options": {"raw": "o_raw", "refining": "o_ref"}},
        "Last State": {"id": "LS", "options": {"raw": "o_lraw"}},
        "Agent": {"id": "AG", "options": {"developer": "o_dev", "product-manager": "o_pm"}},
        "HITL Blocked": {"id": "HB", "options": {"ready_for_dev": "o_rfd"}},
        "HITL Audit": {"id": "HA", "options": {"ship": "o_ship"}},
        "HITL Input": {"id": "HI", "options": {"scope": "o_scope"}},
        "HITL Claim": {
            "id": "HC",
            "options": {"reviewing": "o_rev", "auditing": "o_aud", "advising": "o_adv"},
        },
        "HITL Signal": {"id": "HG", "options": {"approved": "o_app", "resolved": "o_res"}},
        "Collected By": {"id": "CB", "options": {}},
    }
    return backend


def test_native_marker_change_to_field_ops_sets_and_clears() -> None:
    backend = _native_backend()
    ops = backend._marker_change_to_field_ops(
        MarkerChange(set_state="refining", set_agent_claim="developer")
    )
    assert {"fieldId": "FS", "singleSelectOptionId": "o_ref"} in ops
    assert {"fieldId": "AG", "singleSelectOptionId": "o_dev"} in ops

    # Clears use the field's delete op.
    assert backend._marker_change_to_field_ops(MarkerChange(clear_agent_claim=True)) == [
        {"fieldId": "AG", "delete": True}
    ]


def test_native_marker_change_claim_and_signal() -> None:
    backend = _native_backend()
    # Reviewing singleton → HITL Claim.
    assert backend._marker_change_to_field_ops(MarkerChange(set_reviewing=True)) == [
        {"fieldId": "HC", "singleSelectOptionId": "o_rev"}
    ]
    # respond: clears advising + input, records resolved signal.
    ops = backend._marker_change_to_field_ops(
        MarkerChange(set_advising=False, clear_human_input=True, record_response=True)
    )
    assert {"fieldId": "HC", "delete": True} in ops
    assert {"fieldId": "HI", "delete": True} in ops
    assert {"fieldId": "HG", "singleSelectOptionId": "o_res"} in ops


def test_native_single_select_errors_are_clear() -> None:
    backend = _native_backend()
    with pytest.raises(BackendError, match="not provisioned"):
        backend._single_select_op("Nonexistent Field", "x")
    with pytest.raises(BackendError, match="no option"):
        backend._single_select_op("Workflow State", "unknown_state")


def test_native_apply_marker_change_sets_field_value() -> None:
    backend = _native_backend()
    with (
        mock.patch.object(
            backend,
            "_read_native",
            return_value=("ISSUE_NODE", IssueState(issue_id="1", state="raw", agent_claim=None)),
        ),
        mock.patch.object(backend, "_graphql") as gql,
    ):
        backend.apply_marker_change("1", MarkerChange(set_state="refining"))
    args, kwargs = gql.call_args
    assert "setIssueFieldValue" in args[0]
    var_input = args[1]["input"]
    assert var_input["issueId"] == "ISSUE_NODE"
    assert var_input["issueFields"] == [{"fieldId": "FS", "singleSelectOptionId": "o_ref"}]


def test_native_read_issue_maps_fields_to_state() -> None:
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    payload = {
        "repository": {
            "issue": {
                "id": "ISSUE_NODE",
                "issueType": {"name": "Bug"},
                "issueFieldValues": {
                    "nodes": [
                        {
                            "__typename": "IssueFieldSingleSelectValue",
                            "name": "refining",
                            "field": {"name": "Workflow State"},
                        },
                        {
                            "__typename": "IssueFieldSingleSelectValue",
                            "name": "developer",
                            "field": {"name": "Agent"},
                        },
                        {
                            "__typename": "IssueFieldSingleSelectValue",
                            "name": "reviewing",
                            "field": {"name": "HITL Claim"},
                        },
                    ]
                },
            }
        }
    }
    with mock.patch.object(backend, "_graphql", return_value=payload):
        state = backend.read_issue("1")
    assert state.state == "refining"
    assert state.agent_claim == "developer"
    assert state.native_issue_type == "Bug"
    assert state.reviewing is True and state.auditing is False


def test_native_create_issue_sets_state_field() -> None:
    backend = _native_backend()
    with (
        mock.patch.object(backend, "_create_bare_issue", return_value="7") as bare,
        mock.patch.object(
            backend,
            "_read_native",
            return_value=("N7", IssueState(issue_id="7", state=None, agent_claim=None)),
        ),
        mock.patch.object(backend, "_graphql") as gql,
    ):
        new_id = backend.create_issue(title="T", body="B", state="raw", issue_type="Bug")
    assert new_id == "7"
    bare.assert_called_once()
    set_input = gql.call_args[0][1]["input"]
    assert {"fieldId": "FS", "singleSelectOptionId": "o_raw"} in set_input["issueFields"]


def test_native_list_issues_builds_search_qualifiers() -> None:
    backend = GitHubBackend(repo="blemees/repo", tier="native")

    def fake_gh(*args, **kwargs):
        # issue list / pr list --search ... --json number
        if "list" in args:
            if "--search" in args:
                search = args[args.index("--search") + 1]
                assert 'field."workflow state":"refining"' in search
            return json.dumps([{"number": 5}]) if args[0] == "issue" else json.dumps([])
        return "{}"

    with (
        mock.patch.object(backend, "_gh", side_effect=fake_gh),
        mock.patch.object(
            backend,
            "_read_native",
            return_value=("N5", IssueState(issue_id="5", state="refining", agent_claim=None)),
        ),
    ):
        results = backend.list_issues(IssueFilters(state="refining"))
    assert [r.issue_id for r in results] == ["5"]


def test_native_list_cohort_filters_build_search_qualifiers() -> None:
    """child_of → parent-issue:; collected_by → field."collected by": (#74)."""
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    captured: list[str] = []

    def fake_gh(*args, **kwargs):
        if "list" in args and "--search" in args:
            captured.append(args[args.index("--search") + 1])
        return "[]"

    with mock.patch.object(backend, "_gh", side_effect=fake_gh):
        backend.list_issues(IssueFilters(child_of="100"))
        backend.list_issues(IssueFilters(collected_by="64"))
    assert any("parent-issue:blemees/repo#100" in s for s in captured)
    assert any('field."collected by":"64"' in s for s in captured)


def test_native_collected_by_writes_text_field() -> None:
    """set_collected_by writes the collector id to the Collected By text field (#74)."""
    backend = _native_backend()
    assert backend._marker_change_to_field_ops(MarkerChange(set_collected_by="42")) == [
        {"fieldId": "CB", "textValue": "42"}
    ]
    assert backend._marker_change_to_field_ops(MarkerChange(clear_collected_by=True)) == [
        {"fieldId": "CB", "delete": True}
    ]


def test_native_clear_unprovisioned_field_fails_fast() -> None:
    """An unprovisioned field must error on clear, not silently drop the op."""
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    backend._field_meta = {}  # nothing provisioned
    with pytest.raises(BackendError, match="not provisioned"):
        backend._marker_change_to_field_ops(MarkerChange(clear_agent_claim=True))


def test_native_claim_release_emits_single_clear() -> None:
    """Release sets all three claim flags False — exactly one HITL Claim delete."""
    backend = _native_backend()
    ops = backend._marker_change_to_field_ops(
        MarkerChange(set_reviewing=False, set_auditing=False, set_advising=False)
    )
    assert ops == [{"fieldId": "HC", "delete": True}]


def test_native_create_links_child_of_as_sub_issue() -> None:
    """A child-of extra-label links the new issue under its parent (addSubIssue, #74)."""
    backend = _native_backend()
    with (
        mock.patch.object(backend, "_create_bare_issue", return_value="9"),
        mock.patch.object(
            backend,
            "_read_native",
            return_value=("CHILD_NODE", IssueState(issue_id="9", state=None, agent_claim=None)),
        ),
        mock.patch.object(backend, "_issue_node_id", return_value="PARENT_NODE") as pnode,
        mock.patch.object(backend, "_graphql") as gql,
    ):
        new_id = backend.create_issue(title="T", body="B", state="raw", extra_labels=["child-of/5"])
    assert new_id == "9"
    pnode.assert_called_once_with("5")
    # One of the GraphQL calls is the addSubIssue link (parent ← child).
    add_calls = [c for c in gql.call_args_list if "addSubIssue" in c.args[0]]
    assert len(add_calls) == 1
    sub_input = add_calls[0].args[1]["input"]
    assert sub_input == {"issueId": "PARENT_NODE", "subIssueId": "CHILD_NODE"}


def test_native_create_failure_names_created_issue() -> None:
    backend = _native_backend()
    with (
        mock.patch.object(backend, "_create_bare_issue", return_value="9"),
        mock.patch.object(backend, "_read_native", side_effect=BackendError("boom")),
        pytest.raises(BackendError, match="#9 was created"),
    ):
        backend.create_issue(title="T", body="B", state="raw")


def test_native_read_maps_parent_and_collected_by() -> None:
    """child_of comes from the sub-issue parent; collected_by from the text field (#74)."""
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    payload = {
        "repository": {
            "issue": {
                "id": "N",
                "issueType": {"name": "Task"},
                "parent": {"number": 64},
                "issueFieldValues": {
                    "nodes": [
                        {
                            "__typename": "IssueFieldSingleSelectValue",
                            "name": "refining",
                            "field": {"name": "Workflow State"},
                        },
                        {
                            "__typename": "IssueFieldTextValue",
                            "value": "100",
                            "field": {"name": "Collected By"},
                        },
                    ]
                },
            }
        }
    }
    with mock.patch.object(backend, "_graphql", return_value=payload):
        state = backend.read_issue("9")
    assert state.state == "refining"
    assert state.child_of == "64"
    assert state.collected_by == "100"


def test_native_read_rejects_non_numeric_issue_id() -> None:
    """A malformed issue id surfaces a BackendError, not a raw ValueError (#74 review)."""
    backend = GitHubBackend(repo="blemees/repo", tier="native")
    with pytest.raises(BackendError, match="numeric issue id"):
        backend.read_issue("not-a-number")


def test_ensure_issue_type_sends_is_enabled_as_typed_boolean() -> None:
    """is_enabled must be a JSON boolean (-F), not the string "true" (-f) —
    the API rejects the string form with HTTP 422."""
    backend = GitHubBackend(repo="blemees/repo")
    responses = [
        _proc(stdout=json.dumps([])),  # list_issue_types (empty → not present)
        _proc(stdout=""),  # create
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        created = backend.ensure_issue_type("blemees", name="Bug", description="d", color="red")
    assert created is True
    create_cmd = patched.call_args_list[-1].args[0]
    assert "-F" in create_cmd
    assert create_cmd[create_cmd.index("-F") + 1] == "is_enabled=true"
    # And it must NOT be passed as a raw -f string field.
    raw_values = [create_cmd[i + 1] for i, x in enumerate(create_cmd) if x == "-f"]
    assert "is_enabled=true" not in raw_values
