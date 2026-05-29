# Process: mitigation

Plan and execute a mitigation strategy during an active incident. The responder drafts a plan, then dispatches one or more sub-mitigations — a hotfix, a configuration change, a data change, or any combination — each as a child issue on its own process. `execute_mitigation` declares a multi-spawn rule per mitigation type; the wait-for-all cascade closes this issue at `mitigated` only when every spawned child reaches its respective applied / shipped terminal, which in turn cascades the parent incident to `needs_verification`.

> Defined in: `mitigation-states.json`

## Issue types accepted

- `incident-mitigation` — **Incident mitigation**: A mitigation work item spawned from an incident. The responder drafts a plan in `plan_mitigation` and then in `execute_mitigation` dispatches one or more sub-mitigations (any combination of `hotfix`, `config-change`, `data-change`) as child issues on their respective processes. Tied to the parent incident via `parent-of:<incident>`. Closes at `mitigated` only when every spawned child reaches its applied / shipped terminal — wait-for-all — at which point the parent incident cascades to `needs_verification`.

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    %% Cross-process interfaces:
    %%   Spawn:   execute_mitigation → process (derived from initial_state) (issue_type=hotfix, initial=ready_for_hotfix)
    %%   Spawn:   execute_mitigation → process (derived from initial_state) (issue_type=config-change, initial=ready_for_config_change)
    %%   Spawn:   execute_mitigation → process (derived from initial_state) (issue_type=data-change, initial=ready_for_data_change)
    %%

    ready_for_mitigation --> plan_mitigation: responder claims to plan mitigation
    plan_mitigation --> mitigation_planned: responder finalizes mitigation plan
    mitigation_planned --> execute_mitigation: responder claims to execute mitigation
    execute_mitigation --> mitigated: all spawned mitigations complete (auto via wait-for-all cascade)
    mitigated --> [*]: terminal (shipped)
    [*] --> ready_for_mitigation: spawn
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `ready_for_mitigation` | resting | reversible-fast | — | incident-mitigation | — | — | — |
| `plan_mitigation` | working | — | incident-responder | incident-mitigation | needs-security-review, general | — | — |
| `mitigation_planned` | resting | reversible-fast | — | incident-mitigation | — | — | — |
| `execute_mitigation` | working | — | incident-responder | incident-mitigation | — | — | — |
| `mitigated` | terminal | reversible-slow | — | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `ready_for_mitigation` | `plan_mitigation` | claim | 'responder claims to plan mitigation' | — | — |
| `plan_mitigation` | `mitigation_planned` | advance | 'responder finalizes mitigation plan' | — | — |
| `mitigation_planned` | `execute_mitigation` | claim | 'responder claims to execute mitigation' | — | — |
| `execute_mitigation` | `mitigated` | advance | 'all spawned mitigations complete (auto via wait-for-all cascade)' | — | — |

## Cross-process handoffs

**Spawns** (states that create child issues on other processes):

- `execute_mitigation` (subprocess) → process `(derived from initial_state)` as `hotfix` issue at `ready_for_hotfix`
    - on child `shipped` → parent `mitigated`
- `execute_mitigation` (subprocess) → process `(derived from initial_state)` as `config-change` issue at `ready_for_config_change`
    - on child `config_applied` → parent `mitigated`
- `execute_mitigation` (subprocess) → process `(derived from initial_state)` as `data-change` issue at `ready_for_data_change`
    - on child `data_change_applied` → parent `mitigated`
