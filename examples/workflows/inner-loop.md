# Process: inner-loop

> Defined in: `inner-loop-states.json`

## Issue types accepted

- `bug` — **Bug**: Defect in shipped behavior — something is broken or wrong. Branch prefix `fix/`. Failing test first; reviewer scrutinizes root cause vs surface symptom.
- `feature` — **Feature**: New user-facing capability or enhancement to existing behavior. Branch prefix `feat/`. Standard inner-loop flow with no per-step variations.
- `chore` — **Chore**: Internal cleanup with no user-visible behavior change: refactors, dependency bumps, lint config. Branch prefix `chore/`. QA may be skipped at reviewer discretion.
- `hotfix` — **Hotfix**: Compressed inner-loop work for urgent production fixes during active incidents. Branch prefix `hotfix/`. Spawned by mitigation under IC authority; skips refinement; QA may be bypassed.
- `spike` — **Spike**: Time-boxed investigation. Branch prefix `spike/`. Deliverable is a findings doc, not merged code — the PR is never merged. Follow-ups re-enter refinement.
- `experiment` — **Experiment**: Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.

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

    note left of ready_for_dev: claim-role=developer, reversible-slow
    note right of ready_for_experiment: claim-role=developer, reversible-slow
    note right of ready_for_spike: claim-role=developer, reversible-slow
    note right of ready_for_hotfix: claim-role=developer, reversible-fast
    note right of spike_completed: reversible-fast
    note right of ready_bounced: claim-role=pm, reversible-fast
```

## States

| Name | Class | Reversibility | Claim role | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|
| `ready_for_dev` | resting | reversible-slow | developer | — | — |
| `ready_for_experiment` | resting | reversible-slow | developer | — | — |
| `ready_for_spike` | resting | reversible-slow | developer | — | — |
| `ready_for_hotfix` | resting | reversible-fast | developer | — | — |
| `implementing` | working | — | — | — | — |
| `implementing_experiment` | working | — | — | — | — |
| `implementing_spike` | working | — | — | — | — |
| `implementing_hotfix` | working | — | — | — | — |
| `pr_review` | resting | — | — | — | — |
| `pr_review_experiment` | resting | — | — | — | — |
| `pr_review_hotfix` | resting | — | — | — | — |
| `staged` | resting | — | — | — | — |
| `staged_experiment` | resting | — | — | — | — |
| `staged_hotfix` | resting | — | — | — | — |
| `spike_completed` | terminal | reversible-fast | — | resolved | completed |
| `ready_bounced` | resting | reversible-fast | pm | — | — |

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
