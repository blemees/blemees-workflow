# Process: mitigation

> Defined in: `mitigation-states.json`

## Issue types accepted

- `incident` — **Incident**: Live production incident. Opened by the IC at declaration; carries through incident-response and any mitigation work on the same ticket. Closes at `stabilized`; postmortem is a separate (spawned) ticket.

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Handoff: ready_for_hotfix (shared resting state)
    %%

    ready_for_rollback --> rolling_back: responder claims rollback
    rolling_back --> rolled_back: responder completes rollback
    ready_for_flag_toggle --> toggling_flag: responder claims flag toggle
    toggling_flag --> flag_toggled: responder completes toggle
    rolled_back --> [*]: terminal (shipped)
    flag_toggled --> [*]: terminal (shipped)

    note right of ready_for_rollback: reversible-fast
    note right of ready_for_flag_toggle: reversible-fast
    note right of ready_for_hotfix: handoff, reversible-fast
    note right of rolling_back: role=incident-responder, types=incident
    note right of rolled_back: reversible-slow
    note right of toggling_flag: role=incident-responder, types=incident
    note right of flag_toggled: reversible-slow
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `ready_for_rollback` | resting | reversible-fast | — | — | — | — | — |
| `ready_for_flag_toggle` | resting | reversible-fast | — | — | — | — | — |
| `ready_for_hotfix` | resting | reversible-fast | — | — | — | — | — |
| `rolling_back` | working | — | incident-responder | incident | — | — | — |
| `rolled_back` | terminal | reversible-slow | — | — | — | shipped | completed |
| `toggling_flag` | working | — | incident-responder | incident | — | — | — |
| `flag_toggled` | terminal | reversible-slow | — | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `ready_for_rollback` | `rolling_back` | claim | 'responder claims rollback' | — | — |
| `rolling_back` | `rolled_back` | advance | 'responder completes rollback' | — | — |
| `ready_for_flag_toggle` | `toggling_flag` | claim | 'responder claims flag toggle' | — | — |
| `toggling_flag` | `flag_toggled` | advance | 'responder completes toggle' | — | — |

## Cross-process handoffs

**Handoff states** (shared resting states declared in ≥2 processes):

- `ready_for_hotfix` — interface state, also declared by the partner process(es).
