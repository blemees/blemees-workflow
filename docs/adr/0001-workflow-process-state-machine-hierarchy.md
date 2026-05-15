# ADR 0001: Domain hierarchy is Workflow → Process → State Machine

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

Early in development the codebase used a single overloaded term — first `Lifecycle`, then `Workflow` — to mean both:

1. The runtime tool driving things forward (the `workflow` Python package, the CLI, the runtime context).
2. The thing being modeled (refinement, inner-loop, release as distinct named business activities).

The Python class `Workflow` was simultaneously the state-machine model, while `WorkflowContext` was the runtime bundle, `WorkflowRegistry` was the collection of state machines, and `WorkflowBackend` was the tracker protocol. Every word in this list was reasonable in isolation, but readers had to disambiguate "workflow" from context every sentence.

The upstream principles document (`docs/state-machine-principles.md`) used a different vocabulary — "process" for the named activity, "state machine" for the diagram — without collision. Our code drifted from that vocabulary.

## Decision

Adopt a three-level domain hierarchy with one word per level:

| Level | Term | Means | Python |
|---|---|---|---|
| top | **Workflow** | the collection of related processes that share roles and trust grants; lives in one `--workflow-dir` | `Workflow` (the registry of processes) |
| middle | **Process** | one named business activity — refinement, inner-loop, release — with its own state machine + HCP catalog + trust grants | `Process` (the runtime bundle for one process) |
| bottom | **State machine** | the state graph for one process — states, transitions, HITL markers | `StateMachine` (parsed from `<name>-states.json`) |

Plus matching renames for tool-side names where the old terminology overlapped:

- `WorkflowBackend` → `TrackerBackend` (it's a protocol for issue-tracker backends)
- `WorkItemState`, `WorkItemFilters`, `work_item_id`, `create_work_item` → `IssueState`, `IssueFilters`, `issue_id`, `create_issue` (every named backend — GitHub, GitLab, Linear, Jira — calls it an "issue")

The serialized state machine on disk is named `<name>-states.json`, not `<name>-process.json` or `<name>-workflow.json`. The other process artifacts already follow the same `<content>.json` pattern (`<name>-hcps.json`, `roles.json`). A process is the union of those files, not any one of them.

The package name (`workflow`), CLI command name (`workflow`), and agent-home directory (`.workflow/`) stay as `workflow` — they describe the tool, not the domain. `WorkflowError` (exception base) and the `--workflow-dir` flag (singular: the directory contains one workflow) also stay.

## Consequences

**Wins**:
- Readers reason about three distinct concepts with three distinct words. The principles doc and our code now share vocabulary.
- The serialized format `<name>-states.json` makes it explicit that the file is just one component of a process. Authors who edit it know they're editing the state machine; the rest of the process lives in sibling files.
- Tracker-agnostic vocabulary (`Issue`, `TrackerBackend`) lines up with what GitHub/GitLab/Linear/Jira call things.

**Costs**:
- Did three full renames in quick succession (`Lifecycle` → `Workflow` → `Workflow/Process/StateMachine`). Anyone reading commits or stash history sees the churn.
- The package name `workflow` overlaps lexically with the new `Workflow` class name. Tool-side names like `WorkflowError` stay, which is mildly ambiguous in isolation but unambiguous in context — `Workflow` (capitalized, class) is a model object; `workflow` (lowercase, module path) is the package.

**Boundary stays put**:
- Future tool-level abstractions (e.g., a `WorkflowOrchestrator`, `WorkflowEventBus`) reasonably take the `Workflow*` prefix.
- Future domain refinements (e.g., a `ProcessVersion` for migration history, a `StateMachineDiff` for change detection) take `Process*` / `StateMachine*` prefixes.

## Alternatives considered

1. **Keep `Workflow` overloaded** — what we had. Rejected: every sentence needs context to disambiguate.
2. **`Process` as the state machine class, no `Workflow` distinction** — would have meant the package and CLI describe themselves as "the workflow tool" while the model only knows about `Process`. Rejected because the three-level split (some users will eventually want to manage *multiple* workflows from one tool, e.g., engineering vs. ops) is a real distinction worth preserving in the model.
3. **`Lifecycle` (original)** — accurate but jargon-heavy; "lifecycle" implies a single linear trajectory which misrepresents the branching graph. Also overloaded with software-lifecycle and product-lifecycle meanings.
