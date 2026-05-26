# Process: refinement

> Defined in: `refinement-states.json`

## Issue types accepted

- `bug` — **Bug**: Defect in shipped behavior — something is broken or wrong. Branch prefix `fix/`. Failing test first; reviewer scrutinizes root cause vs surface symptom.
- `chore` — **Chore**: Internal cleanup with no user-visible behavior change: refactors, dependency bumps, lint config. Branch prefix `chore/`. QA may be skipped at reviewer discretion.
- `experiment` — **Experiment**: Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.
- `feature` — **Feature**: New user-facing capability or enhancement to existing behavior. Branch prefix `feat/`. Standard inner-loop flow with no per-step variations.

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Handoff: ready_for_dev (shared resting state)
    %%   Handoff: ready_for_experiment (shared resting state)
    %%   Handoff: ready_bounced (shared resting state)
    %%

    [*] --> raw: issue created (external)
    raw --> refining: PM claims raw
    refining --> duplicate: PM marks duplicate
    refining --> wont_fix: PM marks won't-fix
    refining --> consult_requested: PM requests consult
    consult_requested --> consulting: consultant claims consult
    consulting --> consult_complete: consultant posts findings
    consult_complete --> refining: PM claims consult feedback
    consulting --> spiking: consultant creates spike sub-issue (spawns spike to inner-loop ready_for_spike)
    spiking --> consulting: consultant claims spike findings
    refining --> ready_for_dev: PM marks ready (feat/bug/chore)
    refining --> ready_for_experiment: PM marks ready (exp)
    ready_bounced --> refining: PM claims bounced issue
    refining --> deprioritized: PM parks issue
    ready_for_dev --> deprioritized: PM parks issue
    ready_for_experiment --> deprioritized: PM parks issue
    deprioritized --> refining: PM claims to re-refine (staleness check)
    deprioritized --> wont_fix: PM kills parked issue
    duplicate --> [*]: terminal (deduplicated)
    wont_fix --> [*]: terminal (abandoned)

    note left of raw: reversible-fast
    note right of refining: role=product-manager, types=bug, feature, chore, experiment
    note right of consult_requested: reversible-fast
    note right of consulting: roles=architect, designer, security-engineer, types=bug, feature, chore, experiment
    note right of consult_complete: reversible-fast
    note right of spiking: reversible-fast
    note right of ready_for_dev: handoff, reversible-slow
    note right of ready_for_experiment: handoff, reversible-slow
    note right of ready_bounced: handoff, reversible-fast
    note right of deprioritized: reversible-fast
    note right of duplicate: reversible-fast
    note right of wont_fix: reversible-fast
```

## States

| Name | Class | Reversibility | Roles | Issue types | Input topics | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `raw` | resting | reversible-fast | — | — | — | — | — |
| `refining` | working | — | product-manager | bug, feature, chore, experiment | clarify-scope, needs-arch-review, needs-security-review, needs-ux-input, general | — | — |
| `consult_requested` | resting | reversible-fast | — | — | — | — | — |
| `consulting` | working | — | architect, designer, security-engineer | bug, feature, chore, experiment | needs-arch-review, needs-security-review, general | — | — |
| `consult_complete` | resting | reversible-fast | — | — | — | — | — |
| `spiking` | resting | reversible-fast | — | — | — | — | — |
| `ready_for_dev` | resting | reversible-slow | — | — | — | — | — |
| `ready_for_experiment` | resting | reversible-slow | — | — | — | — | — |
| `ready_bounced` | resting | reversible-fast | — | — | — | — | — |
| `deprioritized` | resting | reversible-fast | — | — | — | — | — |
| `duplicate` | terminal | reversible-fast | — | — | — | deduplicated | not planned |
| `wont_fix` | terminal | reversible-fast | — | — | — | abandoned | not planned |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `raw` | event | 'issue created (external)' | — | — |
| `raw` | `refining` | claim | 'PM claims raw' | — | — |
| `refining` | `duplicate` | advance | 'PM marks duplicate' | — | — |
| `refining` | `wont_fix` | advance | "PM marks won't-fix" | — | — |
| `refining` | `consult_requested` | advance | 'PM requests consult' | — | — |
| `consult_requested` | `consulting` | claim | 'consultant claims consult' | — | — |
| `consulting` | `consult_complete` | advance | 'consultant posts findings' | — | — |
| `consult_complete` | `refining` | claim | 'PM claims consult feedback' | — | — |
| `consulting` | `spiking` | advance | 'consultant creates spike sub-issue (spawns spike to inner-loop ready_for_spike)' | — | — |
| `spiking` | `consulting` | claim | 'consultant claims spike findings' | — | — |
| `refining` | `ready_for_dev` | advance | 'PM marks ready (feat/bug/chore)' | — | — |
| `refining` | `ready_for_experiment` | advance | 'PM marks ready (exp)' | — | — |
| `ready_bounced` | `refining` | claim | 'PM claims bounced issue' | — | — |
| `refining` | `deprioritized` | advance | 'PM parks issue' | — | — |
| `ready_for_dev` | `deprioritized` | advance | 'PM parks issue' | — | — |
| `ready_for_experiment` | `deprioritized` | advance | 'PM parks issue' | — | — |
| `deprioritized` | `refining` | claim | 'PM claims to re-refine (staleness check)' | — | — |
| `deprioritized` | `wont_fix` | advance | 'PM kills parked issue' | — | — |

## Cross-process handoffs

**Handoff states** (shared resting states declared in ≥2 processes):

- `ready_for_dev` — interface state, also declared by the partner process(es).
- `ready_for_experiment` — interface state, also declared by the partner process(es).
- `ready_bounced` — interface state, also declared by the partner process(es).
