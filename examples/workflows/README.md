# Workflow

Generated documentation for the processes defined in this workflow.
Authored sources are the `*.json` files; regenerate with `workflow generate-docs`.

## Process map

Auto-generated overview of every process in this workflow and the handoffs between them. The canonical source is each `<process>-states.json`; the diagram is regenerated from those.

Rendered as a `stateDiagram-v2` so it shares the visual language of the per-process state diagrams. Nodes are processes; the built-in `[*]` sentinel marks external entry (top) and external exit (bottom). The diagram reads top-to-bottom: new issues flow from `[*]`, through processes (handoffs and spawns between them), and back to `[*]` as each closing state is reached.

Edge labels carry a symbol prefix indicating the relationship kind:

- **`▶ <state>`** — entry: a new external issue materializes at the labelled state.
- **`■ <state>`** — exit: an issue closes at the labelled closing state **and** no parent process has it listed as a spawn feedback target. Closing states named in some sibling's `spawn.advance_on` are treated as feedback (the work continues in the parent) and don't render as workflow exits, even though the child issue itself closes.
- **`⊙ <state>`** — handoff: the same work item continues on the destination process. Bidirectional handoffs (each side both sends and receives) emit two edges in opposite directions.
- **`ᐉ <parent_state>`** — spawn: the source process creates a child issue on the destination process. The label names only the parent state where the spawn fires; check the destination's own diagram for the child's initial state.
- **`ꘜ <collector_state>`** — collect: the destination process (authored via `collects`) gathers contributors from another process. The label names only the collector state; the source's `from_states` are visible on the source process's own diagram.
- **`⊡ <state>`** — feedback: for a spawn's `advance_on`, the parent's next state after the child terminates. For a collect's `advance_on`, the collector's state that triggers contributor movement. Pairs with the originating `ᐉ`/`ꘜ` edge to show the round-trip; the trigger / target counterpart is visible on the relevant process's own diagram.
- **`⧄ <collector_state>`** — release: a collect's `release_on` entry. When the collector enters the labelled state, every contributor's `collected-by:<collector>` marker is cleared but no state change happens — the contributors are released back to candidacy and become eligible for a future collector.

Edge labels name the state involved — the shared resting state for handoffs, or the originating → destination state pair for spawns.

```mermaid
stateDiagram-v2
    direction LR

    state "incident" as incident {
        state "config-change" as config_change
        state "data-change" as data_change
        state "incident-response" as incident_response
        mitigation
        postmortem
    }
    state "delivery" as delivery {
        experimentation
        state "inner-loop" as inner_loop
        pr
        refinement
        release
    }

    [*] --> incident_response: ▶ declared
    [*] --> inner_loop: ▶ ready_for_dev
    [*] --> refinement: ▶ raw
    config_change --> mitigation: ⊡ mitigated
    data_change --> mitigation: ⊡ mitigated
    experimentation --> inner_loop: ᐉ killed
    experimentation --> inner_loop: ᐉ promoted
    experimentation --> refinement: ᐉ iterated
    incident_response --> mitigation: ᐉ mitigating
    incident_response --> postmortem: ᐉ stabilized
    inner_loop --> pr: ᐉ implementing
    inner_loop --> refinement: ⊙ ready_bounced
    inner_loop --> refinement: ⊡ spike_returned
    inner_loop --> release: ⊙ staged
    mitigation --> config_change: ᐉ execute_mitigation
    mitigation --> data_change: ᐉ execute_mitigation
    mitigation --> incident_response: ⊡ needs_verification
    mitigation --> inner_loop: ᐉ execute_mitigation
    postmortem --> inner_loop: ᐉ complete
    postmortem --> refinement: ᐉ complete
    pr --> inner_loop: ⊡ staged
    refinement --> inner_loop: ᐉ spiking
    refinement --> inner_loop: ⊙ ready_for_dev
    release --> experimentation: ⊙ measuring
    release --> mitigation: ⊡ mitigated
    experimentation --> [*]: ■ aborted
    refinement --> [*]: ■ duplicate
    refinement --> [*]: ■ wont_fix
    release --> [*]: ■ shipped
```

> Raw mermaid source in: [`process-map.mermaid`](./process-map.mermaid).

## External entry points

States where new issues materialize from outside the workflow — manual `create-issue --to <state>`, a webhook, or a scheduled job. These correspond to the `▶ <state>` edges from `[*]` on the process map above. Distinct from spawn / collect targets, which are reached via upstream work in another process; the framework enforces the two as mutually exclusive per state.

- [`incident-response`](./incident-response.md) · `declared` — alert / report triggers incident (external)
- [`inner-loop`](./inner-loop.md) · `ready_for_dev` — engineer files chore directly (skips refinement)
- [`refinement`](./refinement.md) · `raw` — issue created (external)

## Processes

- [`config-change`](./config-change.md) — Apply a configuration change as an incident mitigation: feature-flag toggle, kill switch, runtime config value, rate-limit adjustment. Linear claim/advance flow — spawned from `mitigation.execute_mitigation`, closes at `config_applied` to release the parent. Out of scope: schema changes that require migrations (use `data-change`).
- [`data-change`](./data-change.md) — Apply a data change as an incident mitigation: corruption repair, manual update, backfill, replay. Backup is mandatory before any mutation — the process serializes `creating_backup` → `backup_ready` → `applying_data_change` so the responder cannot skip the safety net. Spawned from `mitigation.execute_mitigation`, closes at `data_change_applied` to release the parent.
- [`experimentation`](./experimentation.md) — Measure a flag-gated experiment in production and reach a verdict — ship-to-all, kill, or iterate. Owned by the product owner once the dev work is merged. Entry is `measuring`, a shared handoff with `release` — when an experiment-typed contributor lands in a release that ships, `release.cut.collects.advance_on` cascades it here per the `experiment → measuring` per-type rule.
- [`incident-response`](./incident-response.md) — Coordinate the live response to a production incident: declare, mitigate, stabilize. Spawns a postmortem on stabilization.
- [`inner-loop`](./inner-loop.md) — The developer's day-to-day flow: claim a refined ticket, implement, open a PR. After merge, the ticket lands in `staged`, which is a shared handoff with `release` — from there the release process owns the lifecycle through to shipped. Spawns a PR child issue during implementing.
- [`mitigation`](./mitigation.md) — Plan and execute a mitigation strategy during an active incident. The responder drafts a plan, then dispatches one or more sub-mitigations — a hotfix, a configuration change, a data change, or any combination — each as a child issue on its own process. `execute_mitigation` declares a multi-spawn rule per mitigation type; the wait-for-all cascade closes this issue at `mitigated` only when every spawned child reaches its respective applied / shipped closing state, which in turn cascades the parent incident to `needs_verification`.
- [`postmortem`](./postmortem.md) — Document the timeline, root cause, and remediation for an incident. Spawned by incident-response on stabilization; closes at `complete`. On close, the PM files follow-ups (bug/chore/feature) on refinement via the closing-state spawn declaration — `workflow spawn-issue --issue-type bug --initial-state raw` (etc.) for each item.
- [`pr`](./pr.md) — The pull-request lifecycle: draft → review → merged. Spawned from inner-loop's implementing state.
- [`refinement`](./refinement.md) — Shape raw ideas and bug reports into ready-for-dev tickets. The product manager owns the queue, classifies issue type, and either marks ready or parks/kills. Chores skip this process and are filed directly on `inner-loop.ready_for_dev` — engineering hygiene work doesn't need PM refinement.
- [`release`](./release.md) — Cut, review, and ship a release train. Dev tickets (bug/feature/chore/experiment/hotfix) are handed off from `inner-loop.staged` and live here as contributors; a release ticket is created at `cut` (or `hotfix_cut`), collects the staged contributors, then runs review → deploy → released. On `released` the contributor tickets cascade to `shipped`; on `abandoned` / `rolled_back` they're returned to the queue for the next train.

## Shared resources

- [Roles](./roles.md)
- [Issue types](./issue-types.md)
- [Human inputs](./human-inputs.md)
