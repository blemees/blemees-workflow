"""GitHub backend — implements the `WorkflowBackend` protocol via the `gh` CLI.

Per `backends/github-encoding.md`:

- State → `state:<name>` label, exactly one applied at any moment.
- Agent claim → `wip:<role>` label, at most one.
- HITL markers → `hitl:*` labels in the queue / claim / signal subnamespaces.
- Audit records → issue/PR comments.

Atomicity is achieved by passing every label change as a single `gh issue
edit` invocation; `gh` translates this to GraphQL's `replaceLabels` mutation.

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

from workflow.backends.base import MarkerChange, WorkItemFilters, WorkItemState
from workflow.errors import BackendError

logger = logging.getLogger(__name__)


# Label color hints (per github-encoding.md § 10).
_LABEL_COLORS = {
    "state": "1f6feb",  # blue
    "wip": "fbca04",  # yellow
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

    def list_work_items(self, filters: WorkItemFilters) -> list[WorkItemState]:
        """List work items by translating filters to `gh issue list` flags.

        The translation:

        - `filters.state` → `--label state:<name>`
        - `filters.claim_role` → `--label wip:<role>`
        - `filters.awaiting_gate` ("*" → match any awaiting; specific name → that label)
        - `filters.audit_pending` ("*" → match any audit-pending; specific → that label)
        - `filters.awaiting_input` (True → `--label hitl:awaiting-input`; False → exclude)
        - `filters.limit` → `--limit N`

        Returns `WorkItemState` objects derived from each result's labels. For
        wildcard awaiting / audit filters that `gh` can't express with a single
        label match, the backend filters in Python after fetching.
        """
        args: list[str] = ["issue", "list", "--repo", self.repo, "--limit", str(filters.limit)]

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

        for label in label_filters:
            args += ["--label", label]

        args += ["--json", "number,labels,title,state"]

        try:
            output = self._gh(*args)
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue list: {exc}") from exc

        results: list[WorkItemState] = []
        for entry in data:
            number = str(entry.get("number", ""))
            labels = [lbl.get("name", "") for lbl in (entry.get("labels") or [])]
            state = self._labels_to_state(number, labels)

            # Wildcard awaiting / audit filters need post-filtering.
            if wildcard_awaiting and not state.awaiting_gate:
                continue
            if wildcard_audit and not state.audit_pending:
                continue
            if filters.awaiting_input is False and state.awaiting_input:
                continue

            # Title isn't on WorkItemState; we stash it in `extras` so the CLI
            # can display it without re-fetching. `extras` is a dict (mutable
            # even on the frozen dataclass).
            if entry.get("title"):
                state.extras["title"] = entry["title"]
            results.append(state)

        return results

    def create_work_item(
        self,
        title: str,
        body: str,
        state: str,
        extra_labels: list[str] | None = None,
    ) -> str:
        """Create a new GitHub issue with the framework's state marker.

        Uses `gh issue create --title T --body-file BODY --label state:X`,
        adding every label in `extra_labels` (e.g., `wip:<role>` for an
        immediate claim) to the same `--label` flag. Existing labels on
        the repo are required; missing ones are created lazily via
        `ensure_label` before the issue is created so `gh` doesn't error
        on an unknown label.

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
            output = self._gh(
                "issue",
                "create",
                "--repo",
                self.repo,
                "--title",
                title,
                "--body-file",
                tmp_path,
                "--label",
                ",".join(labels),
            )
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

    def read_work_item(self, work_item_id: str) -> WorkItemState:
        result = self._gh(
            "issue",
            "view",
            str(work_item_id),
            "--repo",
            self.repo,
            "--json",
            "number,labels,assignees,state,comments",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {work_item_id}: {exc}") from exc

        labels = [lbl.get("name", "") for lbl in (data.get("labels") or [])]
        return self._labels_to_state(work_item_id, labels)

    def apply_marker_change(
        self,
        work_item_id: str,
        change: MarkerChange,
        audit_comment: str | None = None,
    ) -> None:
        # Compute the add/remove label deltas from the change.
        current = self.read_work_item(work_item_id)
        add, remove = self._marker_change_to_labels(current, change)

        # Ensure any labels we're about to add exist on the repo.
        for label in add:
            self._ensure_label_exists(label)

        # Post the audit comment FIRST per the encoding doc's ordering
        # guarantee (§ 3): validate guards → emit audit comment → swap labels.
        if audit_comment:
            self.post_comment(work_item_id, audit_comment)

        # Apply add + remove in a single `gh` invocation — per `gh`'s docs and
        # the encoding doc, this maps to GraphQL's `replaceLabels` mutation.
        if add or remove:
            args: list[str] = [
                "issue",
                "edit",
                str(work_item_id),
                "--repo",
                self.repo,
            ]
            if add:
                args += ["--add-label", ",".join(sorted(add))]
            if remove:
                args += ["--remove-label", ",".join(sorted(remove))]
            self._gh(*args)

        # Handle assignment for claim / release.
        if change.set_agent_claim:
            role_handle = self.resolve_role(change.set_agent_claim)
            if role_handle:
                self.assign(work_item_id, role_handle)
        if change.clear_agent_claim:
            self.unassign(work_item_id)

    def post_comment(self, work_item_id: str, body: str) -> None:
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
                str(work_item_id),
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

    def read_comments(self, work_item_id: str, since: str | None = None) -> list[dict]:
        result = self._gh(
            "issue",
            "view",
            str(work_item_id),
            "--repo",
            self.repo,
            "--json",
            "comments",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {work_item_id}: {exc}") from exc
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

    def assignee(self, work_item_id: str) -> str | None:
        result = self._gh(
            "issue",
            "view",
            str(work_item_id),
            "--repo",
            self.repo,
            "--json",
            "assignees",
        )
        try:
            data = json.loads(result)
        except json.JSONDecodeError as exc:
            raise BackendError(f"gh returned non-JSON for issue {work_item_id}: {exc}") from exc
        assignees = data.get("assignees") or []
        if not assignees:
            return None
        return assignees[0].get("login")

    def assign(self, work_item_id: str, handle: str) -> None:
        self._gh(
            "issue",
            "edit",
            str(work_item_id),
            "--repo",
            self.repo,
            "--add-assignee",
            handle,
        )

    def unassign(self, work_item_id: str) -> None:
        current = self.assignee(work_item_id)
        if current:
            self._gh(
                "issue",
                "edit",
                str(work_item_id),
                "--repo",
                self.repo,
                "--remove-assignee",
                current,
            )

    # ----- internals -----

    def _labels_to_state(self, work_item_id: str, labels: list[str]) -> WorkItemState:
        state: str | None = None
        agent_claim: str | None = None
        awaiting_gate: str | None = None
        audit_pending: str | None = None
        reviewing = False
        auditing = False
        advising = False
        awaiting_input = False

        for raw in labels:
            label = raw.strip()
            if label.startswith("state:"):
                state = label[len("state:") :]
                continue
            if label.startswith("wip:"):
                agent_claim = label[len("wip:") :]
                continue
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
            elif suffix.startswith("awaiting-"):
                awaiting_gate = suffix[len("awaiting-") :]
            elif suffix.startswith("audit-"):
                audit_pending = suffix[len("audit-") :]
            # signal markers approved-/rejected-/checked-/revoked-* are
            # transient and don't translate to state.

        return WorkItemState(
            work_item_id=str(work_item_id),
            state=state,
            agent_claim=agent_claim,
            awaiting_gate=awaiting_gate,
            reviewing=reviewing,
            audit_pending=audit_pending,
            auditing=auditing,
            awaiting_input=awaiting_input,
            advising=advising,
        )

    def _marker_change_to_labels(
        self,
        current: WorkItemState,
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

        # Outcome / signal markers (transient audit-trace labels).
        if change.record_approval:
            add.add(f"hitl:approved-{change.record_approval}")
        if change.record_rejection:
            add.add(f"hitl:rejected-{change.record_rejection}")
        if change.record_check:
            add.add(f"hitl:checked-{change.record_check}")
        if change.record_revoke:
            add.add(f"hitl:revoked-{change.record_revoke}")
        if change.record_resolution:
            add.add("hitl:resolved")

        # Sanity: never add and remove the same label.
        overlap = add & remove
        if overlap:
            add -= overlap
            remove -= overlap

        return add, remove

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
            # `gh label create` exits non-zero with "already exists" if the
            # label is present. Treat that as benign and cache it.
            if "already exists" in str(exc).lower():
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
