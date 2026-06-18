"""GitHub backend tests — mock subprocess.run; verify constructed `gh` commands."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from workflow.backends.base import IssueFilters, MarkerChange
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
    # First call is ensure_label (state:raw); second is gh issue create.
    responses = [
        _proc(stdout=""),  # label create (state:raw)
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
    # and one --label=state:raw flag all present.
    assert "--title=Fix the login bug" in create_cmd
    assert "--body-file" in create_cmd
    assert "--label=state:raw" in create_cmd


def test_create_issue_with_claim_adds_wip_label() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # Two ensure_label calls (state:raw, wip:product-manager) then gh issue create.
    responses = [
        _proc(stdout=""),  # ensure state:raw
        _proc(stdout=""),  # ensure wip:product-manager
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
            extra_labels=["wip:product-manager"],
        )

    assert new_id == "7"
    create_cmd = [c.args[0] for c in patched.call_args_list if "create" in c.args[0]][-1]
    # One --label flag per label (=form), not a comma-joined value.
    assert "--label=state:raw" in create_cmd
    assert "--label=wip:product-manager" in create_cmd


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
            {"name": "state:refining"},
            {"name": "wip:product-manager"},
            {"name": "hitl:awaiting-ready_for_dev"},
            {"name": "type:feat"},
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


def test_apply_marker_change_constructs_add_remove_labels() -> None:
    backend = GitHubBackend(repo="owner/repo")
    # Pre-state: state:refining + wip:product-manager.
    pre = {
        "labels": [
            {"name": "state:refining"},
            {"name": "wip:product-manager"},
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
        _proc(stdout=""),  # label create (state:ready_for_dev)
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
    # Expected add/remove flags (=form, one per label).
    assert "--add-label=state:ready_for_dev" in edit_cmd
    assert "--remove-label=state:refining" in edit_cmd


def test_apply_marker_change_singletons_and_audit() -> None:
    backend = GitHubBackend(repo="owner/repo")
    pre = {
        "labels": [
            {"name": "state:refining"},
            {"name": "wip:product-manager"},
            {"name": "hitl:awaiting-ready_for_dev"},
        ],
        "assignees": [],
        "state": "OPEN",
        "comments": [],
        "number": 1,
    }
    # approve: set_state, clear_awaiting_gate, set_reviewing=False, record_approval
    responses = [
        _proc(stdout=json.dumps(pre)),  # read pre-state
        _proc(stdout=""),  # label create (state:ready_for_dev)
        _proc(stdout=""),  # label create (hitl:approved-ready_for_dev)
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
    assert "--add-label=state:ready_for_dev" in edit_cmd
    assert "--add-label=hitl:approved-ready_for_dev" in edit_cmd
    assert "--remove-label=state:refining" in edit_cmd
    assert "--remove-label=hitl:awaiting-ready_for_dev" in edit_cmd


def test_apply_marker_change_clear_claim_does_not_unassign_without_mapping() -> None:
    backend = GitHubBackend(repo="owner/repo")
    pre = {
        "labels": [
            {"name": "state:refining"},
            {"name": "wip:product-manager"},
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
    assert any("--remove-label=wip:product-manager" in c for c in calls)
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
                {"name": "state:refining"},
                {"name": "wip:product-manager"},
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
    assert "state:refining" in label_values
    assert "wip:product-manager" in label_values
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
                {"name": "state:refining"},
                {"name": "hitl:awaiting-ready_for_dev"},
            ],
        },
        {
            "number": 2,
            "title": "no awaiting",
            "labels": [{"name": "state:refining"}],
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
        {"number": 1, "title": "a closed issue", "labels": [{"name": "state:shipped"}]},
    ]
    pr_response = [
        {"number": 2, "title": "a merged PR", "labels": [{"name": "state:merged"}]},
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
    """A cohort query (`child-of:<id>`) finds children regardless of whether
    they are closed issues or PRs — wait-for-all depends on this (ADR-0003)."""
    backend = GitHubBackend(repo="owner/repo")
    closed_child = [
        {"number": 11, "title": "closed child", "labels": [{"name": "child-of:100"}]},
    ]
    pr_child = [
        {"number": 12, "title": "PR child", "labels": [{"name": "child-of:100"}]},
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
        assert "child-of:100" in label_vals


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
        {"number": 10, "title": "raw unclaimed", "labels": [{"name": "state:raw"}]},
        # This one is already claimed by someone else — must be excluded.
        {
            "number": 11,
            "title": "raw claimed",
            "labels": [{"name": "state:raw"}, {"name": "wip:peer-reviewer"}],
        },
    ]
    wip_response = [
        # Actionable: wip:product-manager with no HITL markers.
        {
            "number": 20,
            "title": "wip actionable",
            "labels": [{"name": "state:refining"}, {"name": "wip:product-manager"}],
        },
        # Blocked: wip:product-manager with hitl:awaiting-ready_for_dev — must be excluded.
        {
            "number": 21,
            "title": "wip blocked",
            "labels": [
                {"name": "state:refining"},
                {"name": "wip:product-manager"},
                {"name": "hitl:awaiting-ready_for_dev"},
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
    # 20 (wip:product-manager, no HITL): actionable wip.
    # 11 (wip:peer-reviewer): excluded — wrong claim role.
    # 21 (wip:product-manager, awaiting gate): excluded — blocked.
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
        # Only one subprocess.run call: the wip:developer filter
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
        {"name": "state:raw"},
        {"name": "wip:product-manager"},
        {"name": "hitl:reviewing"},
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
    assert sorted(names) == ["hitl:reviewing", "state:raw", "wip:product-manager"]
    # Cache was seeded so subsequent ensure_label calls are no-ops.
    assert "state:raw" in backend._known_labels
    assert "wip:product-manager" in backend._known_labels


def test_ensure_label_creates_missing() -> None:
    backend = GitHubBackend(repo="owner/repo")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        return_value=_proc(stdout=""),
    ) as patched:
        created = backend.ensure_label("state:new_state")

    assert created is True
    cmd = patched.call_args[0][0]
    assert cmd[0] == "gh"
    assert "label" in cmd and "create" in cmd
    assert "state:new_state" in cmd
    assert "--color" in cmd
    # Color came from the state namespace default (blue).
    color_idx = cmd.index("--color") + 1
    assert cmd[color_idx] == "1f6feb"
    # No --force: setup-github preserves user customizations on existing labels.
    assert "--force" not in cmd


def test_ensure_label_is_idempotent_via_cache() -> None:
    backend = GitHubBackend(repo="owner/repo")
    backend._known_labels.add("state:raw")
    with mock.patch(
        "workflow.backends.github.subprocess.run",
    ) as patched:
        created = backend.ensure_label("state:raw")

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
        created = backend.ensure_label("state:raw")

    # `gh` reported "already exists"; the method treats that as benign.
    assert created is False
    assert "state:raw" in backend._known_labels


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
        "labels": [{"name": "state:implementing"}],
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
        _proc(stdout=""),  # ensure state:raw
        _proc(stdout="https://github.com/owner/repo/issues/9\n"),
    ]
    with mock.patch(
        "workflow.backends.github.subprocess.run",
        side_effect=_fake_run_factory(responses),
    ) as patched:
        backend.create_issue(title="--oops looks like a flag", body="", state="raw")
    create_cmd = [c.args[0] for c in patched.call_args_list if "create" in c.args[0]][-1]
    assert "--title=--oops looks like a flag" in create_cmd


def _claim_run_factory(verify_labels: list[str], pre_labels: list[str] | None = None):
    """side_effect for a claim apply: read_issue vs the post-write verify fetch
    are distinguished by the `--json` field set (`labels` only = the verify)."""
    pre = {
        "number": 1,
        "labels": [{"name": lbl} for lbl in (pre_labels or ["state:raw"])],
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
    """A concurrent claim lands a second wip: label; the loser sees both, removes
    its own, and raises OperationError instead of believing it won (#21)."""
    backend = GitHubBackend(repo="owner/repo")
    run = _claim_run_factory(verify_labels=["wip:product-manager", "wip:developer"])
    with (
        mock.patch("workflow.backends.github.subprocess.run", side_effect=run) as patched,
        pytest.raises(OperationError, match="Lost claim race"),
    ):
        backend.apply_marker_change(
            "1", MarkerChange(set_state="refining", set_agent_claim="product-manager")
        )
    # Our own wip label was removed in the self-revert (and nobody else's).
    edits = [c.args[0] for c in patched.call_args_list if "edit" in c.args[0]]
    assert any("--remove-label=wip:product-manager" in e for e in edits)
    assert not any("--remove-label=wip:developer" in e for e in edits)


def test_claim_clean_win_no_revert() -> None:
    """When ours is the only wip: label after the write, the claim stands and no
    self-revert fires (#21)."""
    backend = GitHubBackend(repo="owner/repo")
    run = _claim_run_factory(verify_labels=["wip:product-manager"])
    with mock.patch("workflow.backends.github.subprocess.run", side_effect=run) as patched:
        backend.apply_marker_change(
            "1", MarkerChange(set_state="refining", set_agent_claim="product-manager")
        )
    edits = [c.args[0] for c in patched.call_args_list if "edit" in c.args[0]]
    assert not any(any(a.startswith("--remove-label=wip:") for a in e) for e in edits)
