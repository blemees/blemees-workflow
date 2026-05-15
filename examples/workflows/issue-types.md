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

A proposed code change. Spawned by a developer running `gh pr create` from inner-loop's implementing state. One ticket can spawn zero (spike findings doc), one (typical), or many PRs (incident mitigation chains, hotfix + backports, multi-component features). Not created via `workflow create`; the framework recognises it for cross-process modelling and documentation.

**GitHub entity**: pull request (no native Issue Type)
