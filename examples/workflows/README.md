# Workflow

Generated documentation for the processes defined in this workflow.
Authored sources are the `*.json` files; regenerate with `workflow generate-docs`.

## Process map

Auto-generated overview of every process in this workflow and the handoffs between them. The canonical source is each `<process>-states.json`; the diagram is regenerated from those.

Rendered as a `stateDiagram-v2` so it shares the visual language of the per-process state diagrams. Nodes are processes; the built-in `[*]` sentinel marks external entry (top) and external exit (bottom). The diagram reads top-to-bottom: new issues flow from `[*]`, through processes (handoffs and spawns between them), and back to `[*]` as each terminal state is reached.

Edge labels carry a symbol prefix indicating the relationship kind:

- **`▶ <state>`** — entry: a new external issue materializes at the labelled state.
- **`■ <state>`** — exit: an issue closes at the labelled terminal **and** no parent process has it listed as a spawn feedback target. Terminals named in some sibling's `spawn.advance_on` are treated as feedback (the work continues in the parent) and don't render as workflow exits, even though the child issue itself closes.
- **`⊙ <state>`** — handoff: the same work item continues on the destination process. Bidirectional handoffs (each side both sends and receives) emit two edges in opposite directions.
- **`ᐉ <parent_state>`** — spawn: the source process creates a child issue on the destination process. The label names only the parent state where the spawn fires; check the destination's own diagram for the child's initial state.
- **`ꘜ <collector_state>`** — collect: the destination process (authored via `collects`) gathers contributors from another process. The label names only the collector state; the source's `from_states` are visible on the source process's own diagram.
- **`⊡ <state>`** — feedback: for a spawn's `advance_on`, the parent's next state after the child terminates. For a collect's `advance_on`, the collector's state that triggers contributor movement. Pairs with the originating `ᐉ`/`ꘜ` edge to show the round-trip; the trigger / target counterpart is visible on the relevant process's own diagram.
- **`⧄ <collector_state>`** — release: a collect's `release_on` entry. When the collector enters the labelled state, every contributor's `collected-by:<collector>` marker is cleared but no state change happens — the contributors are released back to candidacy and become eligible for a future collector.

Edge labels name the state involved — the shared resting state for handoffs, or the originating → destination state pair for spawns.

```mermaid
stateDiagram-v2
    direction LR

    state "config-change" as config_change
    state "data-change" as data_change
    state "incident-response" as incident_response
    state "inner-loop" as inner_loop

    [*] --> incident_response: ▶ declared
    [*] --> refinement: ▶ raw
    config_change --> mitigation: ⊡ mitigated
    data_change --> mitigation: ⊡ mitigated
    incident_response --> mitigation: ᐉ mitigating
    incident_response --> postmortem: ᐉ stabilized
    inner_loop --> mitigation: ⊡ mitigated
    inner_loop --> pr: ᐉ implementing
    inner_loop --> refinement: ⊙ ready_bounced
    inner_loop --> refinement: ⊡ spike_returned
    inner_loop --> release: ꘜ cut [bug,feature,chore,experiment]
    inner_loop --> release: ꘜ hotfix_cut [hotfix]
    mitigation --> config_change: ᐉ execute_mitigation
    mitigation --> data_change: ᐉ execute_mitigation
    mitigation --> incident_response: ⊡ needs_verification
    mitigation --> inner_loop: ᐉ execute_mitigation
    pr --> inner_loop: ⊡ staged
    refinement --> inner_loop: ᐉ spiking
    refinement --> inner_loop: ⊙ ready_for_dev
    release --> inner_loop: ⊡ released
    release --> inner_loop: ⧄ abandoned
    release --> inner_loop: ⧄ rolled_back
    experimentation --> [*]: ■ aborted
    experimentation --> [*]: ■ iterated
    experimentation --> [*]: ■ killed
    experimentation --> [*]: ■ promoted
    postmortem --> [*]: ■ complete
    refinement --> [*]: ■ duplicate
    refinement --> [*]: ■ wont_fix
```

The raw mermaid source is also available at [`process-map.mermaid`](./process-map.mermaid).

**What this map does NOT show:** editorial groupings (Build / Ship lanes), edge tiers (happy path vs feedback), or rolled-up labels. Each shared state appears as its own edge.

## Processes

- [`config-change`](./config-change.md) — Apply a configuration change as an incident mitigation: feature-flag toggle, kill switch, runtime config value, rate-limit adjustment. Linear claim/advance flow — spawned from `mitigation.execute_mitigation`, closes at `config_applied` to release the parent. Out of scope: schema changes that require migrations (use `data-change`).
- [`data-change`](./data-change.md) — Apply a data change as an incident mitigation: corruption repair, manual update, backfill, replay. Backup is mandatory before any mutation — the process serializes `creating_backup` → `backup_ready` → `applying_data_change` so the responder cannot skip the safety net. Spawned from `mitigation.execute_mitigation`, closes at `data_change_applied` to release the parent.
- [`experimentation`](./experimentation.md) — Measure a flag-gated experiment in production and reach a verdict — ship-to-all, kill, or iterate. Owned by the product owner once the dev work is merged.
- [`incident-response`](./incident-response.md) — Coordinate the live response to a production incident: declare, mitigate, stabilize. Spawns a postmortem on stabilization.
- [`inner-loop`](./inner-loop.md) — The developer's day-to-day flow: claim a refined ticket, implement, open a PR, ship. Spawns a PR child issue and tracks staged/shipped state.
- [`mitigation`](./mitigation.md) — Plan and execute a mitigation strategy during an active incident. The responder drafts a plan, then dispatches one or more sub-mitigations — a hotfix, a configuration change, a data change, or any combination — each as a child issue on its own process. `execute_mitigation` declares a multi-spawn rule per mitigation type; the wait-for-all cascade closes this issue at `mitigated` only when every spawned child reaches its respective applied / shipped terminal, which in turn cascades the parent incident to `needs_verification`.
- [`postmortem`](./postmortem.md) — Document the timeline, root cause, and remediation for an incident. Spawned by incident-response on stabilization; closes when the writeup is approved.
- [`pr`](./pr.md) — The pull-request lifecycle: draft → review → merged. Spawned from inner-loop's implementing state.
- [`refinement`](./refinement.md) — Shape raw ideas and bug reports into ready-for-dev tickets. The product manager owns the queue, classifies issue type, and either marks ready or parks/kills.
- [`release`](./release.md) — Cut, review, and ship a release train. The release manager assembles a candidate from staged work, runs the go/no-go review, then ships or defers.

## Shared resources

- [Roles](./roles.md)
- [Issue types](./issue-types.md)
- [Human inputs](./human-inputs.md)
