# ADR 0005: A binary capability tier — native issue metadata, or label fallback

- **Status**: Accepted
- **Date**: 2026-06-22

## Context

The framework projects the entire issue-state machine onto **GitHub labels**: state, issue type, agent claim, last-state, the spawn/collect relationships, and every HITL marker. That design predates GitHub's structured-issue features — labels were the lowest-common-denominator primitive available on every tracker, so everything was forced through them.

Two costs followed from "labels for everything":

- **Hand-maintained invariants.** "Exactly one `state:` label" is enforced by the swap logic in `_marker_change_to_labels`; the backend manually removes the old `state:` label and adds the new one. A native single-value field would enforce this for free.
- **An inconsistent, overloaded label grammar.** The label vocabulary grew organically with three different separator conventions (`state:`, `hitl:awaiting-<gate>`, `hitl:topic-<topic>`, `wip:`, `child-of:`), a redundant pair (`hitl:awaiting-input` + `hitl:topic-<topic>`), and names that don't match the domain model (`wip:` vs the `claim` concept; `awaiting` vs the `HumanGateLevel.BLOCK` vocabulary).

GitHub now offers native homes for most of this metadata, and — critically — they are org-level and **apply to every repository without requiring a Project**:

- **Issue Types** (GA) — native, org-level, queryable as `type:<Name>`.
- **Sub-issues** (GA) — native parent/child links, queryable as `parent-issue:owner/repo#N`, mutated via the `addSubIssue` GraphQL mutation (`GraphQL-Features: sub_issues`) and a REST API.
- **Issue Fields** (public preview since 2026-03; all orgs since 2026-05-21) — org-level typed metadata (text / single-select / number / date) that auto-appears on every issue in every repo. Managed via GraphQL (`createIssueField`, `updateIssueField`, `setIssueFieldValue`) and REST. Queryable as `field.<name>:<value>`, with multi-word names quoted: `field."target date":>=2026-03-01`.

The capability cache (ADR introduced it for issue-type encoding only) already models exactly this kind of per-org decision: `encoding ∈ {native, label}`. This ADR generalizes that one axis into the whole projection.

## Decision

Replace the issue-type-only `encoding` axis with a **single binary capability tier** per `(host, owner)`:

- **`native`** — the org supports Issue Fields + Issue Types + sub-issues. *Every* projection that has a native home uses it. Org-level fields are provisioned once and apply across all repos.
- **`label`** — fallback for trackers without the native feature set (older GitHub Enterprise, or instances where Issue Fields preview is unavailable). *Everything* is a label, under one consistent grammar.

It is deliberately **all-or-nothing** — no per-feature mix. This keeps the parse/encode surface to exactly two columns instead of a combinatorial matrix, and keeps a repo's encoding self-consistent and easy to reason about. Issues continue to live on repositories in both modes; **no Project is required**.

### Projection table

| Concept | `native` | `label` |
|---|---|---|
| State | single-select field **Workflow State** = state name | `state/<name>` |
| Issue type | native Issue Type | `type/<id>` |
| Parent (spawn, child-of) | native **sub-issue** link | `child-of/<parent-id>` |
| Collected by | text field **Collected By** = collector id | `collected-by/<collector-id>` |
| Agent claim | single-select field **Agent** = role | `claimed/<agent>` |
| Last state | single-select field **Last State** = state name | `last-state/<name>` |
| HITL blocked | single-select field **HITL Blocked** = gate | `hitl-blocked/<gate>` |
| HITL audit | single-select field **HITL Audit** = gate | `hitl-audit/<gate>` |
| HITL input | single-select field **HITL Input** = topic | `hitl-input/<topic>` |
| HITL claim | single-select field **HITL Claim** = {reviewing, auditing, advising} | `hitl-claim/<value>` |
| HITL signal | single-select field **HITL Signal** = {approved, rejected, checked, revoked} | `hitl-signal/<value>` |

### Label grammar (the `label` tier)

One rule: **`<kebab-classifier>/<value>`**. The classifier is kebab-case (multi-word joined by `-`), a single `/` separates it from the value. This replaces the old grammar wholesale:

- `state:X` → `state/X`
- `wip:X` → `claimed/X` (renamed to match the `claim` concept)
- `hitl:awaiting-<gate>` → `hitl-blocked/<gate>` (renamed `awaiting`→`blocked` to match `HumanGateLevel.BLOCK`)
- `hitl:audit-<gate>` → `hitl-audit/<gate>`
- `hitl:awaiting-input` + `hitl:topic-<topic>` → **merged** into `hitl-input/<topic>` (the topic is always present, so the two were redundant)
- `hitl:reviewing` / `auditing` / `advising` → `hitl-claim/<value>` (these are mutually exclusive — the planner already rejects a second concurrent claim — so they collapse to one classifier)
- `hitl:approved-<gate>` / `rejected-` / `checked-` / `revoked-` → `hitl-signal/<value>`

### Relationship split supersedes ADR-0003 (native tier only)

ADR-0003 unified spawn and collect under a single dependent-side label because the cohort had to be a `list_issues(label=...)` query. In the **native** tier the two relationships get *different* homes:

- **`child-of`** → a native sub-issue link (preserves the issue graph; queryable via `parent-issue:`).
- **`collected-by`** → a **Collected By** text field, kept distinct from the sub-issue link.

They must be distinct because sub-issues express only one relationship kind: an issue that is both spawnable and collectable cannot say "child of X" vs "collected by Y" through sub-issue links alone. In the **label** tier, ADR-0003's reasoning stands unchanged (`child-of/<parent>`, `collected-by/<collector>`, cohort-by-query).

## Consequences

**Wins**

- **Native invariants for free.** Single-select **Workflow State** enforces "exactly one state"; the manual `state/` swap logic in `_marker_change_to_labels` disappears on the native path.
- **One consistent label grammar** on the fallback path, aligned with the domain vocabulary (`claimed`, `hitl-blocked`).
- **Queryable structured metadata.** `view-inbox` / `search-issues` filter natively (`type:`, `parent-issue:`, `field.workflow-state:`, `field.agent:`, …) instead of by overloaded labels.
- **The capability decision stays one bit per org**, with the existing probe/TTL/manual-override machinery essentially intact.

**Costs / open implementation work**

- **Provisioning step (new).** The native tier requires the org's fields and issue types to *exist*. A new operation (e.g. `workflow capabilities --provision`) must create the ~8 single-select/text fields and the process issue types via GraphQL. This is a privileged, one-time, per-org action.
- **`gh` porcelain is insufficient.** Issue Fields and sub-issues are driven through `gh api graphql` with the `GraphQL-Features` headers (`sub_issues`, `issue_types`, and the issue-fields feature flag), not `gh issue` subcommands. The GitHub backend gains a GraphQL code path alongside the existing label path.
- **Preview risk.** Issue Fields is in **public preview**; the API and search syntax may shift, and the feature is absent on GHE without data residency. This is precisely why the `label` tier remains a first-class, fully-supported fallback rather than a deprecated path.
- **Multi-word field-name query syntax.** GitHub does **not** slug multi-word field names to `collected-by`; it quotes them: `field."collected by":<id>`. (This corrects an earlier assumption.) Queries are built programmatically so quoting is not a burden, but field names should be chosen deliberately — single-word names (e.g. **Agent**) keep queries unquoted; multi-word names (**Workflow State**, **Collected By**, **Last State**, **HITL Blocked**) require the quoted form in generated queries.
- **Collected-By queryability.** The cohort lookup depends on exact-match search of a *text* field (`field."collected by":<id>`). Text-field exact match is documented (`field:"exact text"`), to be validated against the live API during implementation.
- **No legacy data → no migration.** This is greenfield: no issues were ever labelled under the pre-ADR-0005 `:` grammar, so the backend writes and parses the new grammar **only** (no dual-read, no scheme-version stamp, no label-rewrite sweep). Label→native backfill (migrating an org that started on the label tier once it enables native) is a **separate migration tool**, not part of v1; v1 ships fresh-native + label-fallback.

## Alternatives considered

1. **Per-feature capability matrix** (state native, HITL labels, etc., independently). Rejected: combinatorial parse/encode paths and per-repo inconsistency for little gain; the all-or-nothing tier is simpler to reason about and test.
2. **Use Projects v2 custom fields.** Rejected: Project fields live on the Project, forcing every issue into a project to be queryable. Org-level Issue Fields attach to issues directly and need no project.
3. **Store `child-of` in a custom field too (uniform with `collected-by`).** Rejected for the native tier: sub-issues are the semantically correct native home for hierarchy and give a first-class `parent-issue:` query and timeline integration; a text field would discard the graph.
4. **Keep labels, just fix the grammar.** Rejected as the *whole* answer: it leaves hand-maintained invariants and unqueryable structured metadata on the table. The grammar fix is retained — but as the fallback tier, not the only tier.
5. **Consolidate the HITL gate fields** (one "HITL Gate" + a mode field) to reduce field count. Rejected: Blocked/Audit/Input are mutually exclusive and *could* collapse, but no per-org field limit was observed, and separate named fields are more self-documenting. Each HITL concern keeps its own field. Revisit only if a field limit surfaces.
