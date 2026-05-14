"""CLI entry point. One sub-command per framework operation.

The CLI is the user-facing surface. Operations are defined in
`workflow.core.operations` and dispatched through the controller. This module
wires them to an argparse command tree — stdlib only, no third-party CLI lib.

See README.md for the full list of operations.
"""

from __future__ import annotations

import argparse
import json as _json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from workflow import __version__
from workflow.backends.base import WorkflowBackend
from workflow.backends.github import GitHubBackend
from workflow.config import WorkflowContext, load_workflow
from workflow.core.controller import Controller, OperationResult
from workflow.core.operations import (
    advance as advance_op,
)
from workflow.core.operations import (
    advise as advise_op,
)
from workflow.core.operations import (
    approve as approve_op,
)
from workflow.core.operations import (
    audit as audit_op,
)
from workflow.core.operations import (
    check as check_op,
)
from workflow.core.operations import (
    claim as claim_op,
)
from workflow.core.operations import (
    reject as reject_op,
)
from workflow.core.operations import (
    release as release_op,
)
from workflow.core.operations import (
    request_input as request_input_op,
)
from workflow.core.operations import (
    resolve as resolve_op,
)
from workflow.core.operations import (
    review as review_op,
)
from workflow.core.operations import (
    revoke as revoke_op,
)

# await_signal and record_action remain importable as internal primitives,
# but are not exposed as CLI subcommands. `advance` dispatches into them
# based on the HCP catalog.
from workflow.core.validator import Severity, validate_workflow
from workflow.errors import (
    BackendError,
    ConfigError,
    ParseError,
    WorkflowError,
)

logger = logging.getLogger(__name__)


PROG = "workflow"


# --------------------------------------------------------------------------- #
# argparse type helpers


def _path_existing_file(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {value!r}")
    if p.is_dir():
        raise argparse.ArgumentTypeError(f"expected a file, got directory: {value!r}")
    return p


def _path_existing_dir(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {value!r}")
    if not p.is_dir():
        raise argparse.ArgumentTypeError(f"expected a directory: {value!r}")
    return p


# --------------------------------------------------------------------------- #
# Parser construction


_TOP_DESCRIPTION = """Canonical operation mechanism for agent workflows.

Agent-facing commands:
  create                        — open a new work item in a given initial state
  advance, claim, release       — lifecycle ownership and state changes
  request-input                 — recognized HITL (state-orthogonal pause)

Human-facing commands:
  review, approve, reject       — pre-action HITL signals (block level)
  audit, check, revoke          — post-action HITL signals (audit level)
  advise, resolve               — recognized HITL responses

Discovery and utility commands (anyone):
  inbox                         — show this agent's claimable items + actionable wip
  search                        — find work items by state, claim, or HITL marker
  view                          — inspect one work item's state and recent comments
  comment                       — post a free-form comment without advancing state

The agent invokes `advance` for every transition; the tool consults the HCP
catalog and team trust grants to determine whether the transition is
ungated, block-gated, or audit-gated, and applies the right primitive
automatically. The `await-signal` and `record-action` primitives exist
inside the framework but are never invoked directly by the agent.

Plus utility commands: init, validate, doctor, setup-labels.
"""


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that appear on every subcommand (via the parent parser pattern).

    These can appear either before or after the subcommand name; argparse
    parses them on whichever parser they land in. Subcommand parsers inherit
    via `parents=[parent]`.
    """
    parser.add_argument(
        "--repo",
        default=os.environ.get("WORKFLOW_REPO"),
        help="Backend repo identifier (e.g., owner/name for GitHub). Env: WORKFLOW_REPO.",
    )
    parser.add_argument(
        "--host",
        dest="host",
        default=os.environ.get("WORKFLOW_GH_HOST"),
        help="Backend host (e.g., ghe.example.com for GitHub Enterprise "
        "Server). For the github backend, sets GH_HOST for every `gh` "
        "invocation. If unset, `gh` falls back to your exported GH_HOST "
        "or the host you authenticated against with `gh auth login`. "
        "Env: WORKFLOW_GH_HOST.",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Plan the operation without executing backend mutations.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Emit structured JSON instead of human-readable output.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--agent-role",
        dest="agent_role",
        default=os.environ.get("AGENT_ROLE"),
        help="The agent's primary role (e.g., pm, developer). Used as the "
        "default for commands that take a per-invocation --role (claim, "
        "list, etc.), and as the actor identity in operation audit "
        "comments. Resolution: this flag > AGENT_ROLE env > the "
        "`agent-role` field in <agent-home>/.workflow/config.json. "
        "Env: AGENT_ROLE.",
    )
    parser.add_argument(
        "--agent-home",
        dest="agent_home",
        type=Path,
        default=(
            Path(os.environ["AGENT_HOME"]).expanduser() if os.environ.get("AGENT_HOME") else None
        ),
        help="Directory holding this agent's `.workflow/` folder (config "
        "and trust grants). If unset, discovered by walking up from cwd "
        "for any directory containing `.workflow/`. For `init`, this is "
        "the target directory where the agent home will be created "
        "(defaults to cwd in that case). Env: AGENT_HOME.",
    )
    parser.add_argument(
        "--workflows-dir",
        dest="workflows_dir",
        type=_path_existing_dir,
        default=(
            Path(os.environ["WORKFLOWS_DIR"]).expanduser()
            if os.environ.get("WORKFLOWS_DIR")
            else None
        ),
        help="Directory containing the workflow files "
        "(`*-lifecycle.mermaid`, `*-hcps.json`, `roles.json`). "
        "If unset, discovered by walking up from cwd for a directory "
        "with `*-lifecycle.mermaid` files, or for the legacy "
        "`skills/workflows/shared/resources/` path. "
        "Env: WORKFLOWS_DIR.",
    )
    parser.add_argument(
        "--grants-dir",
        dest="grants_dir",
        type=_path_existing_dir,
        default=(
            Path(os.environ["GRANTS_DIR"]).expanduser() if os.environ.get("GRANTS_DIR") else None
        ),
        help="Directory containing trust-grant JSON files. Selects which "
        "set of grants applies — point at a team-specific dir, a shared "
        "dir, or a per-environment one. If unset, defaults to "
        "<agent-home>/.workflow/trust-grants/. Env: GRANTS_DIR.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse tree and return it.

    Global options are defined on the top-level parser only. Users must put
    them before the subcommand name (matches click's group-option behavior).
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=_TOP_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )
    _add_global_options(parser)

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- Lifecycle operations ---

    p_advance = subparsers.add_parser(
        "advance",
        help="Advance a work item to a target state.",
        description=(
            "Advance a work item to a target state. The tool finds the "
            "transition from the current state to --to and applies the right "
            "behavior based on the lifecycle and HCP catalog: ungated "
            "transitions change state immediately; block-gated transitions "
            "pause for human signal (requires --packet-from); audit-gated "
            "transitions change state atomically with the audit-pending "
            "marker. The agent does not need to know which path applies — "
            "the catalog decides."
        ),
    )
    p_advance.add_argument(
        "--to",
        dest="destination",
        required=True,
        help="Target state to advance to (e.g., ready_for_dev).",
    )
    p_advance.add_argument("--issue", required=True, help="Work item identifier.")
    p_advance.add_argument(
        "--packet-from",
        dest="packet_from",
        type=_path_existing_file,
        default=None,
        help="Path to a markdown packet. Required when the transition is "
        "block-gated; matches the catalog row's agent_prepares template.",
    )
    p_advance.add_argument(
        "--notes-from",
        dest="notes_from",
        type=_path_existing_file,
        default=None,
        help="Path to a markdown notes file. Optional for ungated transitions; "
        "used as the audit-comment body for audit-gated transitions.",
    )
    p_advance.set_defaults(func=_do_advance)

    p_claim = subparsers.add_parser(
        "claim",
        help="Take responsibility for a resting work item.",
    )
    p_claim.add_argument("--issue", required=True)
    p_claim.add_argument(
        "--role",
        default=None,
        help="Role id taking the claim (e.g., pm). "
        "Defaults to the agent-config `role` if not specified.",
    )
    p_claim.add_argument(
        "--transition",
        dest="transition_label",
        default=None,
        help="Optional claim transition label.",
    )
    p_claim.set_defaults(func=_do_claim)

    p_release = subparsers.add_parser(
        "release",
        help="Give up the claim on a work item.",
    )
    p_release.add_argument("--issue", required=True)
    p_release.set_defaults(func=_do_release)

    # --- Catalogued — block-level ---

    p_review = subparsers.add_parser(
        "review",
        help="Human claims pre-action review of an awaiting gate.",
    )
    p_review.add_argument("--issue", required=True)
    p_review.set_defaults(func=_do_review)

    p_approve = subparsers.add_parser(
        "approve",
        help="Human approves a catalogued HCP; transition fires.",
    )
    p_approve.add_argument("--gate", required=True)
    p_approve.add_argument(
        "--destination", default=None, help="Destination state (for verdict-style HCPs)."
    )
    p_approve.add_argument("--issue", required=True)
    p_approve.add_argument(
        "--comment-from", dest="comment_from", type=_path_existing_file, default=None
    )
    p_approve.set_defaults(func=_do_approve)

    p_reject = subparsers.add_parser(
        "reject",
        help="Human rejects a catalogued HCP packet; agent iterates.",
    )
    p_reject.add_argument("--gate", required=True)
    p_reject.add_argument("--issue", required=True)
    p_reject.add_argument(
        "--feedback-from", dest="feedback_from", type=_path_existing_file, required=True
    )
    p_reject.set_defaults(func=_do_reject)

    # --- Catalogued — audit-level ---

    p_audit = subparsers.add_parser(
        "audit",
        help="Human claims post-action audit.",
    )
    p_audit.add_argument("--issue", required=True)
    p_audit.set_defaults(func=_do_audit)

    p_check = subparsers.add_parser(
        "check",
        help="Human confirms an audit-level action post-hoc.",
    )
    p_check.add_argument("--gate", required=True)
    p_check.add_argument("--issue", required=True)
    p_check.set_defaults(func=_do_check)

    p_revoke = subparsers.add_parser(
        "revoke",
        help="Human revokes an audit-level action; triggers on_revoke remediation.",
    )
    p_revoke.add_argument("--gate", required=True)
    p_revoke.add_argument("--issue", required=True)
    p_revoke.add_argument(
        "--concern-from", dest="concern_from", type=_path_existing_file, required=True
    )
    p_revoke.set_defaults(func=_do_revoke)

    # --- Recognized ---

    p_request_input = subparsers.add_parser(
        "request-input",
        help="Agent recognizes an unanticipated HITL moment; pause for input.",
    )
    p_request_input.add_argument("--issue", required=True)
    p_request_input.add_argument(
        "--question-from", dest="question_from", type=_path_existing_file, required=True
    )
    p_request_input.set_defaults(func=_do_request_input)

    p_advise = subparsers.add_parser(
        "advise",
        help="Human claims the recognition response role.",
    )
    p_advise.add_argument("--issue", required=True)
    p_advise.set_defaults(func=_do_advise)

    p_resolve = subparsers.add_parser(
        "resolve",
        help="Human provides input for a recognized HITL moment.",
    )
    p_resolve.add_argument("--issue", required=True)
    p_resolve.add_argument(
        "--response-from", dest="response_from", type=_path_existing_file, required=True
    )
    p_resolve.set_defaults(func=_do_resolve)

    # --- Read / discovery commands ---

    p_create = subparsers.add_parser(
        "create",
        help="Create a new work item in the given initial state.",
        description=(
            "Create a new work item. Requires a title and an initial state "
            "(--to STATE). The workflow is auto-resolved from --to via "
            "the registry (state names are unique). The new item is "
            "created atomically with its `state:<name>` label so it never "
            "exists without a state. Optionally claim it for the agent in "
            "the same step with --claim, which adds `wip:<agent-role>`."
        ),
    )
    p_create.add_argument(
        "--to",
        dest="initial_state",
        required=True,
        help="Initial state for the new work item (e.g., raw). Determines "
        "which workflow the item belongs to.",
    )
    p_create.add_argument(
        "--title",
        required=True,
        help="Title for the new work item.",
    )
    body_group = p_create.add_mutually_exclusive_group()
    body_group.add_argument(
        "--body",
        default=None,
        help="Inline body for the new work item.",
    )
    body_group.add_argument(
        "--body-from",
        dest="body_from",
        type=_path_existing_file,
        default=None,
        help="Path to a markdown file containing the body.",
    )
    p_create.add_argument(
        "--claim",
        action="store_true",
        default=False,
        help="Atomically claim the new item for the agent's role "
        "(adds `wip:<agent-role>` alongside the state label).",
    )
    p_create.set_defaults(func=_do_create)

    p_inbox = subparsers.add_parser(
        "inbox",
        help="Show the agent's own claimable items and actionable wip.",
        description=(
            "Show this agent's plate, computed from the workflow registry: "
            "(1) inbox — items in resting states whose `claim-role` matches "
            "the agent's role, not currently claimed by anyone; "
            "(2) actionable wip — items with `wip:{role}` that are NOT "
            "blocked waiting for any HITL signal "
            "(no `hitl:awaiting-*`, `hitl:audit-*`, or `hitl:awaiting-input`). "
            "The role comes from --agent-role / AGENT_ROLE / the agent "
            "config's `agent-role` key; override per invocation with "
            "--agent-role to view a different role's inbox."
        ),
    )
    p_inbox.add_argument(
        "--limit", type=int, default=50, help="Max items per backend query (default: 50)."
    )
    p_inbox.set_defaults(func=_do_inbox)

    p_search = subparsers.add_parser(
        "search",
        help="Search work items by framework filters (state, claim, HITL markers).",
        description=(
            "Search work items matching the given filters. Filters compose "
            "with AND. Use `--awaiting-gate '*'` or `--audit-pending '*'` "
            "to find every work item with any awaiting or audit-pending "
            "marker. For the agent's own work, use `inbox` instead."
        ),
    )
    p_search.add_argument("--state", default=None, help="Filter by state (e.g., ready_for_dev).")
    p_search.add_argument(
        "--claim",
        dest="claim_role",
        default=None,
        help="Filter by agent claim (role id, e.g., pm).",
    )
    p_search.add_argument(
        "--awaiting-gate",
        dest="awaiting_gate",
        default=None,
        help="Filter by awaiting-gate name; pass '*' for any awaiting gate.",
    )
    p_search.add_argument(
        "--audit-pending",
        dest="audit_pending",
        default=None,
        help="Filter by audit-pending gate name; pass '*' for any audit-pending.",
    )
    p_search.add_argument(
        "--awaiting-input",
        dest="awaiting_input",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Filter for items awaiting recognized input "
        "(or with --no-awaiting-input, exclude them).",
    )
    p_search.add_argument(
        "--limit", type=int, default=50, help="Max items to return (default: 50)."
    )
    p_search.set_defaults(func=_do_search)

    p_view = subparsers.add_parser(
        "view",
        help="View a single work item's framework-relevant state and recent comments.",
    )
    p_view.add_argument("--issue", required=True)
    p_view.add_argument(
        "--comments",
        type=int,
        default=None,
        help="Limit the number of recent comments shown. By default every "
        "comment is returned (most recent first). Pass a positive N to "
        "cap the list, or 0 to omit comments entirely.",
    )
    p_view.set_defaults(func=_do_view)

    p_comment = subparsers.add_parser(
        "comment",
        help="Post a comment on an issue without advancing state (utility, not a framework operation).",
        description=(
            "Post a free-form comment on an issue. This is NOT a framework "
            "operation — no state change, no markers, no workflow resolution. "
            "Use it for status updates, investigation notes, async pings — "
            "anything that needs to be communicated but doesn't fit any of "
            "the eleven framework operations or the lifecycle commands."
        ),
    )
    p_comment.add_argument("--issue", required=True)
    body_group = p_comment.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", default=None, help="Comment body (inline string).")
    body_group.add_argument(
        "--body-from",
        dest="body_from",
        type=_path_existing_file,
        default=None,
        help="Path to a markdown file containing the comment body.",
    )
    p_comment.set_defaults(func=_do_comment)

    # --- Utility commands ---

    p_validate = subparsers.add_parser(
        "validate",
        help="Validate workflow artifacts against the framework principles.",
    )
    p_validate.set_defaults(func=_do_validate)

    p_doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose the workflow configuration (artifacts, trust grants, backend auth).",
    )
    p_doctor.set_defaults(func=_do_doctor)

    p_init = subparsers.add_parser(
        "init",
        help="Scaffold an agent home with .workflow/ config, workflows, and trust-grants.",
        description=(
            "Create a new agent home. Writes `.workflow/config.json` with "
            "the agent's identity (the global --agent-role, required for "
            "init) and creates empty `.workflow/workflows/` and "
            "`.workflow/trust-grants/` subdirectories — the default "
            "locations for workflow definitions and trust grants. The "
            "target directory is the global --agent-home, defaulting to "
            "cwd. Refuses to overwrite an existing config.json unless "
            "--force is passed."
        ),
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing config.json if present.",
    )
    p_init.set_defaults(func=_do_init)

    p_setup_labels = subparsers.add_parser(
        "setup-labels",
        help="Provision every framework-required label on the configured repo.",
        description=(
            "Enumerate every label the framework needs across all "
            "discovered workflows and create them on the configured backend "
            "repo. Idempotent: existing labels are skipped (their colors "
            "are NOT overwritten). Includes one `state:*` label per "
            "lifecycle state, one `wip:*` per role in roles.json, the five "
            "HITL singleton labels (`hitl:reviewing`, `hitl:auditing`, "
            "`hitl:advising`, `hitl:awaiting-input`, `hitl:resolved`), and "
            "one `hitl:awaiting-<gate>` / `hitl:audit-<gate>` per "
            "catalogued HCP whose `allowed_levels` includes that level. "
            "Transient signal markers (`hitl:approved-<dest>`, "
            "`hitl:rejected-<gate>`, etc.) are NOT pre-created — they're "
            "added lazily by the relevant operations. Use --dry-run to "
            "list the labels without contacting the backend."
        ),
    )
    p_setup_labels.set_defaults(func=_do_setup_labels)

    return parser


# --------------------------------------------------------------------------- #
# Entry point


def cli(argv: list[str] | None = None) -> int:
    """Entry point. Parses argv, dispatches, returns the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args) or 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def main() -> None:
    """Console-script entry point. Exits with the CLI's return code."""
    sys.exit(cli())


# --------------------------------------------------------------------------- #
# Helpers: build backend / controller / context


def _ctx_obj_from_args(args: argparse.Namespace) -> dict:
    """Coerce parsed args into the dict shape downstream helpers expect.

    Note: there is no `workflow_name` here. The framework's state-name
    uniqueness invariant means each work item's workflow is uniquely
    determined by its current state (resolved through the registry), and
    multi-workflow queries iterate every workflow in the registry. The
    agent never has a legitimate reason to override that.
    """
    return {
        "repo": args.repo,
        "host": args.host,
        "dry_run": args.dry_run,
        "json_output": args.json_output,
        "agent_home": args.agent_home,
        "agent_role": args.agent_role,
        "workflows_dir": args.workflows_dir,
        "grants_dir": args.grants_dir,
    }


def _resolve_agent_role(ctx: dict) -> str | None:
    """Resolve the agent's role: CLI/env > agent-config > None.

    The CLI flag's argparse default already merges the env var, so by the
    time we read `ctx['agent_role']` it represents either the CLI value or
    the env value (or None). When None, we fall through to the agent
    home's config.json `agent-role` field.
    """
    explicit = ctx.get("agent_role")
    if explicit:
        return str(explicit).strip("{}").strip() or None

    from workflow.config import discover_agent_home, load_agent_config

    agent_home = ctx.get("agent_home") or discover_agent_home()
    if agent_home is None:
        return None
    try:
        config = load_agent_config(agent_home)
    except WorkflowError:
        return None
    cfg_role = config.get("agent-role")
    if isinstance(cfg_role, str):
        return cfg_role.strip("{}").strip() or None
    return None


def _build_backend(ctx_obj: dict) -> WorkflowBackend:
    """Construct the GitHub backend.

    Resolution order, applied independently to `repo` and `host`:

    1. CLI flag (`--repo` / `--host`) or its env-var fallback
       (`WORKFLOW_REPO` / `WORKFLOW_GH_HOST`).
    2. Auto-discovery from `git remote get-url origin` in the cwd —
       the URL embeds both pieces (`git@ghe.acme.com:org/repo.git` yields
       host=`ghe.acme.com`, slug=`org/repo`).
    3. For `host`: leave None so `gh` falls back to its own resolution
       (the user's exported `GH_HOST` or `gh auth login` config).
    4. For `repo`: ConfigError — the tool can't operate without one.

    Discovery is called at most once per invocation, and only if at least
    one of repo/host needs resolution.

    GitHub is currently the only supported backend; there is no `--backend`
    flag because there is no other choice to make.
    """
    repo = ctx_obj.get("repo")
    host = ctx_obj.get("host")
    if not repo or not host:
        from workflow.backends.github import discover_remote_from_git

        discovered_host, discovered_slug = discover_remote_from_git()
        if not repo and discovered_slug:
            repo = discovered_slug
            logger.debug("Auto-discovered repo from git remote: %s", repo)
        if not host and discovered_host:
            # Only carry through a non-github.com host — github.com is
            # gh's default, so leaving host=None there keeps the
            # subprocess env untouched.
            if discovered_host.lower() != "github.com":
                host = discovered_host
                logger.debug("Auto-discovered host from git remote: %s", host)

    if not repo:
        raise ConfigError(
            "GitHub backend requires a repo. Pass --repo owner/name, set "
            "WORKFLOW_REPO, or run from inside a git checkout with an "
            "origin remote pointing at GitHub / GHES."
        )
    return GitHubBackend(repo=repo, host=host)


def _build_context(
    ctx_obj: dict,
    workflow_name: str,
    *,
    require_backend: bool = True,
) -> WorkflowContext:
    """Build a WorkflowContext for a named workflow.

    `workflow_name` is required and identifies which `<name>-lifecycle.mermaid`
    file under `workflows_dir` to load. Callers that resolved the workflow
    via the registry pass it directly.
    """
    backend: WorkflowBackend | None = None
    if require_backend:
        backend = _build_backend(ctx_obj)
    return load_workflow(
        workflow_name=workflow_name,
        workflows_dir=ctx_obj.get("workflows_dir"),
        grants_dir=ctx_obj.get("grants_dir"),
        backend=backend,
        agent_home=ctx_obj.get("agent_home"),
    )


def _build_context_for_issue(
    ctx_obj: dict,
    work_item_id: str,
    fallback_state: str | None = None,
) -> WorkflowContext:
    """Build a workflow context for an operation on a specific issue.

    Always registry-driven:

    1. Build the backend (needed to read the issue).
    2. Build the workflow registry from the repo.
    3. Read the issue's current state.
    4. Look up which workflow contains that state (state-name uniqueness
       across workflows is a framework invariant).
    5. Load that workflow's context.

    If the issue has no current state (rare — typically a brand-new item),
    `fallback_state` is consulted (e.g., for `advance`, the destination
    state can resolve the workflow).

    Raises `ConfigError` if the workflow source tree can't be discovered or
    the issue's state doesn't belong to any known workflow.
    """
    from workflow.config import build_registry

    backend = _build_backend(ctx_obj)
    registry = build_registry(
        agent_home=ctx_obj.get("agent_home"),
        workflows_dir=ctx_obj.get("workflows_dir"),
        backend=backend,
        grants_dir=ctx_obj.get("grants_dir"),
    )
    if registry is None:
        raise ConfigError(
            "No workflows directory found. Pass --workflows-dir, set "
            "WORKFLOWS_DIR, or run from inside a directory whose tree "
            "contains `*-lifecycle.mermaid` files."
        )

    workflow_name: str | None = None
    try:
        item_state = backend.read_work_item(work_item_id)
        if item_state.state:
            workflow_name = registry.find_workflow_for_state(item_state.state)
    except BackendError as exc:
        # Don't fail here; let the controller's read_work_item fail more
        # informatively when the operation runs. We only swallow the error
        # so we can try fallback_state.
        logger.debug("Could not pre-read issue %s: %s", work_item_id, exc)

    if workflow_name is None and fallback_state:
        workflow_name = registry.find_workflow_for_state(fallback_state)

    if workflow_name is None:
        raise ConfigError(
            f"Cannot resolve workflow for issue {work_item_id!r}. The issue "
            "must have a state that belongs to a discovered workflow "
            "(or, for `advance`, a destination state that does)."
        )

    # Build a context for the resolved workflow.
    return _build_context(ctx_obj, require_backend=True, workflow_name=workflow_name)


def _build_controller(context: WorkflowContext, dry_run: bool) -> Controller:
    if context.backend is None:
        raise ConfigError("No backend configured for controller execution.")
    return Controller(
        backend=context.backend,
        lifecycle=context.lifecycle,
        catalog=context.catalog,
        grants=context.grants,
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------- #
# Output formatting


def _print_result(result: OperationResult, *, json_output: bool) -> None:
    if json_output:
        print(_json.dumps(_result_to_dict(result), indent=2, default=str))
        return

    op = result.operation.value
    header = f"{op} on {result.work_item_id}"
    if result.dry_run:
        header += " (dry-run)"
    print(header)
    print("-" * len(header))
    print(
        f"Pre-state: state={result.pre_state.state!r} "
        f"claim={result.pre_state.agent_claim!r} "
        f"awaiting_gate={result.pre_state.awaiting_gate!r} "
        f"audit_pending={result.pre_state.audit_pending!r}"
    )
    print("")
    print("Planned marker change:")
    for line in _format_marker_change(result.plan.change):
        print(f"  {line}")
    print("")
    print("Audit comment:")
    for line in result.plan.audit_comment.splitlines():
        print(f"  {line}")
    if result.plan.packet_body:
        print("")
        print("Packet/feedback body:")
        for line in result.plan.packet_body.splitlines()[:10]:
            print(f"  {line}")
        if len(result.plan.packet_body.splitlines()) > 10:
            print("  ... (truncated)")
    if not result.dry_run and result.post_state is not None:
        print("")
        print(
            f"Post-state: state={result.post_state.state!r} "
            f"claim={result.post_state.agent_claim!r} "
            f"awaiting_gate={result.post_state.awaiting_gate!r} "
            f"audit_pending={result.post_state.audit_pending!r}"
        )
    if result.findings:
        print("")
        print("Findings:")
        for f in result.findings:
            print(f"  {f}")


def _format_marker_change(change: Any) -> list[str]:
    out: list[str] = []
    for key, value in asdict(change).items():
        if value is None or value is False:
            continue
        out.append(f"{key} = {value!r}")
    return out or ["(no marker changes)"]


def _result_to_dict(result: OperationResult) -> dict:
    return {
        "operation": result.operation.value,
        "work_item_id": result.work_item_id,
        "dry_run": result.dry_run,
        "pre_state": asdict(result.pre_state),
        "post_state": asdict(result.post_state) if result.post_state else None,
        "plan": {
            "change": asdict(result.plan.change),
            "audit_comment": result.plan.audit_comment,
            "packet_body": result.plan.packet_body,
        },
        "findings": [
            {
                "severity": f.severity.value,
                "principle_cite": f.principle_cite,
                "message": f.message,
                "location": f.location,
            }
            for f in result.findings
        ],
    }


def _handle_workflow_error(exc: WorkflowError) -> None:
    """Print a friendly message; the caller is responsible for the exit code."""
    print(f"error: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Subcommand handlers


def _do_advance(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        # advance can fall back to args.destination if the issue has no state.
        context = _build_context_for_issue(ctx, args.issue, fallback_state=args.destination)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        body_path: str | None = None
        if args.packet_from is not None:
            body_path = str(args.packet_from)
        elif args.notes_from is not None:
            body_path = str(args.notes_from)
        result = advance_op.run(
            controller,
            work_item_id=args.issue,
            destination=args.destination,
            body_path=body_path,
            actor=context.agent_role,
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_claim(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        # If --role wasn't passed, fall back to the agent-config role.
        role = args.role or context.agent_role
        if not role:
            raise ConfigError(
                "claim requires --role, or `role` configured in <agent-home>/.workflow/config.json."
            )
        result = claim_op.run(
            controller,
            work_item_id=args.issue,
            role=role,
            transition_label=args.transition_label,
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_release(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = release_op.run(controller, work_item_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_review(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = review_op.run(controller, work_item_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_approve(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        comment_from: str | None = str(args.comment_from) if args.comment_from else None
        result = approve_op.run(
            controller,
            work_item_id=args.issue,
            gate=args.gate,
            destination=args.destination,
            comment_from=comment_from,
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_reject(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = reject_op.run(
            controller,
            work_item_id=args.issue,
            gate=args.gate,
            feedback_from=str(args.feedback_from),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_audit(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = audit_op.run(controller, work_item_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_check(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = check_op.run(controller, work_item_id=args.issue, gate=args.gate)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_revoke(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = revoke_op.run(
            controller,
            work_item_id=args.issue,
            gate=args.gate,
            concern_from=str(args.concern_from),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_request_input(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = request_input_op.run(
            controller,
            work_item_id=args.issue,
            question_from=str(args.question_from),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_advise(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = advise_op.run(controller, work_item_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _do_resolve(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = resolve_op.run(
            controller,
            work_item_id=args.issue,
            response_from=str(args.response_from),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2


def _list_for_role(
    ctx: dict,
    backend: WorkflowBackend,
    role: str,
    limit: int,
) -> list:
    """Compute the role's open work across ALL workflows: inbox + actionable wip.

    Roles often participate in multiple workflows (a PM does refinement,
    postmortem, prioritization). The query uses the workflow registry to
    aggregate inbox states from every workflow whose lifecycle declares
    `claim-role={role}` on a resting state.

    Two categories, deduplicated by work-item id:

    1. **Inbox** — items in resting states with `claim_role == role` and
       no current claim. Aggregated across all workflows.

    2. **Actionable wip** — items with `wip:{role}` where the agent is not
       blocked waiting on a human: no `hitl:awaiting-*`, no `hitl:audit-*`,
       no `hitl:awaiting-input`. (Backend-level filter; not workflow-scoped.)

    Excludes:
      - items where the agent is currently blocked on a human signal
      - items already claimed by another role
    """
    from workflow.backends.base import WorkItemFilters
    from workflow.config import build_registry

    seen: dict[str, Any] = {}
    inbox_states: set[str] = set()

    # Aggregate inbox states from every workflow in the registry whose
    # lifecycle declares this role's claim_role on a resting state.
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflows_dir=ctx.get("workflows_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        raise ConfigError(
            "No workflows directory found. Set WORKFLOWS_DIR or run inside "
            "a directory whose tree contains `*-lifecycle.mermaid` files."
        )
    for wf_name in registry.discovered_workflows():
        try:
            wf_context = registry.get_workflow(wf_name)
        except (ConfigError, Exception) as exc:
            logger.debug("Skipping workflow %r: %s", wf_name, exc)
            continue
        inbox_states |= _states_claimed_by_role(wf_context.lifecycle, role)

    # 1. Inbox: query the backend for each discovered inbox state.
    for state_name in inbox_states:
        for item in backend.list_work_items(WorkItemFilters(state=state_name, limit=limit)):
            if item.agent_claim is None and item.work_item_id not in seen:
                seen[item.work_item_id] = item

    # 2. Actionable wip: wip:{role} AND no awaiting/audit/awaiting-input markers.
    # (This is workflow-agnostic — wip labels don't care about which workflow
    # the item belongs to.)
    for item in backend.list_work_items(WorkItemFilters(claim_role=role, limit=limit)):
        if (
            item.awaiting_gate is None
            and item.audit_pending is None
            and not item.awaiting_input
            and item.work_item_id not in seen
        ):
            seen[item.work_item_id] = item

    return list(seen.values())


def _states_claimed_by_role(lifecycle: Any, role: str) -> set[str]:
    """Find resting states whose `claim_role` matches the given role.

    `claim_role` is a structured field on `State`, declared in the lifecycle
    file via a note such as `note left of raw: claim-role=pm`. Role match is
    case-insensitive and ignores `{...}` placeholder braces.
    """
    role_normalized = role.strip("{}").lower()
    matches: set[str] = set()
    for state in lifecycle.states.values():
        if state.claim_role is None:
            continue
        if state.claim_role.strip("{}").lower() == role_normalized:
            matches.add(state.name)
    return matches


def _do_create(args: argparse.Namespace) -> int:
    """Create a new work item in the given initial state.

    The workflow is resolved from `--to` via the registry (state names are
    unique across the registry). If `--claim` is passed, the agent's role
    is added as a `wip:<role>` label atomically with creation.
    """
    from workflow.config import build_registry

    ctx = _ctx_obj_from_args(args)

    # 1. Resolve workflow + validate state via the registry.
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflows_dir=ctx.get("workflows_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        _handle_workflow_error(
            ConfigError(
                "No workflows directory found. Set WORKFLOWS_DIR or run from "
                "inside a tree containing `*-lifecycle.mermaid` files."
            )
        )
        return 2

    workflow_name = registry.find_workflow_for_state(args.initial_state)
    if workflow_name is None:
        _handle_workflow_error(
            ConfigError(f"State {args.initial_state!r} is not declared in any discovered workflow.")
        )
        return 2

    # 2. Resolve claim role if --claim is set.
    claim_role: str | None = None
    if args.claim:
        claim_role = _resolve_agent_role(ctx)
        if not claim_role:
            _handle_workflow_error(
                ConfigError(
                    "--claim requires an agent role. Pass --agent-role or "
                    "set AGENT_ROLE / config agent-role."
                )
            )
            return 2

    # 3. Load body.
    if args.body_from is not None:
        try:
            body = Path(args.body_from).read_text(encoding="utf-8")
        except OSError as exc:
            _handle_workflow_error(ConfigError(f"Could not read body file {args.body_from}: {exc}"))
            return 2
    elif args.body is not None:
        body = args.body
    else:
        body = ""

    # 4. Dry run path: print the plan, don't touch the backend.
    if ctx["dry_run"]:
        extras = [f"wip:{claim_role}"] if claim_role else []
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "workflow": workflow_name,
                        "initial_state": args.initial_state,
                        "title": args.title,
                        "labels": [f"state:{args.initial_state}", *extras],
                        "body_chars": len(body),
                        "dry_run": True,
                    },
                    indent=2,
                )
            )
        else:
            print(f"[dry-run] would create issue in workflow {workflow_name!r}:")
            print(f"  title:         {args.title}")
            print(f"  initial state: {args.initial_state}")
            if claim_role:
                print(f"  claim:         {claim_role}")
            print(f"  body:          {len(body)} character(s)")
        return 0

    # 5. Build backend + create.
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    extra_labels = [f"wip:{claim_role}"] if claim_role else []
    try:
        new_id = backend.create_work_item(
            title=args.title,
            body=body,
            state=args.initial_state,
            extra_labels=extra_labels,
        )
    except BackendError as exc:
        _handle_workflow_error(exc)
        return 2

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "id": new_id,
                    "workflow": workflow_name,
                    "state": args.initial_state,
                    "title": args.title,
                    "claim": claim_role,
                },
                indent=2,
            )
        )
    else:
        suffix = f", claimed by {claim_role}" if claim_role else ""
        print(
            f"Created issue #{new_id} in {workflow_name!r} workflow, "
            f"state {args.initial_state!r}{suffix}."
        )
    return 0


def _do_inbox(args: argparse.Namespace) -> int:
    """Show the agent's own inbox + actionable wip.

    Role resolution: --agent-role flag > AGENT_ROLE env > agent config
    `agent-role`. Errors if none is set, since inbox has no meaning
    without an agent identity.
    """
    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    role = _resolve_agent_role(ctx)
    if not role:
        _handle_workflow_error(
            ConfigError(
                "inbox needs an agent role. Pass --agent-role, set "
                "AGENT_ROLE, or run `workflow init --agent-role <role>` to "
                "persist one in the agent config."
            )
        )
        return 2

    try:
        items = _list_for_role(ctx, backend, role, args.limit)
    except (ConfigError, BackendError) as exc:
        _handle_workflow_error(exc)
        return 2

    return _emit_work_items(
        items, ctx, empty_message=f"(no inbox items or actionable wip for {role!r})"
    )


def _do_search(args: argparse.Namespace) -> int:
    """Search work items by framework filters."""
    from workflow.backends.base import WorkItemFilters

    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    try:
        filters = WorkItemFilters(
            state=args.state,
            claim_role=args.claim_role,
            awaiting_gate=args.awaiting_gate,
            audit_pending=args.audit_pending,
            awaiting_input=args.awaiting_input,
            limit=args.limit,
        )
        items = backend.list_work_items(filters)
    except (ConfigError, BackendError) as exc:
        _handle_workflow_error(exc)
        return 2

    return _emit_work_items(items, ctx, empty_message="(no work items matched the filters)")


def _emit_work_items(items: list[Any], ctx: dict, *, empty_message: str = "(no work items)") -> int:
    """Render a list of WorkItemState objects as JSON or a columnar table.

    Shared between inbox and search.
    """
    if ctx["json_output"]:
        print(
            _json.dumps(
                [
                    {
                        "id": item.work_item_id,
                        "state": item.state,
                        "claim": item.agent_claim,
                        "awaiting_gate": item.awaiting_gate,
                        "audit_pending": item.audit_pending,
                        "awaiting_input": item.awaiting_input,
                        "title": item.extras.get("title"),
                    }
                    for item in items
                ],
                indent=2,
            )
        )
        return 0

    if not items:
        print(empty_message)
        return 0

    headers = ("ID", "STATE", "CLAIM", "HITL", "TITLE")
    rows: list[tuple[str, str, str, str, str]] = []
    for item in items:
        hitl_marker = "-"
        if item.awaiting_gate:
            hitl_marker = f"awaiting-{item.awaiting_gate}"
        elif item.audit_pending:
            hitl_marker = f"audit-{item.audit_pending}"
        elif item.awaiting_input:
            hitl_marker = "awaiting-input"
        title = item.extras.get("title", "")
        rows.append(
            (
                f"#{item.work_item_id}",
                item.state or "-",
                item.agent_claim or "-",
                hitl_marker,
                title[:60] + ("…" if len(title) > 60 else ""),
            )
        )

    widths = [max(len(headers[i]), max(len(r[i]) for r in rows)) for i in range(len(headers))]

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(row))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))
    print("")
    print(f"({len(rows)} item(s))")
    return 0


def _do_comment(args: argparse.Namespace) -> int:
    """Post a free-form comment on an issue. Backend-only; no workflow context."""
    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    if args.body is not None:
        body = args.body
    else:
        try:
            body = Path(args.body_from).read_text(encoding="utf-8")
        except OSError as exc:
            _handle_workflow_error(ConfigError(f"Could not read body file {args.body_from}: {exc}"))
            return 2

    if not body.strip():
        _handle_workflow_error(ConfigError("Comment body is empty; refusing to post."))
        return 2

    if ctx["dry_run"]:
        print(f"[dry-run] would post comment on #{args.issue}:")
        for line in body.splitlines()[:10]:
            print(f"  {line}")
        if len(body.splitlines()) > 10:
            print("  ... (truncated)")
        return 0

    try:
        backend.post_comment(args.issue, body)
    except BackendError as exc:
        _handle_workflow_error(exc)
        return 2

    if ctx["json_output"]:
        print(_json.dumps({"issue": args.issue, "posted": True}, indent=2))
    else:
        print(f"Comment posted on #{args.issue}.")
    return 0


def _do_view(args: argparse.Namespace) -> int:
    """View one work item's framework-relevant state and recent comments."""
    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    try:
        state = backend.read_work_item(args.issue)
    except BackendError as exc:
        _handle_workflow_error(exc)
        return 2

    # `args.comments` semantics: None → all comments, 0 → omit, N>0 → last N.
    comments: list[dict] = []
    if args.comments != 0:
        try:
            comments = backend.read_comments(args.issue)
        except BackendError as exc:
            # Not fatal — surface a warning but keep going.
            print(f"warning: could not read comments: {exc}", file=sys.stderr)
        # Most-recent first.
        comments = list(reversed(comments))
        if args.comments is not None and args.comments > 0:
            comments = comments[: args.comments]

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "id": state.work_item_id,
                    "state": state.state,
                    "claim": state.agent_claim,
                    "awaiting_gate": state.awaiting_gate,
                    "reviewing": state.reviewing,
                    "audit_pending": state.audit_pending,
                    "auditing": state.auditing,
                    "awaiting_input": state.awaiting_input,
                    "advising": state.advising,
                    "title": state.extras.get("title"),
                    "comments": comments,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    title = state.extras.get("title", "")
    header = f"#{state.work_item_id}" + (f" — {title}" if title else "")
    print(header)
    print("-" * len(header))
    print(f"state:           {state.state or '-'}")
    print(f"claim:           {state.agent_claim or '-'}")
    print(f"awaiting gate:   {state.awaiting_gate or '-'}")
    print(f"audit pending:   {state.audit_pending or '-'}")
    print(f"awaiting input:  {'yes' if state.awaiting_input else 'no'}")
    human_claim = None
    if state.reviewing:
        human_claim = "reviewing"
    elif state.auditing:
        human_claim = "auditing"
    elif state.advising:
        human_claim = "advising"
    print(f"human claim:     {human_claim or '-'}")

    if comments:
        print("")
        print(f"Recent comments ({len(comments)}):")
        for c in comments:
            author = c.get("author", "?")
            created = c.get("created_at", "")
            body = c.get("body", "").strip().splitlines()
            first_line = body[0] if body else ""
            preview = first_line[:120] + ("…" if len(first_line) > 120 else "")
            print(f"  [{created}] {author}: {preview}")

    return 0


def _do_validate(args: argparse.Namespace) -> int:
    """Validate workflow artifacts against the framework principles.

    Iterates every workflow in the registry (`--workflows-dir` / `WORKFLOWS_DIR`
    / discovered by walking up from cwd) and reports per-workflow findings.
    """
    from workflow.config import build_registry

    ctx = _ctx_obj_from_args(args)
    grants_dir = ctx.get("grants_dir")
    json_output = ctx.get("json_output", False)

    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflows_dir=ctx.get("workflows_dir"),
        backend=None,
        grants_dir=grants_dir,
    )
    if registry is None:
        _handle_workflow_error(
            ConfigError(
                "No workflows directory found. Pass --workflows-dir, set "
                "WORKFLOWS_DIR, or run from inside a directory whose tree "
                "contains `*-lifecycle.mermaid` files."
            )
        )
        return 2

    results: list[tuple[str, Any, Any, list]] = []
    for wf_name in registry.discovered_workflows():
        try:
            wf_context = registry.get_workflow(wf_name)
        except (ConfigError, ParseError) as exc:
            logger.warning("Skipping workflow %r: %s", wf_name, exc)
            continue
        findings = validate_workflow(wf_context.lifecycle, wf_context.catalog, wf_context.grants)
        results.append((wf_name, wf_context.lifecycle, wf_context.catalog, findings))

    return _emit_validate_result(results, json_output=json_output)


def _emit_validate_result(
    results: list[tuple[str, Any, Any, list]],
    *,
    json_output: bool,
) -> int:
    """Print validate findings for one or more workflows; return exit code."""
    if json_output:
        print(
            _json.dumps(
                {
                    "workflows": [
                        {
                            "name": name,
                            "lifecycle": lifecycle.source_path,
                            "hcp_catalog": catalog.source_path if catalog else None,
                            "findings": [
                                {
                                    "severity": f.severity.value,
                                    "principle_cite": f.principle_cite,
                                    "message": f.message,
                                    "location": f.location,
                                }
                                for f in findings
                            ],
                        }
                        for name, lifecycle, catalog, findings in results
                    ]
                },
                indent=2,
            )
        )
    else:
        total_counts = {"error": 0, "warning": 0, "info": 0}
        for name, _lifecycle, _catalog, findings in results:
            header = f"workflow: {name}"
            print(header)
            print("-" * len(header))
            if not findings:
                print("  OK: no findings.")
            else:
                for f in findings:
                    total_counts[f.severity.value] = total_counts.get(f.severity.value, 0) + 1
                    print(f"  {f}")
            print("")
        print(
            "Summary: "
            f"{total_counts['error']} error(s), "
            f"{total_counts['warning']} warning(s), "
            f"{total_counts['info']} info "
            f"across {len(results)} workflow(s)."
        )

    has_errors = any(
        f.severity is Severity.ERROR for _n, _l, _c, findings in results for f in findings
    )
    return 1 if has_errors else 0


def _do_doctor(args: argparse.Namespace) -> int:
    """Diagnose the workflow configuration.

    Enumerates every workflow in the registry (no single-workflow concept).
    Surfaces agent-home discovery, registry contents, and optional backend
    reachability when --repo / discovery can resolve one.
    """
    from workflow.config import build_registry, discover_agent_home, load_agent_config

    ctx = _ctx_obj_from_args(args)
    issues: list[str] = []

    # 1. Agent home + config (no backend, no workflows yet).
    agent_home = ctx.get("agent_home") or discover_agent_home()
    if agent_home is not None:
        print(f"agent home: {agent_home}")
        try:
            agent_config = load_agent_config(agent_home)
            if agent_config:
                for key, value in agent_config.items():
                    print(f"  {key} = {value!r}")
            else:
                print("  (config.json is empty)")
        except WorkflowError as exc:
            issues.append(f"agent config: {exc}")
            print(f"  ERROR — {exc}")
    else:
        print("agent home: <not discovered>")

    # 2. Registry: enumerate every workflow found in the workflows dir.
    print("")
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflows_dir=ctx.get("workflows_dir"),
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        issues.append(
            "registry: no workflows directory found "
            "(set WORKFLOWS_DIR or cd into a tree containing "
            "`*-lifecycle.mermaid` files)."
        )
        print("registry: <not discovered>")
    else:
        names = registry.discovered_workflows()
        print(f"registry: {len(names)} workflow(s) discovered")
        for wf_name in names:
            try:
                wf_context = registry.get_workflow(wf_name)
            except WorkflowError as exc:
                issues.append(f"workflow {wf_name}: {exc}")
                print(f"  {wf_name}: ERROR — {exc}")
                continue
            gates = len(wf_context.catalog.entries) if wf_context.catalog else 0
            grants = len(wf_context.grants)
            print(
                f"  {wf_name}: "
                f"{len(wf_context.lifecycle.states)} state(s), "
                f"{gates} catalogued gate(s), "
                f"{grants} trust grant(s)"
            )

    # 3. Backend reachability (optional).
    print("")
    try:
        backend = _build_backend(ctx)
        host_suffix = f" @ {backend.host}" if backend.host else ""
        print(f"backend: {backend.name} on {backend.repo}{host_suffix}")
    except WorkflowError as exc:
        # Backend resolution is informational here; don't count it as an
        # issue unless --repo was explicitly attempted.
        if ctx.get("repo"):
            issues.append(f"backend: {exc}")
            print(f"backend: ERROR — {exc}")
        else:
            print(f"backend: <not resolved> ({exc})")

    if issues:
        print("")
        print(f"doctor: {len(issues)} issue(s) found.")
        return 1
    print("")
    print("doctor: OK.")
    return 0


def _do_init(args: argparse.Namespace) -> int:
    """Scaffold an agent home: write .workflow/config.json + trust-grants/.

    The target directory is the global `--agent-home` value (or AGENT_HOME
    env), defaulting to cwd when neither is set. The agent's role comes
    from the global `--agent-role` (or AGENT_ROLE env) — required for init,
    since there's no existing config to fall back on.
    """
    ctx = _ctx_obj_from_args(args)

    agent_role = ctx.get("agent_role")
    if not agent_role:
        _handle_workflow_error(
            ConfigError(
                "init requires --agent-role (or AGENT_ROLE env). "
                "Example: workflow --agent-role pm init"
            )
        )
        return 2

    target = (ctx.get("agent_home") or Path.cwd()).resolve()
    workflow_dir = target / ".workflow"
    config_path = workflow_dir / "config.json"
    grants_path = workflow_dir / "trust-grants"
    workflows_path = workflow_dir / "workflows"

    if config_path.exists() and not args.force:
        _handle_workflow_error(
            ConfigError(f"{config_path} already exists. Pass --force to overwrite.")
        )
        return 2

    try:
        workflow_dir.mkdir(parents=True, exist_ok=True)
        grants_path.mkdir(parents=True, exist_ok=True)
        workflows_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _handle_workflow_error(ConfigError(f"Could not create {workflow_dir}: {exc}"))
        return 2

    # Build the config from provided values. Strip placeholder braces from
    # the role so `{pm}` and `pm` both produce the same `pm`.
    #
    # Only `agent-role` is persisted by default: `repo`/`host`/`workflow`
    # flags are per-invocation, and the path config keys (`workflows-dir`,
    # `grants-dir`) are written separately when explicitly provided. The
    # file stays minimal so adding future agent-identity fields is
    # unambiguous.
    config: dict[str, Any] = {"agent-role": agent_role.strip("{}").strip()}

    if ctx["dry_run"]:
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "agent_home": str(target),
                        "config_path": str(config_path),
                        "workflows_dir": str(workflows_path),
                        "grants_dir": str(grants_path),
                        "config": config,
                        "dry_run": True,
                    },
                    indent=2,
                )
            )
        else:
            print(f"[dry-run] would initialize agent home at {target}")
            print(f"  config:        {config_path}")
            print(f"  workflows:     {workflows_path}/")
            print(f"  trust grants:  {grants_path}/")
            print("")
            print("Config that would be written:")
            for line in _json.dumps(config, indent=2).splitlines():
                print(f"  {line}")
        return 0

    try:
        config_path.write_text(
            _json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        _handle_workflow_error(ConfigError(f"Could not write {config_path}: {exc}"))
        return 2

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "agent_home": str(target),
                    "config_path": str(config_path),
                    "workflows_dir": str(workflows_path),
                    "grants_dir": str(grants_path),
                    "config": config,
                },
                indent=2,
            )
        )
    else:
        print(f"Initialized agent home at {target}")
        print(f"  config:        {config_path}")
        print(f"  workflows:     {workflows_path}/")
        print(f"  trust grants:  {grants_path}/")
        print("")
        print("Config:")
        for key, value in config.items():
            print(f"  {key} = {value!r}")
    return 0


def _enumerate_required_labels(ctx: dict) -> set[str]:
    """Compute the full set of label names the framework requires for a repo.

    Sources, aggregated across every workflow in the registry:

    - `state:<name>` for every state in every lifecycle.
    - `wip:<role_id>` for every role in roles.json.
    - The five HITL singleton labels (`hitl:reviewing`, `hitl:auditing`,
      `hitl:advising`, `hitl:awaiting-input`, `hitl:resolved`).
    - `hitl:awaiting-<gate>` for every HCP whose `allowed_levels` includes
      BLOCK.
    - `hitl:audit-<gate>` for every HCP whose `allowed_levels` includes
      AUDIT.

    Signal markers (`hitl:approved-*`, `hitl:rejected-*`, `hitl:checked-*`,
    `hitl:revoked-*`) are intentionally omitted — they're transient
    audit-trace labels and the create-on-first-use path during marker
    changes handles them naturally.

    Raises `ConfigError` if no workflow source tree can be discovered.
    """
    from workflow.config import build_registry
    from workflow.core.model.hcp import HCPLevel

    labels: set[str] = {
        "hitl:reviewing",
        "hitl:auditing",
        "hitl:advising",
        "hitl:awaiting-input",
        "hitl:resolved",
    }

    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflows_dir=ctx.get("workflows_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        raise ConfigError(
            "No workflows directory found. Set WORKFLOWS_DIR or run from "
            "inside a tree containing `*-lifecycle.mermaid` files."
        )

    for wf_name in registry.discovered_workflows():
        try:
            wf_context = registry.get_workflow(wf_name)
        except WorkflowError as exc:
            logger.warning("Skipping workflow %r during label enumeration: %s", wf_name, exc)
            continue

        for state_name in wf_context.lifecycle.states:
            labels.add(f"state:{state_name}")

        if wf_context.catalog:
            for hcp in wf_context.catalog.entries.values():
                if HCPLevel.BLOCK in hcp.allowed_levels:
                    labels.add(f"hitl:awaiting-{hcp.gate_name}")
                if HCPLevel.AUDIT in hcp.allowed_levels:
                    labels.add(f"hitl:audit-{hcp.gate_name}")

        if wf_context.role_directory:
            for role_id in wf_context.role_directory.roles:
                labels.add(f"wip:{role_id}")

    return labels


def _do_setup_labels(args: argparse.Namespace) -> int:
    """Provision every framework-required label on the configured repo."""
    ctx = _ctx_obj_from_args(args)

    try:
        required = _enumerate_required_labels(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    if ctx["dry_run"]:
        if ctx["json_output"]:
            print(_json.dumps({"labels": sorted(required)}, indent=2))
        else:
            print(f"[dry-run] would ensure {len(required)} label(s) on repo:")
            for name in sorted(required):
                print(f"  {name}")
        return 0

    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        _handle_workflow_error(exc)
        return 2

    # Snapshot existing labels first so we can report created vs skipped.
    try:
        existing = set(backend.list_labels())
    except BackendError as exc:
        _handle_workflow_error(exc)
        return 2

    created: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for name in sorted(required):
        if name in existing:
            skipped.append(name)
            continue
        try:
            was_created = backend.ensure_label(name)
        except BackendError as exc:
            failed.append((name, str(exc)))
            continue
        if was_created:
            created.append(name)
        else:
            # Backend reported the label already existed despite our
            # snapshot (race or stale cache). Treat as skipped.
            skipped.append(name)

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "created": created,
                    "skipped": skipped,
                    "failed": [{"label": n, "error": e} for n, e in failed],
                },
                indent=2,
            )
        )
    else:
        print(
            f"setup-labels: created {len(created)}, "
            f"skipped {len(skipped)} (already existed), "
            f"failed {len(failed)}."
        )
        if created:
            print("Created:")
            for name in created:
                print(f"  + {name}")
        if failed:
            print("Failed:")
            for name, err in failed:
                print(f"  ! {name}: {err}")

    return 1 if failed else 0


if __name__ == "__main__":
    main()
