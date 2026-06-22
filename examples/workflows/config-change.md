# Process: config-change

Apply a configuration change as an incident mitigation: feature-flag toggle, kill switch, runtime config value, rate-limit adjustment. Linear claim/advance flow — spawned from `mitigation.execute_mitigation`, closes at `config_applied` to release the parent. Out of scope: schema changes that require migrations (use `data-change`).

> Defined in: `config-change-states.json`

## Issue types accepted

- `config-change` — **Config Change**: A runtime configuration change for an incident mitigation: feature-flag toggle, kill switch, config-value tweak, rate-limit adjustment. No code change, no PR. Closes at `config_applied`, cascading the parent mitigation toward `mitigated`.

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    ready_for_config_change --> applying_config_change: responder claims config change
    applying_config_change --> config_applied: responder applies and confirms config change is live
    config_applied --> [*]: ⊡ mitigated
    [*] --> ready_for_config_change: ᐉ execute_mitigation
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Closure taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `ready_for_config_change` | resting | reversible-fast | — | config-change | — | — | — |
| `applying_config_change` | working | — | incident-responder | config-change | clarify-scope, needs-security-review, blocked-on-data, general | — | — |
| `config_applied` | resting | reversible-fast | — | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `ready_for_config_change` | `applying_config_change` | claim | 'responder claims config change' | — | — |
| `applying_config_change` | `config_applied` | advance | 'responder applies and confirms config change is live' | — | — |

## Cross-process interfaces

### Inbound

| State | Kind | From | Detail |
|---|---|---|---|
| `ready_for_config_change` | ᐉ spawn | [`mitigation`](./mitigation.md) · `execute_mitigation` | `config-change` issue |

### Outbound

| State | Kind | To | Detail |
|---|---|---|---|
| `config_applied` | ⊡ feedback | [`mitigation`](./mitigation.md) | advances parent to `mitigated` (spawn from `execute_mitigation`, `config-change`) |
