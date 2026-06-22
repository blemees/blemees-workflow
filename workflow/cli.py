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
from workflow.backends import github_labels as gh_labels
from workflow.backends.base import TrackerBackend
from workflow.backends.github import GitHubBackend
from workflow.config import Process, load_process
from workflow.core.controller import Controller, OperationResult
from workflow.core.operations import (
    advance_issue as advance_issue_op,
)
from workflow.core.operations import (
    approve_audit as approve_audit_op,
)
from workflow.core.operations import (
    approve_blocked as approve_blocked_op,
)
from workflow.core.operations import (
    claim_issue as claim_issue_op,
)
from workflow.core.operations import (
    collect_into as collect_into_op,
)
from workflow.core.operations import (
    create_issue as create_issue_op,
)
from workflow.core.operations import (
    reject_audit as reject_audit_op,
)
from workflow.core.operations import (
    reject_blocked as reject_blocked_op,
)
from workflow.core.operations import (
    release_issue as release_issue_op,
)
from workflow.core.operations import (
    request_input as request_input_op,
)
from workflow.core.operations import (
    respond_request as respond_request_op,
)
from workflow.core.operations import (
    review_audit as review_audit_op,
)
from workflow.core.operations import (
    review_blocked as review_blocked_op,
)
from workflow.core.operations import (
    review_request as review_request_op,
)
from workflow.core.operations import (
    spawn_issue as spawn_issue_op,
)

# await_signal and record_action remain importable as internal primitives,
# but are not exposed as CLI subcommands. `advance-issue` dispatches into
# them based on the human-gate catalog.
from workflow.core.validator import Severity, validate_state_machine
from workflow.errors import (
    BackendError,
    ConfigError,
    OperationError,
    ParseError,
    WorkflowError,
)

logger = logging.getLogger(__name__)

# Exit codes (documented surface — agent scripts branch on these). argparse
# reserves 2 for usage errors, so framework failures never reuse it (#26).
EXIT_OK = 0
EXIT_VALIDATION = 1  # validate-workflow / doctor-workflow reported findings
EXIT_USAGE = 2  # argparse usage error (bad invocation) — set by argparse itself
EXIT_OPERATION = 3  # OperationError — precondition not met; change state, then retry
EXIT_BACKEND = 4  # BackendError — tracker/network failure; usually retryable as-is
EXIT_CONFIG = 5  # ConfigError / ParseError — malformed or unresolvable workflow files


def _exit_code_for(exc: WorkflowError) -> int:
    """Map a WorkflowError to its documented exit code (#26)."""
    if isinstance(exc, OperationError):
        return EXIT_OPERATION
    if isinstance(exc, BackendError):
        return EXIT_BACKEND
    # ConfigError, ParseError, and any other WorkflowError → "fix your inputs".
    return EXIT_CONFIG


PROG = "workflow"


# --------------------------------------------------------------------------- #
# argparse type helpers


def _add_body_args(parser: argparse.ArgumentParser, *, required: bool) -> None:
    """Add the standard `--body` / `--body-from` mutually exclusive group.

    Every command that posts a markdown body to the issue uses the same
    pair: inline content via `--body "text"` or a file via
    `--body-from path.md`. The group is `required=True` for commands where
    the body is mandatory (reject, revoke, request-input, respond), and
    `required=False` for commands where it's optional (advance, approve).
    """
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--body", default=None, help="Inline markdown content.")
    group.add_argument(
        "--body-from",
        dest="body_from",
        type=_path_existing_file,
        default=None,
        help="Path to a markdown file whose content becomes the body.",
    )


def _resolve_body(args: argparse.Namespace) -> str | None:
    """Read the body text from either `--body` (inline) or `--body-from` (file).

    Returns None when neither is set (optional cases). A body that is *provided
    but empty* (`--body ""` or an empty `--body-from` file) is rejected with a
    crisp `ConfigError`: argparse's required-body groups are satisfied by the
    flag's mere presence, so an empty string would otherwise slip past the CLI
    and fail later or post nothing (#26). Errors via `ConfigError` if the file
    can't be read.
    """
    body = getattr(args, "body", None)
    if body is not None:
        if not str(body).strip():
            raise ConfigError("--body was provided but is empty; give non-empty text or omit it.")
        return str(body)
    body_from = getattr(args, "body_from", None)
    if body_from is None:
        return None
    try:
        text = Path(body_from).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read body file {body_from}: {exc}") from exc
    if not text.strip():
        raise ConfigError(f"Body file {body_from} is empty; give non-empty text or omit it.")
    return text


def _format_pr_body(user_body: str, refs: list[str]) -> str:
    """Apply the framework's standard PR message format.

    Appends a horizontal rule and a `Refs #N, #M, ...` footer to the
    user-supplied body. Each ref is normalised to a `#N` form (leading
    `#` stripped from input then re-added) so callers can pass either
    `123` or `#123`. GitHub auto-links these as cross-references.
    """
    refs_line = ", ".join(f"#{r.lstrip('#')}" for r in refs)
    return f"{user_body.rstrip()}\n\n---\n\nRefs {refs_line}\n"


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
  create-issue, spawn-issue             — open a new issue in a given initial state
  collect-into                          — add contributor issues to a collector (states with `collects`)
  advance-issue, claim-issue, release-issue
                                        — workflow ownership and state changes
  request-input                         — recognized HITL (state-orthogonal pause)

Human-facing commands (blocked gates):
  review-blocked, approve-blocked, reject-blocked
                                        — pre-action HITL signals (block level)

Human-facing commands (audit gates):
  review-audit, approve-audit, reject-audit
                                        — post-action HITL signals (audit level)

Human-facing commands (input requests):
  review-request, respond-request       — recognized HITL responses

Discovery and utility commands (anyone):
  view-inbox                            — show this agent's claimable items + actionable wip
  search-issues                         — find issues by state, claim, or HITL marker
  view-issue                            — inspect one issue's state and recent comments
  post-comment                          — post a free-form comment without advancing state
  edit-issue                            — edit title or body (no state change)

The agent invokes `advance-issue` for every transition; the tool consults the
human-gate catalog and team trust grants to determine whether the transition is
ungated, block-gated, or audit-gated, and applies the right primitive
automatically. The `await-signal` and `record-action` primitives exist
inside the framework but are never invoked directly by the agent.

Plus utility commands: init-agent, validate-workflow, doctor-workflow, setup-github, capabilities, generate-docs.
"""


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    """Add the global options to the top-level parser.

    These are defined on the top-level parser ONLY (not inherited by
    subparsers via `parents=`), so they must appear **before** the subcommand
    name — `workflow --repo owner/name view-issue --issue 5`, not
    `workflow view-issue --issue 5 --repo owner/name`. This matches click's
    group-option placement (see `build_parser`). Putting a global flag after
    the subcommand is an argparse usage error (#26).
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
        "for any directory containing `.workflow/`. For `init-agent`, this is "
        "the target directory where the agent home will be created "
        "(defaults to cwd in that case). Env: AGENT_HOME.",
    )
    parser.add_argument(
        "--workflow-dir",
        dest="workflow_dir",
        type=_path_existing_dir,
        default=(
            Path(os.environ["WORKFLOW_DIR"]).expanduser()
            if os.environ.get("WORKFLOW_DIR")
            else None
        ),
        help="Directory containing the workflow files "
        "(`*-states.json`, `*-human-gates.json`, `roles.json`). "
        "If unset, resolved from the WORKFLOW_DIR env var, then the agent "
        "config's `workflow-dir` key, then `<agent-home>/.workflow/workflows/`. "
        "Discovery is agent-scoped — it does NOT walk up from cwd. "
        "Env: WORKFLOW_DIR.",
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

    # --- StateMachine operations ---

    p_advance_issue = subparsers.add_parser(
        "advance-issue",
        help="Advance an issue to a target state.",
        description=(
            "Advance an issue to a target state. The tool finds the "
            "transition from the current state to --to and applies the right "
            "behavior based on the workflow and HumanGate catalog: ungated "
            "transitions change state immediately; block-gated transitions "
            "pause for human signal (requires --body or --body-from); "
            "audit-gated transitions change state atomically with the "
            "audit-pending marker. The agent does not need to know which "
            "path applies — the catalog decides."
        ),
    )
    p_advance_issue.add_argument(
        "--to",
        dest="destination",
        required=True,
        help="Target state to advance to (e.g., ready_for_dev).",
    )
    p_advance_issue.add_argument("--issue", required=True, help="Issue identifier.")
    _add_body_args(p_advance_issue, required=False)
    p_advance_issue.set_defaults(func=_do_advance_issue)

    p_event_fired = subparsers.add_parser(
        "event-fired",
        help="Fire a system/event-type transition (no agent role required).",
        description=(
            "Fire an `event`-type transition. Distinct from `advance-issue`: events "
            "represent system or time triggers (webhook, cron, SLA expiry), "
            "not agent-driven progress. No agent-role check is performed. "
            "Use --triggered-by to record the source of the event in the "
            "audit comment (e.g., 'github-webhook:pr_merged', 'cron:sla_check'). "
            "Refuses if the resolved transition isn't type `event`."
        ),
    )
    p_event_fired.add_argument(
        "--to",
        dest="destination",
        required=True,
        help="Target state for the event transition.",
    )
    p_event_fired.add_argument("--issue", required=True, help="Issue identifier.")
    p_event_fired.add_argument(
        "--triggered-by",
        dest="triggered_by",
        default=None,
        help="String identifying the event source (e.g., "
        "'github-webhook:pr_merged'). Recorded in the audit comment.",
    )
    _add_body_args(p_event_fired, required=False)
    p_event_fired.set_defaults(func=_do_event_fired)

    p_claim_issue = subparsers.add_parser(
        "claim-issue",
        help="Take responsibility for a resting issue.",
    )
    p_claim_issue.add_argument("--issue", required=True)
    p_claim_issue.add_argument(
        "--to",
        dest="destination",
        default=None,
        help="Target working state. Optional when only one CLAIM transition "
        "exists from the current state; required when multiple do.",
    )
    p_claim_issue.set_defaults(func=_do_claim_issue)

    p_release_issue = subparsers.add_parser(
        "release-issue",
        help="Give up the claim on an issue.",
    )
    p_release_issue.add_argument("--issue", required=True)
    p_release_issue.set_defaults(func=_do_release_issue)

    p_spawn_issue = subparsers.add_parser(
        "spawn-issue",
        help="Spawn a subprocess / follow-up issue per the parent state's `spawns` config.",
        description=(
            "Spawn a child issue on the target process declared by the "
            "parent state's `spawns` field. The child gets a Refs #<parent> "
            "footer plus a `child-of/<parent>` label — the sole record of the "
            "relationship; the auto-advance on child close finds the cohort by "
            "querying that label (ADR-0003). For pr-typed spawns, --head is "
            "required (PRs need a source branch)."
        ),
    )
    p_spawn_issue.add_argument("--issue", required=True, help="Parent issue identifier.")
    p_spawn_issue.add_argument(
        "--title",
        default=None,
        help="Child issue title. Defaults to '<spawn-state> follow-up for #<parent>'.",
    )
    p_spawn_issue.add_argument(
        "--head",
        default=None,
        help="(PR-typed spawns only) Source branch for the child pull request.",
    )
    p_spawn_issue.add_argument(
        "--base",
        default=None,
        help="(PR-typed spawns only) Target branch.",
    )
    p_spawn_issue.add_argument(
        "--issue-type",
        dest="spawn_issue_type",
        default=None,
        help="Disambiguator when the parent state declares multiple spawn "
        "rules. Picks the rule whose `issue_type` matches. Required when "
        "multiple rules exist and `--initial-state` doesn't uniquely "
        "identify one.",
    )
    p_spawn_issue.add_argument(
        "--initial-state",
        dest="spawn_initial_state",
        default=None,
        help="Further disambiguator when multiple spawn rules share the "
        "same `issue_type` but target different `initial_state`s on the "
        "destination process.",
    )
    _add_body_args(p_spawn_issue, required=False)
    p_spawn_issue.set_defaults(func=_do_spawn_issue)

    # --- Catalogued — block-level ---

    p_review_blocked = subparsers.add_parser(
        "review-blocked",
        help="Human claims pre-action review of an awaiting blocked gate.",
    )
    p_review_blocked.add_argument("--issue", required=True)
    p_review_blocked.set_defaults(func=_do_review_blocked)

    p_approve_blocked = subparsers.add_parser(
        "approve-blocked",
        help="Human approves a blocked human gate; transition fires.",
    )
    p_approve_blocked.add_argument("--gate", required=True)
    p_approve_blocked.add_argument(
        "--destination", default=None, help="Destination state (for verdict-style gates)."
    )
    p_approve_blocked.add_argument("--issue", required=True)
    _add_body_args(p_approve_blocked, required=False)
    p_approve_blocked.set_defaults(func=_do_approve_blocked)

    p_reject_blocked = subparsers.add_parser(
        "reject-blocked",
        help="Human rejects a blocked human-gate packet; agent iterates.",
    )
    p_reject_blocked.add_argument("--gate", required=True)
    p_reject_blocked.add_argument("--issue", required=True)
    _add_body_args(p_reject_blocked, required=True)
    p_reject_blocked.set_defaults(func=_do_reject_blocked)

    # --- Catalogued — audit-level ---

    p_review_audit = subparsers.add_parser(
        "review-audit",
        help="Human claims post-action review of an audit-level gate.",
    )
    p_review_audit.add_argument("--issue", required=True)
    p_review_audit.set_defaults(func=_do_review_audit)

    p_approve_audit = subparsers.add_parser(
        "approve-audit",
        help="Human approves (confirms) an audit-level action post-hoc.",
    )
    p_approve_audit.add_argument("--gate", required=True)
    p_approve_audit.add_argument("--issue", required=True)
    p_approve_audit.set_defaults(func=_do_approve_audit)

    p_reject_audit = subparsers.add_parser(
        "reject-audit",
        help="Human rejects (revokes) an audit-level action; triggers on_revoke remediation.",
    )
    p_reject_audit.add_argument("--gate", required=True)
    p_reject_audit.add_argument("--issue", required=True)
    _add_body_args(p_reject_audit, required=True)
    p_reject_audit.set_defaults(func=_do_reject_audit)

    # --- Recognized ---

    p_request_input = subparsers.add_parser(
        "request-input",
        help="Agent escalates to a human operator on a catalogued topic.",
        description=(
            "Pause the working state and ask the human operator for input. "
            "The agent's current state must declare `human_inputs`; pass "
            "--topic <id> with one of the declared ids. The shared "
            "`human-inputs.json` defines each topic. Adds one "
            "`hitl-input/<topic>` label so the operator can filter the queue."
        ),
    )
    p_request_input.add_argument("--issue", required=True)
    p_request_input.add_argument(
        "--topic",
        required=True,
        help="Catalogued human-input id (must be declared on the current state).",
    )
    _add_body_args(p_request_input, required=True)
    p_request_input.set_defaults(func=_do_request_input)

    p_review_request = subparsers.add_parser(
        "review-request",
        help="Human claims the recognition response role for a pending input request.",
    )
    p_review_request.add_argument("--issue", required=True)
    p_review_request.set_defaults(func=_do_review_request)

    p_respond_request = subparsers.add_parser(
        "respond-request",
        help="Human provides input for a recognized HITL moment.",
    )
    p_respond_request.add_argument("--issue", required=True)
    _add_body_args(p_respond_request, required=True)
    p_respond_request.set_defaults(func=_do_respond_request)

    # --- Read / discovery commands ---

    p_create_issue = subparsers.add_parser(
        "create-issue",
        help="Create a new issue in the given initial state.",
        description=(
            "Create a new issue. Requires a title and an initial state "
            "(--to STATE). The workflow is auto-resolved from --to via "
            "the registry (state names are unique). The new item is "
            "created atomically with its `state/<name>` label so it never "
            "exists without a state. Optionally claim it for the agent in "
            "the same step with --claim, which adds `claimed/<agent-role>`."
        ),
    )
    p_create_issue.add_argument(
        "--to",
        dest="initial_state",
        required=True,
        help="Initial state for the new issue (e.g., raw). Determines "
        "which workflow the item belongs to.",
    )
    p_create_issue.add_argument(
        "--title",
        required=True,
        help="Title for the new issue.",
    )
    p_create_issue.add_argument(
        "--type",
        dest="issue_type",
        default=None,
        help="Issue type id (e.g., bug, feature). Required when the process "
        "supports multiple types; optional (auto-defaulted) when there's only one.",
    )
    _add_body_args(p_create_issue, required=False)
    p_create_issue.add_argument(
        "--claim",
        action="store_true",
        default=False,
        help="Atomically claim the new item for the agent's role "
        "(adds `claimed/<agent-role>` alongside the state label).",
    )
    # PR-specific flags — applicable only when the resolved issue type maps to
    # the pull-request entity (e.g., type=pr). Validated at runtime, not via
    # argparse `required`, since they don't apply to issue-entity types.
    p_create_issue.add_argument(
        "--head",
        default=None,
        help="(PR types only) Source branch for the pull request.",
    )
    p_create_issue.add_argument(
        "--base",
        default=None,
        help="(PR types only) Target branch for the pull request. "
        "Defaults to the repo's default branch when omitted.",
    )
    p_create_issue.add_argument(
        "--refs",
        dest="refs",
        action="append",
        default=None,
        help="Ticket id(s) referenced by this issue. For PR types: parents the "
        "PR addresses, rendered as 'Refs #N' in the message footer. For "
        "states declaring `collects`: contributor ids to gather into this "
        "collector (each contributor gets a `collected-by/<new>` label — the "
        "sole record of the relationship).",
    )
    p_create_issue.add_argument(
        "--all-candidates",
        action="store_true",
        default=False,
        help="(states with `collects` only) Gather every eligible candidate "
        "in the source process. Mutually exclusive with --refs and --none.",
    )
    p_create_issue.add_argument(
        "--none",
        dest="collect_none",
        action="store_true",
        default=False,
        help="(states with `collects` only) Create an empty collector with "
        "no contributors. Mutually exclusive with --refs and --all-candidates.",
    )
    p_create_issue.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="(states with `collects` only) Skip the candidate-eligibility "
        "check when validating --refs. Use when an already-collected issue "
        "must be re-routed.",
    )
    p_create_issue.set_defaults(func=_do_create_issue)

    p_collect = subparsers.add_parser(
        "collect-into",
        help="Add contributor issue(s) to an existing collector.",
        description=(
            "Append contributors to a collector issue after creation. The "
            "collector must reside on a state declaring `collects`; each "
            "contributor must be in one of `collects.from_states` on the "
            "source process and not already collected (use --force to "
            "override). Applies a `collected-by/<collector>` label to each "
            "contributor — the sole record of the relationship (ADR-0003)."
        ),
    )
    p_collect.add_argument(
        "--issue",
        required=True,
        help="The collector issue id (the one created on a `collects` state).",
    )
    p_collect.add_argument(
        "--refs",
        dest="refs",
        action="append",
        required=True,
        help="Contributor issue id(s). Repeat for multiple (e.g., --refs 101 --refs 102).",
    )
    p_collect.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Skip the candidate-eligibility check.",
    )
    p_collect.set_defaults(func=_do_collect_into)

    p_view_inbox = subparsers.add_parser(
        "view-inbox",
        help="Show the agent's own claimable items and actionable wip.",
        description=(
            "Show this agent's plate, computed from the workflow registry: "
            "(1) inbox — items in resting states whose `claim-role` matches "
            "the agent's role, not currently claimed by anyone; "
            "(2) actionable wip — items with `claimed/{role}` that are NOT "
            "blocked waiting for any HITL signal "
            "(no `hitl-blocked/*`, `hitl-audit/*`, or `hitl-input/*`). "
            "The role comes from --agent-role / AGENT_ROLE / the agent "
            "config's `agent-role` key; override per invocation with "
            "--agent-role to view a different role's inbox."
        ),
    )
    p_view_inbox.add_argument(
        "--limit", type=int, default=50, help="Max items per backend query (default: 50)."
    )
    p_view_inbox.set_defaults(func=_do_view_inbox)

    p_search_issues = subparsers.add_parser(
        "search-issues",
        help="Search issues by framework filters (state, claim, HITL markers).",
        description=(
            "Search issues matching the given filters. Filters compose "
            "with AND. Use `--awaiting-gate '*'` or `--audit-pending '*'` "
            "to find every issue with any awaiting or audit-pending "
            "marker. For the agent's own work, use `view-inbox` instead."
        ),
    )
    p_search_issues.add_argument(
        "--state", default=None, help="Filter by state (e.g., ready_for_dev)."
    )
    p_search_issues.add_argument(
        "--claim",
        dest="claim_role",
        default=None,
        help="Filter by agent claim (role id, e.g., pm).",
    )
    p_search_issues.add_argument(
        "--awaiting-gate",
        dest="awaiting_gate",
        default=None,
        help="Filter by awaiting-gate name; pass '*' for any awaiting gate.",
    )
    p_search_issues.add_argument(
        "--audit-pending",
        dest="audit_pending",
        default=None,
        help="Filter by audit-pending gate name; pass '*' for any audit-pending.",
    )
    p_search_issues.add_argument(
        "--awaiting-input",
        dest="awaiting_input",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Filter for items awaiting recognized input "
        "(or with --no-awaiting-input, exclude them).",
    )
    p_search_issues.add_argument(
        "--limit", type=int, default=50, help="Max items to return (default: 50)."
    )
    p_search_issues.set_defaults(func=_do_search_issues)

    p_view_issue = subparsers.add_parser(
        "view-issue",
        help="View a single issue's framework-relevant state and recent comments.",
    )
    p_view_issue.add_argument("--issue", required=True)
    p_view_issue.add_argument(
        "--comments",
        type=int,
        default=None,
        help="Limit the number of recent comments shown. By default every "
        "comment is returned (most recent first). Pass a positive N to "
        "cap the list, or 0 to omit comments entirely.",
    )
    p_view_issue.set_defaults(func=_do_view_issue)

    p_post_comment = subparsers.add_parser(
        "post-comment",
        help="Post a comment on an issue without advancing state (utility, not a framework operation).",
        description=(
            "Post a free-form comment on an issue. This is NOT a framework "
            "operation — no state change, no markers, no workflow resolution. "
            "Use it for status updates, investigation notes, async pings — "
            "anything that needs to be communicated but doesn't fit any of "
            "the eleven framework operations or the workflow commands."
        ),
    )
    p_post_comment.add_argument("--issue", required=True)
    _add_body_args(p_post_comment, required=True)
    p_post_comment.set_defaults(func=_do_post_comment)

    p_edit_issue = subparsers.add_parser(
        "edit-issue",
        help="Edit an issue's title or body (no state change).",
        description=(
            "Edit the tracker's title or body for an issue. Does not change "
            "workflow state, labels, or markers — those are managed by "
            "advance-issue / claim-issue / release-issue. Use this for typo fixes, scope "
            "adjustments, or rewriting the description as understanding "
            "evolves. At least one of --title, --body, --body-from is required."
        ),
    )
    p_edit_issue.add_argument("--issue", required=True)
    p_edit_issue.add_argument("--title", default=None, help="New title for the issue.")
    _add_body_args(p_edit_issue, required=False)
    p_edit_issue.set_defaults(func=_do_edit_issue)

    # --- Utility commands ---

    p_validate_workflow = subparsers.add_parser(
        "validate-workflow",
        help="Validate workflow artifacts against the framework principles.",
    )
    p_validate_workflow.set_defaults(func=_do_validate_workflow)

    p_gendocs = subparsers.add_parser(
        "generate-docs",
        help="Regenerate mermaid + markdown docs for every process.",
        description=(
            "Regenerate the agent/human-readable documentation alongside "
            "the canonical JSON sources. Emits `<process>-states.mermaid` "
            "(state diagrams), `<process>.md` (per-process reference docs "
            "with states, transitions, human gates, cross-process handoffs, and "
            "active trust grants), `roles.md`, `issue-types.md`, and "
            "`README.md`. Authors edit JSON; regenerate."
        ),
    )
    p_gendocs.set_defaults(func=_do_generate_docs)

    p_doctor_workflow = subparsers.add_parser(
        "doctor-workflow",
        help="Diagnose the workflow configuration (artifacts, trust grants, backend auth).",
    )
    p_doctor_workflow.set_defaults(func=_do_doctor_workflow)

    p_init_agent = subparsers.add_parser(
        "init-agent",
        help="Scaffold an agent home with .workflow/ config, workflows, and trust-grants.",
        description=(
            "Create a new agent home. Writes `.workflow/config.json` with "
            "the agent's identity (the global --agent-role, required for "
            "init) and creates empty `.workflow/workflows/` and "
            "`.workflow/trust-grants/` subdirectories — the default "
            "locations for workflow definitions and trust grants. To "
            "point at a shared workflows directory instead, pass "
            "`--workflow-dir PATH`; the path is written into the config "
            "as the `workflow-dir` entry and overrides the default at "
            "lookup time (relative paths are resolved from the agent "
            "home). The target directory is the global --agent-home, "
            "defaulting to cwd. Refuses to overwrite an existing "
            "config.json unless --force is passed."
        ),
    )
    p_init_agent.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing config.json if present.",
    )
    p_init_agent.add_argument(
        "--workflow-dir",
        dest="init_workflow_dir",
        default=None,
        help="Path to the shared workflows directory. Written into the "
        "config as the `workflow-dir` entry. Relative paths are stored "
        "as-is and resolved from the agent home at lookup time "
        "(e.g., `--workflow-dir ../../workflows`).",
    )
    p_init_agent.set_defaults(func=_do_init_agent)

    p_setup = subparsers.add_parser(
        "setup-github",
        help="Provision GitHub Issue Types (org-level) and labels (repo-level).",
        description=(
            "Default: get the configured repo ready to use. Tries to "
            "provision missing Issue Types at the org (best effort — falls "
            "back to label encoding if it can't), then ensures every "
            "required label exists on the repo. Use --setup-org to "
            "explicitly create Issue Types at the org level (requires org "
            "admin rights); on success the capability cache is refreshed."
        ),
    )
    p_setup.add_argument(
        "--setup-org",
        action="store_true",
        default=False,
        help="Org-level operation only — create missing Issue Types in the "
        "org (requires admin rights). Does not touch repo labels. "
        "Force-refreshes the capability cache even for manual entries.",
    )
    p_setup.set_defaults(func=_do_setup_github)

    p_caps = subparsers.add_parser(
        "capabilities",
        help="Inspect or manage the per-(host, owner) capability cache.",
        description=(
            "The framework caches whether each tracker org supports native "
            "Issue Types or needs label fallback. Default behavior prints "
            "the cache. Use --clear to wipe; --refresh to re-probe "
            "non-manual entries; --set-encoding to pin manually."
        ),
    )
    caps_action = p_caps.add_mutually_exclusive_group()
    caps_action.add_argument(
        "--clear",
        action="store_true",
        default=False,
        help="Wipe every cache entry (including manual pins).",
    )
    caps_action.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Re-probe every non-manual cache entry against its backend. "
        "Manual entries are left alone (use --set-encoding to change them).",
    )
    caps_action.add_argument(
        "--set-encoding",
        dest="set_encoding",
        choices=("native", "label"),
        default=None,
        help="Pin the encoding for the current --repo / --host. Sets "
        "manual=true so it survives --refresh. Use --clear to undo.",
    )
    p_caps.set_defaults(func=_do_capabilities)

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
        return _handle_workflow_error(exc)


def main() -> None:
    """Console-script entry point. Exits with the CLI's return code."""
    sys.exit(cli())


# --------------------------------------------------------------------------- #
# Helpers: build backend / controller / context


def _ctx_obj_from_args(args: argparse.Namespace) -> dict:
    """Coerce parsed args into the dict shape downstream helpers expect.

    Note: there is no `process_name` here. The framework's state-name
    uniqueness invariant means each issue's workflow is uniquely
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
        "workflow_dir": args.workflow_dir,
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


def _build_backend(ctx_obj: dict) -> TrackerBackend:
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
    process_name: str,
    *,
    require_backend: bool = True,
) -> Process:
    """Build a Process for a named workflow.

    `process_name` is required and identifies which `<name>-states.json`
    file under `workflow_dir` to load. Callers that resolved the workflow
    via the registry pass it directly.
    """
    backend: TrackerBackend | None = None
    if require_backend:
        backend = _build_backend(ctx_obj)
    return load_process(
        process_name=process_name,
        workflow_dir=ctx_obj.get("workflow_dir"),
        grants_dir=ctx_obj.get("grants_dir"),
        backend=backend,
        agent_home=ctx_obj.get("agent_home"),
    )


def _build_context_for_issue(
    ctx_obj: dict,
    issue_id: str,
    fallback_state: str | None = None,
) -> Process:
    """Build a workflow context for an operation on a specific issue.

    Always registry-driven:

    1. Build the backend (needed to read the issue).
    2. Build the workflow registry from the repo.
    3. Read the issue's current state.
    4. Look up which workflow contains that state (state-name uniqueness
       across workflows is a framework invariant).
    5. Load that workflow's context.

    If the issue has no current state (rare — typically a brand-new item),
    `fallback_state` is consulted (e.g., for `advance-issue`, the destination
    state can resolve the workflow).

    Raises `ConfigError` if the workflow source tree can't be discovered or
    the issue's state doesn't belong to any known workflow.
    """
    from workflow.config import build_registry

    backend = _build_backend(ctx_obj)
    registry = build_registry(
        agent_home=ctx_obj.get("agent_home"),
        workflow_dir=ctx_obj.get("workflow_dir"),
        backend=backend,
        grants_dir=ctx_obj.get("grants_dir"),
    )
    if registry is None:
        raise ConfigError(
            "No workflows directory found. Pass --workflow-dir, set "
            "WORKFLOW_DIR, or run from inside a directory whose tree "
            "contains `*-states.json` files."
        )

    process_name: str | None = None
    try:
        item_state = backend.read_issue(issue_id)
        if item_state.state:
            process_name = registry.find_process_for_state(item_state.state)
    except BackendError as exc:
        # Don't fail here; let the controller's read_issue fail more
        # informatively when the operation runs. We only swallow the error
        # so we can try fallback_state.
        logger.debug("Could not pre-read issue %s: %s", issue_id, exc)

    if process_name is None and fallback_state:
        process_name = registry.find_process_for_state(fallback_state)

    if process_name is None:
        raise ConfigError(
            f"Cannot resolve workflow for issue {issue_id!r}. The issue "
            "must have a state that belongs to a discovered workflow "
            "(or, for `advance-issue`, a destination state that does)."
        )

    # Build a context for the resolved workflow.
    return _build_context(ctx_obj, require_backend=True, process_name=process_name)


def _build_controller(context: Process, dry_run: bool) -> Controller:
    if context.backend is None:
        raise ConfigError("No backend configured for controller execution.")
    # The cascade pass needs the full registry to look up sibling-process
    # spawn / collect definitions. Discover it the same way we discover
    # the per-process context.
    from workflow.config import build_registry

    registry = build_registry(
        agent_home=context.agent_home,
        workflow_dir=context.workflow_dir,
        backend=context.backend,
        grants_dir=None,
    )
    return Controller(
        backend=context.backend,
        state_machine=context.state_machine,
        catalog=context.catalog,
        grants=context.grants,
        dry_run=dry_run,
        registry=registry,
        issue_type_directory=context.issue_type_directory,
    )


# --------------------------------------------------------------------------- #
# Output formatting


def _next_actions_to_dict(actions: list[Any]) -> list[dict[str, Any]]:
    """JSON-shaped projection of `AvailableTransition` records for an agent."""
    out: list[dict[str, Any]] = []
    for a in actions:
        out.append(
            {
                "label": a.label,
                "destination": a.destination,
                "transition_type": a.transition_type.value,
                "is_gated": a.is_gated,
                "gate": a.gate_name,
                "default_level": a.default_level.value if a.default_level else None,
                "effective_level": a.effective_level.value if a.effective_level else None,
                "grant_relaxed": a.grant_relaxed,
                "triggering_roles": list(a.triggering_roles),
                "agent_prepares": a.agent_prepares_path,
                "destination_class": (
                    a.destination_state_class.value if a.destination_state_class else None
                ),
                "destination_reversibility": (
                    a.destination_reversibility.value if a.destination_reversibility else None
                ),
                "destination_closure_taxonomy": (
                    a.destination_closure_taxonomy.value if a.destination_closure_taxonomy else None
                ),
                "destination_roles": list(a.destination_roles),
            }
        )
    return out


def _human_inputs_for(state_machine: Any, state_name: str | None) -> tuple[str, ...]:
    """Derive the current state's human_inputs — empty when state is unknown."""
    if state_name is None:
        return ()
    s = state_machine.states.get(state_name) if state_machine else None
    return s.human_inputs if s else ()


def _print_next_actions(
    actions: list[Any],
    *,
    current_state: str | None,
    last_state: str | None,
    human_inputs: tuple[str, ...] = (),
) -> None:
    """Human/agent-readable next-actions block.

    Each entry leads with the literal `workflow advance-issue` / `claim-issue` / `release-issue`
    invocation the agent would run, followed by gate / role / template
    details when relevant. When the current state declares `human_inputs`,
    a `request-input` suggestion lists the catalogued topic ids.
    """
    from workflow.core.model.human_gate import HumanGateLevel
    from workflow.core.model.state_machine import TransitionType

    if not actions:
        print("Next actions: (none)")
        return

    print("Next actions:")
    # If the only options are CLAIM transitions, the agent is at a resting
    # state — emit a single `claim` suggestion. Otherwise enumerate advances.
    claim_actions = [a for a in actions if a.transition_type is TransitionType.CLAIM]
    advance_actions = [a for a in actions if a.transition_type is not TransitionType.CLAIM]

    if claim_actions and not advance_actions:

        def _roles_suffix(a: Any) -> str:
            if a.destination_roles:
                return f" (roles: {', '.join(a.destination_roles)})"
            return ""

        if len(claim_actions) == 1:
            a = claim_actions[0]
            print(f"  claim-issue  # → {a.destination}{_roles_suffix(a)} ({a.label!r})")
        else:
            print("  claim-issue --to <state>  # ambiguous; choose one:")
            for a in claim_actions:
                print(f"    --to {a.destination}  # {a.label!r}{_roles_suffix(a)}")
        return

    for a in advance_actions:
        if a.is_gated:
            lvl = a.effective_level.value if a.effective_level else "?"
            tag = f"[HITL {lvl}]"
            if a.grant_relaxed:
                default = a.default_level.value if a.default_level else "?"
                tag += f" (default {default}, trust grant applied)"
        else:
            tag = "[ungated]"

        print(f"  advance-issue --to {a.destination}  {tag}")
        print(f"    label: {a.label!r}")
        if a.is_gated:
            if a.gate_name:
                print(f"    gate: {a.gate_name}")
            if a.triggering_roles:
                print(f"    triggering role(s): {', '.join(a.triggering_roles)}")
            if a.agent_prepares_path:
                kind = "required" if a.effective_level is HumanGateLevel.BLOCK else "optional"
                print(f"    --body-from <{a.agent_prepares_path}>  ({kind})")
        dst_bits: list[str] = []
        if a.destination_reversibility is not None:
            dst_bits.append(a.destination_reversibility.value)
        if a.destination_state_class is not None:
            cls = a.destination_state_class.value
            if a.destination_closure_taxonomy is not None:
                cls += f"/{a.destination_closure_taxonomy.value}"
            dst_bits.append(cls)
        if dst_bits:
            print(f"    destination: {', '.join(dst_bits)}")

    if human_inputs:
        print(
            f'  request-input --topic <id> --body "..."  # ask the human operator; '
            f"topics: {', '.join(human_inputs)}"
        )
    if last_state is not None:
        print(f"  release-issue  # returns to {last_state!r}")


def _print_result(
    result: OperationResult,
    *,
    json_output: bool,
    context: Any = None,
) -> None:
    """Render an operation result.

    When `context` (a Process) is supplied and the post-state has a state,
    the human-readable rendering ends with a `Next actions:` block enumerating
    the agent's options. JSON output is unchanged (callers who want next
    actions in JSON should use `workflow --json view-issue --issue <id>` for
    the structured form — `--json` is a global flag, so it precedes the
    subcommand).
    """
    if json_output:
        print(_json.dumps(_result_to_dict(result), indent=2, default=str))
        return

    op = result.operation.value
    header = f"{op} on {result.issue_id}"
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

    # Show next actions if we have a process context and a known state.
    if context is not None:
        from workflow.core.inspector import available_transitions

        state = result.post_state if result.post_state is not None else result.pre_state
        if state is not None and state.state is not None:
            actions = available_transitions(
                context.state_machine,
                context.catalog,
                context.grants,
                state.state,
            )
            if actions or state.last_state is not None:
                print("")
                _print_next_actions(
                    actions,
                    current_state=state.state,
                    last_state=state.last_state,
                    human_inputs=_human_inputs_for(context.state_machine, state.state),
                )


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
        "issue_id": result.issue_id,
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


def _handle_workflow_error(exc: WorkflowError) -> int:
    """Print a friendly message and return the error's documented exit code (#26)."""
    print(f"error: {exc}", file=sys.stderr)
    return _exit_code_for(exc)


# --------------------------------------------------------------------------- #
# Subcommand handlers


def _do_advance_issue(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        # advance can fall back to args.destination if the issue has no state.
        context = _build_context_for_issue(ctx, args.issue, fallback_state=args.destination)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = advance_issue_op.run(
            controller,
            issue_id=args.issue,
            destination=args.destination,
            body_text=_resolve_body(args),
            # CLAIM-crossing advances need the acting role (#11): explicit
            # flag/env wins, else the context's already-loaded agent-config role.
            actor=_resolve_agent_role(ctx) or context.agent_role,
        )
        _print_result(result, json_output=ctx["json_output"], context=context)

        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_event_fired(args: argparse.Namespace) -> int:
    """Fire an event-type transition (no agent role required).

    Verifies the resolved transition is of type EVENT — refuses otherwise so
    automations can't accidentally trip role_action / claim paths. Prepends
    the `--triggered-by` source to the body comment for audit clarity.
    """
    from workflow.core.model.state_machine import TransitionType

    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue, fallback_state=args.destination)

        # Resolve the issue's current state so we can verify the transition type.
        backend = _build_backend(ctx)
        state = backend.read_issue(args.issue)
        source = state.state or args.destination  # for fallback on intake events

        matches = [
            t
            for t in context.state_machine.transitions
            if t.source == source and t.destination == args.destination
        ]
        if not matches:
            raise ConfigError(
                f"No transition from {source!r} to {args.destination!r} in "
                f"workflow {context.state_machine.name!r}."
            )
        if matches[0].transition_type is not TransitionType.EVENT:
            raise ConfigError(
                f"Transition {source!r} → {args.destination!r} is type "
                f"{matches[0].transition_type.value!r}, not `event`. Use "
                f"`workflow advance-issue` for agent-driven transitions."
            )

        # Compose the body with the triggered-by prefix.
        user_body = _resolve_body(args)
        triggered_by = getattr(args, "triggered_by", None)
        if triggered_by:
            prefix = f"**Triggered by**: `{triggered_by}`"
            body_text = f"{prefix}\n\n{user_body.rstrip()}" if user_body else prefix
        else:
            body_text = user_body

        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = advance_issue_op.run(
            controller,
            issue_id=args.issue,
            destination=args.destination,
            body_text=body_text,
            actor=None,  # event transitions skip role check
            event_fired=True,  # authorize the EVENT (planner refuses it via plain advance)
        )
        _print_result(result, json_output=ctx["json_output"], context=context)
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_claim_issue(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        role = _resolve_agent_role(ctx) or context.agent_role
        if not role:
            raise ConfigError(
                "claim requires --agent-role, AGENT_ROLE env, or `agent-role` "
                "in <agent-home>/.workflow/config.json."
            )
        result = claim_issue_op.run(
            controller,
            issue_id=args.issue,
            role=role,
            destination=args.destination,
        )
        _print_result(result, json_output=ctx["json_output"], context=context)
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_spawn_issue(args: argparse.Namespace) -> int:
    """Spawn a subprocess / follow-up issue per parent state's `spawns` config."""
    from workflow.config import build_registry

    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
        parent_state = backend.read_issue(args.issue)
        if parent_state.state is None:
            raise ConfigError(
                f"Issue #{args.issue} has no `state/` label; cannot resolve spawn config."
            )

        registry = build_registry(
            agent_home=ctx.get("agent_home"),
            workflow_dir=ctx.get("workflow_dir"),
            backend=None,
            grants_dir=ctx.get("grants_dir"),
        )
        if registry is None:
            raise ConfigError(
                "No workflows directory found. Pass --workflow-dir or set WORKFLOW_DIR."
            )

        parent_process = registry.find_process_for_state(parent_state.state)
        if parent_process is None:
            raise ConfigError(f"State {parent_state.state!r} is not declared in any process.")
        parent_ctx = registry.get_process(parent_process)
        sm_state = parent_ctx.state_machine.states.get(parent_state.state)
        if sm_state is None or not sm_state.spawns:
            raise ConfigError(
                f"State {parent_state.state!r} has no `spawns` config; nothing to spawn from here."
            )
        # Pick the spawn rule. With one rule it's unambiguous. With
        # multiple, the author must disambiguate via --issue-type and,
        # if still tied (same type, different initial_state),
        # --initial-state.
        candidates = list(sm_state.spawns)
        if args.spawn_issue_type is not None:
            candidates = [c for c in candidates if c.issue_type == args.spawn_issue_type]
        if args.spawn_initial_state is not None:
            candidates = [c for c in candidates if c.initial_state == args.spawn_initial_state]
        if not candidates:
            available = [
                f"(issue_type={sp.issue_type!r}, initial_state={sp.initial_state!r})"
                for sp in sm_state.spawns
            ]
            raise ConfigError(
                f"State {parent_state.state!r} has no spawn rule matching "
                f"--issue-type={args.spawn_issue_type!r} "
                f"--initial-state={args.spawn_initial_state!r}. Available: "
                f"{', '.join(available)}"
            )
        if len(candidates) > 1:
            available = [
                f"(issue_type={sp.issue_type!r}, initial_state={sp.initial_state!r})"
                for sp in candidates
            ]
            raise ConfigError(
                f"State {parent_state.state!r} has {len(candidates)} spawn "
                f"rules matching the filter. Disambiguate with --issue-type "
                f"and/or --initial-state. Candidates: {', '.join(available)}"
            )
        spawn = candidates[0]

        # Resolve process — author may have omitted it. Derive from
        # initial_state via registry (every state belongs to exactly
        # one process).
        spawn_process_name = spawn.process or registry.find_process_for_state(spawn.initial_state)
        if spawn_process_name is None:
            raise ConfigError(
                f"State {parent_state.state!r}: spawn's `initial_state` "
                f"{spawn.initial_state!r} does not resolve to any known "
                f"process. Check the workflows directory."
            )

        # Resolve child issue type to determine entity (issue vs PR).
        target_ctx = registry.get_process(spawn_process_name)
        type_entry = None
        if target_ctx.issue_type_directory is not None:
            try:
                type_entry = target_ctx.issue_type_directory.get(spawn.issue_type)
            except KeyError:
                raise ConfigError(
                    f"Child issue type {spawn.issue_type!r} not in issue-types.json."
                ) from None
        is_pr = type_entry is not None and type_entry.github_entity == "pull_request"

        # Hand the resolved spawn off to the operation seam: the pure planner
        # assembles the CreationSpec, the controller's create path opens the
        # child and runs the cascade. The parent is never marked — the
        # relationship lives solely on the child's `child-of/` label (ADR-0003).
        controller = Controller(
            backend=backend,
            state_machine=parent_ctx.state_machine,
            catalog=parent_ctx.catalog,
            grants=parent_ctx.grants,
            dry_run=ctx["dry_run"],
            registry=registry,
        )
        result = spawn_issue_op.run(
            controller,
            issue_id=args.issue,
            spawn=spawn,
            parent_process=parent_process,
            entity="pull_request" if is_pr else "issue",
            github_issue_type=type_entry.github_issue_type if type_entry else None,
            title=args.title,
            body=_resolve_body(args),
            head=args.head,
            base=args.base,
            actor=_resolve_agent_role(ctx),
        )

        if result.dry_run:
            spec = result.plan.create
            print(f"[dry-run] would spawn child on process {spawn_process_name!r}:")
            print(f"  parent:        #{args.issue} (state {parent_state.state!r})")
            print(f"  issue_type:    {spawn.issue_type}")
            print(f"  initial_state: {spawn.initial_state}")
            print(f"  title:         {spec.title}")
            if is_pr:
                print(f"  head:          {args.head}")
                print(f"  base:          {args.base or '(repo default)'}")
            print(f"  body:          {len(spec.body)} character(s)")
            return 0

        child_id = result.created_issue_id
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "parent": args.issue,
                        "child": child_id,
                        "process": spawn_process_name,
                        "issue_type": spawn.issue_type,
                        "initial_state": spawn.initial_state,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"Spawned child #{child_id} on process {spawn_process_name!r} "
                f"(issue_type={spawn.issue_type}, state={spawn.initial_state}) "
                f"from parent #{args.issue}."
            )
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_release_issue(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = release_issue_op.run(controller, issue_id=args.issue)
        _print_result(result, json_output=ctx["json_output"], context=context)
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_review_blocked(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = review_blocked_op.run(controller, issue_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_approve_blocked(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = approve_blocked_op.run(
            controller,
            issue_id=args.issue,
            gate=args.gate,
            destination=args.destination,
            body=_resolve_body(args),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_reject_blocked(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = reject_blocked_op.run(
            controller,
            issue_id=args.issue,
            gate=args.gate,
            body=_resolve_body(args),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_review_audit(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = review_audit_op.run(controller, issue_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_approve_audit(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = approve_audit_op.run(controller, issue_id=args.issue, gate=args.gate)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_reject_audit(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = reject_audit_op.run(
            controller,
            issue_id=args.issue,
            gate=args.gate,
            body=_resolve_body(args),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_request_input(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = request_input_op.run(
            controller,
            issue_id=args.issue,
            body=_resolve_body(args),
            topic=args.topic,
        )
        _print_result(result, json_output=ctx["json_output"], context=context)
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_review_request(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = review_request_op.run(controller, issue_id=args.issue)
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_respond_request(args: argparse.Namespace) -> int:
    ctx = _ctx_obj_from_args(args)
    try:
        context = _build_context_for_issue(ctx, args.issue)
        controller = _build_controller(context, dry_run=ctx["dry_run"])
        result = respond_request_op.run(
            controller,
            issue_id=args.issue,
            body=_resolve_body(args),
        )
        _print_result(result, json_output=ctx["json_output"])
        return 0
    except WorkflowError as exc:
        return _handle_workflow_error(exc)


def _do_create_issue(args: argparse.Namespace) -> int:
    """Create a new issue in the given initial state.

    The workflow is resolved from `--to` via the registry (state names are
    unique across the registry). If `--claim` is passed, the agent's role
    is added as a `claimed/<role>` label atomically with creation.
    """
    from workflow.config import build_registry

    ctx = _ctx_obj_from_args(args)

    # 1. Resolve workflow + validate state via the registry.
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflow_dir=ctx.get("workflow_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        return _handle_workflow_error(
            ConfigError(
                "No workflows directory found. Set WORKFLOW_DIR or run from "
                "inside a tree containing `*-states.json` files."
            )
        )

    process_name = registry.find_process_for_state(args.initial_state)
    if process_name is None:
        return _handle_workflow_error(
            ConfigError(f"State {args.initial_state!r} is not declared in any discovered workflow.")
        )

    # 2. Resolve and validate issue type.
    try:
        process = registry.get_process(process_name)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    supported_types = process.state_machine.accepted_issue_types
    issue_type: str | None = args.issue_type
    if supported_types:
        if issue_type is None:
            if len(supported_types) == 1:
                issue_type = supported_types[0]
            else:
                return _handle_workflow_error(
                    ConfigError(
                        f"Process {process_name!r} supports multiple issue types "
                        f"{sorted(supported_types)}; pass --type to disambiguate."
                    )
                )
        elif issue_type not in supported_types:
            return _handle_workflow_error(
                ConfigError(
                    f"Issue type {issue_type!r} is not declared in process "
                    f"{process_name!r}'s supported types {sorted(supported_types)}."
                )
            )
    elif issue_type is not None:
        return _handle_workflow_error(
            ConfigError(
                f"Process {process_name!r} does not declare any issue types; "
                f"--type {issue_type!r} cannot be applied."
            )
        )

    # Resolve the IssueType entry once. We need it early to know whether
    # this is a pull-request create (different backend call, extra required
    # flags, framework-applied message footer).
    type_entry = None
    if issue_type is not None and process.issue_type_directory is not None:
        try:
            type_entry = process.issue_type_directory.get(issue_type)
        except KeyError:
            return _handle_workflow_error(
                ConfigError(
                    f"Issue type {issue_type!r} is declared by the process "
                    f"but not defined in issue-types.json."
                )
            )

    is_pr = type_entry is not None and type_entry.github_entity == "pull_request"

    # Does the target state declare `collects`? If so, the same `--refs`
    # flag is repurposed to name contributor issues (mutually exclusive
    # with the PR-side use of --refs because PR types are never receivers).
    initial_state_def = process.state_machine.states.get(args.initial_state)
    collects = initial_state_def.collects if initial_state_def is not None else None
    # Resolve the source process for `collects` — authored value wins;
    # otherwise derive from `from_states[0]` (state names are unique
    # workflow-wide).
    collects_process: str | None = None
    if collects is not None:
        collects_process = collects.process
        if collects_process is None and collects.from_states:
            collects_process = registry.find_process_for_state(collects.from_states[0])

    # PR-specific flag gating. --head/--base/--refs are valid only when the
    # resolved type maps to pull-requests; conversely, PR creates require
    # --head and at least one --refs. For collect-targeted states, --refs
    # is also accepted (and may be empty when `--none` or `--all-candidates`
    # are used).
    if is_pr:
        if collects is not None:
            return _handle_workflow_error(
                ConfigError(
                    f"State {args.initial_state!r} declares `collects` but the "
                    f"resolved type is a pull request — collectors must not be "
                    f"pull-request types."
                )
            )
        if not args.head:
            return _handle_workflow_error(
                ConfigError("--head BRANCH is required when creating a pull request.")
            )
        if not args.refs:
            return _handle_workflow_error(
                ConfigError(
                    "--refs N is required when creating a pull request "
                    "(repeat for multiple parents, e.g., --refs 123 --refs 456)."
                )
            )
        if args.all_candidates or args.collect_none:
            return _handle_workflow_error(
                ConfigError(
                    "--all-candidates / --none are only valid when --to "
                    "lands on a state declaring `collects`."
                )
            )
    elif collects is not None:
        if args.head or args.base:
            return _handle_workflow_error(
                ConfigError("--head / --base are only valid when creating pull requests.")
            )
        # Exactly one of --refs, --all-candidates, --none must be provided
        # to make the contributor set explicit. Listing candidates without
        # selecting any is a usage error.
        modes = [
            bool(args.refs),
            bool(args.all_candidates),
            bool(args.collect_none),
        ]
        if sum(modes) != 1:
            return _handle_workflow_error(
                ConfigError(
                    f"State {args.initial_state!r} declares `collects`. "
                    f"Pass exactly one of --refs N (repeat for multiple), "
                    f"--all-candidates (gather every candidate), or --none "
                    f"(empty collector)."
                )
            )
    else:
        if args.head or args.base or args.refs:
            return _handle_workflow_error(
                ConfigError(
                    "--head / --base / --refs are only valid when --type "
                    "maps to GitHub pull requests or --to lands on a state "
                    "declaring `collects`."
                )
            )
        if args.all_candidates or args.collect_none:
            return _handle_workflow_error(
                ConfigError(
                    "--all-candidates / --none are only valid when --to "
                    "lands on a state declaring `collects`."
                )
            )

    # 3. Resolve claim role if --claim is set.
    claim_role: str | None = None
    if args.claim:
        claim_role = _resolve_agent_role(ctx)
        if not claim_role:
            return _handle_workflow_error(
                ConfigError(
                    "--claim requires an agent role. Pass --agent-role or "
                    "set AGENT_ROLE / config agent-role."
                )
            )

    # 4. Load body.
    try:
        body_text = _resolve_body(args)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    # For PR creates, the body is mandatory — the framework wraps it in a
    # standard message format and a description is part of the contract.
    if is_pr and (body_text is None or not body_text.strip()):
        return _handle_workflow_error(
            ConfigError(
                "--body / --body-from is required when creating a pull "
                "request — PRs need a description."
            )
        )

    # Contributor ids resolved against `collects`. For dry-run we pin to
    # whatever the user requested (no backend query); real-run also runs
    # the candidate validation against the backend.
    contributor_ids: list[str] = []
    if collects is not None:
        if args.refs:
            contributor_ids = [r.lstrip("#") for r in args.refs]
        # --all-candidates / --none paths populate via the backend below.

    if is_pr:
        body = _format_pr_body(body_text or "", refs=args.refs)
    elif collects is not None and contributor_ids:
        suffix_refs = ", ".join(f"#{c}" for c in contributor_ids)
        base_body = (body_text or "").rstrip()
        sep = "\n\n---\n\n" if base_body else ""
        body = f"{base_body}{sep}Collects {suffix_refs}\n"
    else:
        body = body_text if body_text is not None else ""

    # 5. Dry run path: print the plan, don't touch the backend.
    if ctx["dry_run"]:
        extras = [gh_labels.claim_label(claim_role)] if claim_role else []
        if ctx["json_output"]:
            payload: dict[str, Any] = {
                "workflow": process_name,
                "initial_state": args.initial_state,
                "title": args.title,
                "type": issue_type,
                "labels": [gh_labels.state_label(args.initial_state), *extras],
                "body_chars": len(body),
                "dry_run": True,
            }
            if is_pr:
                payload["entity"] = "pull_request"
                payload["head"] = args.head
                payload["base"] = args.base
                payload["refs"] = [r.lstrip("#") for r in args.refs]
                payload["draft"] = args.initial_state == "draft"
            if collects is not None:
                payload["collects"] = {
                    "process": collects_process,
                    "from_states": list(collects.from_states),
                    "contributors": contributor_ids
                    if not args.all_candidates
                    else "(all candidates)",
                    "mode": (
                        "refs"
                        if args.refs
                        else ("all-candidates" if args.all_candidates else "none")
                    ),
                }
            print(_json.dumps(payload, indent=2))
        else:
            noun = "pull request" if is_pr else "issue"
            print(f"[dry-run] would create {noun} in workflow {process_name!r}:")
            print(f"  title:         {args.title}")
            print(f"  initial state: {args.initial_state}")
            if issue_type:
                print(f"  type:          {issue_type}")
            if is_pr:
                print(f"  head:          {args.head}")
                print(f"  base:          {args.base or '(repo default)'}")
                print(f"  refs:          {', '.join('#' + r.lstrip('#') for r in args.refs)}")
                print(f"  draft:         {args.initial_state == 'draft'}")
            if collects is not None:
                src_states = ", ".join(collects.from_states)
                print(f"  collects from: {collects_process}.{src_states}")
                if args.all_candidates:
                    print("  contributors:  (all candidates — querying skipped in dry-run)")
                elif args.collect_none:
                    print("  contributors:  (none — empty collector)")
                else:
                    refs_str = ", ".join(f"#{c}" for c in contributor_ids)
                    print(f"  contributors:  {refs_str}")
            if claim_role:
                print(f"  claim:         {claim_role}")
            print(f"  body:          {len(body)} character(s)")
        return 0

    # 5. Build backend + create.
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    # Resolve contributors against the backend: query candidates (uncollected
    # issues in any `from_states` of the source process) when --all-candidates,
    # validate --refs when given.
    if collects is not None and not args.collect_none:
        candidates: list[str] = []
        from workflow.backends.base import IssueFilters

        for from_state in collects.from_states:
            try:
                rows = backend.list_issues(IssueFilters(state=from_state, limit=200))
            except BackendError as exc:
                return _handle_workflow_error(exc)
            for row in rows:
                if row.collected_by is None:
                    candidates.append(row.issue_id)
        if args.all_candidates:
            if not candidates:
                return _handle_workflow_error(
                    ConfigError(
                        f"--all-candidates: no uncollected issues found in "
                        f"{collects_process} states "
                        f"{list(collects.from_states)}."
                    )
                )
            contributor_ids = candidates
            # Re-render the body footer now that the set is known.
            suffix_refs = ", ".join(f"#{c}" for c in contributor_ids)
            base_body = (body_text or "").rstrip()
            sep = "\n\n---\n\n" if base_body else ""
            body = f"{base_body}{sep}Collects {suffix_refs}\n"
        elif args.refs and not args.force:
            candidate_set = set(candidates)
            invalid = [c for c in contributor_ids if c not in candidate_set]
            if invalid:
                return _handle_workflow_error(
                    ConfigError(
                        f"--refs references issue(s) {invalid} that are not "
                        f"uncollected candidates in {collects_process} states "
                        f"{list(collects.from_states)}. Pass --force to "
                        f"override the eligibility check."
                    )
                )

    # Resolve issue-type encoding (native vs label) for non-PR creates. PRs
    # carry no native type and no `type/` label (the entity kind is the type).
    backend_issue_type: str | None = None
    type_extra_labels: list[str] = []
    if not is_pr and issue_type is not None:
        encoding = _resolve_encoding(ctx, backend)
        if type_entry is not None:
            if encoding == "native":
                backend_issue_type = type_entry.github_issue_type
            else:
                type_extra_labels = [gh_labels.type_label(issue_type)]
        elif encoding == "label":
            # Fall back to `type/<id>` even without a directory entry.
            type_extra_labels = [gh_labels.type_label(issue_type)]

    # Create through the operation seam: the pure planner assembles the
    # CreationSpec; the controller's create path opens the issue/PR and stamps
    # any gathered contributors `collected-by/<new-id>` atomically (ADR-0003).
    # Claiming, if requested, runs below as a second operation so the state
    # machine moves resting → working with proper claimed/ + last-state/ markers.
    create_controller = Controller(
        backend=backend,
        state_machine=process.state_machine,
        catalog=process.catalog,
        grants=process.grants,
        dry_run=False,
        registry=registry,
    )
    try:
        create_result = create_issue_op.run(
            create_controller,
            title=args.title,
            body=body,
            state=args.initial_state,
            entity="pull_request" if is_pr else "issue",
            issue_type=issue_type,
            github_issue_type=backend_issue_type,
            extra_labels=type_extra_labels,
            head=args.head if is_pr else None,
            base=args.base if is_pr else None,
            draft=is_pr and args.initial_state == "draft",
            collect_contributors=contributor_ids,
        )
    except WorkflowError as exc:
        return _handle_workflow_error(exc)
    new_id = create_result.created_issue_id

    # 6. If --claim, immediately claim the new issue.
    claim_result = None
    claim_context = None
    if claim_role:
        try:
            claim_context = _build_context_for_issue(ctx, new_id)
            controller = _build_controller(claim_context, dry_run=False)
            claim_result = claim_issue_op.run(
                controller,
                issue_id=new_id,
                role=claim_role,
            )
        except WorkflowError as exc:
            # Issue created but claim failed — surface the error; the user
            # can run `workflow claim-issue` manually.
            print(
                f"Created issue #{new_id} but claim failed: {exc}",
                file=sys.stderr,
            )
            return _exit_code_for(exc)

    if ctx["json_output"]:
        payload: dict[str, Any] = {
            "id": new_id,
            "workflow": process_name,
            "state": args.initial_state,
            "title": args.title,
            "claim": claim_role,
        }
        if claim_result and claim_result.post_state:
            payload["state"] = claim_result.post_state.state
            payload["last_state"] = claim_result.post_state.last_state
        print(_json.dumps(payload, indent=2))
        return 0

    suffix = f", claimed by {claim_role}" if claim_role else ""
    final_state = args.initial_state
    if claim_result and claim_result.post_state and claim_result.post_state.state:
        final_state = claim_result.post_state.state
    print(f"Created issue #{new_id} in {process_name!r} workflow, state {final_state!r}{suffix}.")

    # Show next actions for the agent's convenience. Use the post-claim
    # state when --claim was used; otherwise show actions from the initial
    # resting state (which will suggest a `claim`).
    from workflow.core.inspector import available_transitions

    if claim_result is not None and claim_context is not None:
        post = claim_result.post_state
        if post is not None and post.state is not None:
            actions = available_transitions(
                claim_context.state_machine,
                claim_context.catalog,
                claim_context.grants,
                post.state,
            )
            if actions or post.last_state is not None:
                print("")
                _print_next_actions(
                    actions,
                    current_state=post.state,
                    last_state=post.last_state,
                    human_inputs=_human_inputs_for(claim_context.state_machine, post.state),
                )
    else:
        # No claim: show actions from the resting initial state.
        try:
            process = registry.get_process(process_name)
        except WorkflowError:
            process = None
        if process is not None:
            actions = available_transitions(
                process.state_machine,
                process.catalog,
                process.grants,
                args.initial_state,
            )
            if actions:
                print("")
                _print_next_actions(actions, current_state=args.initial_state, last_state=None)
    return 0


def _do_collect_into(args: argparse.Namespace) -> int:
    """Add contributor issue(s) to an existing collector.

    Validates that the collector resides on a state declaring `collects`,
    that each contributor lives on one of the declared `from_states`, and
    (unless --force) is not already collected. Marks each contributor with a
    single `collected-by/<collector>` label (ADR-0003).
    """
    from workflow.backends.base import IssueFilters

    ctx = _ctx_obj_from_args(args)
    contributor_ids = [r.lstrip("#") for r in args.refs]
    if not contributor_ids:
        return _handle_workflow_error(ConfigError("--refs requires at least one id."))

    # 1. Resolve the collector — read its state, then look up the
    #    `collects` declaration on that state.
    try:
        collector_ctx = _build_context_for_issue(ctx, args.issue)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)
    backend = collector_ctx.backend
    try:
        collector_state_obj = backend.read_issue(args.issue)
    except BackendError as exc:
        return _handle_workflow_error(exc)
    if collector_state_obj.state is None:
        return _handle_workflow_error(
            ConfigError(f"Issue #{args.issue} has no `state/` label; cannot resolve `collects`.")
        )
    collector_state_def = collector_ctx.state_machine.states.get(collector_state_obj.state)
    collects = collector_state_def.collects if collector_state_def is not None else None
    collects_process: str | None = None
    if collects is not None:
        collects_process = collects.process
        if collects_process is None and collects.from_states:
            from workflow.config import build_registry

            registry = build_registry(
                agent_home=ctx.get("agent_home"),
                workflow_dir=ctx.get("workflow_dir"),
                backend=backend,
                grants_dir=ctx.get("grants_dir"),
            )
            if registry is not None:
                collects_process = registry.find_process_for_state(collects.from_states[0])
    if collects is None:
        return _handle_workflow_error(
            ConfigError(
                f"Issue #{args.issue} is on state "
                f"{collector_state_obj.state!r}, which does not declare "
                f"`collects`. Only states with a `collects` field can act "
                f"as collectors."
            )
        )

    # 2. Validate each contributor against the candidate set (unless --force).
    if not args.force:
        candidates: set[str] = set()
        for from_state in collects.from_states:
            try:
                rows = backend.list_issues(IssueFilters(state=from_state, limit=200))
            except BackendError as exc:
                return _handle_workflow_error(exc)
            for row in rows:
                if row.collected_by is None:
                    candidates.add(row.issue_id)
        invalid = [cid for cid in contributor_ids if cid not in candidates]
        if invalid:
            return _handle_workflow_error(
                ConfigError(
                    f"--refs references issue(s) {invalid} that are not "
                    f"uncollected candidates in {collects_process} states "
                    f"{list(collects.from_states)}. Pass --force to override."
                )
            )

    # 3. Dry-run path.
    if ctx["dry_run"]:
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "collector": str(args.issue),
                        "contributors": contributor_ids,
                        "dry_run": True,
                    },
                    indent=2,
                )
            )
        else:
            print(f"[dry-run] would add {len(contributor_ids)} contributor(s) to #{args.issue}:")
            for cid in contributor_ids:
                print(f"  #{cid}")
        return 0

    # 4. Mark each contributor `collected-by/<collector>` through the operation
    #    seam: the pure planner validates eligibility, the controller applies the
    #    marker. The collector is never touched (ADR-0003). Step 2 already
    #    validated the whole set, so this loop is all-or-nothing in practice.
    controller = _build_controller(collector_ctx, dry_run=False)
    marked: list[str] = []
    for cid in contributor_ids:
        try:
            collect_into_op.run(
                controller,
                issue_id=cid,
                collector_id=str(args.issue),
                from_states=collects.from_states,
                issue_types=collects.issue_types or (),
                force=args.force,
            )
            marked.append(cid)
        except WorkflowError as exc:
            # Partial application: contributors are marked one at a time, so the
            # ones before this failure already carry `collected-by/<collector>`.
            # The marker write is idempotent, so re-running the same command is
            # the repair — name what landed and what didn't so the operator
            # isn't left guessing (#26).
            remaining = contributor_ids[contributor_ids.index(cid) :]
            print(
                f"Failed to collect contributor #{cid} into #{args.issue} — {exc}\n"
                f"Partially applied: {len(marked)} of {len(contributor_ids)} contributor(s) "
                f"already marked collected-by/{args.issue} ({', '.join('#' + m for m in marked) or 'none'}). "
                f"Not yet marked: {', '.join('#' + r for r in remaining)}. "
                f"Re-run the same `collect-into` to finish — the marker is idempotent.",
                file=sys.stderr,
            )
            return _exit_code_for(exc)

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "collector": str(args.issue),
                    "contributors": contributor_ids,
                },
                indent=2,
            )
        )
    else:
        refs_str = ", ".join(f"#{c}" for c in contributor_ids)
        print(f"Added {refs_str} as contributor(s) to #{args.issue}.")
    return 0


def _do_view_inbox(args: argparse.Namespace) -> int:
    """Show the agent's own inbox + actionable wip.

    Role resolution: --agent-role flag > AGENT_ROLE env > agent config
    `agent-role`. Errors if none is set, since inbox has no meaning
    without an agent identity.
    """
    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    role = _resolve_agent_role(ctx)
    if not role:
        return _handle_workflow_error(
            ConfigError(
                "inbox needs an agent role. Pass --agent-role, set "
                "AGENT_ROLE, or run `workflow init-agent --agent-role <role>` to "
                "persist one in the agent config."
            )
        )

    from workflow.config import build_registry
    from workflow.core.inspector import inbox_for_role

    try:
        registry = build_registry(
            agent_home=ctx.get("agent_home"),
            workflow_dir=ctx.get("workflow_dir"),
            backend=None,
            grants_dir=ctx.get("grants_dir"),
        )
        if registry is None:
            raise ConfigError(
                "No workflows directory found. Set WORKFLOW_DIR or run inside "
                "a directory whose tree contains `*-states.json` files."
            )
        items = inbox_for_role(registry, backend, role, args.limit)
    except (ConfigError, BackendError) as exc:
        return _handle_workflow_error(exc)

    return _emit_issues(
        items, ctx, empty_message=f"(no inbox items or actionable wip for {role!r})"
    )


def _do_search_issues(args: argparse.Namespace) -> int:
    """Search issues by framework filters."""
    from workflow.backends.base import IssueFilters

    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    try:
        filters = IssueFilters(
            state=args.state,
            claim_role=args.claim_role,
            awaiting_gate=args.awaiting_gate,
            audit_pending=args.audit_pending,
            awaiting_input=args.awaiting_input,
            limit=args.limit,
        )
        items = backend.list_issues(filters)
    except (ConfigError, BackendError) as exc:
        return _handle_workflow_error(exc)

    return _emit_issues(items, ctx, empty_message="(no issues matched the filters)")


def _emit_issues(items: list[Any], ctx: dict, *, empty_message: str = "(no issues)") -> int:
    """Render a list of IssueState objects as JSON or a columnar table.

    Shared between inbox and search.
    """
    if ctx["json_output"]:
        print(
            _json.dumps(
                [
                    {
                        "id": item.issue_id,
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
                f"#{item.issue_id}",
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


def _do_post_comment(args: argparse.Namespace) -> int:
    """Post a free-form comment on an issue. Backend-only; no workflow context."""
    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    if args.body is not None:
        body = args.body
    else:
        try:
            body = Path(args.body_from).read_text(encoding="utf-8")
        except OSError as exc:
            return _handle_workflow_error(
                ConfigError(f"Could not read body file {args.body_from}: {exc}")
            )

    if not body.strip():
        return _handle_workflow_error(ConfigError("Comment body is empty; refusing to post."))

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
        return _handle_workflow_error(exc)

    if ctx["json_output"]:
        print(_json.dumps({"issue": args.issue, "posted": True}, indent=2))
        return 0

    print(f"Comment posted on #{args.issue}.")

    # Resolve the workflow + next actions for the agent. Best-effort —
    # `comment` works even on issues outside any discovered workflow, so
    # failure to resolve is silent.
    from workflow.core.inspector import available_transitions

    try:
        state = backend.read_issue(args.issue)
        if state.state is not None:
            context = _build_context_for_issue(ctx, args.issue)
            actions = available_transitions(
                context.state_machine,
                context.catalog,
                context.grants,
                state.state,
            )
            if actions or state.last_state is not None:
                print("")
                _print_next_actions(
                    actions,
                    current_state=state.state,
                    last_state=state.last_state,
                    human_inputs=_human_inputs_for(context.state_machine, state.state),
                )
    except (BackendError, WorkflowError) as exc:
        logger.debug("comment: could not resolve next actions: %s", exc)
    return 0


def _do_edit_issue(args: argparse.Namespace) -> int:
    """Edit an issue's title and/or body on the tracker. No workflow state
    change. Independent of `comment` (which posts a new comment instead of
    rewriting the issue's description)."""
    ctx = _ctx_obj_from_args(args)
    title = args.title
    try:
        body = _resolve_body(args)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    if title is None and body is None:
        return _handle_workflow_error(
            ConfigError("edit requires at least one of --title, --body, --body-from.")
        )

    if ctx["dry_run"]:
        print(f"[dry-run] would edit #{args.issue}:")
        if title is not None:
            print(f"  title: {title}")
        if body is not None:
            print(f"  body:  {len(body)} character(s)")
        return 0

    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    try:
        backend.edit_issue(args.issue, title=title, body=body)
    except BackendError as exc:
        return _handle_workflow_error(exc)

    if ctx["json_output"]:
        payload: dict[str, Any] = {"issue": args.issue, "edited": True}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body_chars"] = len(body)
        print(_json.dumps(payload, indent=2))
        return 0

    changed = []
    if title is not None:
        changed.append("title")
    if body is not None:
        changed.append("body")
    print(f"Edited #{args.issue} ({', '.join(changed)}).")

    # Show next actions for the agent (best-effort, like `comment`).
    from workflow.core.inspector import available_transitions

    try:
        state = backend.read_issue(args.issue)
        if state.state is not None:
            context = _build_context_for_issue(ctx, args.issue)
            actions = available_transitions(
                context.state_machine,
                context.catalog,
                context.grants,
                state.state,
            )
            if actions or state.last_state is not None:
                print("")
                _print_next_actions(
                    actions,
                    current_state=state.state,
                    last_state=state.last_state,
                    human_inputs=_human_inputs_for(context.state_machine, state.state),
                )
    except (BackendError, WorkflowError) as exc:
        logger.debug("edit: could not resolve next actions: %s", exc)
    return 0


def _do_view_issue(args: argparse.Namespace) -> int:
    """View one issue's framework-relevant state and recent comments."""
    from workflow.core.inspector import available_transitions

    ctx = _ctx_obj_from_args(args)
    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    try:
        state = backend.read_issue(args.issue)
    except BackendError as exc:
        return _handle_workflow_error(exc)

    # Resolve the issue's process via the registry so we can enrich `view`
    # with next-action info. Failure to resolve is non-fatal — `view` still
    # shows the raw state.
    actions: list[Any] = []
    context = None
    try:
        context = _build_context_for_issue(ctx, args.issue)
        if state.state is not None:
            actions = available_transitions(
                context.state_machine,
                context.catalog,
                context.grants,
                state.state,
            )
    except WorkflowError as exc:
        logger.debug("view: could not resolve process for %s: %s", args.issue, exc)

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
                    "id": state.issue_id,
                    "state": state.state,
                    "claim": state.agent_claim,
                    "last_state": state.last_state,
                    "awaiting_gate": state.awaiting_gate,
                    "reviewing": state.reviewing,
                    "audit_pending": state.audit_pending,
                    "auditing": state.auditing,
                    "awaiting_input": state.awaiting_input,
                    "advising": state.advising,
                    "title": state.extras.get("title"),
                    "next_actions": _next_actions_to_dict(actions),
                    "comments": comments,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    title = state.extras.get("title", "")
    header = f"#{state.issue_id}" + (f" — {title}" if title else "")
    print(header)
    print("-" * len(header))
    print(f"state:           {state.state or '-'}")
    print(f"claim:           {state.agent_claim or '-'}")
    print(f"wip from:        {state.last_state or '-'}")
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

    if actions or state.last_state is not None:
        print("")
        _print_next_actions(
            actions,
            current_state=state.state,
            last_state=state.last_state,
            human_inputs=_human_inputs_for(
                context.state_machine if context is not None else None, state.state
            ),
        )

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


def _do_generate_docs(args: argparse.Namespace) -> int:
    """Regenerate mermaid + markdown docs for every discovered process.

    Emits, alongside the JSON sources in the workflow directory:

    - `<process>-states.mermaid` per process (state diagram)
    - `<process>.md` per process (reference doc)
    - `roles.md` if `roles.json` is present
    - `issue-types.md` if `issue-types.json` is present
    - `README.md` (index)
    """
    from workflow.config import build_registry
    from workflow.core.emitter import (
        InboundSpawn,
        OutboundCollect,
        OutboundFeedback,
        ProcessDocInput,
        emit_human_inputs_doc,
        emit_index_doc,
        emit_issue_types_doc,
        emit_mermaid,
        emit_process_doc,
        emit_process_map,
        emit_roles_doc,
        spawn_sources_from_inbound,
    )

    ctx = _ctx_obj_from_args(args)
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflow_dir=ctx.get("workflow_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        return _handle_workflow_error(
            ConfigError("No workflows directory found. Pass --workflow-dir or set WORKFLOW_DIR.")
        )

    written: list[str] = []
    process_names = registry.discovered_processes()
    workflow_dir = None
    role_directory = None
    issue_type_directory = None
    human_input_directory = None
    processes_loaded: list[Any] = []
    # First pass: load every discovered process.
    for wf_name in process_names:
        try:
            process = registry.get_process(wf_name)
        except WorkflowError as exc:
            return _handle_workflow_error(exc)
        processes_loaded.append(process)
        if workflow_dir is None:
            workflow_dir = process.workflow_dir
        # Capture shared directories from the first process that has them —
        # they're shared across processes in a workflow.
        if role_directory is None and process.role_directory is not None:
            role_directory = process.role_directory
        if issue_type_directory is None and process.issue_type_directory is not None:
            issue_type_directory = process.issue_type_directory
        if human_input_directory is None and process.human_input_directory is not None:
            human_input_directory = process.human_input_directory

    # Compute spawn-sources per process: for each sibling's `spawns`,
    # record the parent state and destination `initial_state` so the
    # child diagram gets a `[*] --> state: ᐉ <parent_state>` arrow.
    # Resolve the target process via the state-name-uniqueness invariant
    # when the author omits `process`.
    state_to_process: dict[str, str] = {}
    for p in processes_loaded:
        for state_name in p.state_machine.states:
            state_to_process[state_name] = p.process_name
    inbound_spawns_by_process: dict[str, list[InboundSpawn]] = {}
    outbound_feedback_by_process: dict[str, list[OutboundFeedback]] = {}
    # Mirror of the collector side: for each `collects`, the source process(es)
    # whose states are gathered FROM get an outbound "collected-from" row.
    outbound_collects_by_process: dict[str, list[OutboundCollect]] = {}
    for p in processes_loaded:
        for s in p.state_machine.states.values():
            for sp in s.spawns:
                target_proc = sp.process or state_to_process.get(sp.initial_state)
                if target_proc is None:
                    continue
                inbound_spawns_by_process.setdefault(target_proc, []).append(
                    InboundSpawn(
                        target_state=sp.initial_state,
                        source_process=p.process_name,
                        source_state=s.name,
                        issue_type=sp.issue_type,
                    )
                )
                for child_closing_state, parent_next in sp.advance_on:
                    outbound_feedback_by_process.setdefault(target_proc, []).append(
                        OutboundFeedback(
                            child_closing_state=child_closing_state,
                            parent_process=p.process_name,
                            parent_state=s.name,
                            parent_next=parent_next,
                            issue_type=sp.issue_type,
                        )
                    )
            if s.collects is not None:
                c = s.collects
                for from_state in c.from_states:
                    source_proc = c.process or state_to_process.get(from_state)
                    if source_proc is None or source_proc == p.process_name:
                        # Intra-process collect — no cross-process row to draw.
                        continue
                    outbound_collects_by_process.setdefault(source_proc, []).append(
                        OutboundCollect(
                            source_state=from_state,
                            collector_process=p.process_name,
                            collector_state=s.name,
                            issue_types=c.issue_types,
                        )
                    )
    for proc_name in inbound_spawns_by_process:
        inbound_spawns_by_process[proc_name].sort(
            key=lambda row: (
                row.target_state,
                row.source_process,
                row.source_state,
                row.issue_type,
            )
        )
    for proc_name in outbound_feedback_by_process:
        outbound_feedback_by_process[proc_name].sort(
            key=lambda row: (
                row.child_closing_state,
                row.parent_process,
                row.parent_state,
                row.parent_next,
                row.issue_type,
            )
        )
    for proc_name in outbound_collects_by_process:
        outbound_collects_by_process[proc_name].sort(
            key=lambda row: (
                row.source_state,
                row.collector_process,
                row.collector_state,
            )
        )

    # Second pass: emit mermaid + markdown per process now that
    # cross-process info is available.
    for process in processes_loaded:
        wf_name = process.process_name
        inbound_spawns_raw = inbound_spawns_by_process.get(wf_name)
        inbound_spawns = tuple(inbound_spawns_raw) if inbound_spawns_raw else None
        outbound_feedback_raw = outbound_feedback_by_process.get(wf_name)
        outbound_feedback = tuple(outbound_feedback_raw) if outbound_feedback_raw else None
        outbound_collects_raw = outbound_collects_by_process.get(wf_name)
        outbound_collects = tuple(outbound_collects_raw) if outbound_collects_raw else None
        spawn_sources = spawn_sources_from_inbound(inbound_spawns)

        if workflow_dir is None:
            # No on-disk target; print to stdout and continue.
            print(
                emit_mermaid(
                    process.state_machine,
                    spawn_sources=spawn_sources,
                    outbound_feedback=outbound_feedback,
                )
            )
            continue

        mermaid_path = workflow_dir / f"{wf_name}-states.mermaid"
        mermaid_path.write_text(
            emit_mermaid(
                process.state_machine,
                spawn_sources=spawn_sources,
                outbound_feedback=outbound_feedback,
            ),
            encoding="utf-8",
        )
        written.append(str(mermaid_path))

        doc_path = workflow_dir / f"{wf_name}.md"
        doc_path.write_text(
            emit_process_doc(
                ProcessDocInput(
                    state_machine=process.state_machine,
                    catalog=process.catalog,
                    issue_type_directory=process.issue_type_directory,
                    grants=process.grants,
                    inbound_spawns=inbound_spawns,
                    outbound_feedback=outbound_feedback,
                    outbound_collects=outbound_collects,
                )
            ),
            encoding="utf-8",
        )
        written.append(str(doc_path))

    process_map_mermaid: str | None = None
    if workflow_dir is not None and processes_loaded:
        # Process map shows up only when we've got at least one process to
        # plot — otherwise the diagram would be empty. The raw mermaid lands
        # at `process-map.mermaid` for downstream tools; README.md embeds it
        # inline alongside the reader's guide.
        process_map_mermaid = emit_process_map(processes_loaded)
        map_path = workflow_dir / "process-map.mermaid"
        map_path.write_text(process_map_mermaid, encoding="utf-8")
        written.append(str(map_path))

    if workflow_dir is not None:
        sms = [p.state_machine for p in processes_loaded]
        if role_directory is not None:
            roles_path = workflow_dir / "roles.md"
            roles_path.write_text(emit_roles_doc(role_directory, sms), encoding="utf-8")
            written.append(str(roles_path))
        if issue_type_directory is not None:
            types_path = workflow_dir / "issue-types.md"
            types_path.write_text(emit_issue_types_doc(issue_type_directory), encoding="utf-8")
            written.append(str(types_path))
        if human_input_directory is not None:
            human_inputs_path = workflow_dir / "human-inputs.md"
            human_inputs_path.write_text(
                emit_human_inputs_doc(human_input_directory, sms), encoding="utf-8"
            )
            written.append(str(human_inputs_path))
        readme_path = workflow_dir / "README.md"
        readme_path.write_text(
            emit_index_doc(
                {p.state_machine.name: p.state_machine.description for p in processes_loaded},
                has_roles=role_directory is not None,
                has_issue_types=issue_type_directory is not None,
                has_human_inputs=human_input_directory is not None,
                process_map_mermaid=process_map_mermaid,
                state_machines=[p.state_machine for p in processes_loaded],
            ),
            encoding="utf-8",
        )
        written.append(str(readme_path))

    if ctx["json_output"]:
        print(_json.dumps({"written": written}, indent=2))
    else:
        for path in written:
            print(f"Wrote {path}")
    return 0


def _do_validate_workflow(args: argparse.Namespace) -> int:
    """Validate workflow artifacts against the framework principles.

    Iterates every workflow in the registry (`--workflow-dir` / `WORKFLOW_DIR`
    / discovered by walking up from cwd) and reports per-workflow findings.
    """
    from workflow.config import build_registry

    ctx = _ctx_obj_from_args(args)
    grants_dir = ctx.get("grants_dir")
    json_output = ctx.get("json_output", False)

    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflow_dir=ctx.get("workflow_dir"),
        backend=None,
        grants_dir=grants_dir,
    )
    if registry is None:
        return _handle_workflow_error(
            ConfigError(
                "No workflows directory found. Pass --workflow-dir, set "
                "WORKFLOW_DIR, or run from inside a directory whose tree "
                "contains `*-states.json` files."
            )
        )

    # Build a cross-process handoff index: state_name → {process names declaring
    # that state with handoff: true}. The validator uses this to confirm every
    # handoff has at least one partner process.
    handoff_index: dict[str, set[str]] = {}
    contexts: dict[str, Any] = {}
    for wf_name in registry.discovered_processes():
        try:
            wf_context = registry.get_process(wf_name)
        except (ConfigError, ParseError) as exc:
            logger.warning("Skipping workflow %r: %s", wf_name, exc)
            continue
        contexts[wf_name] = wf_context
        for state in wf_context.state_machine.states.values():
            if state.handoff:
                handoff_index.setdefault(state.name, set()).add(wf_name)

    # Sibling-machine index for cross-process spawn validation.
    sibling_machines = {name: ctx.state_machine for name, ctx in contexts.items()}

    results: list[tuple[str, Any, Any, list]] = []
    for wf_name, wf_context in contexts.items():
        findings = validate_state_machine(
            wf_context.state_machine,
            wf_context.catalog,
            wf_context.grants,
            issue_type_directory=wf_context.issue_type_directory,
            human_input_directory=wf_context.human_input_directory,
            handoff_index=handoff_index,
            sibling_machines=sibling_machines,
        )
        results.append((wf_name, wf_context.state_machine, wf_context.catalog, findings))

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
                            "workflow": workflow.source_path,
                            "human_gate_catalog": catalog.source_path if catalog else None,
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
                        for name, workflow, catalog, findings in results
                    ]
                },
                indent=2,
            )
        )
    else:
        total_counts = {"error": 0, "warning": 0, "info": 0}
        for name, _workflow, _catalog, findings in results:
            header = f"process: {name}"
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
            f"across {len(results)} process(es)."
        )

    has_errors = any(
        f.severity is Severity.ERROR for _n, _l, _c, findings in results for f in findings
    )
    return 1 if has_errors else 0


def _do_doctor_workflow(args: argparse.Namespace) -> int:
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
        workflow_dir=ctx.get("workflow_dir"),
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        issues.append(
            "registry: no workflows directory found "
            "(set WORKFLOW_DIR or cd into a tree containing "
            "`*-states.json` files)."
        )
        print("registry: <not discovered>")
    else:
        names = registry.discovered_processes()
        print(f"registry: {len(names)} process(es) discovered")
        for wf_name in names:
            try:
                wf_context = registry.get_process(wf_name)
            except WorkflowError as exc:
                issues.append(f"workflow {wf_name}: {exc}")
                print(f"  {wf_name}: ERROR — {exc}")
                continue
            gates = len(wf_context.catalog.entries) if wf_context.catalog else 0
            grants = len(wf_context.grants)
            print(
                f"  {wf_name}: "
                f"{len(wf_context.state_machine.states)} state(s), "
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


def _do_init_agent(args: argparse.Namespace) -> int:
    """Scaffold an agent home: write .workflow/config.json + trust-grants/.

    The target directory is the global `--agent-home` value (or AGENT_HOME
    env), defaulting to cwd when neither is set. The agent's role comes
    from the global `--agent-role` (or AGENT_ROLE env) — required for init,
    since there's no existing config to fall back on.

    Always scaffolds `.workflow/workflows/` (the per-agent default lookup
    location). Pass `--workflow-dir PATH` to record an override in the
    config — useful when multiple per-role agent homes share a single
    workflows directory. Relative paths are resolved from the agent home
    at lookup time.
    """
    ctx = _ctx_obj_from_args(args)

    agent_role = ctx.get("agent_role")
    if not agent_role:
        return _handle_workflow_error(
            ConfigError(
                "init requires --agent-role (or AGENT_ROLE env). "
                "Example: workflow --agent-role pm init"
            )
        )

    target = (ctx.get("agent_home") or Path.cwd()).resolve()
    workflow_dir = target / ".workflow"
    config_path = workflow_dir / "config.json"
    grants_path = workflow_dir / "trust-grants"
    workflows_path = workflow_dir / "workflows"

    if config_path.exists() and not args.force:
        return _handle_workflow_error(
            ConfigError(f"{config_path} already exists. Pass --force to overwrite.")
        )

    # Build the config from provided values. Strip placeholder braces from
    # the role so `{pm}` and `pm` both produce the same `pm`.
    #
    # `agent-role` is always persisted. `workflow-dir` is persisted when the
    # user passes --workflow-dir at init — stored verbatim (relative paths
    # are resolved from the agent home at lookup time) and overrides the
    # default `.workflow/workflows/` location. Other CLI flags like
    # --repo / --host stay per-invocation and are never written here.
    config: dict[str, Any] = {"agent-role": agent_role.strip("{}").strip()}
    init_workflow_dir = getattr(args, "init_workflow_dir", None)
    if init_workflow_dir:
        config["workflow-dir"] = init_workflow_dir

    if ctx["dry_run"]:
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "agent_home": str(target),
                        "config_path": str(config_path),
                        "workflow_dir": str(workflows_path),
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
            if init_workflow_dir:
                print(f"  workflow-dir:  {init_workflow_dir} (override, recorded in config)")
            print("")
            print("Config that would be written:")
            for line in _json.dumps(config, indent=2).splitlines():
                print(f"  {line}")
        return 0

    # Past the dry-run gate — now it's safe to touch the filesystem.
    try:
        workflow_dir.mkdir(parents=True, exist_ok=True)
        grants_path.mkdir(parents=True, exist_ok=True)
        workflows_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _handle_workflow_error(ConfigError(f"Could not create {workflow_dir}: {exc}"))

    try:
        config_path.write_text(
            _json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return _handle_workflow_error(ConfigError(f"Could not write {config_path}: {exc}"))

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "agent_home": str(target),
                    "config_path": str(config_path),
                    "workflow_dir": str(workflows_path),
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


def _enumerate_required_labels(ctx: dict, *, encoding: str) -> set[str]:
    """Compute the full set of label names the framework requires for a repo.

    Sources, aggregated across every workflow in the registry (ADR-0005
    label grammar — `<kebab-classifier>/<value>`):

    - `state/<name>` for every state in every workflow.
    - `claimed/<role_id>` for every role in roles.json.
    - `last-state/<state>` for every resting state (origin marker).
    - `hitl-claim/<reviewing|auditing|advising>` (the human-claim singletons).
    - `hitl-signal/<approved|rejected|checked|revoked|resolved>` (the bounded
      transient signal outcomes — now pre-provisioned since they no longer
      carry a per-gate suffix).
    - `hitl-blocked/<gate>` / `hitl-audit/<gate>` per catalogued HumanGate.
    - `type/<type_id>` per declared issue type WHEN encoding is `"label"`.

    `hitl-input/<topic>` is NOT pre-provisioned: the topic is open-ended, so
    those labels are created lazily on first use.
    """
    from workflow.config import build_registry
    from workflow.core.model.human_gate import HumanGateLevel

    labels: set[str] = {gh_labels.hitl_claim_label(v) for v in gh_labels.CLAIM_VALUES} | {
        gh_labels.hitl_signal_label(v) for v in gh_labels.SIGNAL_VALUES
    }

    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflow_dir=ctx.get("workflow_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        raise ConfigError(
            "No workflows directory found. Set WORKFLOW_DIR or run from "
            "inside a tree containing `*-states.json` files."
        )

    for wf_name in registry.discovered_processes():
        try:
            wf_context = registry.get_process(wf_name)
        except WorkflowError as exc:
            logger.warning("Skipping workflow %r during label enumeration: %s", wf_name, exc)
            continue

        for state_name, state in wf_context.state_machine.states.items():
            labels.add(gh_labels.state_label(state_name))
            # Closing states are resting but are sinks — a closed issue has no
            # origin to return to, so they get no `last-state/` marker.
            if state.state_class.value == "resting" and not state.is_closing:
                labels.add(gh_labels.last_state_label(state_name))

        if wf_context.catalog:
            for gate in wf_context.catalog.entries.values():
                if HumanGateLevel.BLOCK in gate.allowed_levels:
                    labels.add(gh_labels.hitl_blocked_label(gate.gate_name))
                if HumanGateLevel.AUDIT in gate.allowed_levels:
                    labels.add(gh_labels.hitl_audit_label(gate.gate_name))

        if wf_context.role_directory:
            for role_id in wf_context.role_directory.roles:
                labels.add(gh_labels.claim_label(role_id))

        # When encoding is `"label"`, type is conveyed via `type/<id>` labels.
        if encoding == "label" and wf_context.issue_type_directory:
            for type_id in wf_context.issue_type_directory.types:
                labels.add(gh_labels.type_label(type_id))

    return labels


def _provision_labels(
    backend: Any,
    required: set[str],
    *,
    json_output: bool,
) -> int:
    """Idempotently create every label in `required` on the backend repo."""
    try:
        existing = set(backend.list_labels())
    except BackendError as exc:
        return _handle_workflow_error(exc)

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
            skipped.append(name)

    if json_output:
        print(
            _json.dumps(
                {
                    "labels_created": created,
                    "labels_skipped": skipped,
                    "labels_failed": [{"label": n, "error": e} for n, e in failed],
                },
                indent=2,
            )
        )
    else:
        print(
            f"labels: created {len(created)}, "
            f"skipped {len(skipped)} (already existed), "
            f"failed {len(failed)}."
        )
        for name in created:
            print(f"  + {name}")
        for name, err in failed:
            print(f"  ! {name}: {err}")
    return 1 if failed else 0


def _resolve_encoding(
    ctx: dict, backend: Any, *, force_probe: bool = False, persist: bool = True
) -> str:
    """Resolve the encoding for the current (host, owner), consulting the cache.

    - Manual entries are returned as-is (unless `force_probe=True`).
    - Non-manual, non-expired entries are returned as-is.
    - Otherwise, probes via `backend.list_issue_types(owner)` and caches.

    `persist=False` skips writing the probed result back to the cache — used by
    dry-run, which may read (probe) the tracker but must not leave side effects
    on disk (#26).

    Returns `"native"` or `"label"`.
    """
    from workflow.core.capability_cache import CapabilityCache

    host, owner = _host_and_owner(backend)
    cache = CapabilityCache.load()
    entry = cache.get(host, owner)
    if entry is not None and not force_probe:
        if entry.manual or not entry.is_expired():
            return entry.encoding

    types = backend.list_issue_types(owner)
    encoding = "native" if (types is not None and types) else "label"
    if persist:
        cache.set(host, owner, encoding, manual=False)
        cache.save()
    return encoding


def _host_and_owner(backend: Any) -> tuple[str, str]:
    """Extract (host, owner) from a backend for cache keying."""
    host = backend.host or "github.com"
    owner = backend.repo.split("/", 1)[0] if backend.repo else "unknown"
    return host, owner


def _do_setup_github(args: argparse.Namespace) -> int:
    """Provision GitHub Issue Types (org-level) and labels (repo-level).

    Default: best-effort org types + always repo labels (with encoding
    auto-fallback). `--setup-org`: org types only; force-refreshes cache.
    """
    from workflow.config import build_registry
    from workflow.core.capability_cache import CapabilityCache

    ctx = _ctx_obj_from_args(args)

    try:
        backend = _build_backend(ctx)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)
    host, owner = _host_and_owner(backend)

    # Collect issue types from the registry (the first process's directory wins
    # since it's shared across processes in a workflow).
    registry = build_registry(
        agent_home=ctx.get("agent_home"),
        workflow_dir=ctx.get("workflow_dir"),
        backend=None,
        grants_dir=ctx.get("grants_dir"),
    )
    if registry is None:
        return _handle_workflow_error(
            ConfigError(
                "No workflows directory found. Set WORKFLOW_DIR or run from "
                "inside a tree containing `*-states.json` files."
            )
        )
    type_directory = None
    for wf_name in registry.discovered_processes():
        try:
            wf_context = registry.get_process(wf_name)
        except WorkflowError:
            continue
        if wf_context.issue_type_directory is not None:
            type_directory = wf_context.issue_type_directory
            break

    # Dry-run gate — BEFORE any cache write or org-type creation. Both the
    # default and --setup-org paths mutate (cache.save / ensure_issue_type), so
    # the gate sits here, ahead of them. Encoding is resolved read-only and
    # nothing is created (#26).
    if ctx["dry_run"]:
        return _report_setup_github_dry_run(ctx, backend, host, owner, type_directory)

    # --- --setup-org path: force-probe, create types, refresh cache, stop ---
    if args.setup_org:
        types_now = backend.list_issue_types(owner)
        if types_now is None:
            return _handle_workflow_error(
                ConfigError(
                    f"Org {owner!r} does not support Issue Types (or you "
                    f"lack permission to read them)."
                )
            )
        existing = set(types_now)
        if type_directory is None or not type_directory.types:
            print(f"No issue-types.json found; nothing to provision at org {owner!r}.")
            cache = CapabilityCache.load()
            cache.set(host, owner, "native" if existing else "label", manual=False)
            cache.save()
            return 0
        created: list[str] = []
        skipped: list[str] = []
        for entry in type_directory.types.values():
            gh_name = entry.github_issue_type
            if not gh_name:
                continue
            if gh_name in existing:
                skipped.append(gh_name)
                continue
            try:
                backend.ensure_issue_type(
                    owner,
                    name=gh_name,
                    description=entry.description,
                    color=entry.github_issue_type_color,
                )
                created.append(gh_name)
            except BackendError as exc:
                return _handle_workflow_error(exc)
        # Refresh cache (always native after a successful org provisioning).
        cache = CapabilityCache.load()
        cache.set(host, owner, "native", manual=False)
        cache.save()
        if ctx["json_output"]:
            print(
                _json.dumps(
                    {
                        "org": owner,
                        "types_created": created,
                        "types_skipped": skipped,
                    },
                    indent=2,
                )
            )
        else:
            print(f"setup-github --setup-org: created {len(created)}, skipped {len(skipped)}.")
            for name in created:
                print(f"  + {name}")
        return 0

    # --- Default path: best-effort org, then repo labels ---
    # First, resolve the encoding. If cache says native (or no entry yet and
    # probe says native), attempt org provisioning best-effort.
    encoding = _resolve_encoding(ctx, backend)

    if encoding == "native" and type_directory is not None and type_directory.types:
        existing_types = backend.list_issue_types(owner) or []
        existing_set = set(existing_types)
        org_failed = False
        for entry in type_directory.types.values():
            gh_name = entry.github_issue_type
            if not gh_name or gh_name in existing_set:
                continue
            try:
                backend.ensure_issue_type(
                    owner,
                    name=gh_name,
                    description=entry.description,
                    color=entry.github_issue_type_color,
                )
            except BackendError as exc:
                logger.info(
                    "Could not provision org type %r (%s); falling back to label encoding.",
                    gh_name,
                    exc,
                )
                org_failed = True
                break
        if org_failed:
            encoding = "label"
            cache = CapabilityCache.load()
            cache.set(host, owner, "label", manual=False)
            cache.save()
        else:
            # Org provisioning succeeded — refresh cache to native (idempotent
            # for the already-native case; ensures the timestamp is fresh).
            cache = CapabilityCache.load()
            cache.set(host, owner, "native", manual=False)
            cache.save()

    if not ctx["json_output"]:
        print(f"Encoding for {host}/{owner}: {encoding}")

    try:
        required = _enumerate_required_labels(ctx, encoding=encoding)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    return _provision_labels(backend, required, json_output=ctx["json_output"])


def _report_setup_github_dry_run(
    ctx: dict, backend: Any, host: str, owner: str, type_directory: Any
) -> int:
    """Report what `setup-github` would do, with no side effects (#26).

    Resolves the encoding read-only (`persist=False` — may probe the tracker
    but never writes the capability cache) and lists the org types and repo
    labels that would be provisioned, without creating any.
    """
    encoding = _resolve_encoding(ctx, backend, persist=False)

    types_to_create: list[str] = []
    if encoding == "native" and type_directory is not None and type_directory.types:
        existing = set(backend.list_issue_types(owner) or [])
        types_to_create = sorted(
            entry.github_issue_type
            for entry in type_directory.types.values()
            if entry.github_issue_type and entry.github_issue_type not in existing
        )

    try:
        required = _enumerate_required_labels(ctx, encoding=encoding)
    except WorkflowError as exc:
        return _handle_workflow_error(exc)

    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    "encoding": encoding,
                    "org_types_to_create": types_to_create,
                    "labels": sorted(required),
                    "dry_run": True,
                },
                indent=2,
            )
        )
    else:
        print(f"[dry-run] encoding for {host}/{owner}: {encoding} (cache not written)")
        if types_to_create:
            print(f"[dry-run] would create {len(types_to_create)} org issue type(s):")
            for name in types_to_create:
                print(f"  + {name}")
        print(f"[dry-run] would ensure {len(required)} label(s) on repo:")
        for name in sorted(required):
            print(f"  {name}")
    return 0


def _do_capabilities(args: argparse.Namespace) -> int:
    """Inspect or manage the capability cache."""
    from workflow.core.capability_cache import CapabilityCache

    ctx = _ctx_obj_from_args(args)
    cache = CapabilityCache.load()

    if args.clear:
        cache.clear()
        cache.save()
        print("Capability cache cleared.")
        return 0

    if args.set_encoding:
        try:
            backend = _build_backend(ctx)
        except WorkflowError as exc:
            return _handle_workflow_error(exc)
        host, owner = _host_and_owner(backend)
        cache.set(host, owner, args.set_encoding, manual=True)
        cache.save()
        print(f"Pinned {host}/{owner} encoding to {args.set_encoding!r} (manual).")
        return 0

    if args.refresh:
        try:
            backend = _build_backend(ctx)
        except WorkflowError as exc:
            return _handle_workflow_error(exc)
        refreshed: list[str] = []
        skipped: list[str] = []
        for key, entry in list(cache.entries.items()):
            if entry.manual:
                skipped.append(key)
                continue
            host, _, owner = key.partition("/")
            if not host or not owner:
                continue
            # Use the configured backend's host/owner to bound the probe
            # to a single endpoint we have credentials for.
            cb_host, cb_owner = _host_and_owner(backend)
            if (host, owner) != (cb_host, cb_owner):
                # Skip entries that aren't for the currently-configured backend.
                skipped.append(key)
                continue
            types = backend.list_issue_types(owner)
            encoding = "native" if (types is not None and types) else "label"
            cache.set(host, owner, encoding, manual=False)
            refreshed.append(f"{key} → {encoding}")
        cache.save()
        if ctx["json_output"]:
            print(_json.dumps({"refreshed": refreshed, "skipped": skipped}, indent=2))
        else:
            print(f"Refreshed {len(refreshed)} entry(ies); skipped {len(skipped)}.")
            for line in refreshed:
                print(f"  {line}")
        return 0

    # Default: print the cache.
    if ctx["json_output"]:
        print(
            _json.dumps(
                {
                    key: {
                        "encoding": e.encoding,
                        "checked_at": e.checked_at,
                        "manual": e.manual,
                    }
                    for key, e in cache.entries.items()
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not cache.entries:
        print("(empty capability cache)")
        return 0
    width = max(len(k) for k in cache.entries) + 2
    for key, entry in sorted(cache.entries.items()):
        suffix = " [manual]" if entry.manual else ""
        print(f"  {key.ljust(width)} {entry.encoding}{suffix}  ({entry.checked_at})")
    return 0


if __name__ == "__main__":
    main()
