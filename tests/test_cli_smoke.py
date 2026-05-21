"""CLI smoke tests — exercise the argparse command tree end-to-end.

Uses pytest's `capsys` fixture to capture stdout/stderr. The CLI's `cli()`
function returns an exit code; tests assert on it directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from workflow.cli import cli


def test_help(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "advance" in output
    assert "claim" in output
    assert "request-input" in output


def test_validate_against_shipped_workflows(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Validate iterates every workflow in the directory. The shipped tree
    predates HITL, so we expect either no findings or warnings — no errors."""
    rc = cli(["--workflow-dir", str(workflow_dir), "validate"])
    assert rc in (0, 1)
    output = capsys.readouterr().out
    assert output  # non-empty
    # At least one shipped process is reported.
    assert "process:" in output


def test_validate_json_output(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = cli(["--json", "--workflow-dir", str(workflow_dir), "validate"])
    assert rc in (0, 1)
    output = capsys.readouterr().out
    payload = json.loads(output)
    # Validate output is always grouped per workflow.
    assert "workflows" in payload
    assert isinstance(payload["workflows"], list)
    assert len(payload["workflows"]) >= 1
    wf = payload["workflows"][0]
    assert "findings" in wf
    assert isinstance(wf["findings"], list)


def test_advance_dry_run_does_not_call_backend(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Dry-run plans the operation without invoking the backend's mutating
    methods. We stub `read_issue` and ensure `apply_marker_change` is
    never called."""
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.read_issue",
            return_value=_fake_state(state_name="raw"),
        ) as read_mock,
        mock.patch(
            "workflow.backends.github.GitHubBackend.apply_marker_change",
        ) as apply_mock,
    ):
        rc = cli(
            [
                "--dry-run",
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "advance",
                "--to",
                "refining",
                "--issue",
                "123",
            ]
        )

    output = capsys.readouterr().out
    assert rc == 0, output
    assert read_mock.called
    assert not apply_mock.called
    assert "dry-run" in output


def test_advance_unknown_destination_errors_clean(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    with mock.patch(
        "workflow.backends.github.GitHubBackend.read_issue",
        return_value=_fake_state(state_name="raw"),
    ):
        rc = cli(
            [
                "--dry-run",
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "advance",
                "--to",
                "definitely_not_a_real_state",
                "--issue",
                "123",
            ]
        )
    captured = capsys.readouterr()
    assert rc == 2
    assert "error" in (captured.out + captured.err).lower()


def test_comment_inline_body_posts(
    capsys: pytest.CaptureFixture,
) -> None:
    """`workflow comment --body 'note'` posts via backend.post_comment without
    resolving a workflow or reading the issue."""
    with mock.patch(
        "workflow.backends.github.GitHubBackend.post_comment",
    ) as post_mock:
        rc = cli(
            [
                "--repo",
                "owner/test",
                "comment",
                "--issue",
                "123",
                "--body",
                "Quick status update.",
            ]
        )
    output = capsys.readouterr().out
    assert rc == 0, output
    post_mock.assert_called_once()
    args, _ = post_mock.call_args
    assert args[0] == "123"
    assert args[1] == "Quick status update."
    assert "Comment posted" in output


def test_comment_empty_body_rejected(
    capsys: pytest.CaptureFixture,
) -> None:
    rc = cli(
        [
            "--repo",
            "owner/test",
            "comment",
            "--issue",
            "123",
            "--body",
            "   ",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "empty" in (captured.out + captured.err).lower()


def test_comment_dry_run_does_not_call_backend(
    capsys: pytest.CaptureFixture,
) -> None:
    with mock.patch(
        "workflow.backends.github.GitHubBackend.post_comment",
    ) as post_mock:
        rc = cli(
            [
                "--dry-run",
                "--repo",
                "owner/test",
                "comment",
                "--issue",
                "123",
                "--body",
                "Dry run note.",
            ]
        )
    output = capsys.readouterr().out
    assert rc == 0
    assert not post_mock.called
    assert "[dry-run]" in output


def test_doctor_runs(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    cli(
        [
            "--workflow-dir",
            str(workflow_dir),
            "doctor",
        ]
    )
    output = capsys.readouterr().out
    # doctor exits 0 when nothing failed; non-zero otherwise. Either way it
    # produces output.
    assert output
    # Registry was enumerated.
    assert "registry:" in output


def test_build_backend_auto_discovers_host_and_repo_from_remote() -> None:
    """With neither --host nor --repo, discovery extracts both from
    `git remote get-url origin`. The non-github.com host carries through."""
    from workflow.cli import _build_backend

    with mock.patch(
        "workflow.backends.github.discover_remote_from_git",
        return_value=("ghe.acme.com", "myorg/widget"),
    ) as discover_mock:
        backend = _build_backend({"backend": "github", "repo": None, "host": None})

    assert backend.repo == "myorg/widget"
    assert backend.host == "ghe.acme.com"
    assert discover_mock.called


def test_build_backend_skips_host_when_discovery_finds_github_com() -> None:
    """Discovery finding github.com leaves backend.host as None — that's
    gh's default, so no need to override the subprocess env."""
    from workflow.cli import _build_backend

    with mock.patch(
        "workflow.backends.github.discover_remote_from_git",
        return_value=("github.com", "owner/repo"),
    ):
        backend = _build_backend({"backend": "github", "repo": None, "host": None})

    assert backend.repo == "owner/repo"
    assert backend.host is None


def test_build_backend_explicit_host_wins_over_discovery() -> None:
    """--host beats whatever git remote URL parsing would have yielded."""
    from workflow.cli import _build_backend

    with mock.patch(
        "workflow.backends.github.discover_remote_from_git",
        return_value=("ghe.discovered.com", "discovered/slug"),
    ):
        backend = _build_backend(
            {
                "backend": "github",
                "repo": "explicit/repo",
                "host": "ghe.explicit.com",
            }
        )

    assert backend.repo == "explicit/repo"
    assert backend.host == "ghe.explicit.com"


def test_build_backend_skips_discovery_when_both_provided() -> None:
    """No discovery call at all when --host and --repo are both explicit."""
    from workflow.cli import _build_backend

    with mock.patch(
        "workflow.backends.github.discover_remote_from_git",
    ) as discover_mock:
        backend = _build_backend(
            {
                "backend": "github",
                "repo": "explicit/repo",
                "host": "ghe.acme.com",
            }
        )

    assert not discover_mock.called
    assert backend.repo == "explicit/repo"
    assert backend.host == "ghe.acme.com"


def test_build_backend_raises_when_discovery_fails() -> None:
    """If neither --repo nor discovery yield a slug, the error message
    points at both resolution paths."""
    from workflow.cli import _build_backend
    from workflow.errors import ConfigError

    with (
        mock.patch(
            "workflow.backends.github.discover_remote_from_git",
            return_value=(None, None),
        ),
        pytest.raises(ConfigError) as exc_info,
    ):
        _build_backend({"backend": "github", "repo": None, "host": None})

    msg = str(exc_info.value)
    assert "--repo" in msg
    assert "WORKFLOW_REPO" in msg


def test_init_creates_config_and_trust_grants_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`workflow init --role pm --agent-home <dir>` writes config.json and
    scaffolds the default `.workflow/workflows/` + `.workflow/trust-grants/`
    subdirectories."""
    rc = cli(
        [
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "product-manager",
            "init",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0, output

    config_path = tmp_path / ".workflow" / "config.json"
    grants_path = tmp_path / ".workflow" / "trust-grants"
    workflows_path = tmp_path / ".workflow" / "workflows"
    assert config_path.is_file()
    assert grants_path.is_dir()
    assert workflows_path.is_dir()

    config = json.loads(config_path.read_text())
    # Only `agent-role` is written when --workflow-dir is not supplied.
    # `repo`, `host`, and the workflow path fields stay per-invocation or
    # use the default location.
    assert config == {"agent-role": "product-manager"}


def test_init_records_workflow_dir_when_provided(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Passing --workflow-dir to init writes the relative path into the
    config as the `workflow-dir` entry — that's how a per-role agent home
    points at a shared workflows directory. The default
    `.workflow/workflows/` is still scaffolded; the config entry overrides
    it at lookup time."""
    rc = cli(
        [
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "product-manager",
            "init",
            "--workflow-dir",
            "../../workflows",
        ]
    )
    assert rc == 0
    config = json.loads((tmp_path / ".workflow" / "config.json").read_text())
    assert config == {
        "agent-role": "product-manager",
        "workflow-dir": "../../workflows",
    }
    # Default workflows/ is still created — the config entry just overrides
    # the lookup target.
    assert (tmp_path / ".workflow" / "workflows").is_dir()


def test_init_minimal_config_contains_only_role(
    tmp_path: Path,
) -> None:
    """Even when global flags like --repo are present, init only persists
    `role`. Everything else stays per-invocation."""
    rc = cli(
        [
            "--repo",
            "acme/myrepo",
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "developer",
            "init",
        ]
    )
    assert rc == 0
    config = json.loads((tmp_path / ".workflow" / "config.json").read_text())
    assert config == {"agent-role": "developer"}


def test_init_strips_placeholder_braces_from_role(
    tmp_path: Path,
) -> None:
    """`--agent-role {product-manager}` is normalized to `product-manager` —
    accept the braced placeholder form skill prose uses."""
    rc = cli(
        [
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "{product-manager}",
            "init",
        ]
    )
    assert rc == 0
    config = json.loads((tmp_path / ".workflow" / "config.json").read_text())
    assert config["agent-role"] == "product-manager"


def test_init_refuses_to_overwrite_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A second init without --force fails cleanly and preserves the existing
    config."""
    config_path = tmp_path / ".workflow" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text('{"agent-role": "existing"}\n')

    rc = cli(
        [
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "different",
            "init",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "already exists" in (captured.out + captured.err).lower()
    # Existing config is untouched.
    assert json.loads(config_path.read_text())["agent-role"] == "existing"


def test_init_force_overwrites(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".workflow" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text('{"agent-role": "old"}\n')

    rc = cli(
        [
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "new",
            "init",
            "--force",
        ]
    )
    assert rc == 0
    assert json.loads(config_path.read_text())["agent-role"] == "new"


def test_init_dry_run_does_not_write_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = cli(
        [
            "--dry-run",
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "product-manager",
            "init",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0, output
    assert "[dry-run]" in output
    assert not (tmp_path / ".workflow" / "config.json").exists()


def test_init_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = cli(
        [
            "--json",
            "--agent-home",
            str(tmp_path),
            "--agent-role",
            "product-manager",
            "init",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0, output
    payload = json.loads(output)
    assert payload["config"]["agent-role"] == "product-manager"
    assert payload["config_path"].endswith("config.json")
    assert payload["agent_home"] == str(tmp_path)
    # Both default subdirectories are reported in the JSON output.
    assert payload["workflow_dir"].endswith(".workflow/workflows")
    assert payload["grants_dir"].endswith(".workflow/trust-grants")
    assert payload["grants_dir"].endswith(".workflow/trust-grants")


def test_create_dry_run_does_not_call_backend(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`create --dry-run` should print the plan without invoking the
    backend's create_issue method."""
    with mock.patch(
        "workflow.backends.github.GitHubBackend.create_issue",
    ) as create_mock:
        rc = cli(
            [
                "--dry-run",
                "--workflow-dir",
                str(workflow_dir),
                "create",
                "--to",
                "raw",
                "--type",
                "bug",
                "--title",
                "Fix login bug",
            ]
        )

    output = capsys.readouterr().out
    assert rc == 0, output
    assert not create_mock.called
    assert "[dry-run]" in output
    assert "raw" in output


def test_create_pr_dry_run_shows_pr_specific_plan(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`create --to draft` resolves to the pr process and the pr type;
    the dry-run output names the PR-specific fields (head, base, refs, draft)."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "draft",
            "--title",
            "feat: add the thing",
            "--body",
            "Implements thing X.",
            "--head",
            "feat/thing",
            "--base",
            "main",
            "--refs",
            "42",
            "--refs",
            "43",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0, output
    assert "would create pull request" in output
    assert "head:          feat/thing" in output
    assert "base:          main" in output
    assert "#42, #43" in output
    assert "draft:         True" in output


def test_create_pr_requires_head(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Creating a PR without --head fails fast with a clear error."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "draft",
            "--title",
            "x",
            "--body",
            "y",
            "--refs",
            "1",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "--head" in (captured.out + captured.err)


def test_create_pr_requires_refs(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Creating a PR without any --refs fails fast."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "draft",
            "--title",
            "x",
            "--body",
            "y",
            "--head",
            "feat/x",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "--refs" in (captured.out + captured.err)


def test_create_pr_requires_body(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """PRs need a description — body is mandatory."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "draft",
            "--title",
            "x",
            "--head",
            "feat/x",
            "--refs",
            "1",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "body" in (captured.out + captured.err).lower()


def test_create_issue_rejects_pr_flags(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """--head / --base / --refs are PR-only; passing them on an issue create
    fails fast."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "raw",
            "--type",
            "bug",
            "--title",
            "x",
            "--head",
            "branch",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "--head" in (captured.out + captured.err)


def test_create_pr_invokes_backend_pull_request_path(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A successful PR create calls backend.create_pull_request (not
    create_issue) and the body carries the framework's Refs footer."""
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.create_issue",
        ) as create_issue_mock,
        mock.patch(
            "workflow.backends.github.GitHubBackend.create_pull_request",
            return_value="77",
        ) as create_pr_mock,
    ):
        rc = cli(
            [
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "create",
                "--to",
                "draft",
                "--title",
                "feat: thing",
                "--body",
                "Adds thing.",
                "--head",
                "feat/thing",
                "--refs",
                "42",
            ]
        )
    output = capsys.readouterr().out
    assert rc == 0, output
    assert not create_issue_mock.called
    create_pr_mock.assert_called_once()
    kwargs = create_pr_mock.call_args.kwargs
    assert kwargs["title"] == "feat: thing"
    assert kwargs["state"] == "draft"
    assert kwargs["head"] == "feat/thing"
    assert kwargs["draft"] is True
    assert "Refs #42" in kwargs["body"]
    assert kwargs["body"].startswith("Adds thing.")


def test_create_unknown_state_errors_clean(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """An initial state not present in any workflow fails fast and clean."""
    rc = cli(
        [
            "--dry-run",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "definitely_not_a_real_state",
            "--title",
            "X",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "not declared" in (captured.out + captured.err).lower()


def test_create_invokes_backend_with_resolved_state(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A successful create returns the new id from the backend. When the
    capability probe returns existing Issue Types, encoding is `native` →
    issue_type is passed through as the GitHub type name."""
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.list_issue_types",
            return_value=["Bug", "Feature", "Task"],  # native encoding
        ),
        mock.patch(
            "workflow.backends.github.GitHubBackend.create_issue",
            return_value="123",
        ) as create_mock,
    ):
        rc = cli(
            [
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "create",
                "--to",
                "raw",
                "--type",
                "bug",
                "--title",
                "Fix the thing",
                "--body",
                "Steps to reproduce: ...",
            ]
        )

    output = capsys.readouterr().out
    assert rc == 0, output
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["title"] == "Fix the thing"
    assert kwargs["state"] == "raw"
    assert kwargs["body"] == "Steps to reproduce: ..."
    assert kwargs["extra_labels"] == []
    # Issue type resolved to GitHub's "Bug" via issue-types.json.
    assert kwargs["issue_type"] == "Bug"
    assert "#123" in output
    # StateMachine was auto-resolved to refinement (state:raw belongs to refinement).
    assert "refinement" in output


def test_create_with_claim_creates_then_claims(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`--claim` creates the issue at the resting initial state, then runs
    a proper `claim` operation against it. This sets `wip:<role>` and
    `wip-from:<initial_state>` atomically with moving to the working state —
    no resting-state-with-wip-label invariant violation."""
    from workflow.backends.base import IssueState

    raw_state = IssueState(issue_id="99", state="raw", agent_claim=None, wip_from=None)
    refining_state = IssueState(
        issue_id="99",
        state="refining",
        agent_claim="product-manager",
        wip_from="raw",
    )
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.list_issue_types",
            return_value=["Bug", "Feature", "Task"],
        ),
        mock.patch(
            "workflow.backends.github.GitHubBackend.create_issue",
            return_value="99",
        ) as create_mock,
        mock.patch(
            "workflow.backends.github.GitHubBackend.read_issue",
            side_effect=[raw_state, raw_state, refining_state],
        ),
        mock.patch(
            "workflow.backends.github.GitHubBackend.apply_marker_change"
        ) as apply_mock,
    ):
        rc = cli(
            [
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "--agent-role",
                "product-manager",
                "create",
                "--to",
                "raw",
                "--type",
                "bug",
                "--title",
                "Mine",
                "--claim",
            ]
        )

    assert rc == 0
    # Step 1: create_issue called with no wip label (initial state is resting).
    create_kwargs = create_mock.call_args.kwargs
    assert create_kwargs["extra_labels"] == []
    # Step 2: claim marker change followed — moves state and sets wip + wip-from.
    assert apply_mock.called
    change = apply_mock.call_args.args[1]
    assert change.set_state == "refining"
    assert change.set_agent_claim == "product-manager"
    assert change.set_wip_from == "raw"


def test_create_with_claim_but_no_agent_role_errors(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--claim without any agent role available is a clean error."""
    monkeypatch.delenv("AGENT_ROLE", raising=False)
    rc = cli(
        [
            "--repo",
            "owner/test",
            "--workflow-dir",
            str(workflow_dir),
            "create",
            "--to",
            "raw",
            "--type",
            "bug",
            "--title",
            "X",
            "--claim",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "agent role" in (captured.out + captured.err).lower()


def test_setup_github_dry_run_enumerates_without_calling_backend(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Dry-run prints the label set without contacting the backend's mutating
    operations. The capability probe still runs (returns None → label mode)."""
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.list_issue_types",
            return_value=None,  # feature not available → label encoding
        ),
        mock.patch(
            "workflow.backends.github.GitHubBackend.ensure_label",
        ) as ensure_mock,
    ):
        rc = cli(
            [
                "--dry-run",
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "setup-github",
            ]
        )

    output = capsys.readouterr().out
    assert rc == 0, output
    assert not ensure_mock.called
    # The fixed HITL singletons must always appear.
    for fixed in (
        "hitl:reviewing",
        "hitl:auditing",
        "hitl:advising",
        "hitl:awaiting-input",
        "hitl:resolved",
    ):
        assert fixed in output, f"expected {fixed} in dry-run output"
    assert "state:raw" in output
    assert "wip:product-manager" in output
    assert "wip:developer" in output
    # In label-encoding mode, `type:*` labels are also enumerated.
    assert "type:bug" in output


def test_setup_github_json_dry_run(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    with mock.patch(
        "workflow.backends.github.GitHubBackend.list_issue_types",
        return_value=None,
    ):
        rc = cli(
            [
                "--json",
                "--dry-run",
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "setup-github",
            ]
        )
    output = capsys.readouterr().out
    assert rc == 0, output
    payload = json.loads(output)
    assert "labels" in payload
    labels = payload["labels"]
    assert isinstance(labels, list)
    assert labels == sorted(labels)
    assert "hitl:reviewing" in labels


def test_setup_github_creates_only_missing(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Missing labels are created via ensure_label; existing ones are skipped."""
    existing = ["hitl:reviewing"]
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.list_issue_types",
            return_value=None,  # label encoding
        ),
        mock.patch(
            "workflow.backends.github.GitHubBackend.list_labels",
            return_value=existing,
        ) as list_mock,
        mock.patch(
            "workflow.backends.github.GitHubBackend.ensure_label",
            return_value=True,
        ) as ensure_mock,
    ):
        rc = cli(
            [
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "setup-github",
            ]
        )

    output = capsys.readouterr().out
    assert rc == 0, output
    assert list_mock.called
    called_labels = {call.args[0] for call in ensure_mock.call_args_list}
    assert "hitl:reviewing" not in called_labels  # skipped
    assert "hitl:auditing" in called_labels
    assert "state:raw" in called_labels
    assert "skipped 1" in output


# --------------------------------------------------------------------------- #
# helpers


def _fake_state(state_name: str = "raw", agent_claim: str | None = None):
    from workflow.backends.base import IssueState

    return IssueState(
        issue_id="123",
        state=state_name,
        agent_claim=agent_claim,
    )


def test_edit_invokes_backend_with_title_and_body(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """edit calls TrackerBackend.edit_issue with title and body content."""
    from workflow.backends.base import IssueState

    state_after = IssueState(issue_id="42", state="raw", agent_claim=None, wip_from=None)
    with (
        mock.patch(
            "workflow.backends.github.GitHubBackend.edit_issue",
        ) as edit_mock,
        mock.patch(
            "workflow.backends.github.GitHubBackend.read_issue",
            return_value=state_after,
        ),
    ):
        rc = cli(
            [
                "--repo",
                "owner/test",
                "--workflow-dir",
                str(workflow_dir),
                "edit",
                "--issue",
                "42",
                "--title",
                "New title",
                "--body",
                "New body content",
            ]
        )

    assert rc == 0
    kwargs = edit_mock.call_args.kwargs
    assert kwargs["title"] == "New title"
    assert kwargs["body"] == "New body content"


def test_edit_without_title_or_body_errors(
    workflow_dir: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """edit refuses when neither --title nor --body / --body-from is given."""
    rc = cli(
        [
            "--repo",
            "owner/test",
            "--workflow-dir",
            str(workflow_dir),
            "edit",
            "--issue",
            "42",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "at least one of --title" in err
