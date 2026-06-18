# Process: inner-loop

The developer's day-to-day flow: claim a refined ticket, implement, open a PR. After merge, the ticket lands in `staged`, which is a shared handoff with `release` — from there the release process owns the lifecycle through to shipped. Spawns a PR child issue during implementing.

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
    direction TB
    %% Cross-process interfaces:
    %%   Handoff: ready_for_dev (shared resting state)
    %%   Handoff: staged (shared resting state)
    %%   Handoff: ready_bounced (shared resting state)
    %%   Spawn:   implementing → process pr (issue_type=pr, initial=draft)
    %%

    [*] --> ready_for_dev: ▶ ready_for_dev
    ready_for_dev --> implementing: developer claims issue
    ready_for_chore --> implementing: developer claims spawned chore
    ready_for_spike --> implementing_spike: developer claims spike
    ready_for_hotfix --> implementing: developer claims hotfix
    implementing --> ready_bounced: developer bounces ticket
    implementing --> staged: PR merged (auto from pr)
    implementing_spike --> spike_completed: developer posts spike findings
    spike_completed --> [*]: ⊡ spike_returned
    [*] --> ready_for_dev: ⊙ ready_for_dev
    staged --> [*]: ⊙ staged
    ready_bounced --> [*]: ⊙ ready_bounced
    [*] --> ready_for_chore: ᐉ complete
    [*] --> ready_for_chore: ᐉ killed
    [*] --> ready_for_chore: ᐉ promoted
    [*] --> ready_for_hotfix: ᐉ execute_mitigation
    [*] --> ready_for_spike: ᐉ spiking

    note left of implementing
        ᐉ draft (pr)
    end note
    note left of staged
        ⊡ merged (pr)
    end note
```

## States

| Name | Class | Reversibility | Roles | Issue types | Human inputs | Closure taxonomy | Close reason |
|---|---|---|---|---|---|---|---|
| `ready_for_dev` | resting | reversible-slow | — | bug, feature, chore, experiment | — | — | — |
| `ready_for_chore` | resting | reversible-fast | — | chore | — | — | — |
| `ready_for_spike` | resting | reversible-slow | — | spike | — | — | — |
| `ready_for_hotfix` | resting | reversible-fast | — | hotfix | — | — | — |
| `implementing` | working | — | developer | bug, feature, chore, experiment, hotfix | clarify-scope, needs-arch-review, needs-security-review, needs-ux-input, blocked-on-data, general | — | — |
| `implementing_spike` | working | — | developer | spike | clarify-scope, needs-arch-review, needs-security-review, needs-ux-input, blocked-on-data, general | — | — |
| `staged` | resting | reversible-slow | — | bug, feature, chore, experiment, hotfix | — | — | — |
| `spike_completed` | resting | reversible-fast | — | — | — | resolved | completed |
| `ready_bounced` | resting | reversible-fast | — | bug, feature, chore, experiment | — | — | — |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `ready_for_dev` | `implementing` | claim | 'developer claims issue' | — | — |
| `ready_for_chore` | `implementing` | claim | 'developer claims spawned chore' | — | — |
| `ready_for_spike` | `implementing_spike` | claim | 'developer claims spike' | — | — |
| `ready_for_hotfix` | `implementing` | claim | 'developer claims hotfix' | — | — |
| `implementing` | `ready_bounced` | advance | 'developer bounces ticket' | — | — |
| `implementing` | `staged` | advance | 'PR merged (auto from pr)' | — | — |
| `implementing_spike` | `spike_completed` | advance | 'developer posts spike findings' | — | — |

## Cross-process interfaces

### Inbound

| State | Kind | From | Detail |
|---|---|---|---|
| `ready_for_dev` | ▶ entry | — (external) | `create-issue --to ready_for_dev` — engineer files chore directly (skips refinement) |
| `ready_for_chore` | ᐉ spawn | [`experimentation`](./experimentation.md) · `killed` | `chore` issue |
| `ready_for_chore` | ᐉ spawn | [`experimentation`](./experimentation.md) · `promoted` | `chore` issue |
| `ready_for_chore` | ᐉ spawn | [`postmortem`](./postmortem.md) · `complete` | `chore` issue |
| `ready_for_hotfix` | ᐉ spawn | [`mitigation`](./mitigation.md) · `execute_mitigation` | `hotfix` issue |
| `ready_for_spike` | ᐉ spawn | [`refinement`](./refinement.md) · `spiking` | `spike` issue |
| `staged` | ⊡ feedback | [`pr`](./pr.md) · `merged` | child terminates → advance (spawned from `implementing`, `pr`) |
| `ready_for_dev` | ⊙ handoff | partner process(es) | shared resting state (also outbound) |
| `staged` | ⊙ handoff | partner process(es) | shared resting state (also outbound) |
| `ready_bounced` | ⊙ handoff | partner process(es) | shared resting state (also outbound) |

### Outbound

| State | Kind | To | Detail |
|---|---|---|---|
| `shipped` | ⊡ feedback | [`mitigation`](./mitigation.md) | advances parent to `mitigated` (spawn from `execute_mitigation`, `hotfix`) |
| `spike_completed` | ⊡ feedback | [`refinement`](./refinement.md) | advances parent to `spike_returned` (spawn from `spiking`, `spike`) |
| `implementing` | ᐉ spawn | [`pr`](./pr.md) · `draft` | as `pr` issue (subprocess) |
| `ready_for_dev` | ⊙ handoff | partner process(es) | shared resting state (also inbound) |
| `staged` | ⊙ handoff | partner process(es) | shared resting state (also inbound) |
| `ready_bounced` | ⊙ handoff | partner process(es) | shared resting state (also inbound) |
