# ADR 0003: Spawn and collect relationships are a single dependent-side label; the cohort is a query

- **Status**: Accepted
- **Date**: 2026-06-17

## Context

Two relationships attach dependent issues to an anchor issue:

- **Spawn** — a parent spawns children (incident → mitigation, a ticket → its PRs, a failed experiment → its successor).
- **Collect** — a collector gathers contributors from another process.

Both drive a cascade. The spawn cascade uses **wait-for-all** `advance_on`: the parent advances only when *every* child has reached its trigger state (cascade.py, `_apply_spawn_parent_cascade`). The collector cascade advances or releases contributors per `advance_on` / `release_on` (`_apply_collector_cascade`).

Each relationship needs to be readable in **two directions**:

1. **Trigger edge (dependent → anchor).** The cascade fires when a *child* enters a closing state, or a *contributor* changes state; it must find which anchor to act on.
2. **Cohort enumeration (anchor → dependents).** To apply wait-for-all (and to release a cohort), it must enumerate the siblings — the **cohort**.

The original model satisfied both with **two labels** per relationship: a dependent-side back-pointer (`child-of:<parent>` / `collected-by:<collector>`) for direction 1, and an anchor-side registry (`subprocess:<child>` / `collects:<contributor>`) for direction 2. The anchor thus carried its cohort, readable in a single `read_issue(anchor)`.

The cost of the dual-label model: every spawn/collect is a multi-issue, multi-label write with no atomicity (the `apply_marker_change` sequence is already non-transactional — see the backend atomicity finding), and the two labels can drift. The only reason the anchor-side registry exists is that the alternative — enumerating the cohort with `list_issues(label=...)` — is unreliable on the current GitHub backend, whose `list_issues` excludes PRs and defaults to open. The dependents that just closed and triggered the cascade are exactly the closed/PR issues such a query would miss. The anchor-side label is, in effect, a workaround for that backend limitation.

## Decision

Record each relationship with a **single dependent-side label** — `child-of:<parent>` on each child, `collected-by:<collector>` on each contributor. Remove the anchor-side registries (`subprocess:`, `collects:`).

- The **cohort** is discovered on demand by `list_issues(label="child-of:<parent>")` / `list_issues(label="collected-by:<collector>")`, not read off the anchor.
- This makes cascade correctness depend on the backend's list visibility covering **closed issues and PRs**. That backend fix ships in the **same slice** as this change; wait-for-all is not correct until it lands.
- A dependent is **always** labelled, even when its spawn/collect declares no `advance_on` rule. Labeling is uniform; for a no-cascade relationship the link is informational only (next-actions, docs).

Per-dependent *direct* reads (`read_issue(child)` to check current state) are unaffected — `gh issue view` / `gh pr view` by id already see closed and PR entities. Only list-based discovery was broken, and only that needed fixing.

This decision also reshapes the operations that create these relationships (candidate #1): with no anchor-side write, **`spawn-issue` collapses to a single-issue create** (make the child, stamp `child-of:`), and **`collect-into` collapses to a single existing-issue mutation** (stamp the contributor `collected-by:`). The anchor is touched only later, by the cascade.

## Consequences

**Wins**:
- **Single source of truth.** One label per relationship; no dual-write, no drift between an anchor registry and the dependent's back-pointer.
- **Halves the non-atomic write surface** for spawn/collect — one labelled issue instead of two — narrowing the partial-failure modes of the non-transactional apply sequence.
- **The cohort becomes a query, not stored state.** A cohort is defined by the live set of dependents pointing at an anchor, which can't go stale relative to the issues that actually exist.
- **Simpler operations.** Removing the anchor-side write is what lets spawn/collect drop out of the CLI and into the standard operation → planner seam as single-issue effects.

**Costs**:
- **Correctness is coupled to backend list visibility.** Until `list_issues` returns closed issues and PRs, wait-for-all silently under-counts the cohort. This ADR is only safe shipped together with that fix.
- **A list query per cascade** replaces a single-read cohort lookup. Acceptable: cascades are infrequent and the query is one API call.
- **Migration.** `IssueState.subprocess_children` / the collector-side registry field, the `subprocess:` / `collects:` parsing and encoding, and any docs describing the dual-label model are removed; existing issues carrying those labels keep them harmlessly (ignored) or are swept.

## Alternatives considered

1. **Keep the dual-label model (status quo).** Robust today regardless of backend list limits — the anchor always carries its full cohort. Rejected: dual-write consistency cost and extra non-atomic write surface, to work around a backend limitation that is being fixed anyway.
2. **Conditional labeling — only when `advance_on` is present.** Label nothing for relationships with no cascade rule, since the link is then purely informational. Rejected: non-uniform behavior keyed on a rule's presence is a footgun; the informational link (next-actions, docs) is worth keeping, and a uniform rule is easier to reason about.
3. **Single-side for spawn but keep dual-side for collect** (or vice versa). Rejected: the two relationships have the same shape and the same backend limitation; asymmetry would be arbitrary and harder to teach.
