# Process: inner-loop

> Defined in: `inner-loop-states.json`

## Issue types accepted

- `bug` — **Bug**: Defect in shipped behavior — something is broken or wrong. Branch prefix `fix/`. Failing test first; reviewer scrutinizes root cause vs surface symptom.
- `chore` — **Chore**: Internal cleanup with no user-visible behavior change: refactors, dependency bumps, lint config. Branch prefix `chore/`. QA may be skipped at reviewer discretion.
- `experiment` — **Experiment**: Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.
- `feature` — **Feature**: New user-facing capability or enhancement to existing behavior. Branch prefix `feat/`. Standard inner-loop flow with no per-step variations.
- `hotfix` — **Hotfix**: Compressed inner-loop work for urgent production fixes during active incidents. Branch prefix `hotfix/`. Spawned by mitigation under IC authority; skips refinement; QA may be bypassed.
- `spike` — **Spike**: Time-boxed investigation. Branch prefix `spike/`. Deliverable is a findings doc, not merged code — the PR is never merged. Follow-ups re-enter refinement.

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Entry (shared): ready_for_dev from process refinement
    %%   Entry (shared): ready_for_experiment from process refinement
    %%   Entry (spawn ): ready_for_spike from process refinement
    %%   Entry (shared): ready_for_hotfix from process mitigation
    %%   Exit  (shared): ready_bounced to process refinement
    %%   Exit  (spawn ): staged to process release
    %%   Exit  (spawn ): staged_experiment to process release
    %%   Exit  (spawn ): staged_hotfix to process release
    %%

    [*] --> ready_for_dev: from process refinement
    [*] --> ready_for_experiment: from process refinement
    [*] --> ready_for_spike: from process refinement (spawn — consultant creates spike sub-issue)
    [*] --> ready_for_hotfix: from process mitigation
    ready_for_dev --> implementing: developer claims issue
    ready_for_experiment --> implementing_experiment: developer claims experiment
    ready_for_spike --> implementing_spike: developer claims spike
    ready_for_hotfix --> implementing_hotfix: developer claims hotfix
    implementing --> ready_bounced: developer bounces ticket
    implementing --> pr_review: developer requests PR review
    implementing_experiment --> pr_review_experiment: developer requests PR review
    implementing_hotfix --> pr_review_hotfix: developer requests PR review
    implementing_spike --> spike_completed: developer posts spike findings
    pr_review --> staged: from process pr-review (PR merged)
    pr_review_experiment --> staged_experiment: from process pr-review (PR merged)
    pr_review_hotfix --> staged_hotfix: from process pr-review (PR merged)
    ready_bounced --> [*]: to process refinement
    staged --> [*]: to process release (standard train)
    staged_experiment --> [*]: to process release (standard train)
    staged_hotfix --> [*]: to process release (hotfix patch train)
    spike_completed --> [*]: terminal (resolved)

    note left of ready_for_dev: reversible-slow
    note right of ready_for_experiment: reversible-slow
    note right of ready_for_spike: reversible-slow
    note right of ready_for_hotfix: reversible-fast
    note right of implementing: role=developer, types=bug, feature, chore
    note right of implementing_experiment: role=developer, types=experiment
    note right of implementing_spike: role=developer, types=spike
    note right of implementing_hotfix: role=developer, types=hotfix
    note right of pr_review: reversible-fast
    note right of pr_review_experiment: reversible-fast
    note right of pr_review_hotfix: reversible-fast
    note right of staged: reversible-slow
    note right of staged_experiment: reversible-slow
    note right of staged_hotfix: reversible-slow
    note right of spike_completed: reversible-fast
    note right of ready_bounced: reversible-fast
```

## States

| Name | Class | Reversibility | Roles | Issue types | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|
| `ready_for_dev` | resting | reversible-slow | — | — | — | — |
| `ready_for_experiment` | resting | reversible-slow | — | — | — | — |
| `ready_for_spike` | resting | reversible-slow | — | — | — | — |
| `ready_for_hotfix` | resting | reversible-fast | — | — | — | — |
| `implementing` | working | — | developer | bug, feature, chore | — | — |
| `implementing_experiment` | working | — | developer | experiment | — | — |
| `implementing_spike` | working | — | developer | spike | — | — |
| `implementing_hotfix` | working | — | developer | hotfix | — | — |
| `pr_review` | resting | reversible-fast | — | — | — | — |
| `pr_review_experiment` | resting | reversible-fast | — | — | — | — |
| `pr_review_hotfix` | resting | reversible-fast | — | — | — | — |
| `staged` | resting | reversible-slow | — | — | — | — |
| `staged_experiment` | resting | reversible-slow | — | — | — | — |
| `staged_hotfix` | resting | reversible-slow | — | — | — | — |
| `spike_completed` | terminal | reversible-fast | — | — | resolved | completed |
| `ready_bounced` | resting | reversible-fast | — | — | — | — |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `ready_for_dev` | cross_process | 'from process refinement' | — | — |
| `[*]` | `ready_for_experiment` | cross_process | 'from process refinement' | — | — |
| `[*]` | `ready_for_spike` | cross_process | 'from process refinement (spawn — consultant creates spike sub-issue)' | — | — |
| `[*]` | `ready_for_hotfix` | cross_process | 'from process mitigation' | — | — |
| `ready_for_dev` | `implementing` | claim | 'developer claims issue' | — | — |
| `ready_for_experiment` | `implementing_experiment` | claim | 'developer claims experiment' | — | — |
| `ready_for_spike` | `implementing_spike` | claim | 'developer claims spike' | — | — |
| `ready_for_hotfix` | `implementing_hotfix` | claim | 'developer claims hotfix' | — | — |
| `implementing` | `ready_bounced` | role_action | 'developer bounces ticket' | — | — |
| `implementing` | `pr_review` | role_action | 'developer requests PR review' | — | — |
| `implementing_experiment` | `pr_review_experiment` | role_action | 'developer requests PR review' | — | — |
| `implementing_hotfix` | `pr_review_hotfix` | role_action | 'developer requests PR review' | — | — |
| `implementing_spike` | `spike_completed` | role_action | 'developer posts spike findings' | — | — |
| `pr_review` | `staged` | external | 'from process pr-review (PR merged)' | — | — |
| `pr_review_experiment` | `staged_experiment` | external | 'from process pr-review (PR merged)' | — | — |
| `pr_review_hotfix` | `staged_hotfix` | external | 'from process pr-review (PR merged)' | — | — |
| `ready_bounced` | `[*]` | cross_process | 'to process refinement' | — | — |
| `staged` | `[*]` | cross_process | 'to process release (standard train)' | — | — |
| `staged_experiment` | `[*]` | cross_process | 'to process release (standard train)' | — | — |
| `staged_hotfix` | `[*]` | cross_process | 'to process release (hotfix patch train)' | — | — |

## Cross-process handoffs

**Entries** (issues arriving from other processes):

- `ready_for_dev` ← process `refinement` (shared) — `from process refinement`
- `ready_for_experiment` ← process `refinement` (shared) — `from process refinement`
- `ready_for_spike` ← process `refinement` (spawn) — `from process refinement (spawn — consultant creates spike sub-issue)`
- `ready_for_hotfix` ← process `mitigation` (shared) — `from process mitigation`

**Exits** (issues handed to other processes):

- `ready_bounced` → process `refinement` (shared) — `to process refinement`
- `staged` → process `release` (spawn) — `to process release (standard train)`
- `staged_experiment` → process `release` (spawn) — `to process release (standard train)`
- `staged_hotfix` → process `release` (spawn) — `to process release (hotfix patch train)`
