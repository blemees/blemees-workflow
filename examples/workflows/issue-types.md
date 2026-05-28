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

A proposed code change. Spawned by a developer running `gh pr create` from inner-loop's implementing state. One ticket can spawn zero (spike findings doc), one (typical), or many PRs (incident mitigation chains, hotfix + backports, multi-component features). Not created via `workflow create-issue`; the framework recognises it for cross-process modelling and documentation.

**GitHub entity**: pull request (no native Issue Type)

## `incident` — Incident

Live production incident. Opened by the IC at declaration; carries through incident-response from declaration to stabilization. Mitigation work is spawned as separate `incident`-typed tickets on the mitigation process — the IC stays on the parent in `mitigating` until a mitigation child returns. Closes at `stabilized`; postmortem is another separate (spawned) ticket.

**GitHub Issue Type**: `Incident` · **Color**: `red`

## `postmortem` — Postmortem

Post-incident review work. Spawned at `incident.stabilized`; owns the narrative, root-cause integration, and follow-up filing. Distinct ticket from the incident itself so the postmortem can outlive the incident's close.

**GitHub Issue Type**: `Postmortem` · **Color**: `purple`

## `release` — Release

A release-train work item. Tracks one cut through prep → deploy → monitor; experimentation and progressive-rollout are phases that operate on the same release ticket.

**GitHub Issue Type**: `Release` · **Color**: `blue`

## `backport` — Backport

Port of an already-merged change onto a release/patch branch. Distinct from a regular bug/hotfix because the work is mechanical (cherry-pick + verify), not net-new.

**GitHub Issue Type**: `Backport` · **Color**: `yellow`
