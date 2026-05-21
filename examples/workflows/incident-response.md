# Process: incident-response

> Defined in: `incident-response-states.json`

## State diagram

```mermaid
stateDiagram-v2
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

    note right of triaging: role=incident-commander
    note right of diagnosing: role=incident-responder
    note right of mitigating: role=incident-commander
    note right of verifying: role=incident-responder
    note right of stabilized: reversible-fast
```

## States

| Name | Class | Reversibility | Roles | Issue types | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|
| `declared` | resting | — | — | — | — | — |
| `triaging` | working | — | incident-commander | — | — | — |
| `needs_diagnosis` | resting | — | — | — | — | — |
| `diagnosing` | working | — | incident-responder | — | — | — |
| `cause_identified` | resting | — | — | — | — | — |
| `mitigating` | working | — | incident-commander | — | — | — |
| `needs_verification` | resting | — | — | — | — | — |
| `verifying` | working | — | incident-responder | — | — | — |
| `stabilized` | terminal | reversible-fast | — | — | superseded | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `declared` | external | 'alert / report triggers incident (external)' | — | — |
| `declared` | `triaging` | claim | 'IC claims incident' | — | — |
| `triaging` | `needs_diagnosis` | role_action | 'IC assigns severity, assigns responder' | — | — |
| `needs_diagnosis` | `diagnosing` | claim | 'responder claims diagnosis' | — | — |
| `diagnosing` | `cause_identified` | role_action | 'responder reports root cause' | — | — |
| `cause_identified` | `mitigating` | claim | 'IC claims incident for mitigation' | — | — |
| `mitigating` | `needs_verification` | role_action | 'IC requests verification (spawns work items on process mitigation)' | — | — |
| `needs_verification` | `verifying` | claim | 'responder claims verification' | — | — |
| `verifying` | `cause_identified` | role_action | 'responder reports mitigation failed' | — | — |
| `verifying` | `stabilized` | role_action | 'responder confirms production stable (spawns postmortem on process postmortem)' | — | — |
