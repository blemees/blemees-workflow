# Process: postmortem

Document the timeline, root cause, and remediation for an incident. Spawned by incident-response on stabilization; closes when the writeup is approved.

> Defined in: `postmortem-states.json`

## Issue types accepted

- `postmortem` — **Postmortem**: Post-incident review work. Spawned at `incident.stabilized`; owns the narrative, root-cause integration, and follow-up filing. Distinct ticket from the incident itself so the postmortem can outlive the incident's close.

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    pending --> drafting: PM claims postmortem
    drafting --> ready_for_followups: PM completes postmortem narrative
    ready_for_followups --> creating_followups: PM claims for follow-up filing
    creating_followups --> complete: PM files compensating issues (spawn events to process refinement)
    complete --> [*]: terminal (resolved)
    [*] --> pending: spawn
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `pending` | resting | reversible-slow | — | — | — | — | — |
| `drafting` | working | — | product-manager | postmortem | — | — | — |
| `ready_for_followups` | resting | reversible-fast | — | — | — | — | — |
| `creating_followups` | working | — | product-manager | postmortem | — | — | — |
| `complete` | terminal | reversible-slow | — | — | — | resolved | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `pending` | `drafting` | claim | 'PM claims postmortem' | — | — |
| `drafting` | `ready_for_followups` | advance | 'PM completes postmortem narrative' | — | — |
| `ready_for_followups` | `creating_followups` | claim | 'PM claims for follow-up filing' | — | — |
| `creating_followups` | `complete` | advance | 'PM files compensating issues (spawn events to process refinement)' | — | — |
