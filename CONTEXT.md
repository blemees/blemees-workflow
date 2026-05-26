# Domain Glossary

The language this codebase uses. Update inline when terms get resolved or refined; never let prose drift away from this glossary.

## Workflow

The whole collection: one or more processes plus the shared roles, HCP catalogs, and trust grants that they reference together. Lives in one directory on disk (the `--workflow-dir`).

The `Workflow` Python class is the registry of processes — it discovers and loads each process on demand.

## Process

A single named business activity that moves work through a state machine. Examples: refinement, inner-loop, release, incident-response. Each process is defined by:

- A state machine (states + transitions + HITL gate markers)
- An HCP catalog (the gates' levels, reversibility, triggering roles)
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
- **Terminal** — final; no further transitions. Carries a taxonomy tag.

Per `docs/state-machine-principles.md` §1.

## Transition

A directed edge from one state to another, with a typed label. Four types:

- **Claim** — resting → working (e.g., `developer claims ready_for_dev`).
- **Role action** — working → resting | terminal (e.g., `developer opens PR`).
- **External** — resting → resting | terminal, driven by system event (e.g., `PR merged (external)`).
- **Cross-process** — resting ↔ `[*]`, with handoff metadata pointing at the other process.

Per `docs/state-machine-principles.md` §2.

## HCP (Human Control Point)

A gated transition that requires a human signal before firing. Authored as `hitl: true` on a transition, with a `gate` field naming the HCP catalog row. Each catalog row declares:

- Default level (`block` for pre-action gating, `audit` for post-action review)
- Reversibility class (inherited from destination state)
- Triggering role (who fires the gate)
- Allowed levels (subset of `block`, `audit`)
- Path to the agent-prepares template

Per `docs/state-machine-principles.md` §11 and the upstream hitl-principles.md.

## Gate

Synonym for HCP. The `gate_name` is the key used to look up an HCP in the catalog, in transition declarations, and in trust grants.

## Role

A framework-defined actor identity (e.g., `product-manager`, `developer`, `peer-reviewer`). Roles are declared once in the shared `roles.json` and referenced from working states' `roles` lists and HCP `triggering_role` fields. Roles map to backend handles (GitHub usernames, etc.) per team config — that mapping is outside the workflow tool's scope.

## Agent

A specific instance of a role acting on the system. An agent has an identity (the `agent-role` config field in its agent home) and operates against one workflow. Many agents may share a workflow; each has its own agent home.

The framework is actor-agnostic — agents may be humans, automated bots, or LLMs. The role is what matters.

## Issue

The unit of work flowing through a process. Called *issue* across the framework regardless of backend, since GitHub, GitLab, Linear, and Jira all use that term. GitHub PRs are treated as issue-like for framework purposes.

In code: `IssueState`, `IssueFilters`, `create_issue`, `read_issue`, `list_issues`, `issue_id`.

## Tracker (backend)

The system that stores issue state and provides query/mutation primitives — GitHub, GitLab, Linear, Jira. The `TrackerBackend` Python protocol defines the operations every tracker implementation must support. The current implementation is `GitHubBackend`.

## Trust grant

A per-team relaxation of an HCP's default level. The catalog defines a gate's default; a team that has earned discretion can author a trust grant flipping the level (e.g., block → audit) with mandatory evidence, expiry, and revocation procedure.

Lives in `trust-grants/<process>/<gate>.json`. Per the upstream trust-grant-schema.md.

## Next actions

Every single-item command (`view`, `create`, `claim`, `release`, `advance`, `request-input`, `comment`) ends its human-readable output with a `Next actions:` block describing what the agent could do next from the current state. The data behind it lives in `workflow.core.inspector.available_transitions` — a read-only function that walks the state machine's outgoing transitions and enriches each with the relevant HCP row and any active trust grant. The block is informational on read-only commands and best-effort (silent on failure) so commands that don't strictly need workflow context still work outside a known workflow.

For each gated transition the block surfaces: gate name, default vs effective level (so trust-grant relaxations are visible), triggering role, destination class + reversibility + terminal taxonomy, and the path to the `agent_prepares` template the agent should attach via `--packet-from`. For resting states, the block emits a `claim` suggestion (auto-resolved when unambiguous). For working states with a `wip_from` marker, it also surfaces `release` and where it returns to.

This means the agent does not have to consult the process documentation to figure out the next command — every operation that surfaces an issue's state surfaces its options too.

## Generated documentation

`workflow generate-docs` regenerates the agent/human-readable layer of the workflow directory: state-machine diagrams in mermaid, per-process reference docs in markdown, plus shared `roles.md`, `issue-types.md`, and a top-level `README.md` index. All emitted artifacts live alongside the canonical JSON sources.

Per-process markdown (`<name>.md`) contains everything an agent needs to operate on that process without chasing links: issue types accepted, embedded state diagram, states table, transitions table (with HITL level after trust-grant resolution), HCP details, cross-process handoff list, and any active trust grants. The emitter is read-only and deterministic — two runs produce byte-identical output, so pre-commit hooks can verify the checked-in docs are in sync with the JSON.

## Issue type

Issue types are declared at the **working-state** level. Every working state lists which types it accepts via `issue_types: [...]`; the field is required there and forbidden on resting / terminal states. The process's overall accepted set is derived as the union of every working state's `issue_types` (`StateMachine.accepted_issue_types`).

The type ids resolve against a shared `issue-types.json` (alongside `roles.json`) defining each type's display name, description, and optional backend-specific mappings (`github_issue_type`, `github_issue_type_color`).

`workflow create` requires `--type` when the process accepts multiple types; auto-defaults when only one. Issue type is set at creation and **immutable** — if a type needs to change, that's a new issue, not a retype. The validator checks every type id referenced by a working state exists in `issue-types.json`; missing ids are an ERROR, missing directory is a WARNING. The planner checks at claim time that the issue's type is in the destination working state's set — a typed ticket can't be claimed into a working state that doesn't accept its type.

This is how inner-loop's `implementing` (accepts bug/feature/chore), `implementing_experiment` (accepts experiment), `implementing_spike` (accepts spike), and `implementing_hotfix` (accepts hotfix) fan a single ticket into the right working flow.

### `pr` — the pre-defined pull-request type

`pr` is a built-in type that maps to GitHub pull-request entities rather than issues. The `IssueType` entry carries `"github_entity": "pull_request"` (default for every other type is `"issue"`); `github_issue_type` is forbidden on a `pull_request` entry because PRs are not a native GitHub Issue Type — the type is implicit in the entity kind.

The PR process declares `"issue_types": ["pr"]`. PRs are created via the same `workflow create` command as issues, with **extra required flags** that drive a framework-applied message format:

- `--head BRANCH` (required) — source branch.
- `--base BRANCH` (optional) — target branch; backend defaults to the repo's default branch when omitted.
- `--refs N` (required, repeatable) — parent ticket id(s) the PR addresses. Renders as a `Refs #N, #M, ...` footer in the body.
- `--body` is required for PRs (PRs need a description; the framework wraps it).

The backend dispatches to `create_pull_request` (which shells out to `gh pr create`) rather than `create_issue`. When the initial state is `draft`, the PR is opened in GitHub's draft mode. The `state:<name>` label is attached atomically. The framework does **not** apply a `type:` label or native Issue Type to PRs — the entity kind itself conveys the type.

One ticket can spawn zero (spike findings doc only), one (typical), or many PRs (incident mitigation chain, hotfix + backports, multi-component feature) — the cardinality between an issue and its PRs is **1:N**, and the framework does not gate the parent ticket's advancement on the PR set. Each PR is an independent work item; the agent decides when the parent advances. The spawn from `inner-loop.implementing → pr.draft` is modelled on `pr-states.json` for documentation; agents may also create PRs directly with `workflow create --to draft --head ... --refs ...` (e.g., for backports that don't pass through inner-loop).

#### PR draft / ready lifecycle

Every PR the framework creates is opened as a **GitHub draft PR** (`gh pr create --draft` is always passed). The framework's `pr-states.json` starts new PRs at `state:draft`, and that aligns with the tracker's draft state.

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
- **Label** — `type:<framework_id>` regular label (e.g., `type:bug`). Works on every tracker without preconditions.

The choice is **auto-detected** per (host, owner) by probing the org's `/issue-types` endpoint via `gh api`. A non-empty type list → native; anything else (404, 403, empty list, network error) → label. The decision is cached in `~/.config/blemees-workflow/capabilities.json` with a 30-day TTL.

A manual override is set via `workflow capabilities --set-encoding native|label` — `manual=true` on the entry pins it so refreshes don't touch it. `--clear` wipes the cache. `--refresh` re-probes non-manual entries.

### `workflow setup-github`

Provisions both org Issue Types and repo labels:

- **Default** (`workflow setup-github`): best-effort — tries to create missing org Issue Types; if it can't (permissions), falls back to label encoding and provisions `type:*` labels on the repo. Always provisions state/wip/wip-from/hitl labels.
- **`--setup-org`**: admin path; creates missing Issue Types at the org and refreshes the capability cache. Fails loudly on any error. Does not touch repo labels.

## Closing the tracker's issue

Every terminal state declares a `close_reason` in `<name>-states.json` — the literal string the backend hands to the tracker (GitHub: `"completed"` or `"not planned"`). The field is **required on terminals** (parser-enforced) and forbidden elsewhere. Advancing into a terminal state closes the tracker's issue with that reason as part of the same atomic apply step.

There is no "open-after-terminal" mode: a terminal always closes the issue. Work that *continues* under a different process keeps the same issue open via a **shared resting state** (cross-process `kind: shared`), not via a terminal. Work that **spawns a follow-up issue** terminates the original (taxonomy `superseded`, close_reason `completed`) and creates a new issue on the receiving process (`kind: spawn`).

Authoring lives in JSON, not in code. Projects map taxonomies to reasons however they want without changing the framework — typical pairings: `shipped`/`resolved`/`reverted`/`superseded` → `completed`; `abandoned`/`deduplicated` → `not planned`.

## `superseded` is for follow-up work, not handoff

Per upstream principle 8 (updated), `superseded` means *"work continues on a follow-up issue"* — the original work item terminates and a new one is created to continue. Covers both same-process iteration (a failed experiment ticket terminates as `superseded`; a fresh experiment ticket is opened with a revised hypothesis) and cross-process spawn (incident `stabilized` → new postmortem ticket).

This is **not** the same as a cross-process *handoff* (per principle 9), where the **same** work item continues on another process's diagram. Handoffs are modelled as shared resting states, not as terminals.

Our example's `bounced_back` in inner-loop is currently labeled `terminal_taxonomy: superseded`, which is a known misuse — the bounce-back is a handoff (same issue continues in refinement), not supersession. Because close behavior is driven by `close_reason` (absent for `bounced_back`), the tracker issue correctly stays open, but the taxonomy tag is wrong. A proper fix would restructure `bounced_back` as a shared resting state declared in both processes; that's a follow-up that requires resolving the registry's first-load-wins routing.

## Origin marker (`wip-from`)

When `claim` fires, the backend records `wip-from:<source-state>` alongside `wip:<role>`. On `release`, the planner reads this marker and returns the issue to that resting state — the user never specifies a destination, eliminating the footgun of allowing arbitrary state jumps without a valid CLAIM transition.

A working state can have multiple incoming CLAIM transitions (e.g., `implementing` is claimed from both `ready_for_dev` initially and `staged` for revisions). The marker disambiguates without requiring user input.

Whenever an issue leaves a working state (advance, approve, record-action, release), both `wip:<role>` and `wip-from:<source>` are cleared atomically. Per principle 1, working = exactly one role owns the item; once the issue is resting or terminal, no role owns it.

If the marker drifts (no CLAIM transition from `wip_from` → current state), `release` errors rather than corrupting state.

## CLI flag conventions

- **`--to <state>`** is the canonical way to specify a destination state. Used by both `advance` and `claim`. For `claim` it's optional when the current state has a single CLAIM transition out (auto-picked); required when ambiguous.
- **`--body <inline>` / `--body-from <path>`** is the standard body-input pair used by every command that posts markdown to the issue: `advance`, `approve`, `reject`, `revoke`, `request-input`, `respond`, `comment`, `create`. The two are mutually exclusive; `--body` accepts inline content for short bodies; `--body-from` reads a file for longer or templated bodies. Per-command-named flags (`--packet-from`, `--feedback-from`, etc.) were removed — one consistent flag pair across the surface.
- **No per-command `--role`.** Agent role comes from `--agent-role`, `AGENT_ROLE`, or the agent home's `config.json`. An agent has exactly one role at a time; per-command overrides would be a footgun.

## Operation verbs

The catalogued-HITL operations come in pre/post-action pairs:

| Pre-action (block) | Post-action (audit) | Meaning |
|---|---|---|
| `review` | `audit` | human takes the human-claim singleton |
| `approve` | `confirm` | human ratifies the transition / past action |
| `reject` | `revoke` | human declines / undoes |

The recognized-HITL pair is `request-input` (agent asks) → `respond` (human answers). Don't say `resolve` — it collides with tracker "issue resolved" status semantics. The post-action confirmation verb is `confirm`, not `check`, since `check` is bland and could read as "check the status of…".

## Input topics

`request-input` is now **catalogued at the state level**. A working state declares `input_topics: [...]` — a list of topic ids from the shared `input-topics.json` directory (parallel to `roles.json` and `issue-types.json`). The agent's invocation requires `--topic <id>`; the topic must be one of the declared ids on the current state.

Semantics:
- States without `input_topics` declared CANNOT host `request-input` — the agent must release the issue or stay put. No free-form fallback.
- Add a `general` topic to a state's list to keep an explicit escape valve.
- Topics route to **the human operator** (not a specific framework role) — these are escalations out of the agent loop.
- Markers: `hitl:awaiting-input` (existing queue marker) + `hitl:topic-<id>` (companion, set on request and cleared on respond). Operators can filter by topic.

Validator: every topic id referenced on a working state must resolve in `input-topics.json` (missing directory → WARNING; declared id absent from directory → ERROR).

## Cross-process handoff

The mechanism by which a work item passes from one process to another. Two flavors:

- **Shared resting state** — the same issue continues. The state appears in both processes' state machines (e.g., `ready_for_dev` ends refinement and starts inner-loop).
- **Spawn event** — the originating process creates a new issue that starts fresh in another process (e.g., incident response spawning a postmortem issue).

Convention: the receiver declares the shared state's `reversibility`; the role-restriction lives on the receiver's working state(s) reached via CLAIM from this resting state. The sender declares the state exists with its class.

## Workflow directory (`--workflow-dir`)

The on-disk directory containing one workflow: every `<name>-states.json`, `<name>-hcps.json`, the shared `roles.json`, the `trust-grants/` subdirectory, and any `agent_prepares` template files referenced from catalogs.

Discovered via the `--workflow-dir` CLI flag, the `WORKFLOW_DIR` env var, or the agent home's `.workflow/workflows/` default.

## Agent home (`.workflow/`)

The directory on the user's filesystem representing one agent's identity. Contains:

- `config.json` — agent identity (role, optional workflow-dir / grants-dir overrides)
- `workflows/` — default workflow-dir location
- `trust-grants/` — default grants-dir location

Multiple agent homes (e.g., one per role) may share the same workflow directory via the `workflow-dir` config key.
