# Process: incident-response

> Defined in: `incident-response-states.json`

## Issue types accepted

- `incident` — **Incident**: Live production incident. Opened by the IC at declaration; carries through incident-response and any mitigation work on the same ticket. Closes at `stabilized`; postmortem is a separate (spawned) ticket.

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Spawn:   stabilized → process postmortem (issue_type=postmortem, initial=pending)
    %%

    [*] --> declared: alert / report triggers incident (external)
    declared --> triaging: IC claims incident
    triaging --> needs_diagnosis: IC assigns severity, assigns responder
    needs_diagnosis --> diagnosing: responder claims diagnosis
    diagnosing --> cause_identified: responder reports root cause
    cause_identified --> mitigating: IC claims incident for mitigation
    mitigating --> needs_verification: IC requests verification (spawns work items on process mitigation)
    needs_verification --> verifying: responder claims verification
    verifying --> cause_identified: responder reports mitigation failed
    verifying --> stabilized: responder confirms production stable (spawns postmortem on process postmortem)
    stabilized --> [*]: terminal (superseded)

    note left of declared: reversible-fast
    note right of triaging: role=incident-commander, types=incident
    note right of needs_diagnosis: reversible-fast
    note right of diagnosing: role=incident-responder, types=incident
    note right of cause_identified: reversible-fast
    note right of mitigating: role=incident-commander, types=incident
    note right of needs_verification: reversible-fast
    note right of verifying: role=incident-responder, types=incident
    note right of stabilized: reversible-fast
```

## States

| Name | Class | Reversibility | Roles | Issue types | Input topics | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `declared` | resting | reversible-fast | — | — | — | — | — |
| `triaging` | working | — | incident-commander | incident | — | — | — |
| `needs_diagnosis` | resting | reversible-fast | — | — | — | — | — |
| `diagnosing` | working | — | incident-responder | incident | blocked-on-data, general | — | — |
| `cause_identified` | resting | reversible-fast | — | — | — | — | — |
| `mitigating` | working | — | incident-commander | incident | needs-security-review, blocked-on-data, general | — | — |
| `needs_verification` | resting | reversible-fast | — | — | — | — | — |
| `verifying` | working | — | incident-responder | incident | — | — | — |
| `stabilized` | terminal | reversible-fast | — | — | — | superseded | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `declared` | event | 'alert / report triggers incident (external)' | — | — |
| `declared` | `triaging` | claim | 'IC claims incident' | — | — |
| `triaging` | `needs_diagnosis` | advance | 'IC assigns severity, assigns responder' | — | — |
| `needs_diagnosis` | `diagnosing` | claim | 'responder claims diagnosis' | — | — |
| `diagnosing` | `cause_identified` | advance | 'responder reports root cause' | — | — |
| `cause_identified` | `mitigating` | claim | 'IC claims incident for mitigation' | — | — |
| `mitigating` | `needs_verification` | advance | 'IC requests verification (spawns work items on process mitigation)' | — | — |
| `needs_verification` | `verifying` | claim | 'responder claims verification' | — | — |
| `verifying` | `cause_identified` | advance | 'responder reports mitigation failed' | — | — |
| `verifying` | `stabilized` | advance | 'responder confirms production stable (spawns postmortem on process postmortem)' | — | — |

## Cross-process handoffs

**Spawns** (states that create child issues on other processes):

- `stabilized` (independent) → process `postmortem` as `postmortem` issue at `pending`
