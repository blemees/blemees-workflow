# Process: release

> Defined in: `release-states.json`

## Issue types accepted

- `release` — **Release**: A release-train work item. Tracks one cut through prep → deploy → monitor; experimentation and progressive-rollout are phases that operate on the same release ticket.

## State diagram

```mermaid
stateDiagram-v2
    [*] --> accumulating: new train opens (on cadence, or after previous release closes)
    accumulating --> cut: cadence timer elapses (time)
    accumulating --> cut: RM triggers cut script
    cut --> preparing: RM claims train
    preparing --> ready_for_release_decision: RM publishes release notes, tags release candidate
    ready_for_release_decision --> reviewing_release: PO claims train
    reviewing_release --> gated_nogo: PO defers with blocking reason
    reviewing_release --> deploying: PO approves go (or IC approves for patch train)
    gated_nogo --> reviewing_release: PO re-claims deferred train
    gated_nogo --> abandoning: PO claims to abandon train
    abandoning --> abandoned: PO closes train, issues revert to inner-loop.staged for next train
    deploying --> rolling_out: production deploy completes (external — progressive rollout starts)
    rolling_out --> ready_for_monitoring: rollout reaches 100% (from process progressive-rollout)
    ready_for_monitoring --> monitoring: on-call claims post-deploy watch
    monitoring --> released: on-call confirms post-deploy window clean
    monitoring --> rolling_back: on-call triggers rollback
    rolling_back --> rolled_back: rollback completes
    abandoned --> [*]: terminal (abandoned)
    rolled_back --> [*]: terminal (reverted)
    released --> [*]: terminal (shipped)

    note left of accumulating: reversible-fast
    note right of cut: reversible-slow
    note right of preparing: role=release-manager, types=release
    note right of ready_for_release_decision: reversible-slow
    note right of reviewing_release: role=product-owner, types=release
    note right of gated_nogo: reversible-fast
    note right of abandoning: role=product-owner, types=release
    note right of abandoned: reversible-fast
    note right of deploying: reversible-slow
    note right of rolling_out: reversible-slow
    note right of ready_for_monitoring: reversible-slow
    note right of monitoring: role=developer, types=release
    note right of rolling_back: roles=incident-commander, release-manager, types=release
    note right of rolled_back: reversible-slow
    note right of released: reversible-slow
```

## States

| Name | Class | Reversibility | Roles | Issue types | Input topics | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `accumulating` | resting | reversible-fast | — | — | — | — | — |
| `cut` | resting | reversible-slow | — | — | — | — | — |
| `preparing` | working | — | release-manager | release | — | — | — |
| `ready_for_release_decision` | resting | reversible-slow | — | — | — | — | — |
| `reviewing_release` | working | — | product-owner | release | — | — | — |
| `gated_nogo` | resting | reversible-fast | — | — | — | — | — |
| `abandoning` | working | — | product-owner | release | — | — | — |
| `abandoned` | terminal | reversible-fast | — | — | — | abandoned | not planned |
| `deploying` | resting | reversible-slow | — | — | — | — | — |
| `rolling_out` | resting | reversible-slow | — | — | — | — | — |
| `ready_for_monitoring` | resting | reversible-slow | — | — | — | — | — |
| `monitoring` | working | — | developer | release | — | — | — |
| `rolling_back` | working | — | incident-commander, release-manager | release | — | — | — |
| `rolled_back` | terminal | reversible-slow | — | — | — | reverted | completed |
| `released` | terminal | reversible-slow | — | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `accumulating` | event | 'new train opens (on cadence, or after previous release closes)' | — | — |
| `accumulating` | `cut` | event | 'cadence timer elapses (time)' | — | — |
| `accumulating` | `cut` | event | 'RM triggers cut script' | — | — |
| `cut` | `preparing` | claim | 'RM claims train' | — | — |
| `preparing` | `ready_for_release_decision` | advance | 'RM publishes release notes, tags release candidate' | — | — |
| `ready_for_release_decision` | `reviewing_release` | claim | 'PO claims train' | — | — |
| `reviewing_release` | `gated_nogo` | advance | 'PO defers with blocking reason' | — | — |
| `reviewing_release` | `deploying` | advance | 'PO approves go (or IC approves for patch train)' | — | — |
| `gated_nogo` | `reviewing_release` | claim | 'PO re-claims deferred train' | — | — |
| `gated_nogo` | `abandoning` | claim | 'PO claims to abandon train' | — | — |
| `abandoning` | `abandoned` | advance | 'PO closes train, issues revert to inner-loop.staged for next train' | — | — |
| `deploying` | `rolling_out` | event | 'production deploy completes (external — progressive rollout starts)' | — | — |
| `rolling_out` | `ready_for_monitoring` | event | 'rollout reaches 100% (from process progressive-rollout)' | — | — |
| `ready_for_monitoring` | `monitoring` | claim | 'on-call claims post-deploy watch' | — | — |
| `monitoring` | `released` | advance | 'on-call confirms post-deploy window clean' | — | — |
| `monitoring` | `rolling_back` | advance | 'on-call triggers rollback' | — | — |
| `rolling_back` | `rolled_back` | advance | 'rollback completes' | — | — |
