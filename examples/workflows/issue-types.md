# Issue types

> Defined in: `issue-types.json`

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

A proposed code change. Spawned by a developer running `workflow spawn-issue` from inner-loop's implementing state (the CLI in turn invokes `gh pr create` against the backend). One ticket can spawn zero (spike findings doc), one (typical), or many PRs (incident mitigation chains, multi-component features). The framework owns the spawn relationship and the cross-process modelling.

**GitHub entity**: pull request (no native Issue Type)

## `incident` — Incident

Live production incident. Opened by the IC at declaration; carries through incident-response from declaration to stabilization. Mitigation work is spawned as separate `incident-mitigation`-typed tickets on the mitigation process — the IC stays on the parent in `mitigating` until a mitigation child returns. Closes at `stabilized`; postmortem is another separate (spawned) ticket.

**GitHub Issue Type**: `Incident` · **Color**: `red`

## `incident-mitigation` — Incident mitigation

A mitigation work item spawned from an incident. The responder drafts a plan in `plan_mitigation` and then in `execute_mitigation` dispatches one or more sub-mitigations (any combination of `hotfix`, `config-change`, `data-change`) as child issues on their respective processes. Tied to the parent incident via `parent-of:<incident>`. Closes at `mitigated` only when every spawned child reaches its applied / shipped terminal — wait-for-all — at which point the parent incident cascades to `needs_verification`.

**GitHub Issue Type**: `Incident mitigation` · **Color**: `orange`

## `config-change` — Config change

A runtime configuration change applied as part of an incident mitigation: feature-flag toggle, kill switch, config-value tweak, rate-limit adjustment. No code change, no PR. Tied to the parent incident-mitigation via `parent-of:<incident-mitigation>`. Closes at `config_applied`, cascading the parent mitigation toward `mitigated`.

**GitHub Issue Type**: `Config change` · **Color**: `yellow`

## `data-change` — Data change

A data mutation applied as part of an incident mitigation: corruption repair, manual update, backfill, event replay. Always preceded by a backup step (`creating_backup` → `backup_ready`) — the workflow enforces the safety net. Tied to the parent incident-mitigation via `parent-of:<incident-mitigation>`. Closes at `data_change_applied`, cascading the parent mitigation toward `mitigated`.

**GitHub Issue Type**: `Data change` · **Color**: `purple`

## `postmortem` — Postmortem

Post-incident review work. Spawned at `incident.stabilized`; owns the narrative, root-cause integration, and follow-up filing. Distinct ticket from the incident itself so the postmortem can outlive the incident's close.

**GitHub Issue Type**: `Postmortem` · **Color**: `purple`

## `release` — Release

A release-train work item. Tracks one cut through prep → deploy → monitor; experimentation is a phase that operates on the same release ticket.

**GitHub Issue Type**: `Release` · **Color**: `blue`
