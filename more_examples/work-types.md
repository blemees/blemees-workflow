# Work-type modifiers

The standard inner-loop flow in `SKILL.md` is written for a **feature** (`feat/`). Other work types modify specific steps. This file is the quick reference for a `{developer}` running the loop.

Each work type below names the **canonical owner** of its variation — the process doc that defines the full override structure — and lists the step-level modifiers a developer needs at their desk. For the conceptual model (why hotfixes skip QA, why spikes don't merge, etc.), read the variation in the canonical owner.

Conventions apply uniformly:

- Label vocabulary (`type:*`, `priority:*`), branch naming, commit format, PR title format — `inner-loop-conventions.md`.
- Release tags, monitoring windows, soak windows, release notes — `release-conventions.md`.
- Severity levels, incident communication cadence, post-mitigation windows — `incident-conventions.md`.

---

## Bug (`fix/`)

**Canonical owner:** `inner-loop-process.md`, "Variation: Bug." **PR discipline:** `pr-process.md`, root-cause defense at review.

A bug fix's defining property is that the bug existed and now doesn't. The mechanics serve that proof.

- **Step 3 (Implement) — failing test first.** Write the test that reproduces the bug. Run it and confirm it fails. Commit it on its own, then write the fix. Reviewers will check for this commit specifically.
- **Step 8 (Review feedback) — defend the root cause.** The reviewer scrutinizes whether the fix addresses the *root* cause or just the surface symptom. Be ready to explain why the fix is at the right layer. If a comment surfaces a deeper problem, fix the deeper problem.

---

## Hotfix (`hotfix/`)

**Canonical owner (spawn authority, rollout pressure):** `mitigation-process.md`, "Variation: Hotfix." **Inner-loop execution:** `inner-loop-process.md`, "Variation: Hotfix." **PR mechanics (reviewer can approve past QA under IC authority):** `pr-process.md`, hotfix variation. **Backport (if release branch affected):** `backport-process.md`. **Post-mitigation:** `postmortem-process.md` (24-hour window per `incident-conventions.md`).

A hotfix compresses the loop without removing the safety rails. CI still runs, one reviewer is still mandatory, the PR still exists. What is skipped is queue position and full QA — replaced by post-deploy monitoring (per `release-conventions.md` hotfix window) and a postmortem.

- **Step 1 (Claim) — entered via `ready_for_hotfix`.** The issue is spawned by `mitigation-process.md` with `{incident-commander}` authority; it does not come from refinement.
- **Step 2 (Branch) — branch from the release tag** if `main` has diverged from production:
  ```bash
  git checkout -b hotfix/{N}-{slug} {release-tag}
  ```
  If `main` and production are on the same commit, branch from `main` as usual.
- **Step 7 (Request review) — fast-track.** Apply `priority:critical` and request *any* available reviewer. Under IC authority the reviewer may approve past QA; see `pr-process.md`.
- **Step 9 (Merge) — squash.** If branched from a release tag, `backport-process.md` owns the cherry-pick back to `main` (and to any other affected release branches). The developer does not ad-hoc cherry-pick.
- **Step 10 (Post-merge) — postmortem inside the 24-hour window.** Owned by `postmortem-process.md`; cadence defined in `incident-conventions.md`.

---

## Chore (`chore/`)

**Canonical owner:** `inner-loop-process.md`, "Variation: Chore." **PR QA-skip decision:** `pr-process.md`.

Chores are internal cleanup — refactors, dependency bumps, lint config changes — with no user-visible behavior change.

- **Step 3 (Implement) — no new behavior tests required, but the existing suite stays 100% green.** "It's just a refactor" is the most common cover for accidental behavior changes. Run the whole suite.
- **Step 7 (Request review) — flag security-sensitive deps.** For dependency bumps in auth, crypto, network, or code that parses untrusted input, explicitly call this out in the PR body so the reviewer checks the changelog and upgrade diff.
- **QA may be skipped** for purely internal changes — the reviewer decides and labels per `pr-process.md`.

---

## Spike (`spike/`)

**Canonical owner:** `inner-loop-process.md`, "Variation: Spike." **PR review target (findings doc, not diff):** `pr-process.md`, spike variation. **Follow-up issues:** re-enter `refinement-process.md`.

A spike is an investigation. The branch is **never merged** — its purpose is to teach something so a real ticket can be written afterward.

- **Step 1 (Claim) — verify the question and time box exist.** A spike issue must specify both. If either is missing, ask the issue author before claiming. A spike without a time box becomes a side project.
- **Step 3 (Implement) — code is throwaway.** No tests, no refactors, no polish. Optimize for *learning*. If cleanup is happening, spike mode has slipped.
- **Step 5 (PR) — open as draft and stay in draft.** The deliverable is a findings document committed to `docs/spikes/{N}-{slug}.md`.
- **Step 7 (Review) — reviewer reviews the findings doc**, not the diff. Is the question answered? Is the recommendation supported? Are follow-up tickets specified?
- **Step 9 (Merge) — do not merge.** Close the PR; close the branch. If the findings doc is worth keeping, it lands via a separate normal `chore/` PR.
- **Step 10 (Post-merge) — open follow-up issues.** They re-enter `refinement-process.md` as `feat/` or `chore/` and do the real work.

---

## Experiment (`exp/`)

**Canonical owner (pre-merge):** `inner-loop-process.md`, "Variation: Experiment." **Canonical owner (post-merge measurement, decision):** `experimentation-process.md`.

An experiment ships to production behind a feature flag, often to a cohort. "Done" is a *decision based on metrics*, not the merge.

- **Step 1 (Claim) — verify hypothesis, metric, and cohort.** All three must be on the issue per `experimentation-process.md`'s artifact template. Missing fields are a refinement gap; bounce or ask.
- **Step 3 (Implement) — flag-gated, OFF by default.** Add metric instrumentation alongside the feature code. The metric is the whole point.
- **Step 5 (PR) — standard `Refs #{N}` footer like any other PR.** No footer override for experiments (see `pr-process.md` Rule 9, universal).
- **Step 9 (Merge) — standard squash, flag still off.** 100% of users see the existing behavior. Cohort enablement happens separately.
- **Step 10 (Post-merge) — issue is excluded from release-automation closure at `released` and stays open through measurement.** It closes at verdict on the experimentation lifecycle under `{product-owner}` authority.

---

## See also

- `inner-loop-process.md` — canonical owner of the variation structure (Bug, Hotfix, Chore, Spike, Experiment)
- `mitigation-process.md` — spawning authority for hotfix work items
- `pr-process.md` — PR-level variations (hotfix approval bypass, spike findings review)
- `backport-process.md` — cherry-picks required by release-tag-branched hotfixes
- `postmortem-process.md` — 24-hour postmortem window after a hotfix
- `experimentation-process.md` — post-merge measurement and verdict for experiments
- `refinement-process.md` — where spike follow-ups re-enter
- `inner-loop-conventions.md` — label vocabulary, branch naming, commit format, PR templates
- `release-conventions.md` — monitoring windows, tag format
- `incident-conventions.md` — severity and post-mitigation windows
- `roles.md` — role definitions
