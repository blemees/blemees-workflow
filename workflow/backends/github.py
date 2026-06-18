"""GitHub backend — implements the `TrackerBackend` protocol via the `gh` CLI.

Per `backends/github-encoding.md`:

- State → `state:<name>` label, exactly one applied at any moment.
- Agent claim → `wip:<role>` label, at most one.
- HITL markers → `hitl:*` labels in the queue / claim / signal subnamespaces.
- Audit records → issue/PR comments.

The label swap is the one atomic step: every add/remove rides a single `gh
issue edit` invocation (GraphQL `replaceLabels`), so the state marker never
tears. The surrounding follow-ups in `apply_marker_change` — assignment, close,
pr-ready, audit comment — are a best-effort sequence, NOT part of that
transaction. The label swap goes first (it carries the state change); a
follow-up failure raises a partial-apply error with a repair hint rather than
leaving a silent inconsistency. `gh` has no multi-resource transaction, so a
fully atomic apply would need a single GraphQL mutation (future work).

The backend creates missing labels lazily with namespace-appropriate colors.
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

from workflow.backends.base import IssueFilters, IssueState, MarkerChange
from workflow.errors import BackendError

logger = logging.getLogger(__name__)


# Label color hints (per github-encoding.md § 10).
_LABEL_COLORS = {
    "state": "1f6feb",  # blue
    "wip": "fbca04",  # yellow
    "last-state": "fef2c0",  # pale yellow — adjacent to wip
    "hitl": "8957e5",  # purple
}


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
    # Cached per-session: labels we've already ensured exist on this repo.
    _known_labels: set[str] = field(default_factory=set)

    # ----- backend protocol -----

    def list_issues(self, filters: IssueFilters) -> list[IssueState]:
        """List issues AND pull requests by translating filters to `gh` flags.

        `gh issue list` excludes pull requests and `gh pr list` excludes issues,
        so this queries both and merges the results, de-duplicated by id. Both
        are queried with `--state all` so closed issues and closed/merged PRs
        are visible — cohort queries (`child-of:` / `collected-by:`) and
        closing-state searches depend on it (ADR-0003).

        The filter translation (applied identically to issues and PRs):

        - `filters.state` → `--label state:<name>`
        - `filters.claim_role` → `--label wip:<role>`
        - `filters.awaiting_gate` ("*" → match any awaiting; specific name → that label)
        - `filters.audit_pending` ("*" → match any audit-pending; specific → that label)
        - `filters.awaiting_input` (True → `--label hitl:awaiting-input`; False → exclude)
        - `filters.child_of` → `--label child-of:<id>` (cohort: a parent's children)
        - `filters.collected_by` → `--label collected-by:<id>` (cohort: a collector's contributors)
        - `filters.limit` → `--limit N` (applied per entity kind)

        For wildcard awaiting / audit filters that `gh` can't express with a
        single label match, the backend filters in Python after fetching.
        """
        wildcard_awaiting = filters.awaiting_gate == "*"
        wildcard_audit = filters.audit_pending == "*"

        label_filters: list[str] = []
        if filters.state:
            label_filters.append(f"state:{filters.state}")
        if filters.claim_role:
            label_filters.append(f"wip:{filters.claim_role}")
        if filters.awaiting_gate and not wildcard_awaiting:
            label_filters.append(f"hitl:awaiting-{filters.awaiting_gate}")
        if filters.audit_pending and not wildcard_audit:
            label_filters.append(f"hitl:audit-{filters.audit_pending}")
        if filters.awaiting_input is True:
            label_filters.append("hitl:awaiting-input")
        if filters.child_of:
            label_filters.append(f"child-of:{filters.child_of}")
        if filters.collected_by:
            label_filters.append(f"collected-by:{filters.collected_by}")

        entries = self._list_entities("issue", label_filters, filters.limit)
        entries += self._list_entities("pr", label_filters, filters.limit)

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

            # Wildcard awaiting / audit filters need post-filtering.
            if wildcard_awaiting and not state.awaiting_gate:
                continue
            if wildcard_audit and not state.audit_pending:
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

        Uses `gh issue create --title T --body-file BODY --label state:X`,
        adding every label in `extra_labels` (e.g., `wip:<role>` for an
        immediate claim) to the same `--label` flag. Existing labels on
        the repo are required; missing ones are created lazily via
        `ensure_label` before the issue is created so `gh` doesn't error
        on an unknown label.

        When `issue_type` is set, `gh issue create --type <issue_type>` is
        added — this is GitHub's first-class Issue Type field. The string
        must be the exact GitHub Issue Type name (e.g., "Bug", "Feature").

        The issue URL printed by `gh` is parsed back into the issue number
        and returned as a string.
        """
        labels = [f"state:{state}"]
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

        The framework's `state:<name>` label is attached atomically with
        creation (gh `pr create` accepts `--label`). Labels are ensured to
        exist on the repo before the call so gh doesn't error on missing
        names. Returns the new PR's number parsed from gh's output URL.
        """
        labels = [f"state:{state}"]
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
        result = self._gh(
            "issue",
            "view",
            str(issue_id),
            "--repo",
            self.repo,
            "--json",
            "number,labels,assignees,state,comments",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {issue_id}: {exc}") from exc

        labels = [lbl.get("name", "") for lbl in (data.get("labels") or [])]
        return self._labels_to_state(issue_id, labels)

    def apply_marker_change(
        self,
        issue_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
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
        # caller untranslated. The wip:<role> label is always applied; only the
        # GitHub assignee is conditionally set.
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
        # Claims are framework-managed via `wip:<role>` labels, but GitHub
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

    def _labels_to_state(self, issue_id: str, labels: list[str]) -> IssueState:
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

        for raw in labels:
            label = raw.strip()
            if label.startswith("state:"):
                state = label[len("state:") :]
                continue
            # `last-state:` is checked before `wip:` because both share a prefix.
            if label.startswith("last-state:"):
                last_state = label[len("last-state:") :]
                continue
            if label.startswith("wip:"):
                agent_claim = label[len("wip:") :]
                continue
            if label.startswith("type:"):
                issue_type = label[len("type:") :]
                continue
            if label.startswith("collected-by:"):
                collected_by = label[len("collected-by:") :]
                continue
            # `collects:` (collector-side contributor registry) is intentionally
            # not parsed — the cohort is a `collected-by:` query now (ADR-0003).
            if label.startswith("child-of:"):
                child_of = label[len("child-of:") :]
                continue
            # `subprocess:` (parent-side child registry) is intentionally not
            # parsed — the cohort is a `child-of:` query now (ADR-0003).
            if not label.startswith("hitl:"):
                continue
            suffix = label[len("hitl:") :]
            if suffix == "reviewing":
                reviewing = True
            elif suffix == "auditing":
                auditing = True
            elif suffix == "advising":
                advising = True
            elif suffix == "awaiting-input":
                awaiting_input = True
            elif suffix == "resolved":
                pass  # signal marker only
            elif suffix.startswith("topic-"):
                human_input = suffix[len("topic-") :]
            elif suffix.startswith("awaiting-"):
                awaiting_gate = suffix[len("awaiting-") :]
            elif suffix.startswith("audit-"):
                audit_pending = suffix[len("audit-") :]
            # signal markers approved-/rejected-/checked-/revoked-* are
            # transient and don't translate to state.

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
        )

    def _marker_change_to_labels(
        self,
        current: IssueState,
        change: MarkerChange,
    ) -> tuple[set[str], set[str]]:
        add: set[str] = set()
        remove: set[str] = set()

        # State transitions: swap state: labels.
        if change.set_state is not None:
            if current.state is not None and current.state != change.set_state:
                remove.add(f"state:{current.state}")
            add.add(f"state:{change.set_state}")

        # Agent claim.
        if change.clear_agent_claim and current.agent_claim:
            remove.add(f"wip:{current.agent_claim}")
        if change.set_agent_claim:
            if current.agent_claim and current.agent_claim != change.set_agent_claim:
                remove.add(f"wip:{current.agent_claim}")
            add.add(f"wip:{change.set_agent_claim}")

        # Origin marker (the resting state we came from on claim).
        if change.clear_last_state and current.last_state:
            remove.add(f"last-state:{current.last_state}")
        if change.set_last_state:
            if current.last_state and current.last_state != change.set_last_state:
                remove.add(f"last-state:{current.last_state}")
            add.add(f"last-state:{change.set_last_state}")

        # Awaiting gate.
        if change.clear_awaiting_gate and current.awaiting_gate:
            remove.add(f"hitl:awaiting-{current.awaiting_gate}")
        if change.set_awaiting_gate:
            if current.awaiting_gate and current.awaiting_gate != change.set_awaiting_gate:
                remove.add(f"hitl:awaiting-{current.awaiting_gate}")
            add.add(f"hitl:awaiting-{change.set_awaiting_gate}")

        # Audit pending.
        if change.clear_audit_pending and current.audit_pending:
            remove.add(f"hitl:audit-{current.audit_pending}")
        if change.set_audit_pending:
            if current.audit_pending and current.audit_pending != change.set_audit_pending:
                remove.add(f"hitl:audit-{current.audit_pending}")
            add.add(f"hitl:audit-{change.set_audit_pending}")

        # Singleton claim markers.
        if change.set_reviewing is True:
            add.add("hitl:reviewing")
        elif change.set_reviewing is False and current.reviewing:
            remove.add("hitl:reviewing")
        if change.set_auditing is True:
            add.add("hitl:auditing")
        elif change.set_auditing is False and current.auditing:
            remove.add("hitl:auditing")
        if change.set_advising is True:
            add.add("hitl:advising")
        elif change.set_advising is False and current.advising:
            remove.add("hitl:advising")

        # Recognized markers.
        if change.set_awaiting_input is True:
            add.add("hitl:awaiting-input")
        elif change.set_awaiting_input is False and current.awaiting_input:
            remove.add("hitl:awaiting-input")
        # Companion topic label — `hitl:topic-<name>`, set alongside
        # `hitl:awaiting-input` so humans can filter the queue by topic.
        if change.set_human_input:
            add.add(f"hitl:topic-{change.set_human_input}")
        if change.clear_human_input and current.human_input:
            remove.add(f"hitl:topic-{current.human_input}")

        # Outcome / signal markers (transient audit-trace labels).
        if change.record_approval:
            add.add(f"hitl:approved-{change.record_approval}")
        if change.record_rejection:
            add.add(f"hitl:rejected-{change.record_rejection}")
        if change.record_confirm:
            add.add(f"hitl:checked-{change.record_confirm}")
        if change.record_revoke:
            add.add(f"hitl:revoked-{change.record_revoke}")
        if change.record_response:
            add.add("hitl:resolved")

        # Fan-in — only the contributor-side `collected-by:` label (ADR-0003).
        if change.set_collected_by:
            if current.collected_by and current.collected_by != change.set_collected_by:
                remove.add(f"collected-by:{current.collected_by}")
            add.add(f"collected-by:{change.set_collected_by}")
        if change.clear_collected_by and current.collected_by:
            remove.add(f"collected-by:{current.collected_by}")

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
            prefix = name.split(":", 1)[0] if ":" in name else ""
            color = _LABEL_COLORS.get(prefix, "ededed")
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
        prefix = label.split(":", 1)[0] if ":" in label else ""
        color = _LABEL_COLORS.get(prefix, "ededed")
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
            )
        except FileNotFoundError as exc:
            raise BackendError(f"`{self.gh_bin}` binary not found on PATH: {exc}") from exc
        if proc.returncode != 0 and check:
            raise BackendError(
                f"gh failed ({proc.returncode}) {' '.join(args)!r}: "
                + (proc.stderr.strip() or proc.stdout.strip() or "no output")
            )
        return proc.stdout
