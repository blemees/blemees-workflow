# Process: postmortem

> Defined in: `postmortem-states.json`

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Entry (spawn ): pending from process incident-response
    %%

    [*] --> pending: from process incident-response (incident stabilized)
    pending --> drafting: PM claims postmortem
    drafting --> ready_for_followups: PM completes postmortem narrative
    ready_for_followups --> creating_followups: PM claims for follow-up filing
    creating_followups --> complete: PM files compensating issues (spawn events to process refinement)
    complete --> [*]: terminal (resolved)

    note left of pending: claim-role=pm, reversible-slow
    note right of ready_for_followups: claim-role=pm
    note right of complete: reversible-slow
```

## States

| Name | Class | Reversibility | Claim role | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|
| `pending` | resting | reversible-slow | pm | — | — |
| `drafting` | working | — | — | — | — |
| `ready_for_followups` | resting | — | pm | — | — |
| `creating_followups` | working | — | — | — | — |
| `complete` | terminal | reversible-slow | — | resolved | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `pending` | cross_process | 'from process incident-response (incident stabilized)' | — | — |
| `pending` | `drafting` | claim | 'PM claims postmortem' | — | — |
| `drafting` | `ready_for_followups` | role_action | 'PM completes postmortem narrative' | — | — |
| `ready_for_followups` | `creating_followups` | claim | 'PM claims for follow-up filing' | — | — |
| `creating_followups` | `complete` | role_action | 'PM files compensating issues (spawn events to process refinement)' | — | — |

## Cross-process handoffs

**Entries** (issues arriving from other processes):

- `pending` ← process `incident-response` (spawn) — `from process incident-response (incident stabilized)`
