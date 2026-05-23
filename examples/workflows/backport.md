# Process: backport

> Defined in: `backport-states.json`

## Issue types accepted

- `backport` — **Backport**: Port of an already-merged change onto a release/patch branch. Distinct from a regular bug/hotfix because the work is mechanical (cherry-pick + verify), not net-new.

## State diagram

```mermaid
stateDiagram-v2
    ready_for_backport --> cherry_picking: developer claims backport
    cherry_picking --> backport_pr_review: developer opens backport PR (spawns PR on process pr-review)
    backport_pr_review --> backport_merged: from process pr-review (backport PR merged to release branch)
    backport_merged --> patch_releasing: patch release triggered (spawns patch train on process release, IC bypass of release gate)
    patch_releasing --> backported: from process release (patch deployed to production)
    backported --> [*]: terminal (shipped)

    note right of ready_for_backport: reversible-slow
    note right of cherry_picking: role=developer, types=backport
    note right of backport_pr_review: reversible-fast
    note right of backport_merged: reversible-slow
    note right of patch_releasing: reversible-slow
    note right of backported: reversible-slow
```

## States

| Name | Class | Reversibility | Roles | Issue types | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|
| `ready_for_backport` | resting | reversible-slow | — | — | — | — |
| `cherry_picking` | working | — | developer | backport | — | — |
| `backport_pr_review` | resting | reversible-fast | — | — | — | — |
| `backport_merged` | resting | reversible-slow | — | — | — | — |
| `patch_releasing` | resting | reversible-slow | — | — | — | — |
| `backported` | terminal | reversible-slow | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `ready_for_backport` | `cherry_picking` | claim | 'developer claims backport' | — | — |
| `cherry_picking` | `backport_pr_review` | advance | 'developer opens backport PR (spawns PR on process pr-review)' | — | — |
| `backport_pr_review` | `backport_merged` | event | 'from process pr-review (backport PR merged to release branch)' | — | — |
| `backport_merged` | `patch_releasing` | event | 'patch release triggered (spawns patch train on process release, IC bypass of release gate)' | — | — |
| `patch_releasing` | `backported` | event | 'from process release (patch deployed to production)' | — | — |
