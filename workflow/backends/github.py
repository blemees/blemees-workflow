"""GitHub backend — implements the `TrackerBackend` protocol via the `gh` CLI.

The label tier of the encoding (ADR-0005). The framework's markers map to
GitHub labels under one grammar, `<kebab-classifier>/<value>`:

- State → `state/<name>` label, exactly one applied at any moment.
- Agent claim → `claimed/<role>` label, at most one.
- HITL markers → `hitl-blocked/`, `hitl-audit/`, `hitl-input/`, `hitl-claim/`,
  and `hitl-signal/` labels.
- Audit records → issue/PR comments.

The grammar itself — encoding and parsing — lives in `github_labels`; this
backend only drives `gh` with the strings it produces.

The label swap is the one atomic step: every add/remove rides a single `gh
issue edit` invocation (GraphQL `replaceLabels`), so the state marker never
tears. The surrounding follow-ups in `apply_marker_change` — assignment, close,
pr-ready, audit comment — are a best-effort sequence, NOT part of that
transaction. The label swap goes first (it carries the state change); a
follow-up failure raises a partial-apply error with a repair hint rather than
leaving a silent inconsistency. `gh` has no multi-resource transaction, so a
fully atomic apply would need a single GraphQL mutation (future work).

The backend creates missing labels lazily with classifier-appropriate colors.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from workflow.backends import github_labels as gh_labels
from workflow.backends.base import IssueFilters, IssueState, MarkerChange
from workflow.errors import BackendError, OperationError

logger = logging.getLogger(__name__)


# Native-tier Issue Field names (ADR-0005). The single source of truth for both
# provisioning (CLI) and the native read/write path. Names are ≤25 chars
# (GitHub's field-name cap). The framework markers map onto these fields:
#   Workflow State ← state          Last State ← last_state
#   Agent          ← agent_claim    HITL Blocked ← awaiting_gate
#   HITL Audit     ← audit_pending  HITL Input  ← awaiting_input + topic
#   HITL Claim     ← reviewing/auditing/advising (one single-select)
#   HITL Signal    ← approved/rejected/checked/revoked/resolved
#   Collected By   ← collected_by (text; written via the #74 relationship path)
FIELD_STATE = "Workflow State"
FIELD_LAST_STATE = "Last State"
FIELD_AGENT = "Agent"
FIELD_HITL_BLOCKED = "HITL Blocked"
FIELD_HITL_AUDIT = "HITL Audit"
FIELD_HITL_INPUT = "HITL Input"
FIELD_HITL_CLAIM = "HITL Claim"
FIELD_HITL_SIGNAL = "HITL Signal"
FIELD_COLLECTED_BY = "Collected By"


# Matches every git remote URL form for GitHub / GHES:
#
#   https://[user@]host[:port]/owner/repo[.git]
#   http://[user@]host[:port]/owner/repo[.git]
#   git://host[:port]/owner/repo[.git]
#   ssh://[user@]host[:port]/owner/repo[.git]
#   user@host:owner/repo[.git]              (scp-style shorthand)
#
# Captures `host`, `owner`, and `repo`. Trailing `.git` and any single trailing
# slash are stripped.
_GIT_REMOTE_URL_RE = re.compile(
    r"""
    ^
    (?:
        (?:https?|git|ssh)://
        (?:[^@/]+@)?
        (?P<host_url>[^:/]+)
        (?::\d+)?
        /
      |
        (?:[^@\s]+@)
        (?P<host_scp>[^:/]+)
        :
    )
    (?P<owner>[^/]+)/(?P<repo>[^/]+?)
    (?:\.git)?/?
    $
    """,
    re.VERBOSE,
)


def parse_git_remote_url(url: str) -> tuple[str, str] | None:
    """Parse a git remote URL into `(host, owner/name)`.

    Returns None for anything that doesn't match a recognized git URL shape.
    Trailing `.git` and trailing slashes are stripped; ports and embedded
    credentials are ignored.

    Examples:
        >>> parse_git_remote_url("git@ghe.acme.com:myorg/myrepo.git")
        ('ghe.acme.com', 'myorg/myrepo')
        >>> parse_git_remote_url("https://github.com/owner/repo")
        ('github.com', 'owner/repo')
    """
    m = _GIT_REMOTE_URL_RE.match(url.strip())
    if not m:
        return None
    host = m.group("host_url") or m.group("host_scp")
    if not host:
        return None
    return host, f"{m.group('owner')}/{m.group('repo')}"


def discover_remote_from_git(
    cwd: Path | None = None,
    remote: str = "origin",
    git_bin: str = "git",
) -> tuple[str | None, str | None]:
    """Discover (host, owner/name) from a git remote in the working directory.

    Runs `git remote get-url <remote>` (default `origin`) and parses the
    resulting URL. Both host and slug come from the same URL — for GitHub
    Enterprise Server checkouts, the host is already embedded in the
    remote (`ghe.acme.com` etc.), so this single source resolves both.

    Returns `(host, slug)` on success, `(None, None)` on any failure:
        - `git` is not installed,
        - cwd is not inside a git repository,
        - the named remote doesn't exist,
        - the remote URL doesn't parse as a recognizable git URL.

    Never raises.
    """
    try:
        proc = subprocess.run(
            [git_bin, "remote", "get-url", remote],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError:
        logger.debug("git binary not found; cannot auto-discover remote.")
        return None, None

    if proc.returncode != 0:
        logger.debug(
            "git remote get-url %s failed (rc=%s): %s",
            remote,
            proc.returncode,
            (proc.stderr or proc.stdout).strip(),
        )
        return None, None

    parsed = parse_git_remote_url(proc.stdout)
    if parsed is None:
        logger.debug("Could not parse remote URL: %r", proc.stdout.strip())
        return None, None
    return parsed


@dataclass
class GitHubBackend:
    """Concrete backend for GitHub Issues / PRs.

    `repo` is the `owner/name` slug. `gh_bin` is the `gh` executable name;
    defaults to `gh` on PATH.

    `host` is the GitHub host (e.g., `ghe.example.com` for GitHub Enterprise
    Server). When set, every `gh` invocation runs with `GH_HOST=<host>` in
    its environment, which directs gh at that server's API. When unset, gh
    falls back to its own resolution: the user's exported `GH_HOST` env
    var, or the host they authenticated against with `gh auth login`.
    """

    repo: str
    gh_bin: str = "gh"
    host: str | None = None
    name: str = "github"
    # Capability tier (ADR-0005): "label" encodes markers as labels; "native"
    # encodes them as org Issue Fields / native Issue Type / sub-issues. The CLI
    # resolves this per (host, owner) and constructs the backend with it.
    tier: str = "label"
    # Cached per-session: labels we've already ensured exist on this repo.
    _known_labels: set[str] = field(default_factory=set)
    # Cached per-session (native tier): org field metadata —
    # {field_name: {"id": <field node id>, "options": {opt_name: opt_id}}}.
    _field_meta: dict | None = field(default=None)

    # ----- backend protocol -----

    def list_issues(self, filters: IssueFilters) -> list[IssueState]:
        """List issues AND pull requests by translating filters to `gh` flags.

        `gh issue list` excludes pull requests and `gh pr list` excludes issues,
        so this queries both and merges the results, de-duplicated by id. Both
        are queried with `--state all` so closed issues and closed/merged PRs
        are visible — cohort queries (`child-of/` / `collected-by/`) and
        closing-state searches depend on it (ADR-0003).

        The filter translation (applied identically to issues and PRs):

        - `filters.state` → `--label state/<name>`
        - `filters.claim_role` → `--label claimed/<role>`
        - `filters.awaiting_gate` ("*" → match any awaiting; specific name → that label)
        - `filters.audit_pending` ("*" → match any audit-pending; specific → that label)
        - `filters.awaiting_input` (True → `--label hitl-input/<topic>` is not a single
          fixed label, so this falls to post-fetch filtering)
        - `filters.child_of` → `--label child-of/<id>` (cohort: a parent's children)
        - `filters.collected_by` → `--label collected-by/<id>` (cohort: a collector's contributors)
        - `filters.limit` → `--limit N` (applied per entity kind)

        For wildcard awaiting / audit filters and `awaiting_input` that `gh`
        can't express with a single label match, the backend filters in Python
        after fetching.

        In the native tier this routes to `_list_issues_native`, which uses
        `field."<name>":<value>` / `type:` search qualifiers instead of labels.
        """
        if self.tier == "native":
            return self._list_issues_native(filters)

        wildcard_awaiting = filters.awaiting_gate == "*"
        wildcard_audit = filters.audit_pending == "*"

        label_filters: list[str] = []
        if filters.state:
            label_filters.append(gh_labels.state_label(filters.state))
        if filters.claim_role:
            label_filters.append(gh_labels.claim_label(filters.claim_role))
        if filters.awaiting_gate and not wildcard_awaiting:
            label_filters.append(gh_labels.hitl_blocked_label(filters.awaiting_gate))
        if filters.audit_pending and not wildcard_audit:
            label_filters.append(gh_labels.hitl_audit_label(filters.audit_pending))
        if filters.child_of:
            label_filters.append(gh_labels.child_of_label(filters.child_of))
        if filters.collected_by:
            label_filters.append(gh_labels.collected_by_label(filters.collected_by))

        issue_entries = self._list_entities("issue", label_filters, filters.limit)
        pr_entries = self._list_entities("pr", label_filters, filters.limit)

        # Wildcard gate/audit and any `awaiting_input` filter can't be expressed
        # as a single `gh` label filter (`hitl-input/<topic>` is topic-keyed, so
        # there is no fixed label to match on), so they're applied in Python
        # *after* the `--limit` cap. If a kind's raw fetch hit that cap, matches
        # beyond it were never seen — warn rather than silently under-report
        # (#26). The honest fix is pagination; until then, raise --limit.
        post_filtering = wildcard_awaiting or wildcard_audit or (filters.awaiting_input is not None)
        if post_filtering:
            for kind, fetched in (("issue", issue_entries), ("pr", pr_entries)):
                if len(fetched) >= filters.limit:
                    logger.warning(
                        "list_issues hit the --limit %d cap on %ss while a post-fetch filter "
                        "(wildcard gate/audit or awaiting_input) is active; matches beyond "
                        "the first %d may be missed. Raise --limit to widen the window.",
                        filters.limit,
                        kind,
                        filters.limit,
                    )

        entries = issue_entries + pr_entries

        results: list[IssueState] = []
        seen: set[str] = set()
        for entry in entries:
            number = str(entry.get("number", ""))
            # Issues and PRs share one number space, so a number is one or the
            # other; dedup is defensive against any overlap in the merge.
            if number in seen:
                continue
            seen.add(number)

            labels = [lbl.get("name", "") for lbl in (entry.get("labels") or [])]
            state = self._labels_to_state(number, labels)

            # Wildcard awaiting / audit and awaiting_input filters post-filter here.
            if wildcard_awaiting and not state.awaiting_gate:
                continue
            if wildcard_audit and not state.audit_pending:
                continue
            if filters.awaiting_input is True and not state.awaiting_input:
                continue
            if filters.awaiting_input is False and state.awaiting_input:
                continue

            # Title isn't on IssueState; we stash it in `extras` so the CLI
            # can display it without re-fetching. `extras` is a dict (mutable
            # even on the frozen dataclass).
            if entry.get("title"):
                state.extras["title"] = entry["title"]
            results.append(state)

        return results

    def _list_entities(self, kind: str, label_filters: list[str], limit: int) -> list[dict]:
        """Run `gh <kind> list --state all` with label filters; return raw entries.

        `kind` is "issue" or "pr". Querying with `--state all` makes closed
        issues and closed/merged PRs visible; `list_issues` calls this once per
        kind and merges, because neither `gh` subcommand returns the other's
        entities.
        """
        args: list[str] = [
            kind,
            "list",
            "--repo",
            self.repo,
            "--state",
            "all",
            "--limit",
            str(limit),
        ]
        for label in label_filters:
            args += ["--label", label]
        args += ["--json", "number,labels,title,state"]

        try:
            output = self._gh(*args)
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for {kind} list: {exc}") from exc

    def create_issue(
        self,
        title: str,
        body: str,
        state: str,
        extra_labels: list[str] | None = None,
        issue_type: str | None = None,
    ) -> str:
        """Create a new GitHub issue with the framework's state marker.

        Uses `gh issue create --title T --body-file BODY --label state/X`,
        adding every label in `extra_labels` (e.g., `claimed/<role>` for an
        immediate claim) to the same `--label` flag. Existing labels on
        the repo are required; missing ones are created lazily via
        `ensure_label` before the issue is created so `gh` doesn't error
        on an unknown label.

        When `issue_type` is set, `gh issue create --type <issue_type>` is
        added — this is GitHub's first-class Issue Type field. The string
        must be the exact GitHub Issue Type name (e.g., "Bug", "Feature").

        The issue URL printed by `gh` is parsed back into the issue number
        and returned as a string.

        In the native tier the state (and any claim) is written as Issue Field
        values after creation rather than as labels — see `_create_issue_native`.
        """
        if self.tier == "native":
            return self._create_issue_native(title, body, state, extra_labels, issue_type)

        labels = [gh_labels.state_label(state)]
        if extra_labels:
            labels.extend(extra_labels)
        for label in labels:
            self.ensure_label(label)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            delete=False,
        ) as tmp:
            tmp.write(body or "")
            tmp_path = tmp.name
        try:
            # `--flag=value` form (and one `--label` per label) so a title /
            # label that starts with `-` isn't parsed as a flag and an id
            # containing a comma isn't split (#27).
            args: list[str] = [
                "issue",
                "create",
                "--repo",
                self.repo,
                f"--title={title}",
                "--body-file",
                tmp_path,
            ]
            args += [f"--label={lbl}" for lbl in labels]
            if issue_type:
                args += [f"--type={issue_type}"]
            output = self._gh(*args)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # `gh issue create` prints the new issue URL on the last non-empty line:
        #   https://github.com/owner/repo/issues/123
        url = ""
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped:
                url = stripped
                break
        if not url:
            raise BackendError(f"`gh issue create` returned no output: {output!r}")
        if "/issues/" not in url:
            raise BackendError(
                f"`gh issue create` returned unexpected output (no /issues/ in URL): {url!r}"
            )
        return url.rsplit("/", 1)[-1]

    def create_pull_request(
        self,
        title: str,
        body: str,
        state: str,
        head: str,
        base: str | None = None,
        draft: bool = True,  # kept for backwards compat; always passed as draft
        extra_labels: list[str] | None = None,
    ) -> str:
        """Create a GitHub PR via `gh pr create --head H --base B --title T
        --body-file F --draft --label L1,L2`.

        Every framework-created PR opens as a GitHub draft PR regardless of
        the `draft` argument — the framework's PR lifecycle starts at
        `draft` and the `mark_pr_ready` state field is what flips the PR
        to ready-for-review (via `mark_ready_for_review`). The `draft`
        parameter is retained for protocol compatibility but ignored.

        The framework's `state/<name>` label is attached atomically with
        creation (gh `pr create` accepts `--label`). Labels are ensured to
        exist on the repo before the call so gh doesn't error on missing
        names. Returns the new PR's number parsed from gh's output URL.
        """
        labels = [gh_labels.state_label(state)]
        if extra_labels:
            labels.extend(extra_labels)
        for label in labels:
            self.ensure_label(label)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            delete=False,
        ) as tmp:
            tmp.write(body or "")
            tmp_path = tmp.name
        try:
            # `--flag=value` form (and one `--label` per label) — see create_issue (#27).
            args: list[str] = [
                "pr",
                "create",
                "--repo",
                self.repo,
                f"--title={title}",
                "--body-file",
                tmp_path,
                f"--head={head}",
            ]
            args += [f"--label={lbl}" for lbl in labels]
            if base:
                args += [f"--base={base}"]
            # Always create PRs as drafts — the framework's PR lifecycle
            # starts at `draft`, and the `mark_pr_ready` state field is the
            # explicit signal to flip the PR to ready-for-review.
            args.append("--draft")
            output = self._gh(*args)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # `gh pr create` prints the new PR URL on the last non-empty line:
        #   https://github.com/owner/repo/pull/123
        url = ""
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped:
                url = stripped
                break
        if not url:
            raise BackendError(f"`gh pr create` returned no output: {output!r}")
        if "/pull/" not in url:
            raise BackendError(
                f"`gh pr create` returned unexpected output (no /pull/ in URL): {url!r}"
            )
        return url.rsplit("/", 1)[-1]

    def read_issue(self, issue_id: str) -> IssueState:
        if self.tier == "native":
            return self._read_native(issue_id)[1]
        result = self._gh(
            "issue",
            "view",
            str(issue_id),
            "--repo",
            self.repo,
            "--json",
            "number,labels,assignees,state,issueType",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {issue_id}: {exc}") from exc

        labels = [lbl.get("name", "") for lbl in (data.get("labels") or [])]
        # `issueType` is GitHub's native Issue Type ({"name": "Bug"} or null).
        # Under native encoding there's no `type/` label, so this is the only
        # signal of the issue's type.
        issue_type_obj = data.get("issueType") or {}
        native_type = issue_type_obj.get("name") if isinstance(issue_type_obj, dict) else None
        return self._labels_to_state(issue_id, labels, native_issue_type=native_type)

    def apply_marker_change(
        self,
        issue_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
        if self.tier == "native":
            self._apply_marker_change_native(issue_id, change, audit_comment)
            return
        # This is NOT a single atomic transaction (gh has no multi-resource
        # transaction). Only the label swap is atomic (one `gh issue edit` →
        # GraphQL `replaceLabels`); it carries the state change, so it goes
        # FIRST. Everything after is a best-effort sequence: a failure there
        # means the state label is already updated, so we raise a partial-apply
        # error naming what to repair rather than leaving a silent inconsistency
        # (#20). The audit comment is posted LAST, so it never records a
        # transition that didn't happen.
        current = self.read_issue(issue_id)
        add, remove = self._marker_change_to_labels(current, change)

        # Ensure any labels we're about to add exist on the repo.
        for label in add:
            self._ensure_label_exists(label)

        # 1. The atomic state change: add + remove in a single `gh` invocation.
        #    If this fails, nothing has changed — a bare error is correct.
        if add or remove:
            args: list[str] = ["issue", "edit", str(issue_id), "--repo", self.repo]
            # One flag per label (`--flag=value`) so ids never split on a comma
            # or parse as a flag (#27).
            args += [f"--add-label={lbl}" for lbl in sorted(add)]
            args += [f"--remove-label={lbl}" for lbl in sorted(remove)]
            self._gh(*args)

        # 1a. Claim concurrency control. GitHub labels have no compare-and-swap,
        #     and `--add-label claimed/<role>` is additive: two agents claiming
        #     the same resting issue both pass the planner's snapshot precondition
        #     and both claim labels land, violating "at most one claim" (#21).
        #     Verify after the write — if our claim didn't win cleanly, self-revert
        #     and raise so the loser doesn't believe it holds the claim.
        if change.set_agent_claim:
            self._verify_claim_won(issue_id, change.set_agent_claim)

        # 2. Best-effort follow-ups. Past this point the state label is set, so a
        #    failure is a *partial* apply — surface it with a repair hint.
        try:
            if change.set_agent_claim:
                role_handle = self.resolve_role(change.set_agent_claim)
                if role_handle:
                    self.assign(issue_id, role_handle)
            if change.clear_agent_claim:
                self.unassign(issue_id)
            # Close the issue when the advance lands on a closing state.
            if change.close_issue:
                self.close_issue(issue_id, reason=change.close_reason)
            # Flip the PR draft → ready when the destination declared
            # `mark_pr_ready`. Self-non-fatal when the issue isn't a PR.
            if change.set_pr_ready:
                self.mark_pr_ready(issue_id)
        except BackendError as exc:
            raise BackendError(
                f"Issue #{issue_id}: the state label was updated but a follow-up step failed "
                f"({exc}). The issue is partially applied — re-run the same operation "
                f"(label edits are idempotent) or repair the assignment/close state by hand."
            ) from exc

        # 3. Audit comment last — best-effort. A missing comment is an audit gap,
        #    not a state inconsistency, so it never fails the operation.
        if audit_comment:
            try:
                self.post_comment(issue_id, audit_comment)
            except BackendError as exc:
                logger.warning(
                    "Issue #%s: state applied but audit comment failed to post: %s",
                    issue_id,
                    exc,
                )

    def _verify_claim_won(self, issue_id: str, role: str) -> None:
        """Confirm our `claimed/<role>` claim is the only one on the issue (#21).

        Called immediately after a claim's label swap. Re-reads the live label
        set and checks exactly one claiming role is present and it is ours. If a
        concurrent claim also landed (a second claim label), or the surviving
        claim isn't ours, we lost the race: remove our own claim label so we
        don't leave a phantom claim, then raise `OperationError`.

        This narrows but does not fully close the race. Both contenders may
        observe two labels and both self-revert (leaving the issue unclaimed for
        a retry), or — depending on read/write interleaving — one may read its
        label alone and return success while the other reverts. Either way the
        invariant that matters holds: no caller ever returns believing it won
        while a second claim label survives. The residual window is inherent to
        a tracker without compare-and-swap; a single GraphQL mutation would be
        the real fix.
        """
        expected = gh_labels.claim_label(role)
        claim_roles = sorted(
            {
                parsed.value
                for raw in self._fetch_labels(issue_id)
                if (parsed := gh_labels.parse_label(raw)) is not None
                and parsed.kind == gh_labels.CLAIM
                and parsed.value
            }
        )
        if claim_roles == [role]:
            return  # clean win
        # Lost (or contended) — drop our own label so no phantom claim remains.
        try:
            self._gh(
                "issue",
                "edit",
                str(issue_id),
                "--repo",
                self.repo,
                f"--remove-label={expected}",
            )
        except BackendError as exc:
            logger.warning(
                "Issue #%s: lost claim race and failed to self-revert %r: %s",
                issue_id,
                expected,
                exc,
            )
        raise OperationError(
            f"Lost claim race on #{issue_id}: expected only role {role!r} after claiming, "
            f"but the live claiming roles are {claim_roles}. A concurrent agent claimed the "
            f"same issue; our claim was reverted. Re-poll the queue and claim a different issue."
        )

    def _fetch_labels(self, issue_id: str) -> list[str]:
        """Return the raw label names currently on the issue.

        Unlike `read_issue`, which collapses the label set into an `IssueState`
        (one claim wins), this preserves every label — needed by the claim race
        check, which must see duplicate claim labels.
        """
        result = self._gh("issue", "view", str(issue_id), "--repo", self.repo, "--json", "labels")
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {issue_id}: {exc}") from exc
        return [lbl.get("name", "") for lbl in (data.get("labels") or [])]

    def mark_pr_ready(self, pr_id: str) -> None:
        """Flip the PR from draft to ready-for-review via `gh pr ready`.

        Non-fatal: if the issue isn't a PR, gh exits non-zero — we log
        and continue. This keeps the framework's "set_pr_ready on every
        mark_pr_ready state" semantic from breaking advances on
        non-PR issues that happen to land on a state with the flag.
        """
        try:
            self._gh("pr", "ready", str(pr_id), "--repo", self.repo)
        except BackendError as exc:
            logger.warning(
                "gh pr ready %s failed (non-fatal; not a PR or already ready?): %s",
                pr_id,
                exc,
            )

    def post_comment(self, issue_id: str, body: str) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            delete=False,
        ) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        try:
            self._gh(
                "issue",
                "comment",
                str(issue_id),
                "--repo",
                self.repo,
                "--body-file",
                tmp_path,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def read_comments(self, issue_id: str, since: str | None = None) -> list[dict]:
        result = self._gh(
            "issue",
            "view",
            str(issue_id),
            "--repo",
            self.repo,
            "--json",
            "comments",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {issue_id}: {exc}") from exc
        comments = data.get("comments") or []
        if since is not None:
            comments = [c for c in comments if c.get("createdAt", "") >= since]
        return [
            {
                "author": (c.get("author") or {}).get("login", ""),
                "body": c.get("body", ""),
                "created_at": c.get("createdAt", ""),
            }
            for c in comments
        ]

    def resolve_role(self, role_id: str) -> str | None:
        # TODO: role-to-handle mapping is the team config's concern; until the
        # workflow tool grows a config loader, role names are returned to the
        # caller untranslated. The claimed/<role> label is always applied; only
        # the GitHub assignee is conditionally set.
        logger.debug(
            "resolve_role(%r) — no team mapping configured; returning None.",
            role_id,
        )
        return None

    def assignee(self, issue_id: str) -> str | None:
        result = self._gh(
            "issue",
            "view",
            str(issue_id),
            "--repo",
            self.repo,
            "--json",
            "assignees",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {issue_id}: {exc}") from exc
        assignees = data.get("assignees") or []
        if not assignees:
            return None
        return assignees[0].get("login")

    def assign(self, issue_id: str, handle: str) -> None:
        self._gh(
            "issue",
            "edit",
            str(issue_id),
            "--repo",
            self.repo,
            "--add-assignee",
            handle,
        )

    def unassign(self, issue_id: str) -> None:
        # Claims are framework-managed via `claimed/<role>` labels, but GitHub
        # assignees are not yet framework-managed because role→handle mapping
        # is still unresolved. Do not remove a human/UI assignment that this
        # backend cannot prove it created.
        logger.debug(
            "unassign(%r) skipped; no framework-managed role-to-handle mapping configured.",
            issue_id,
        )

    def edit_issue(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        """Edit the issue's title and/or body via `gh issue edit`.

        The body is passed via `--body-file` (a temp file) so newlines and
        markdown formatting survive shell escaping. Either or both of title
        and body may be set — neither is also valid (no-op), but the CLI
        rejects that case before reaching here.
        """
        if title is None and body is None:
            return
        args: list[str] = ["issue", "edit", str(issue_id), "--repo", self.repo]
        if title is not None:
            args += ["--title", title]
        if body is None:
            self._gh(*args)
            return
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            delete=False,
        ) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        try:
            self._gh(*args, "--body-file", tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def close_issue(self, issue_id: str, reason: str | None = None) -> None:
        """Close the GitHub issue via `gh issue close`.

        `reason` is `--reason completed` or `--reason "not planned"` (GitHub's
        two valid values). An unrecognised non-None reason is rejected loudly
        rather than silently dropped (which would close as `completed` and lose
        the authored intent) (#27).
        """
        if reason is not None and reason not in ("completed", "not planned"):
            raise BackendError(
                f"Unsupported close reason {reason!r} for GitHub — expected "
                f"'completed' or 'not planned'. Fix the closing state's `closes.reason`."
            )
        args: list[str] = [
            "issue",
            "close",
            str(issue_id),
            "--repo",
            self.repo,
        ]
        if reason is not None:
            args += ["--reason", reason]
        self._gh(*args)

    # ----- internals -----

    def _labels_to_state(
        self, issue_id: str, labels: list[str], native_issue_type: str | None = None
    ) -> IssueState:
        state: str | None = None
        agent_claim: str | None = None
        last_state: str | None = None
        awaiting_gate: str | None = None
        audit_pending: str | None = None
        issue_type: str | None = None
        human_input: str | None = None
        collected_by: str | None = None
        child_of: str | None = None
        reviewing = False
        auditing = False
        advising = False
        awaiting_input = False

        # The grammar module resolves each label; here we only fan each parsed
        # marker out to its field. `collects:` / `subprocess:` were never
        # encoded — those cohorts are `collected-by/` / `child-of/` queries
        # (ADR-0003).
        for raw in labels:
            parsed = gh_labels.parse_label(raw)
            if parsed is None:
                continue
            kind, value = parsed.kind, parsed.value
            if kind == gh_labels.STATE:
                state = value
            elif kind == gh_labels.CLAIM:
                agent_claim = value
            elif kind == gh_labels.LAST_STATE:
                last_state = value
            elif kind == gh_labels.TYPE:
                issue_type = value
            elif kind == gh_labels.COLLECTED_BY:
                collected_by = value
            elif kind == gh_labels.CHILD_OF:
                child_of = value
            elif kind == gh_labels.HITL_BLOCKED:
                awaiting_gate = value
            elif kind == gh_labels.HITL_AUDIT:
                audit_pending = value
            elif kind == gh_labels.HITL_INPUT:
                # `hitl-input/<topic>` carries both the queue marker and the topic.
                awaiting_input = True
                if value:
                    human_input = value
            elif kind == gh_labels.HITL_CLAIM:
                if value == gh_labels.CLAIM_REVIEWING:
                    reviewing = True
                elif value == gh_labels.CLAIM_AUDITING:
                    auditing = True
                elif value == gh_labels.CLAIM_ADVISING:
                    advising = True
            elif kind == gh_labels.HITL_SIGNAL:
                pass  # transient audit-trace; never translates to state

        return IssueState(
            issue_id=str(issue_id),
            state=state,
            agent_claim=agent_claim,
            last_state=last_state,
            issue_type=issue_type,
            awaiting_gate=awaiting_gate,
            reviewing=reviewing,
            audit_pending=audit_pending,
            auditing=auditing,
            awaiting_input=awaiting_input,
            human_input=human_input,
            advising=advising,
            collected_by=collected_by,
            child_of=child_of,
            # Only surface the native type when no `type/` label gave us the
            # framework id directly (label encoding wins).
            native_issue_type=native_issue_type if issue_type is None else None,
        )

    def _marker_change_to_labels(
        self,
        current: IssueState,
        change: MarkerChange,
    ) -> tuple[set[str], set[str]]:
        add: set[str] = set()
        remove: set[str] = set()

        # State transitions: swap state/ labels.
        if change.set_state is not None:
            if current.state is not None and current.state != change.set_state:
                remove.add(gh_labels.state_label(current.state))
            add.add(gh_labels.state_label(change.set_state))

        # Agent claim.
        if change.clear_agent_claim and current.agent_claim:
            remove.add(gh_labels.claim_label(current.agent_claim))
        if change.set_agent_claim:
            if current.agent_claim and current.agent_claim != change.set_agent_claim:
                remove.add(gh_labels.claim_label(current.agent_claim))
            add.add(gh_labels.claim_label(change.set_agent_claim))

        # Origin marker (the resting state we came from on claim).
        if change.clear_last_state and current.last_state:
            remove.add(gh_labels.last_state_label(current.last_state))
        if change.set_last_state:
            if current.last_state and current.last_state != change.set_last_state:
                remove.add(gh_labels.last_state_label(current.last_state))
            add.add(gh_labels.last_state_label(change.set_last_state))

        # Awaiting (block) gate.
        if change.clear_awaiting_gate and current.awaiting_gate:
            remove.add(gh_labels.hitl_blocked_label(current.awaiting_gate))
        if change.set_awaiting_gate:
            if current.awaiting_gate and current.awaiting_gate != change.set_awaiting_gate:
                remove.add(gh_labels.hitl_blocked_label(current.awaiting_gate))
            add.add(gh_labels.hitl_blocked_label(change.set_awaiting_gate))

        # Audit pending.
        if change.clear_audit_pending and current.audit_pending:
            remove.add(gh_labels.hitl_audit_label(current.audit_pending))
        if change.set_audit_pending:
            if current.audit_pending and current.audit_pending != change.set_audit_pending:
                remove.add(gh_labels.hitl_audit_label(current.audit_pending))
            add.add(gh_labels.hitl_audit_label(change.set_audit_pending))

        # Singleton claim markers → hitl-claim/<which>.
        if change.set_reviewing is True:
            add.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_REVIEWING))
        elif change.set_reviewing is False and current.reviewing:
            remove.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_REVIEWING))
        if change.set_auditing is True:
            add.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_AUDITING))
        elif change.set_auditing is False and current.auditing:
            remove.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_AUDITING))
        if change.set_advising is True:
            add.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_ADVISING))
        elif change.set_advising is False and current.advising:
            remove.add(gh_labels.hitl_claim_label(gh_labels.CLAIM_ADVISING))

        # Recognized input — one merged label `hitl-input/<topic>` carries both
        # the queue marker and the topic. Keyed on the topic, so request-input
        # adds it (set_human_input) and respond removes it (clear_human_input /
        # set_awaiting_input False against the live topic).
        if change.set_human_input:
            add.add(gh_labels.hitl_input_label(change.set_human_input))
        clearing_input = change.clear_human_input or change.set_awaiting_input is False
        if clearing_input and current.human_input:
            remove.add(gh_labels.hitl_input_label(current.human_input))

        # Outcome / signal markers → hitl-signal/<value> (transient audit-trace).
        if change.record_approval:
            add.add(gh_labels.hitl_signal_label(gh_labels.SIGNAL_APPROVED))
        if change.record_rejection:
            add.add(gh_labels.hitl_signal_label(gh_labels.SIGNAL_REJECTED))
        if change.record_confirm:
            add.add(gh_labels.hitl_signal_label(gh_labels.SIGNAL_CHECKED))
        if change.record_revoke:
            add.add(gh_labels.hitl_signal_label(gh_labels.SIGNAL_REVOKED))
        if change.record_response:
            add.add(gh_labels.hitl_signal_label(gh_labels.SIGNAL_RESOLVED))

        # Fan-in — only the contributor-side `collected-by/` label (ADR-0003).
        if change.set_collected_by:
            if current.collected_by and current.collected_by != change.set_collected_by:
                remove.add(gh_labels.collected_by_label(current.collected_by))
            add.add(gh_labels.collected_by_label(change.set_collected_by))
        if change.clear_collected_by and current.collected_by:
            remove.add(gh_labels.collected_by_label(current.collected_by))

        # Sanity: never add and remove the same label.
        overlap = add & remove
        if overlap:
            add -= overlap
            remove -= overlap

        return add, remove

    def list_issue_types(self, org: str) -> list[str] | None:
        """Read org-level Issue Types via `gh api orgs/{org}/issue-types`.

        Returns:
          - `list[str]` (names) on success — empty list means feature on
            but no types defined.
          - `None` on 403 (no permission), 404 (feature absent), or any
            backend error. The CLI treats `None` as "encode as labels".

        Side effect: none — read-only.
        """
        try:
            # --paginate follows Link headers so orgs with >30 Issue Types
            # aren't truncated (which would break ensure_issue_type's
            # idempotence check) (#27). gh merges array pages into one array.
            output = self._gh("api", f"orgs/{org}/issue-types", "--paginate", check=False)
        except BackendError:
            return None
        if not output.strip():
            return None
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return None
        # On 4xx, `gh api` typically returns a JSON error object like
        # {"message": "...", "documentation_url": "...", "status": "404"}.
        if isinstance(data, dict) and "message" in data and "status" in data:
            return None
        if not isinstance(data, list):
            return None
        names: list[str] = []
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
        return names

    def ensure_issue_type(
        self,
        org: str,
        name: str,
        description: str,
        color: str | None = None,
    ) -> bool:
        """Create the GitHub Issue Type at the org if missing.

        Idempotent via a pre-check: list existing types, skip if present.
        Otherwise calls `gh api orgs/{org}/issue-types -f name=...`.
        Raises `BackendError` if the API call fails — the caller's
        `setup-github --setup-org` path expects loud failures.
        """
        existing = self.list_issue_types(org)
        if existing is None:
            raise BackendError(
                f"Cannot list issue types for org {org!r}; feature may not "
                f"be enabled or you may lack permission."
            )
        if name in existing:
            return False
        args = [
            "api",
            f"orgs/{org}/issue-types",
            "-f",
            f"name={name}",
            "-f",
            f"description={description}",
            "-f",
            "is_enabled=true",
        ]
        if color:
            args += ["-f", f"color={color}"]
        self._gh(*args)
        return True

    def list_labels(self) -> list[str]:
        """Return every label currently defined on the repo.

        Side-effect: seeds `_known_labels` so subsequent `ensure_label` calls
        on already-existing labels become no-ops.
        """
        output = self._gh(
            "label",
            "list",
            "--repo",
            self.repo,
            "--limit",
            "1000",
            "--json",
            "name",
        )
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for label list: {exc}") from exc
        names = [entry.get("name", "") for entry in data if entry.get("name")]
        self._known_labels.update(names)
        return names

    def ensure_label(self, name: str, color: str | None = None) -> bool:
        """Create the label if it doesn't already exist. Returns True if created.

        Never overwrites the color of an existing label. The lazy operational
        path (during marker changes) uses `_ensure_label_exists` instead,
        which is more permissive about errors; `ensure_label` is for the
        one-shot setup path where the caller wants accurate created/skipped
        accounting.
        """
        if name in self._known_labels:
            return False
        if color is None:
            color = gh_labels.color_for(name)
        try:
            self._gh(
                "label",
                "create",
                name,
                "--repo",
                self.repo,
                "--color",
                color,
            )
        except BackendError as exc:
            # `gh label create` exits non-zero if the label already exists.
            # Confirm by listing labels rather than matching gh's stderr wording
            # (which is brittle); fall back to the message only if the list call
            # also fails (#27).
            try:
                already_present = name in self.list_labels()
            except BackendError:
                already_present = "already exists" in str(exc).lower()
            if already_present:
                self._known_labels.add(name)
                return False
            raise
        self._known_labels.add(name)
        return True

    def _ensure_label_exists(self, label: str) -> None:
        """Lazy ensure used during marker changes. Tolerates all errors and
        uses `--force` so the framework's color wins on existing labels.

        For one-shot provisioning, callers should use `ensure_label` instead,
        which preserves user customizations on pre-existing labels.
        """
        if label in self._known_labels:
            return
        color = gh_labels.color_for(label)
        try:
            self._gh(
                "label",
                "create",
                label,
                "--repo",
                self.repo,
                "--color",
                color,
                "--force",
                check=False,
            )
        except BackendError as exc:
            # Already exists is a benign failure mode; the next operation
            # will succeed regardless.
            logger.debug("gh label create %r: %s", label, exc)
        self._known_labels.add(label)

    def _gh(
        self,
        *args: str,
        check: bool = True,
        input_text: str | None = None,
    ) -> str:
        cmd = [self.gh_bin, *args]
        env = None
        if self.host:
            # Copy parent env so PATH / HOME / GITHUB_TOKEN / etc. survive;
            # override GH_HOST to direct gh at the configured server.
            env = os.environ.copy()
            env["GH_HOST"] = self.host
        logger.debug("Running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                input=input_text,
            )
        except FileNotFoundError as exc:
            raise BackendError(f"`{self.gh_bin}` binary not found on PATH: {exc}") from exc
        if proc.returncode != 0 and check:
            raise BackendError(
                f"gh failed ({proc.returncode}) {' '.join(args)!r}: "
                + (proc.stderr.strip() or proc.stdout.strip() or "no output")
            )
        return proc.stdout

    # ----- native GraphQL path (ADR-0005 native tier) -----

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query/mutation via `gh api graphql --input -`.

        The request body (`{"query": ..., "variables": ...}`) is piped on stdin
        so object/array variables (e.g. an Issue Field's option list) survive
        without shell-escaping. Returns the `data` object; raises `BackendError`
        on a transport failure or any GraphQL `errors`.
        """
        body = json.dumps({"query": query, "variables": variables or {}})
        out = self._gh("api", "graphql", "--input", "-", input_text=body)
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for GraphQL: {exc}") from exc
        if payload.get("errors"):
            raise BackendError(f"GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}

    def org_node_id(self, org: str) -> str:
        """Resolve an org login to its GraphQL node id (the `ownerId` for fields)."""
        data = self._graphql("query($l:String!){organization(login:$l){id}}", {"l": org})
        node_id = (data.get("organization") or {}).get("id")
        if not node_id:
            raise BackendError(f"Could not resolve org node id for {org!r}.")
        return node_id

    def list_issue_fields(self, org: str) -> list[str] | None:
        """Read org-level Issue Field names, or None if unavailable.

        Returns `list[str]` of field names on success (empty list = feature on,
        no fields). `None` on any error (feature absent / no permission) — the
        caller treats that like the label tier. `issueFields` is a GraphQL union,
        so each concrete field type is selected via an inline fragment.
        """
        query = (
            "query($l:String!,$after:String){organization(login:$l){"
            "issueFields(first:100,after:$after){"
            "pageInfo{hasNextPage endCursor} nodes{"
            "__typename "
            "... on IssueFieldText{name} "
            "... on IssueFieldSingleSelect{name} "
            "... on IssueFieldNumber{name} "
            "... on IssueFieldDate{name} "
            "... on IssueFieldMultiSelect{name}"
            "}}}}"
        )
        names: list[str] = []
        cursor: str | None = None
        # Paginate the connection so orgs with >100 fields aren't truncated —
        # a truncated list would break ensure_issue_field's idempotence check.
        while True:
            try:
                data = self._graphql(query, {"l": org, "after": cursor})
            except BackendError:
                return None
            conn = (data.get("organization") or {}).get("issueFields") or {}
            for n in conn.get("nodes") or []:
                if isinstance(n, dict) and n.get("name"):
                    names.append(n["name"])
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        return names

    def ensure_issue_field(
        self,
        org: str,
        name: str,
        data_type: str,
        *,
        description: str = "",
        options: list[str] | None = None,
    ) -> bool:
        """Create the org-level Issue Field if missing. Idempotent by name.

        Returns True if a new field was created, False if it already existed.
        `data_type` is an `IssueFieldDataType` (e.g. "SINGLE_SELECT", "TEXT").
        For single-select fields, `options` is the ordered list of option names
        (each created GRAY, priority = position). Raises `BackendError` if the
        org's fields can't be listed (feature/permission) — the provisioning
        path wants loud failures.
        """
        existing = self.list_issue_fields(org)
        if existing is None:
            raise BackendError(
                f"Cannot list issue fields for org {org!r}; feature may not be "
                f"enabled or you may lack permission."
            )
        if name in existing:
            return False
        if data_type == "SINGLE_SELECT" and not options:
            # GitHub rejects a single-select with no options; fail fast with a
            # clear error rather than making a request that always errors.
            raise BackendError(f"Cannot create single-select issue field {name!r} with no options.")
        field_input: dict = {
            "ownerId": self.org_node_id(org),
            "name": name,
            "dataType": data_type,
        }
        if description:
            field_input["description"] = description
        if data_type == "SINGLE_SELECT":
            field_input["options"] = [
                {"name": opt, "color": "GRAY", "priority": i} for i, opt in enumerate(options or [])
            ]
        mutation = (
            "mutation($input:CreateIssueFieldInput!){createIssueField(input:$input)"
            "{issueField{__typename}}}"
        )
        self._graphql(mutation, {"input": field_input})
        return True

    # ----- native tier: read/write marker values as Issue Fields -----

    def _load_field_meta(self) -> dict:
        """Lazily load (and cache) the org's Issue Field metadata for writes.

        Returns `{field_name: {"id": <node id>, "options": {opt_name: opt_id}}}`.
        Single-select writes need the option *id*, not its name, so options are
        resolved here. Paginated so >100 fields aren't truncated.
        """
        if self._field_meta is not None:
            return self._field_meta
        org = self.repo.split("/", 1)[0]
        query = (
            "query($l:String!,$after:String){organization(login:$l){"
            "issueFields(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{"
            "__typename "
            "... on IssueFieldText{id name} "
            "... on IssueFieldSingleSelect{id name options{id name}} "
            "... on IssueFieldNumber{id name} "
            "... on IssueFieldDate{id name}"
            "}}}}"
        )
        meta: dict = {}
        cursor: str | None = None
        while True:
            data = self._graphql(query, {"l": org, "after": cursor})
            conn = (data.get("organization") or {}).get("issueFields") or {}
            for n in conn.get("nodes") or []:
                name = n.get("name") if isinstance(n, dict) else None
                if not name:
                    continue
                options = {o["name"]: o["id"] for o in (n.get("options") or []) if o.get("name")}
                meta[name] = {"id": n.get("id"), "options": options}
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        self._field_meta = meta
        return meta

    def _single_select_op(self, field_name: str, value: str) -> dict:
        """Build a setIssueFieldValue op selecting `value` on a single-select field."""
        meta = self._load_field_meta()
        fm = meta.get(field_name)
        if fm is None:
            raise BackendError(
                f"Native field {field_name!r} is not provisioned on this org — "
                f"run `workflow capabilities --provision`."
            )
        option_id = fm["options"].get(value)
        if option_id is None:
            raise BackendError(
                f"Field {field_name!r} has no option {value!r} — re-provision to add it."
            )
        return {"fieldId": fm["id"], "singleSelectOptionId": option_id}

    def _clear_op(self, field_name: str) -> dict | None:
        """Build a setIssueFieldValue op clearing a field; None if not provisioned."""
        fm = self._load_field_meta().get(field_name)
        if fm is None:
            return None
        return {"fieldId": fm["id"], "delete": True}

    def _read_native(self, issue_id: str) -> tuple[str, IssueState]:
        """Read an issue's node id + framework state from its native fields.

        Resolves the GraphQL node id (needed for writes), the native Issue Type,
        and every single-select field value, mapping each back to its
        `IssueState` field.
        """
        owner, _, name = self.repo.partition("/")
        query = (
            "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){"
            "id issueType{name} "
            "issueFieldValues(first:50){nodes{__typename "
            "... on IssueFieldSingleSelectValue{name field{... on IssueFieldSingleSelect{name}}}"
            "}}}}}"
        )
        data = self._graphql(query, {"o": owner, "r": name, "n": int(issue_id)})
        issue = ((data.get("repository") or {}).get("issue")) or {}
        node_id = issue.get("id")
        if not node_id:
            raise BackendError(f"Could not resolve issue #{issue_id} on {self.repo}.")
        values: dict[str, str] = {}
        for node in (issue.get("issueFieldValues") or {}).get("nodes") or []:
            fld = (node.get("field") or {}).get("name")
            if fld and node.get("name") is not None:
                values[fld] = node["name"]
        native_type = (issue.get("issueType") or {}).get("name")
        claim = values.get(FIELD_HITL_CLAIM)
        state = IssueState(
            issue_id=str(issue_id),
            state=values.get(FIELD_STATE),
            agent_claim=values.get(FIELD_AGENT),
            last_state=values.get(FIELD_LAST_STATE),
            issue_type=None,
            native_issue_type=native_type,
            awaiting_gate=values.get(FIELD_HITL_BLOCKED),
            audit_pending=values.get(FIELD_HITL_AUDIT),
            reviewing=claim == gh_labels.CLAIM_REVIEWING,
            auditing=claim == gh_labels.CLAIM_AUDITING,
            advising=claim == gh_labels.CLAIM_ADVISING,
            awaiting_input=FIELD_HITL_INPUT in values,
            human_input=values.get(FIELD_HITL_INPUT),
        )
        return node_id, state

    def _marker_change_to_field_ops(self, change: MarkerChange) -> list[dict]:
        """Translate an abstract MarkerChange into setIssueFieldValue ops.

        Mirrors `_marker_change_to_labels` for the native tier. Clears use the
        field's `delete` op. `collected_by` is intentionally not handled here —
        it rides the #74 relationship path.
        """
        ops: list[dict] = []

        def set_ss(field_name: str, value: str) -> None:
            ops.append(self._single_select_op(field_name, value))

        def clear(field_name: str) -> None:
            op = self._clear_op(field_name)
            if op is not None:
                ops.append(op)

        if change.set_state is not None:
            set_ss(FIELD_STATE, change.set_state)
        if change.set_agent_claim:
            set_ss(FIELD_AGENT, change.set_agent_claim)
        elif change.clear_agent_claim:
            clear(FIELD_AGENT)
        if change.set_last_state:
            set_ss(FIELD_LAST_STATE, change.set_last_state)
        elif change.clear_last_state:
            clear(FIELD_LAST_STATE)
        if change.set_awaiting_gate:
            set_ss(FIELD_HITL_BLOCKED, change.set_awaiting_gate)
        elif change.clear_awaiting_gate:
            clear(FIELD_HITL_BLOCKED)
        if change.set_audit_pending:
            set_ss(FIELD_HITL_AUDIT, change.set_audit_pending)
        elif change.clear_audit_pending:
            clear(FIELD_HITL_AUDIT)

        # The three claim singletons collapse to one HITL Claim field.
        for value, flag in (
            (gh_labels.CLAIM_REVIEWING, change.set_reviewing),
            (gh_labels.CLAIM_AUDITING, change.set_auditing),
            (gh_labels.CLAIM_ADVISING, change.set_advising),
        ):
            if flag is True:
                set_ss(FIELD_HITL_CLAIM, value)
            elif flag is False:
                clear(FIELD_HITL_CLAIM)

        if change.set_human_input:
            set_ss(FIELD_HITL_INPUT, change.set_human_input)
        elif change.clear_human_input or change.set_awaiting_input is False:
            clear(FIELD_HITL_INPUT)

        for value, flag in (
            (gh_labels.SIGNAL_APPROVED, change.record_approval),
            (gh_labels.SIGNAL_REJECTED, change.record_rejection),
            (gh_labels.SIGNAL_CHECKED, change.record_confirm),
            (gh_labels.SIGNAL_REVOKED, change.record_revoke),
        ):
            if flag:
                set_ss(FIELD_HITL_SIGNAL, value)
        if change.record_response:
            set_ss(FIELD_HITL_SIGNAL, gh_labels.SIGNAL_RESOLVED)

        return ops

    def _apply_marker_change_native(
        self, issue_id: str, change: MarkerChange, audit_comment: str | None
    ) -> None:
        """Native-tier marker change: set Issue Field values via one
        `setIssueFieldValue`, then the same best-effort follow-ups as the label
        path (assignment, close, pr-ready, audit comment)."""
        node_id, _current = self._read_native(issue_id)
        ops = self._marker_change_to_field_ops(change)
        if ops:
            self._graphql(
                "mutation($input:SetIssueFieldValueInput!)"
                "{setIssueFieldValue(input:$input){clientMutationId}}",
                {"input": {"issueId": node_id, "issueFields": ops}},
            )
        try:
            if change.set_agent_claim:
                role_handle = self.resolve_role(change.set_agent_claim)
                if role_handle:
                    self.assign(issue_id, role_handle)
            if change.clear_agent_claim:
                self.unassign(issue_id)
            if change.close_issue:
                self.close_issue(issue_id, reason=change.close_reason)
            if change.set_pr_ready:
                self.mark_pr_ready(issue_id)
        except BackendError as exc:
            raise BackendError(
                f"Issue #{issue_id}: field values were updated but a follow-up step "
                f"failed ({exc}). Re-run the same operation (field writes are idempotent) "
                f"or repair the assignment/close state by hand."
            ) from exc
        if audit_comment:
            try:
                self.post_comment(issue_id, audit_comment)
            except BackendError as exc:
                logger.warning(
                    "Issue #%s: state applied but audit comment failed to post: %s",
                    issue_id,
                    exc,
                )

    def _create_issue_native(
        self,
        title: str,
        body: str,
        state: str,
        extra_labels: list[str] | None,
        issue_type: str | None,
    ) -> str:
        """Native create: open the issue with its native Issue Type, then set
        the Workflow State (and any claim) field value.

        Unlike the label tier this is not a single atomic call — `gh issue
        create --type` sets the type atomically, but the Workflow State value is
        a follow-up `setIssueFieldValue`, so there is a one-call window where the
        issue has its type but not yet its state value. `child-of` extra-labels
        are ignored here (they ride the #74 sub-issue path)."""
        new_id = self._create_bare_issue(title, body, issue_type)
        node_id, _ = self._read_native(new_id)
        ops = [self._single_select_op(FIELD_STATE, state)]
        for raw in extra_labels or []:
            parsed = gh_labels.parse_label(raw)
            if parsed is not None and parsed.kind == gh_labels.CLAIM and parsed.value:
                ops.append(self._single_select_op(FIELD_AGENT, parsed.value))
        self._graphql(
            "mutation($input:SetIssueFieldValueInput!)"
            "{setIssueFieldValue(input:$input){clientMutationId}}",
            {"input": {"issueId": node_id, "issueFields": ops}},
        )
        return new_id

    def _create_bare_issue(self, title: str, body: str, issue_type: str | None) -> str:
        """`gh issue create` with no framework labels (native sets fields after)."""
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", delete=False
        ) as tmp:
            tmp.write(body or "")
            tmp_path = tmp.name
        try:
            args = [
                "issue",
                "create",
                "--repo",
                self.repo,
                f"--title={title}",
                "--body-file",
                tmp_path,
            ]
            if issue_type:
                args += [f"--type={issue_type}"]
            output = self._gh(*args)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return self._parse_created_number(output, "/issues/")

    def _parse_created_number(self, output: str, marker: str) -> str:
        """Parse the issue/PR number from the URL `gh ... create` prints last."""
        url = ""
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped:
                url = stripped
                break
        if not url:
            raise BackendError(f"`gh create` returned no output: {output!r}")
        if marker not in url:
            raise BackendError(
                f"`gh create` returned unexpected output (no {marker} in URL): {url!r}"
            )
        return url.rsplit("/", 1)[-1]

    def _list_issues_native(self, filters: IssueFilters) -> list[IssueState]:
        """Native list: translate filters to `field."<name>":<value>` /
        `type:` search qualifiers and resolve each match's fields.

        Relationship-cohort filters (`child_of` / `collected_by`) are not yet
        supported here — they ride the #74 sub-issue / Collected-By path.
        """
        if filters.child_of or filters.collected_by:
            raise BackendError(
                "Native cohort queries (child_of / collected_by) are not yet implemented (#74)."
            )
        quals: list[str] = []
        if filters.state:
            quals.append(f'field."{FIELD_STATE.lower()}":"{filters.state}"')
        if filters.claim_role:
            quals.append(f'field."{FIELD_AGENT.lower()}":"{filters.claim_role}"')
        if filters.awaiting_gate and filters.awaiting_gate != "*":
            quals.append(f'field."{FIELD_HITL_BLOCKED.lower()}":"{filters.awaiting_gate}"')
        if filters.audit_pending and filters.audit_pending != "*":
            quals.append(f'field."{FIELD_HITL_AUDIT.lower()}":"{filters.audit_pending}"')
        search = " ".join(quals)

        numbers: list[str] = []
        for kind in ("issue", "pr"):
            args = [
                kind,
                "list",
                "--repo",
                self.repo,
                "--state",
                "all",
                "--limit",
                str(filters.limit),
            ]
            if search:
                args += ["--search", search]
            args += ["--json", "number"]
            try:
                entries = json.loads(self._gh(*args))
            except json.JSONDecodeError as exc:
                raise BackendError(f"gh returned non-JSON for native {kind} list: {exc}") from exc
            numbers += [str(e["number"]) for e in entries if e.get("number") is not None]

        results: list[IssueState] = []
        seen: set[str] = set()
        for num in numbers:
            if num in seen:
                continue
            seen.add(num)
            _node, state = self._read_native(num)
            # Post-filter the markers that have no single search qualifier.
            if filters.awaiting_gate == "*" and not state.awaiting_gate:
                continue
            if filters.audit_pending == "*" and not state.audit_pending:
                continue
            if filters.awaiting_input is True and not state.awaiting_input:
                continue
            if filters.awaiting_input is False and state.awaiting_input:
                continue
            results.append(state)
        return results
