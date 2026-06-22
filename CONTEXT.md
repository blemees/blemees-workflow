# Domain Glossary

The language this codebase uses. Update inline when terms get resolved or refined; never let prose drift away from this glossary.

## Workflow

The whole collection: one or more processes plus the shared roles, human-gate catalogs, and trust grants that they reference together. Lives in one directory on disk (the `--workflow-dir`).

The `Workflow` Python class is the registry of processes — it discovers and loads each process on demand.

## Process

A single named business activity that moves work through a state machine. Examples: refinement, inner-loop, release, incident-response. Each process is defined by:

- A state machine (states + transitions + HITL gate markers)
- A human-gate catalog (the gates' levels, reversibility, triggering roles)
- Trust grants relaxing those gates per team (optional)
- Roles referenced from the shared role directory

The `Process` Python class bundles those static parts plus the runtime context (backend, agent identity) needed to operate on issues flowing through the process.

A workflow contains many processes. A process belongs to exactly one workflow.

## State machine

The state graph of one process: states, transitions between them, HITL markers on gated transitions. Lives in `<name>-states.json`. Rendered to `<name>-states.mermaid` by the emitter for human-readable visualization.

The `StateMachine` Python class is the parsed, validated representation. The `.mermaid` rendering is generated only — never authored.

## State

A node in a state machine. Every state is exactly one of:

- **Resting** — work item parked; no agent actively owns it. Pollable queue.
- **Working** — claimed by an agent who is actively progressing the work.

Per `docs/state-machine-principles.md` §1. Two ownership classes. **Closing** is not a class — it's a `closes: {taxonomy, reason}` annotation on a resting state that makes it a sink closing the issue on entry (ADR-0002).

## Transition

A directed edge from one state to another, with a typed label. Three types (`TransitionType`):

- **Claim** (`claim`) — resting → working (e.g., `developer claims ready_for_dev`).
- **Advance** (`advance`) — working → resting or a closing state, agent-driven (e.g., `developer opens PR`); landing in a closing state closes the issue.
- **Event** (`event`) — a system/time event fired by automation, not an agent (e.g., `PR merged (external)`).

There is no `cross_process` transition type — the parser rejects it. Cross-process movement is modeled with `handoff: true` states shared between processes (see "Cross-process handoff" below).

Per `docs/state-machine-principles.md` §2.

## Human gate

A gated transition that requires a human signal before firing. Authored by setting a `human_gate` field on a transition — its value names a row in the per-process human-gate catalog (`<process>-human-gates.json`). Presence of `human_gate` IS the HITL marker; there is no separate `hitl` boolean. Each catalog row declares:

- Default level (`block` for pre-action gating, `audit` for post-action review)
- Allowed levels (subset of `block`, `audit`)
- Gate type (`authority` / `knowledge` / `judgment` / `reality`)
- Path to the agent-prepares template
- Optional rationale

Structural attributes — source state, destinations, triggering roles, reversibility class — are not authored on the catalog row; they are derived from the paired state machine via `StateMachine.gate_*` helpers.

Per `docs/state-machine-principles.md` §11 and the upstream hitl-principles.md.

## Gate

Shorthand for "human gate". The `gate_name` is the key used to look up a gate in the catalog, in transition declarations (`human_gate: <name>`), and in trust grants (`control_point: <name>`).

## Role

A framework-defined actor identity (e.g., `product-manager`, `developer`, `peer-reviewer`). Roles are declared once in the shared `roles.json` and referenced from working states' `roles` lists. Roles map to backend handles (GitHub usernames, etc.) per team config — that mapping is outside the workflow tool's scope. A gate's triggering role is derived from the `roles` of its source state, not authored on the catalog row.

## Agent

A specific instance of a role acting on the system. An agent has an identity (the `agent-role` config field in its agent home) and operates against one workflow. Many agents may share a workflow; each has its own agent home.

The framework is actor-agnostic — agents may be humans, automated bots, or LLMs. The role is what matters.

## Issue

The unit of work flowing through a process. Called *issue* across the framework regardless of backend, since GitHub, GitLab, Linear, and Jira all use that term. GitHub PRs are treated as issue-like for framework purposes.

In code: `IssueState`, `IssueFilters`, `create_issue`, `read_issue`, `list_issues`, `issue_id`.

## Tracker (backend)

The system that stores issue state and provides query/mutation primitives — GitHub, GitLab, Linear, Jira. The `TrackerBackend` Python protocol defines the operations every tracker implementation must support. The current implementation is `GitHubBackend`.

## Trust grant

A per-team relaxation of a human gate's default level. The catalog defines a gate's default; a team that has earned discretion can author a trust grant flipping the level (e.g., block → audit) with mandatory evidence, expiry, and revocation procedure.

Lives in `trust-grants/<process>/<gate>.json`. Per the upstream trust-grant-schema.md.

## Next actions

Every single-item command (`view-issue`, `create-issue`, `claim-issue`, `release-issue`, `advance-issue`, `request-input`, `post-comment`) ends its human-readable output with a `Next actions:` block describing what the agent could do next from the current state. The data behind it lives in `workflow.core.inspector.available_transitions` — a read-only function that walks the state machine's outgoing transitions and enriches each with the relevant human-gate row and any active trust grant. The block is informational on read-only commands and best-effort (silent on failure) so commands that don't strictly need workflow context still work outside a known workflow.

For each gated transition the block surfaces: gate name, default vs effective level (so trust-grant relaxations are visible), triggering role, destination class + reversibility + closure taxonomy, and the path to the `agent_prepares` template the agent should attach via `--packet-from`. For resting states, the block emits a `claim-issue` suggestion (auto-resolved when unambiguous). For working states with a `last-state` marker, it also surfaces `release-issue` and where it returns to.

This means the agent does not have to consult the process documentation to figure out the next command — every operation that surfaces an issue's state surfaces its options too.

## Generated documentation

`workflow generate-docs` regenerates the agent/human-readable layer of the workflow directory: state-machine diagrams in mermaid, per-process reference docs in markdown, plus shared `roles.md`, `issue-types.md`, and a top-level `README.md` index. All emitted artifacts live alongside the canonical JSON sources.

Per-process markdown (`<name>.md`) contains everything an agent needs to operate on that process without chasing links: issue types accepted, embedded state diagram, states table, transitions table (with HITL level after trust-grant resolution), human-gate details, cross-process handoff list, and any active trust grants. The emitter is read-only and deterministic — two runs produce byte-identical output, so pre-commit hooks can verify the checked-in docs are in sync with the JSON.

## Issue type

Issue types are declared per state via `issue_types: [...]`. The field is **required on working states and on non-closing resting states**, and **forbidden on closing states** (mutually exclusive with `closes`). The process's overall accepted set is the union of every state's `issue_types` — working *and* resting (`StateMachine.accepted_issue_types`); this matters for a process that carries a type by handoff/collect into a resting queue without ever claiming it into a working state (e.g. `release` holding dev tickets in `staged`).

The type ids resolve against a shared `issue-types.json` (alongside `roles.json`) defining each type's display name, description, and optional backend-specific mappings (`github_issue_type`, `github_issue_type_color`).

`workflow create-issue` requires `--type` when the process accepts multiple types; auto-defaults when only one. Issue type is set at creation and **immutable** — if a type needs to change, that's a new issue, not a retype. The validator checks every type id referenced by a working state exists in `issue-types.json`; missing ids are an ERROR, missing directory is a WARNING. The planner checks at claim time that the issue's type is in the destination working state's set — a typed ticket can't be claimed into a working state that doesn't accept its type.

This is how inner-loop's `implementing` (accepts bug/feature/chore), `implementing_experiment` (accepts experiment), `implementing_spike` (accepts spike), and `implementing_hotfix` (accepts hotfix) fan a single ticket into the right working flow.

### `pr` — the pre-defined pull-request type

`pr` is a built-in type that maps to GitHub pull-request entities rather than issues. The `IssueType` entry carries `"github_entity": "pull_request"` (default for every other type is `"issue"`); `github_issue_type` is forbidden on a `pull_request` entry because PRs are not a native GitHub Issue Type — the type is implicit in the entity kind.

The PR process declares `"issue_types": ["pr"]`. PRs are created via the same `workflow create-issue` command as issues, with **extra required flags** that drive a framework-applied message format:

- `--head BRANCH` (required) — source branch.
- `--base BRANCH` (optional) — target branch; backend defaults to the repo's default branch when omitted.
- `--refs N` (required, repeatable) — parent ticket id(s) the PR addresses. Renders as a `Refs #N, #M, ...` footer in the body.
- `--body` is required for PRs (PRs need a description; the framework wraps it).

The backend dispatches to `create_pull_request` (which shells out to `gh pr create`) rather than `create_issue`. When the initial state is `draft`, the PR is opened in GitHub's draft mode. The `state/<name>` label is attached atomically. The framework does **not** apply a `type/` label or native Issue Type to PRs — the entity kind itself conveys the type.

One ticket can spawn zero (spike findings doc only), one (typical), or many PRs (incident mitigation chain, hotfix + backports, multi-component feature) — the cardinality between an issue and its PRs is **1:N**, and the framework does not gate the parent ticket's advancement on the PR set. Each PR is an independent work item; the agent decides when the parent advances. The spawn from `inner-loop.implementing → pr.draft` is modelled on `pr-states.json` for documentation; agents may also create PRs directly with `workflow create-issue --to draft --head ... --refs ...` (e.g., for backports that don't pass through inner-loop).

#### PR draft / ready lifecycle

Every PR the framework creates is opened as a **GitHub draft PR** (`gh pr create --draft` is always passed). The framework's `pr-states.json` starts new PRs at `state/draft`, and that aligns with the tracker's draft state.

To flip a PR from draft → ready-for-review, the destination state declares `mark_pr_ready: true`. When the framework advances into that state, the backend calls `gh pr ready <id>`. The example pr process marks `needs_review` with the flag, so:

- `draft → drafting` (claim by developer) — still draft on tracker.
- `drafting → needs_review` (advance) — `mark_pr_ready: true` fires → `gh pr ready`.

The flag is a no-op when applied to non-PR issues (gh errors out cleanly; the framework logs the warning and continues). Use it freely on shared states that might host both PRs and other types.

#### Standard PR message format

The framework appends a footer to whatever body the user supplies:

```
<user body>

---

Refs #<ticket>[, #<ticket>...]
```

GitHub auto-links the `#N` references as cross-references on the parent ticket. The footer is mandatory and not user-configurable at this layer — projects that want richer templates should compose their body before passing it to `--body`.

### Type encoding (native vs label)

How the type is recorded on the tracker depends on what the org supports:

- **Native** — GitHub's first-class Issue Type field (`gh issue create --type "Bug"`). Requires GHES ≥ ?? / GitHub.com, the org to have Issue Types enabled, and the user to have read access to the types.
- **Label** — `type/<framework_id>` regular label (e.g., `type/bug`). Works on every tracker without preconditions.

All framework labels follow one grammar, `<kebab-classifier>/<value>` (ADR-0005): `state/<name>`, `claimed/<role>`, `last-state/<name>`, `type/<id>`, `child-of/<id>`, `collected-by/<id>`, `hitl-blocked/<gate>`, `hitl-audit/<gate>`, `hitl-input/<topic>`, `hitl-claim/<reviewing|auditing|advising>`, `hitl-signal/<approved|rejected|checked|revoked|resolved>`. The grammar lives in `workflow/backends/github_labels.py` — the single source of truth for encoding and parsing.

The choice is **auto-detected** per (host, owner) by probing the org's `/issue-types` endpoint via `gh api`. A non-empty type list → native; anything else (404, 403, empty list, network error) → label. The decision is cached in `~/.config/blemees-workflow/capabilities.json` with a 30-day TTL.

A manual override is set via `workflow capabilities --set-encoding native|label` — `manual=true` on the entry pins it so refreshes don't touch it. `--clear` wipes the cache. `--refresh` re-probes non-manual entries.

### `workflow setup-github`

Provisions both org Issue Types and repo labels:

- **Default** (`workflow setup-github`): best-effort — tries to create missing org Issue Types; if it can't (permissions), falls back to label encoding and provisions `type/*` labels on the repo. Always provisions `state/`, `claimed/`, `last-state/`, and `hitl-*/` labels.
- **`--setup-org`**: admin path; creates missing Issue Types at the org and refreshes the capability cache. Fails loudly on any error. Does not touch repo labels.

## Closing the tracker's issue

Every closing state carries `closes: {taxonomy, reason}` in `<name>-states.json` — `reason` is the literal string the backend hands to the tracker (GitHub: `"completed"` or `"not planned"`). Both fields are **required inside `closes`** (parser-enforced); `closes` itself is only valid on resting states. Advancing into a closing state closes the tracker's issue with that reason as part of the same atomic apply step.

There is no "open-after-close" mode: entering a closing state always closes the issue. Work that *continues* under a different process keeps the same issue open via a **shared resting state** (cross-process `kind: shared`), not a closing state. Work that **spawns a follow-up issue** closes the original (taxonomy `superseded`, reason `completed`) and creates a new issue on the receiving process (`kind: spawn`).

Authoring lives in JSON, not in code. Projects map taxonomies to reasons however they want without changing the framework — typical pairings: `shipped`/`resolved`/`reverted`/`superseded` → `completed`; `abandoned`/`deduplicated` → `not planned`.

## `superseded` is for follow-up work, not handoff

Per upstream principle 8 (updated), `superseded` means *"work continues on a follow-up issue"* — the original work item terminates and a new one is created to continue. Covers both same-process iteration (a failed experiment ticket terminates as `superseded`; a fresh experiment ticket is opened with a revised hypothesis) and cross-process spawn (incident `stabilized` → new postmortem ticket).

This is **not** the same as a cross-process *handoff* (per principle 9), where the **same** work item continues on another process's diagram. Handoffs are modelled as shared resting states, not as closing states.

A bounce-back (e.g. inner-loop → refinement) is a handoff: the same issue continues elsewhere, so it must be a plain **shared resting state**, never a closing state. ADR-0002 makes the old "mark it superseded but keep it open" mislabel structurally impossible — a closing state always closes (`closes.reason` is required), so there's no way to tag a state as closing-superseded yet leave the issue open. Continuation is a shared resting state declared in both processes.

## Origin marker (`last-state`)

When `claim-issue` fires, the backend records `last-state/<source-state>` alongside `claimed/<role>`. On `release-issue`, the planner reads this marker and returns the issue to that resting state — the user never specifies a destination, eliminating the footgun of allowing arbitrary state jumps without a valid CLAIM transition.

A working state can have multiple incoming CLAIM transitions (e.g., `implementing` is claimed from both `ready_for_dev` initially and `staged` for revisions). The marker disambiguates without requiring user input.

Whenever an issue leaves a working state (advance, approve, record-action, release), both `claimed/<role>` and `last-state/<source>` are cleared atomically. Per principle 1, working = exactly one role owns the item; once the issue is resting (incl. closing), no role owns it.

If the marker drifts (no CLAIM transition from `last-state` → current state), `release-issue` errors rather than corrupting state.

## CLI flag conventions

- **`--to <state>`** is the canonical way to specify a destination state. Used by both `advance-issue` and `claim-issue`. For `claim-issue` it's optional when the current state has a single CLAIM transition out (auto-picked); required when ambiguous.
- **`--body <inline>` / `--body-from <path>`** is the standard body-input pair used by every command that posts markdown to the issue: `advance-issue`, `approve-blocked`, `reject-blocked`, `reject-audit`, `request-input`, `respond-request`, `post-comment`, `create-issue`. The two are mutually exclusive; `--body` accepts inline content for short bodies; `--body-from` reads a file for longer or templated bodies. Per-command-named flags (`--packet-from`, `--feedback-from`, etc.) were removed — one consistent flag pair across the surface.
- **No per-command `--role`.** Agent role comes from `--agent-role`, `AGENT_ROLE`, or the agent home's `config.json`. An agent has exactly one role at a time; per-command overrides would be a footgun.

## Operation verbs

The catalogued-HITL operations come in pre/post-action pairs:

| Pre-action (block) | Post-action (audit) | Meaning |
|---|---|---|
| `review-blocked` | `review-audit` | human takes the human-claim singleton |
| `approve-blocked` | `approve-audit` | human ratifies the transition / past action |
| `reject-blocked` | `reject-audit` | human declines / undoes |

The recognized-HITL pair is `request-input` (agent asks) → `respond-request` (human answers). The intermediate `review-request` mirrors the gate-review verbs: a human claims the response role before answering. The verbs match the pre/post-action gate verbs so the surface stays uniform: every human-side operation is one of `review-* / approve-* / reject-* / respond-*`.

## Human inputs

`request-input` is now **catalogued at the state level**. A working state declares `human_inputs: [...]` — a list of ids from the shared `human-inputs.json` directory (parallel to `roles.json` and `issue-types.json`). The agent's invocation requires `--topic <id>`; the id must be one of those declared on the current state.

Semantics:
- States without `human_inputs` declared CANNOT host `request-input` — the agent must release the issue or stay put. No free-form fallback.
- Add a `general` entry to a state's list to keep an explicit escape valve.
- Inputs route to **the human operator** (not a specific framework role) — these are escalations out of the agent loop.
- Marker: `hitl-input/<topic>` (ADR-0005) carries both the queue marker and the topic in one label — set on request, cleared on respond. Operators can filter by topic.

Validator: every id referenced on a working state must resolve in `human-inputs.json` (missing directory → WARNING; declared id absent from directory → ERROR).

## Cross-process handoff

The mechanism by which a work item passes from one process to another. Two flavors:

- **Shared resting state** — the same issue continues. The state appears in both processes' state machines (e.g., `ready_for_dev` ends refinement and starts inner-loop).
- **Spawn event** — the originating process creates a new issue that starts fresh in another process (e.g., incident response spawning a postmortem issue).

Convention: the receiver declares the shared state's `reversibility`; the role-restriction lives on the receiver's working state(s) reached via CLAIM from this resting state. The sender declares the state exists with its class.

## Cohort

The set of dependent issues attached to one anchor issue. Two instances:

- **Child cohort** — the children spawned from one parent (spawn relationship).
- **Contributor cohort** — the contributors gathered into one collector (collect relationship).

Both follow one rule (ADR-0003): the relationship is recorded by a **single label on the dependent side** — `child-of/<parent>` on each child, `collected-by/<collector>` on each contributor. There is no anchor-side registry (no `subprocess:`, no `collects:`). One label, one source of truth. (Under the native tier, ADR-0005 splits these: `child-of` becomes a sub-issue link, `collected-by` a custom field.)

The label is the **trigger edge**: when a child enters a closing state, or a contributor changes state, the cascade reads that dependent's own label to find the anchor to advance or release. The **cohort** — all dependents of one anchor — is discovered on demand by querying `list_issues(child_of=<parent>)` / `list_issues(collected_by=<collector>)`, never by reading the anchor. This is why the spawn cascade's **wait-for-all** semantics depend on the backend's list visibility covering closed issues and PRs (a closed dependent that just triggered the cascade must still appear in the cohort query).

A dependent is always labelled, even when its spawn/collect declares no `advance_on` rule (uniform labeling; the link is then informational only).

## Workflow directory (`--workflow-dir`)

The on-disk directory containing one workflow: every `<name>-states.json`, `<name>-human-gates.json`, the shared `roles.json`, the `trust-grants/` subdirectory, and any `agent_prepares` template files referenced from catalogs.

Discovered via the `--workflow-dir` CLI flag, the `WORKFLOW_DIR` env var, or the agent home's `.workflow/workflows/` default.

## Agent home (`.workflow/`)

The directory on the user's filesystem representing one agent's identity. Contains:

- `config.json` — agent identity (role, optional workflow-dir / grants-dir overrides)
- `workflows/` — default workflow-dir location
- `trust-grants/` — default grants-dir location

Multiple agent homes (e.g., one per role) may share the same workflow directory via the `workflow-dir` config key.
