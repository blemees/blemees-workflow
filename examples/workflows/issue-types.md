# Issue types

Each process declares which of these it accepts via its `issue_types` field. Type is set at creation and immutable.

## `bug` — Bug

Defect in shipped behavior — something is broken or wrong. Branch prefix `fix/`. Failing test first; reviewer scrutinizes root cause vs surface symptom.

**GitHub Issue Type**: `Bug` · **Color**: `red`

## `feature` — Feature

New user-facing capability or enhancement to existing behavior. Branch prefix `feat/`. Standard inner-loop flow with no per-step variations.

**GitHub Issue Type**: `Feature` · **Color**: `green`

## `hotfix` — Hotfix

Compressed inner-loop work for urgent production fixes during active incidents. Branch prefix `hotfix/`. Spawned by mitigation under IC authority; skips refinement; QA may be bypassed.

**GitHub Issue Type**: `Hotfix` · **Color**: `orange`

## `chore` — Chore

Internal cleanup with no user-visible behavior change: refactors, dependency bumps, lint config. Branch prefix `chore/`. QA may be skipped at reviewer discretion.

**GitHub Issue Type**: `Chore` · **Color**: `blue`

## `spike` — Spike

Time-boxed investigation. Branch prefix `spike/`. Deliverable is a findings doc, not merged code — the PR is never merged. Follow-ups re-enter refinement.

**GitHub Issue Type**: `Spike` · **Color**: `purple`

## `experiment` — Experiment

Flag-gated feature shipped to a cohort for measurement. Branch prefix `exp/`. Requires hypothesis, metric, and cohort up-front. Post-merge owned by product-owner; closes at verdict on the experimentation lifecycle.

**GitHub Issue Type**: `Experiment` · **Color**: `pink`

## `pr` — Pull Request

A proposed code change. Spawned by a developer running `workflow spawn-issue` from inner-loop's implementing state (which invokes `gh pr create`). One ticket can spawn zero, one, or many PRs; the framework owns the spawn relationship.

**GitHub entity**: pull request (no native Issue Type)

## `incident` — Incident

Live production incident. Opened by the IC at declaration; runs through incident-response to stabilization. Mitigation work is spawned as separate `incident-mitigation` tickets. Closes at `stabilized`; the postmortem is a separate spawned ticket.

**GitHub Issue Type**: `Incident` · **Color**: `red`

## `incident-mitigation` — Incident mitigation

A mitigation work item spawned from an incident. The responder plans in `plan_mitigation`, then `execute_mitigation` dispatches sub-mitigations (hotfix/config-change/data-change) as children. Closes at `mitigated` when all children reach a closing state.

**GitHub Issue Type**: `Incident mitigation` · **Color**: `orange`

## `config-change` — Config change

A runtime configuration change for an incident mitigation: feature-flag toggle, kill switch, config-value tweak, rate-limit adjustment. No code change, no PR. Closes at `config_applied`, cascading the parent mitigation toward `mitigated`.

**GitHub Issue Type**: `Config change` · **Color**: `yellow`

## `data-change` — Data change

A data mutation for an incident mitigation: corruption repair, manual update, backfill, event replay. Always preceded by a backup (`creating_backup` → `backup_ready`). Closes at `data_change_applied`, cascading the parent mitigation toward `mitigated`.

**GitHub Issue Type**: `Data change` · **Color**: `purple`

## `postmortem` — Postmortem

Post-incident review work. Spawned at `incident.stabilized`; owns the narrative, root-cause integration, and follow-up filing. Distinct ticket from the incident itself so the postmortem can outlive the incident's close.

**GitHub Issue Type**: `Postmortem` · **Color**: `purple`

## `release` — Release

A release-train work item. Tracks one cut through prep → deploy → monitor; experimentation is a phase that operates on the same release ticket.

**GitHub Issue Type**: `Release` · **Color**: `blue`
