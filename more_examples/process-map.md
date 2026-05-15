# Process Map — Reader's Guide

This is the **one-level-zoomed-out view** of this workflow process set. It is not a process doc (no rules, no variations, no flow — those live in the per-process `*-process.md` files). It is not a state machine (no claim/work/rest discipline — those live in `*-lifecycle.mermaid`). It is a **composition diagram**: each node is a whole process, each edge is a handoff between processes.

Use it to answer:

- Where does this work item come from, and where does it go next?
- Which processes feed which, and through what shared state or spawn event?
- Where are the feedback loops that send work back into refinement?

For anything state-level — individual transitions, claim scripts, terminal taxonomy, role actions inside a process — drop into the relevant `*-lifecycle.mermaid` and `*-process.md` pair.

---

## The diagram

See `process-map.mermaid` for the canonical diagram.

## How to read it

Nodes are grouped into four lanes, roughly by phase of work:

- **🔨 Build** — `refinement`, `inner-loop`, `pr`. Where issues become merged code.
- **🚀 Ship** — `release`, `progressive-rollout`. Where merged code reaches users.
- **🚨 Respond** — `incident-response`, `mitigation`, `backport`, `postmortem`. The incident path and its recovery satellites.
- **🔬 Learn** — `experimentation`. Post-release measurement that feeds back into refinement.

The **prioritization** overlay sits outside the lanes because it has no lifecycle — it is an optional cadence-based layer on the refinement queue. See `prioritization-process.md`.

Edge styling is the signal:

- **Thick edges (`==>`)** are the primary happy path: raw issue → refinement → inner-loop → pr → release → rollout. Incident trigger → incident-response → mitigation / backport / postmortem. If you are reading the diagram to answer "what is the default flow for this work," follow the thick edges.
- **Thin edges (`-->`)** are secondary or conditional handoffs, mostly how mitigation and backport reuse the build and ship rails under incident authority.
- **Dashed edges (`-.->`)** are feedback loops and overlay effects. They are the parts of the system that close the cycle: inner-loop bouncing work back to refinement, postmortem filing follow-ups, experiments iterating, prioritization attributing rank.

Edge labels name the **interface**:

- A shared resting state (`ready_for_dev`, `ready_for_hotfix`, `ready_bounced`, `staged`) means the same work item crosses the boundary — it appears on both processes' lifecycles per `state-machine-principles.md` §9.
- A **spawn** description (`spawn mitigation`, `spawn backport per branch`, `spawn measurement`, `follow-up issues`) means a new work item is filed on the downstream process and the upstream process does not render that item's states.

## What this diagram is not

- **Not a state machine.** Nodes are processes, not states. No claim/work/rest discipline applies at this level. Do not use `stateDiagram-v2` for this view.
- **Not a process doc.** No rules, artifact templates, or variations are implied by edges here. Each edge is elaborated in the corresponding `*-process.md` handoff description.
- **Not exhaustive.** Overlay effects, escalation paths, and rare reroutes are omitted to keep the view readable. If you need to trace a specific work item type through every possible transition, you need the underlying lifecycles, not this map.
- **Not a source of truth.** The canonical truth is the set of `*-lifecycle.mermaid` files. If the map disagrees with a lifecycle, the lifecycle wins and the map is out of date.

## When to update it

Update `process-map.mermaid` when:

1. A new process is added (new lifecycle + process doc + node on the map).
2. A process is removed or merged into another (node disappears; dependent edges re-route).
3. A cross-process handoff is added, removed, or retargeted. Every new `from process X` / `to process X` edge on a lifecycle corresponds to an edge on this map.

Do **not** update it when:

- A state is renamed within a single process (internal, doesn't cross boundaries).
- A rule, variation, or anti-pattern changes in a process doc.
- A convention or template changes.

---

## See also

- `state-machine-principles.md` — the shared-state interface and spawn-event conventions that edges on this map represent
- `process-doc-principles.md` — scopes what belongs in process docs (explains why this map is not a process doc)
- Every `*-lifecycle.mermaid` file — canonical state-level view of each node
- Every `*-process.md` file — prose companion to each lifecycle
