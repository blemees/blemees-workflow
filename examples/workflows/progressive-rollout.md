# Process: progressive-rollout

> Defined in: `progressive-rollout-states.json`

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Entry (spawn ): soaking from process release
    %%

    [*] --> soaking: from process release (first stage activated)
    soaking --> ready_for_stage_analysis: soak window elapses (time)
    ready_for_stage_analysis --> analyzing: on-call claims stage analysis
    analyzing --> soaking: on-call advances to next stage
    analyzing --> complete: on-call confirms final stage healthy
    analyzing --> holding: on-call pauses rollout
    analyzing --> killing: on-call claims to kill rollout
    holding --> analyzing: on-call re-claims held rollout
    killing --> kill_switched: on-call disables flag, files compensating bug
    complete --> [*]: terminal (shipped)
    kill_switched --> [*]: terminal (reverted)

    note right of analyzing: role=developer
    note right of complete: reversible-slow
    note right of kill_switched: reversible-slow
```

## States

| Name | Class | Reversibility | Roles | Issue types | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|
| `soaking` | resting | — | — | — | — | — |
| `ready_for_stage_analysis` | resting | — | — | — | — | — |
| `analyzing` | working | — | developer | — | — | — |
| `holding` | resting | — | — | — | — | — |
| `killing` | working | — | — | — | — | — |
| `complete` | terminal | reversible-slow | — | — | shipped | completed |
| `kill_switched` | terminal | reversible-slow | — | — | reverted | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `soaking` | cross_process | 'from process release (first stage activated)' | — | — |
| `soaking` | `ready_for_stage_analysis` | external | 'soak window elapses (time)' | — | — |
| `ready_for_stage_analysis` | `analyzing` | claim | 'on-call claims stage analysis' | — | — |
| `analyzing` | `soaking` | role_action | 'on-call advances to next stage' | — | — |
| `analyzing` | `complete` | role_action | 'on-call confirms final stage healthy' | — | — |
| `analyzing` | `holding` | role_action | 'on-call pauses rollout' | — | — |
| `analyzing` | `killing` | role_action | 'on-call claims to kill rollout' | — | — |
| `holding` | `analyzing` | claim | 'on-call re-claims held rollout' | — | — |
| `killing` | `kill_switched` | role_action | 'on-call disables flag, files compensating bug' | — | — |

## Cross-process handoffs

**Entries** (issues arriving from other processes):

- `soaking` ← process `release` (spawn) — `from process release (first stage activated)`
