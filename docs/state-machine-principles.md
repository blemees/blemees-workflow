# State Machine Design Principles

These principles govern the design of state machine diagrams for agent workflow skills. Apply them whenever creating or editing a state diagram.

---

## 1. Every state is resting, working, or terminal

- **Resting** — no agent is actively working. The issue is parked, waiting for someone to claim it or for an external event. These are pollable queues.
- **Working** — an agent has claimed the issue and is actively doing something. Exactly one role owns the item in this state.
- **Terminal** — the issue is done. No further transitions.

There are no other state types. If a state doesn't fit one of these three, the diagram needs restructuring.

## 2. Transitions follow strict type rules

| Transition type | From | To | Label pattern | Trigger mechanism |
|---|---|---|---|---|
| **Claim** | resting | working | `{role} claims {what}` | Agent polls queue or receives trigger, then runs a claim script |
| **Role action** | working | resting or terminal | `{role} {verb}` | Agent completes work and runs a state-transition script |
| **External event** | resting | resting or terminal | `{description} (external)`, `(time)`, or `from/to process {name}` | System event (CI, deploy, timer, webhook) or cross-process handoff (see principle 9) |

No other transition patterns are allowed:

- **resting → resting** is only valid for external/system events (PR merged, production deploy, timer elapses, handoff from another process). Never for agent actions within the same process.
- **working → working** is only valid when the same actor continues (e.g., consultant resumes after spike completes). Never across different roles.
- **resting → terminal** is only valid for external events (auto-close).

## 3. Agents must claim before working

An agent never acts directly on a resting state. They first claim it (resting → working), then do their work, then park it (working → resting) or close it (working → terminal). This two-step pattern ensures:

- The polling/trigger mechanism knows who is responsible.
- No work is done without an accountable owner.
- The gh helper scripts map 1:1 to transitions: each claim is a script call, each park/close is a script call.

The "bounce at the gate" anti-pattern (agent rejects a resting item without claiming it first) is not allowed. If a developer evaluates a `ready_for_dev` issue and decides it's not ready, the path is: `ready_for_dev → implementing` (claim) → `ready_bounced` (bounce). The claim happened, the working state existed, even if briefly.

This also applies to negative decisions that might feel like "just a judgment": aborting an experiment, killing a rollout, deferring a release — all require claim-then-act. Hence `measuring → aborting → aborted`, not `measuring → aborted`.

## 4. Label conventions

- **Claims** use the verb "claims": `PM claims raw`, `developer claims issue`, `QA claims PR`.
- **Role actions** use active verbs: `PM marks ready`, `developer marks PR ready for review`, `reviewer approves`.
- **External events** are annotated: `PR merged (external)`, `production deploy (external)`, `measurement window closes (time)`. Cross-process handoffs use `to process X` / `from process X` per principle 9.
- **Ready states** are named `ready_for_{purpose}`: `ready_for_dev`, `ready_for_experiment`, `ready_for_spike`, `ready_for_rollback`, `ready_for_flag_toggle`, `ready_for_hotfix`, `ready_for_backport`, `ready_for_release_decision`, `ready_for_followups`. Queue semantics (urgency, claim role, SLA) differ across ready variants — the suffix makes that visible.

Labels always start with the role performing the action, except external events which describe the system event or inter-process handoff.

## 5. Passive/system states are resting

States where no agent is active but something is happening in the background (measuring an experiment, waiting for CI, waiting for deploy, soaking a rollout stage) are classified as **resting**. The system is doing work, but no agent owns the state. Transitions out of these states are external events or time-based, never claims from the same actor.

## 6. Each process has its own state machine

The canonical unit of a state machine is a **process**, not an issue type. Processes map 1:1 to diagrams:

- Refinement → `refinement-states.mermaid`
- Inner loop → `inner-loop-states.mermaid`
- Pull request review → `pr-states.mermaid`
- Release → `release-states.mermaid`
- Progressive rollout → `progressive-rollout-states.mermaid`
- Experimentation → `experimentation-states.mermaid`
- Incident response → `incident-response-states.mermaid`
- Mitigation → `mitigation-states.mermaid`
- Postmortem → `postmortem-states.mermaid`
- Backport → `backport-states.mermaid`

An issue (issue, PR, release, incident) may pass through multiple processes over its lifetime. At any given moment, it is tracked by exactly one process's state machine. Handoffs between processes use the **shared-state interface pattern** (see principle 9).

Never model another process's internal states on the wrong diagram. When tempted to model sub-states that "help the reader," add a note instead.

## 7. Claim scripts are the enforcement layer

Each claim transition maps to a gh helper script (e.g., `claim-raw.sh`, `claim-issue.sh`, `claim-release.sh`). These scripts:

- Swap the state label (resting label → working label).
- Assign the claiming agent.
- Are idempotent — calling claim on an already-claimed item fails cleanly.

This means the state machine isn't just a diagram — it's the contract that the scripts enforce. If a transition isn't on the diagram, there shouldn't be a script for it.

## 8. Terminal states carry a taxonomy

Not all terminals are equivalent. Every terminal state must be tagged with one of the following categories. The tag supports metrics, dashboards, and post-hoc analysis.

| Tag | Meaning | Examples |
|---|---|---|
| **shipped** | Work reached users | `released`, `promoted` (exp), `hotfix_applied`, `complete` (rollout), `backported` |
| **resolved** | Process-complete terminal with no user-facing output (meta-work) | `complete` (postmortem, ADR, retro) |
| **reverted** | Work reached users then was withdrawn | `rolled_back`, `kill_switched` |
| **abandoned** | Work was not shipped — stopped on purpose (whether at intake or in flight) | `wont_fix`, `killed` (exp), `aborted` (exp), `abandoned` (release train) |
| **deduplicated** | Work was not shipped because it was a duplicate | `duplicate` |
| **superseded** | Work continues on a follow-up issue — same-process iteration or cross-process handoff | `iterated` (exp), `stabilized` (incident → postmortem), `ready_bounced` (inner loop → refinement) |

The tag is a label on the terminal state node, or in the note adjacent to it. A terminal without a tag is incomplete.

## 9. Cross-process handoffs use shared resting states

The state between two processes is a resting state that neither process uniquely owns — it's the **interface** between them. When the same issue continues from one process into the next, both diagrams render the shared state explicitly.

### Label convention

All cross-process transitions use `to process {name}` and `from process {name}`. This is distinct from system events, which continue to use `(external)` or `(time)`.

```
%% on the producing diagram (refinement)
refining --> ready_for_dev: PM marks ready
ready_for_dev --> [*]: to process inner-loop

%% on the consuming diagram (inner-loop)
[*] --> ready_for_dev: from process refinement
ready_for_dev --> implementing: developer claims ticket
```

### Shared states vs. spawn events

Cross-process transitions come in two flavors:

**Shared resting state** — the same issue continues. The queue appears on **both** diagrams with matching names, forming the interface between them.

- Refinement ↔ Inner loop: `ready_for_dev`, `ready_for_experiment`, `ready_bounced`.
- Mitigation ↔ Inner loop: `ready_for_hotfix` (the hotfix issue is the issue; mitigation spawns it but it is then tracked end-to-end as an issue).

**Spawn event** — a process spawns a **new** issue that starts its life on another process's diagram. The originating diagram does not render the new item's state; it just notes the spawn in the adjacent note. Label still uses `to process X` / `from process X`, but the state only exists on the consuming diagram.

- Refinement → Inner loop (spike): consultant creates a spike sub-issue; the new issue enters inner-loop as `ready_for_spike`. Refinement does not render `ready_for_spike`.
- Incident response → Mitigation: IC opens mitigation issues (rollback, flag toggle, hotfix). Those are new issues that start on mitigation.
- Incident response → Postmortem: postmortem is a new issue spawned when the incident stabilizes.
- Mitigation → Backport: backport is a new issue spawned when a hotfix needs a backport.
- Release → Experimentation: `measuring` starts when an experiment issue releases to production. The experiment issue was released as part of a train; after release, experimentation picks it up.

### Legend requirement

Every diagram must declare its cross-process entry and exit states in a top-level note, distinguishing shared states from spawn events:

- **Entry shared states**: `from process X` arrivals that originate as the same issue on process X's diagram.
- **Entry spawn events**: `from process X` arrivals that start fresh on this diagram.
- **Exit shared states**: `to process X` departures continuing as the same issue on process X's diagram.
- **Exit spawn events**: new issues created here that start on process X's diagram.

This forces reconciliation against the partner diagrams. If diagram A exits with `ready_for_dev → [*]: to process inner-loop` as a shared state, diagram B must have a matching entry `[*] → ready_for_dev: from process refinement`. Mismatches are modeling bugs.

## 10. StateMachine diagrams ship with the skills that implement them

The `.mermaid` file is not just documentation — per principle 7 it is the contract the scripts enforce. Every skill that runs claim or role-action scripts for a process **bundles** that process's workflow file in its `references/` directory, copied via the project's assemble script.

Concretely:

- The skill's assemble step lists the workflow file alongside the process doc, conventions, and roles.
- The skill's "Read first" section names the workflow file by filename, with a one-line reason ("the diagram the scripts in this bundle enforce").
- The same workflow file may be bundled in multiple skills when multiple roles operate on the same process — that is expected, not duplication. The single source of truth is the canonical copy in the project's shared resources; skill bundles are mechanical copies kept in sync by the assemble script.

Skills that operate across multiple processes (multi-mode skills per `skill-authoring-principles.md` principle 6) bundle every workflow their modes touch. A release-manager skill that runs release, progressive-rollout, and backport modes bundles all three diagrams.

A skill whose scripts transition states on a workflow the skill does not bundle is a bug in two directions: the agent has no canonical reference for the contract their scripts enforce, and the skill is silently coupled to a file outside its own bundle.

## 11. Transitions may be gated; gating is an overlay, not a state class

Transitions can require a human signal before they fire. Gating is an **overlay** on the existing state machine — it does not introduce a new state class (the resting/working/terminal taxonomy of principle 1 stands) and it does not introduce a new transition type (the four types of principle 2 stand). It marks specific transitions with a HITL annotation; the underlying graph is unchanged.

On the workflow diagram:

- Every gated transition carries the marker `[hitl]` at the end of the transition label. Example: `refining --> ready_for_dev: PM marks ready [hitl]`.
- A comment-block legend at the top of the `.mermaid` file declares every gated transition with its reversibility class. Strict listing — every `[hitl]` marker in the diagram has a corresponding legend entry, and vice versa.
- The legend's first line points at the canonical catalog location (`hitl-principles.md` defines the catalog row schema; the catalog itself lives in the process doc's "Human control points" section).
- Level information (block / audit, parameters) is **not** on the diagram. The level is a runtime property, declared per team in trust grants per `hitl-principles.md` principle 12. The same diagram serves every team.

Reversibility is a property of the *destination state*, not the transition itself; transitions inherit their destination's reversibility class. The class is declared in the workflow file (typically in the legend or in a note adjacent to the state). The three classes are defined in `hitl-principles.md` principle 4: `irreversible`, `reversible-fast`, `reversible-slow`.

Two static checks the workflow file must pass:

1. **Every transition to an `irreversible` destination carries `[hitl]`.** No silent paths into irreversible states.
2. **The legend matches the markers.** Every legend entry has a corresponding `[hitl]` in the diagram, and every marker has a legend entry. Drift between the two is the most common authoring failure.

Recognized HCPs (state-orthogonal moments the agent recognizes at runtime per `hitl-principles.md` principle 10) have no representation on the diagram. By definition the moment is not transition-bound and is not pre-declared. The diagram describes transitions; recognized HCPs are about agent behavior between transitions.

Full discipline — operation vocabulary, level taxonomy, reversibility constraints, recognition criteria, comment templates, trust-grant schema — lives in `hitl-principles.md`. Backend-specific encodings of those operations (labels for GitHub, etc.) live in `backends/*-encoding.md`. This principle establishes only the diagram-level annotation convention; the diagram is backend-neutral.
