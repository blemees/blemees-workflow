# Process: refinement

Shape raw ideas and bug reports into ready-for-dev tickets. The product manager owns the queue, classifies issue type, and either marks ready or parks/kills.

> Defined in: `refinement-states.json`

## Issue types accepted

- `bug` — **Bug**: Defect in shipped behavior — something is broken or wrong. Branch prefix `fix/`. Failing test first; reviewer scrutinizes root cause vs surface symptom.
- `chore` — **Chore**: Internal cleanup with no user-visible behavior change: refactors, dependency bumps, lint config. Branch prefix `chore/`. QA may be skipped at reviewer discretion.
- `experiment` — **Experiment**: Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.
- `feature` — **Feature**: New user-facing capability or enhancement to existing behavior. Branch prefix `feat/`. Standard inner-loop flow with no per-step variations.

## External entry points

States where new issues materialize from outside the workflow — manual `create-issue --to <state>`, a webhook, or a scheduled job. Distinct from spawn / collect targets, which are reached via upstream work in another process; the framework enforces the two as mutually exclusive per state.

- `raw` — issue created (external)

## State diagram

```mermaid
stateDiagram-v2
    direction TB
    %% Cross-process interfaces:
    %%   Handoff: ready_for_dev (shared resting state)
    %%   Handoff: ready_bounced (shared resting state)
    %%   Spawn:   spiking → process inner-loop (issue_type=spike, initial=ready_for_spike)
    %%

    [*] --> raw: issue created (external)
    raw --> refining: PM claims raw
    refining --> duplicate: PM marks duplicate
    refining --> wont_fix: PM marks won't-fix
    refining --> consult_requested: PM requests consult
    consult_requested --> consulting: consultant claims consult
    consulting --> consult_complete: consultant posts findings
    consult_complete --> refining: PM claims consult feedback
    consulting --> spiking: consultant requests spike sub-issue on inner-loop
    spiking --> spike_returned: spike sub-issue completed (auto from inner-loop)
    spike_returned --> consulting: consultant claims to process spike findings
    refining --> ready_for_dev: PM marks ready (feat/bug/chore/exp)
    ready_bounced --> refining: PM claims bounced issue
    refining --> deprioritized: PM parks issue
    ready_for_dev --> deprioritized: PM parks issue
    deprioritized --> refining: PM claims to re-refine (staleness check)
    deprioritized --> wont_fix: PM kills parked issue
    duplicate --> [*]: terminal (deduplicated)
    wont_fix --> [*]: terminal (abandoned)
    ready_for_dev --> [*]: handoff
    [*] --> ready_bounced: handoff
    [*] --> raw: spawn
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `raw` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `refining` | working | — | product-manager | bug, feature, chore, experiment | clarify-scope, needs-arch-review, needs-security-review, needs-ux-input, general | — | — |
| `consult_requested` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `consulting` | working | — | architect, designer, security-engineer | bug, feature, chore, experiment | needs-arch-review, needs-security-review, general | — | — |
| `consult_complete` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `spiking` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `spike_returned` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `ready_for_dev` | resting | reversible-slow | — | bug, feature, chore, experiment | — | — | — |
| `ready_bounced` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `deprioritized` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |
| `duplicate` | terminal | reversible-fast | — | — | — | deduplicated | not planned |
| `wont_fix` | terminal | reversible-fast | — | — | — | abandoned | not planned |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `raw` | `refining` | claim | 'PM claims raw' | — | — |
| `refining` | `duplicate` | advance | 'PM marks duplicate' | — | — |
| `refining` | `wont_fix` | advance | "PM marks won't-fix" | — | — |
| `refining` | `consult_requested` | advance | 'PM requests consult' | — | — |
| `consult_requested` | `consulting` | claim | 'consultant claims consult' | — | — |
| `consulting` | `consult_complete` | advance | 'consultant posts findings' | — | — |
| `consult_complete` | `refining` | claim | 'PM claims consult feedback' | — | — |
| `consulting` | `spiking` | advance | 'consultant requests spike sub-issue on inner-loop' | — | — |
| `spiking` | `spike_returned` | event | 'spike sub-issue completed (auto from inner-loop)' | — | — |
| `spike_returned` | `consulting` | claim | 'consultant claims to process spike findings' | — | — |
| `refining` | `ready_for_dev` | advance | 'PM marks ready (feat/bug/chore/exp)' | — | — |
| `ready_bounced` | `refining` | claim | 'PM claims bounced issue' | — | — |
| `refining` | `deprioritized` | advance | 'PM parks issue' | — | — |
| `ready_for_dev` | `deprioritized` | advance | 'PM parks issue' | — | — |
| `deprioritized` | `refining` | claim | 'PM claims to re-refine (staleness check)' | — | — |
| `deprioritized` | `wont_fix` | advance | 'PM kills parked issue' | — | — |

## Cross-process handoffs

**Handoff states** (shared resting states declared in ≥2 processes):

- `ready_for_dev` — interface state, also declared by the partner process(es).
- `ready_bounced` — interface state, also declared by the partner process(es).

**Spawns** (states that create child issues on other processes):

- `spiking` (independent) → process `inner-loop` as `spike` issue at `ready_for_spike`
    - on child `spike_completed` → parent `spike_returned`
