# Process: experimentation

Measure a flag-gated experiment in production and reach a verdict — ship-to-all, kill, or iterate. Owned by the product owner once the dev work is merged.

> Defined in: `experimentation-states.json`

## Issue types accepted

- `experiment` — **Experiment**: Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    measuring --> measurement_complete: measurement window elapses (time)
    measuring --> aborting: PO claims to abort
    aborting --> aborted: PO publishes abort reason
    measurement_complete --> analyzing: PO claims experiment for analysis
    analyzing --> measuring: PO extends experiment, resets window
    analyzing --> promoted: PO promotes experiment to feature (files promotion chore on process refinement)
    analyzing --> killed: PO kills experiment (files cleanup chore on process refinement)
    analyzing --> iterated: PO requests experiment iteration (files new experiment on process refinement)
    promoted --> [*]: terminal (shipped)
    killed --> [*]: terminal (abandoned)
    iterated --> [*]: terminal (superseded)
    aborted --> [*]: terminal (abandoned)
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `measuring` | resting | reversible-slow | — | — | — | — | — |
| `measurement_complete` | resting | reversible-fast | — | — | — | — | — |
| `analyzing` | working | — | product-owner | experiment | — | — | — |
| `aborting` | working | — | product-owner | experiment | — | — | — |
| `promoted` | terminal | reversible-slow | — | — | — | shipped | completed |
| `killed` | terminal | reversible-slow | — | — | — | abandoned | not planned |
| `iterated` | terminal | reversible-fast | — | — | — | superseded | not planned |
| `aborted` | terminal | reversible-fast | — | — | — | abandoned | not planned |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `measuring` | `measurement_complete` | event | 'measurement window elapses (time)' | — | — |
| `measuring` | `aborting` | claim | 'PO claims to abort' | — | — |
| `aborting` | `aborted` | advance | 'PO publishes abort reason' | — | — |
| `measurement_complete` | `analyzing` | claim | 'PO claims experiment for analysis' | — | — |
| `analyzing` | `measuring` | advance | 'PO extends experiment, resets window' | — | — |
| `analyzing` | `promoted` | advance | 'PO promotes experiment to feature (files promotion chore on process refinement)' | — | — |
| `analyzing` | `killed` | advance | 'PO kills experiment (files cleanup chore on process refinement)' | — | — |
| `analyzing` | `iterated` | advance | 'PO requests experiment iteration (files new experiment on process refinement)' | — | — |
