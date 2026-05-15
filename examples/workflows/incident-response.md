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
    stabilized --> [*]: terminal (stabilized)

    note left of declared: claim-role=incident-commander
    note right of needs_diagnosis: claim-role=responder
    note right of cause_identified: claim-role=incident-commander
    note right of needs_verification: claim-role=responder
    note right of stabilized: reversible-fast
```

## States

| Name | Class | Reversibility | Claim role | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|
| `declared` | resting | — | incident-commander | — | — |
| `triaging` | working | — | — | — | — |
| `needs_diagnosis` | resting | — | responder | — | — |
| `diagnosing` | working | — | — | — | — |
| `cause_identified` | resting | — | incident-commander | — | — |
| `mitigating` | working | — | — | — | — |
| `needs_verification` | resting | — | responder | — | — |
| `verifying` | working | — | — | — | — |
| `stabilized` | terminal | reversible-fast | — | stabilized | completed |

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
