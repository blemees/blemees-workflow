# Process: pr

> Defined in: `pr-states.json`

## Issue types accepted

- `pr` — **Pull Request**: A proposed code change. Spawned by a developer running `gh pr create` from inner-loop's implementing state. One ticket can spawn zero (spike findings doc), one (typical), or many PRs (incident mitigation chains, hotfix + backports, multi-component features). Not created via `workflow create`; the framework recognises it for cross-process modelling and documentation.

## State diagram

```mermaid
stateDiagram-v2
    %% Cross-process interfaces:
    %%   Entry (spawn ): draft from process inner-loop
    %%

    [*] --> draft: from process inner-loop (developer opens draft PR at ticket claim)
    draft --> drafting: developer claims draft
    drafting --> needs_review: developer marks PR ready for review
    needs_review --> reviewing: reviewer claims PR
    reviewing --> needs_qa: reviewer approves PR
    reviewing --> qa_passed: reviewer approves hotfix PR (QA skipped, IC discretion)
    reviewing --> changes_requested: reviewer requests changes
    changes_requested --> fixing_review: developer claims PR for fixes
    fixing_review --> needs_review: developer re-requests review
    needs_qa --> verifying: QA claims PR
    verifying --> qa_passed: QA posts pass verdict
    verifying --> qa_failed: QA posts fail verdict
    qa_failed --> fixing_qa: developer claims PR for fixes
    fixing_qa --> needs_review: developer re-requests review
    qa_passed --> merging: developer claims and merges
    merging --> staged: verified staging deploy
    merging --> needs_review: merge conflict or failed staging deploy
    staged --> [*]: terminal (shipped)

    note right of drafting: role=developer
    note right of reviewing: role=peer-reviewer
    note right of fixing_review: role=developer
    note right of verifying: role=tester
    note right of fixing_qa: role=developer
    note right of merging: role=developer
    note right of staged: reversible-slow
```

## States

| Name | Class | Reversibility | Roles | Issue types | Terminal taxonomy | Close reason |
|---|---|---|---|---|---|---|
| `draft` | resting | — | — | — | — | — |
| `drafting` | working | — | developer | — | — | — |
| `needs_review` | resting | — | — | — | — | — |
| `reviewing` | working | — | peer-reviewer | — | — | — |
| `changes_requested` | resting | — | — | — | — | — |
| `fixing_review` | working | — | developer | — | — | — |
| `needs_qa` | resting | — | — | — | — | — |
| `verifying` | working | — | tester | — | — | — |
| `qa_passed` | resting | — | — | — | — | — |
| `qa_failed` | resting | — | — | — | — | — |
| `fixing_qa` | working | — | developer | — | — | — |
| `merging` | working | — | developer | — | — | — |
| `staged` | terminal | reversible-slow | — | — | shipped | completed |

## Transitions

| From | To | Type | Label | Gate | HITL level |
|---|---|---|---|---|---|
| `[*]` | `draft` | cross_process | 'from process inner-loop (developer opens draft PR at ticket claim)' | — | — |
| `draft` | `drafting` | claim | 'developer claims draft' | — | — |
| `drafting` | `needs_review` | role_action | 'developer marks PR ready for review' | — | — |
| `needs_review` | `reviewing` | claim | 'reviewer claims PR' | — | — |
| `reviewing` | `needs_qa` | role_action | 'reviewer approves PR' | — | — |
| `reviewing` | `qa_passed` | role_action | 'reviewer approves hotfix PR (QA skipped, IC discretion)' | — | — |
| `reviewing` | `changes_requested` | role_action | 'reviewer requests changes' | — | — |
| `changes_requested` | `fixing_review` | claim | 'developer claims PR for fixes' | — | — |
| `fixing_review` | `needs_review` | role_action | 'developer re-requests review' | — | — |
| `needs_qa` | `verifying` | claim | 'QA claims PR' | — | — |
| `verifying` | `qa_passed` | role_action | 'QA posts pass verdict' | — | — |
| `verifying` | `qa_failed` | role_action | 'QA posts fail verdict' | — | — |
| `qa_failed` | `fixing_qa` | claim | 'developer claims PR for fixes' | — | — |
| `fixing_qa` | `needs_review` | role_action | 'developer re-requests review' | — | — |
| `qa_passed` | `merging` | claim | 'developer claims and merges' | — | — |
| `merging` | `staged` | role_action | 'verified staging deploy' | — | — |
| `merging` | `needs_review` | role_action | 'merge conflict or failed staging deploy' | — | — |

## Cross-process handoffs

**Entries** (issues arriving from other processes):

- `draft` ← process `inner-loop` (spawn) — `from process inner-loop (developer opens draft PR at ticket claim)`
