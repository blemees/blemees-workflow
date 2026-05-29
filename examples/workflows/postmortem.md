# Process: postmortem

Document the timeline, root cause, and remediation for an incident. Spawned by incident-response on stabilization; closes at `complete`. On close, the PM files follow-ups (bug/chore/feature) on refinement via the terminal-state spawn declaration — `workflow spawn-issue --issue-type bug --initial-state raw` (etc.) for each item.

> Defined in: `postmortem-states.json`

## Issue types accepted

- `postmortem` — **Postmortem**: Post-incident review work. Spawned at `incident.stabilized`; owns the narrative, root-cause integration, and follow-up filing. Distinct ticket from the incident itself so the postmortem can outlive the incident's close.

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    %% Cross-process interfaces:
    %%   Spawn:   complete → process (derived from initial_state) (issue_type=bug, initial=raw)
    %%   Spawn:   complete → process (derived from initial_state) (issue_type=chore, initial=raw)
    %%   Spawn:   complete → process (derived from initial_state) (issue_type=feature, initial=raw)
    %%

    pending --> drafting: PM claims postmortem
    drafting --> complete: PM closes postmortem (then files follow-ups on refinement)
    complete --> [*]: terminal (resolved)
    [*] --> pending: spawn
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `pending` | resting | reversible-slow | — | postmortem | — | — | — |
| `drafting` | working | — | product-manager | postmortem | — | — | — |
| `complete` | terminal | reversible-slow | — | — | — | resolved | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `pending` | `drafting` | claim | 'PM claims postmortem' | — | — |
| `drafting` | `complete` | advance | 'PM closes postmortem (then files follow-ups on refinement)' | — | — |

## Cross-process handoffs

**Spawns** (states that create child issues on other processes):

- `complete` (independent) → process `(derived from initial_state)` as `bug` issue at `raw`
- `complete` (independent) → process `(derived from initial_state)` as `chore` issue at `raw`
- `complete` (independent) → process `(derived from initial_state)` as `feature` issue at `raw`
