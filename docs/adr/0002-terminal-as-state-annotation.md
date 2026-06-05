# ADR 0002: Terminal is an annotation on a resting state, not a state class

- **Status**: Accepted
- **Date**: 2026-06-04

## Context

[Principle 1](../state-machine-principles.md) declares a closed taxonomy: **every state is resting, working, or terminal**. The class drives parsing (the `class` enum is parse-enforced), validation (which sibling fields are required/forbidden), the transition-type rules of Principle 2, and the emitters (the `[*]` sink, exit edges, feedback-terminal detection).

Two of those three classes describe **ownership**:

- **working** — exactly one role owns the item (Principle 1).
- **resting** — no role owns the item; it is a pollable queue (Principles 1, 5).

The third, **terminal**, does not describe ownership. A terminal state is unowned — no role acts on it, it has no outgoing transitions. By the ownership lens it *is* a resting state. What distinguishes it is a **behavior on entry**: landing there closes the tracker issue. That is the same shape as the annotations we already layer onto states:

- `spawns: {...}` — landing/sitting here can dispatch a child issue. Valid on working, resting, *and* terminal states.
- `collects: {...}` — issues created here gather contributors from another process.
- `handoff: true` — this resting state is a cross-process interface.
- **`is_initial: true`** — this resting state is an external entry point. New issues materialize here from outside the workflow.

Note the asymmetry. **Entry is already an annotation on a resting state** (`is_initial`), not its own class. **Exit is a class** (`terminal`). The system models "where work enters" as a flag and "where work leaves" as a type — for no reason other than history.

[Principle 11](../state-machine-principles.md) sets the precedent for resolving exactly this kind of thing. It frames HITL gating as *"an overlay, not a state class … it marks specific transitions with an annotation; the underlying graph is unchanged."* We have already decided, once, that a behavior layered onto the graph should be an annotation rather than a new class. Terminal is the mirror case on the node side.

What `class: "terminal"` carries today, all of which must be re-homed if it stops being a class:

| Concern | Current rule (keyed on `class == terminal`) |
|---|---|
| Close metadata | `terminal_taxonomy` **required** (`shipped`/`resolved`/`reverted`/`abandoned`/`deduplicated`/`superseded`) |
| Tracker close | `close_reason` **required** (GitHub: `completed` / `not planned`); **forbidden** elsewhere |
| Ownership fields | `roles` **forbidden**; `issue_types` **forbidden** |
| Reversibility | `reversibility` **required** (same as resting) |
| Graph shape | sink — **no outgoing transitions** |
| Transition targets | role-action → `resting | terminal`; external → `resting | terminal`; `resting → terminal` only via external |

## Decision

Collapse the node taxonomy to the two ownership classes — **resting** and **working** — and express termination as a `closes` annotation on a resting state.

```jsonc
{
  "name": "shipped",
  "class": "resting",
  "reversibility": "irreversible",
  "closes": {
    "taxonomy": "shipped",
    "reason": "completed"
  }
}
```

A resting state carrying `closes` is a **closing state**: entering it closes the tracker issue with `reason` and tags the closure with `taxonomy`. Entry and exit become symmetric annotations on the one unowned class:

- `is_initial` (optionally `initial_label`) — work *enters* here from outside.
- `closes: { taxonomy, reason }` — work *leaves* here; the issue closes.

### Validation moves from class-keyed to annotation-keyed

- `closes` present ⟹ the state has **no outgoing transitions** (sink). This is the invariant that made terminal a terminal; it is now enforced against the annotation.
- `closes.taxonomy` and `closes.reason` are **required** when `closes` is present, **forbidden** when absent. (Replaces the top-level `terminal_taxonomy` / `close_reason` fields.)
- `closes` and `is_initial` are **mutually exclusive** — a state cannot be both an entry and an exit.
- `roles` remain forbidden on resting states (closing or not) — no change, since terminals already forbade roles and so do resting states.
- `issue_types`: today **required** on resting (queue semantics) and **forbidden** on terminal. A closing state holds nothing, so `closes` present ⟹ `issue_types` **forbidden**. This is the one genuine inversion: the requirement that holds for ordinary resting states is suppressed by the annotation.
- `reversibility` remains **required** on every resting state — no change.

### Principle 2 simplifies

The transition table loses its `| terminal` branches. There are still three transition types, but the destination column collapses:

| Transition type | From | To |
|---|---|---|
| Claim | resting | working |
| Role action | working | resting |
| External | resting | resting |

"Closing" is no longer a transition target type — it is the property of the destination state. A role action into a closing resting state is a close; an external event into one is an auto-close. Principle 3's "claim before working" needs no change: a closing state has no outgoing claim, so inbox discovery (which traverses outgoing CLAIM edges) skips it automatically — the same way it does today.

### This finishes a job already half-done

`spawns` is already valid on terminal states (spawn-a-follow-up-on-close), and `collects.from_states` may already name terminal states. The model already composes annotations *with* terminal; this change removes the special case rather than adding one.

## Consequences

**Wins**:
- **Entry/exit symmetry.** `is_initial` and `closes` are both annotations on the single unowned class. The model stops treating "where work enters" and "where work leaves" as different *kinds* of thing.
- **Two classes mean two ownership states.** The taxonomy now answers exactly one question — does a role own this item? — instead of conflating ownership with lifecycle position.
- **Principle 2 gets shorter.** The `| terminal` alternatives in every transition rule disappear; closing becomes a node property, checked once.
- **Consistent with Principle 11.** Behaviors layered on the graph are annotations; this applies the same rule to the node side.

**Costs**:
- **Principle 1 is load-bearing and teachable.** "Every state is resting, working, or terminal" is a strong, legible invariant for both humans and agents reading the docs. "Every state is resting or working, and a resting state may close" is correct but a longer sentence, and it asks the reader to hold an annotation in mind to spot a sink.
- **A sink now looks like a queue at a glance.** The three-class model made terminals unmistakable. A reader (or a sloppy tool) that ignores the `closes` annotation could mis-read a closing state as a pollable resting queue. Mitigated by emitters rendering closing states distinctly (e.g., the `■ exit` rows and `[*]` sink), but the *raw* class no longer screams "sink."
- **The `issue_types` inversion.** Most validation maps cleanly (terminal's forbids become the annotation's forbids), but the resting `issue_types`-required rule has to be conditionally suppressed by `closes`. That is the one place the rules get less uniform, not more.
- **Migration touches everything that keys on the class.** Parser/schema (`class` enum loses `terminal`; new `closes` object), validators, every example `*-states.json`, and the emitters — `is_terminal`, exit-edge detection, feedback-terminal detection, and the cross-process `■ exit` rows all re-point from `StateClass.TERMINAL` to "resting with `closes`."
- **Doc churn.** Principle 1 and 2, `workflow-authoring.md`, the JSON schema, and `CONTEXT.md` all rewrite around the new taxonomy. This is a Principle-1-level change, not a refactor.

**Migration**:
- Mechanical codemod on the JSON sources: `class: "terminal"` → `class: "resting"`, move `terminal_taxonomy`/`close_reason` into `closes: { taxonomy, reason }`.
- Given the project is single-user and has favored clean breaks over compatibility shims, a hard cutover (no dual-format acceptance window) is acceptable — migrate the examples in the same change.

## Implementation decisions

Resolved during implementation planning (the binding choices for the build):

1. **Model — two real classes.** `StateClass` keeps only `RESTING` and `WORKING`; `TERMINAL` is **removed**, not kept as a derived alias. A new frozen `Closes { taxonomy: ClosureTaxonomy, reason: str }` lives on `State.closes`, and `is_closing` is a property (`closes is not None`). Every `state_class is StateClass.TERMINAL` check is swept to `state.is_closing`.
2. **Vocabulary — "closing", everywhere.** `is_terminal` → `is_closing`; `TerminalTaxonomy` → `ClosureTaxonomy`. The rename is a full substring sweep: `child_terminal` → `child_closing_state`, `feedback_terminals_*` → `feedback_closing_states` / `closing_states_from_outbound`, plus comments and docs. Target: `grep -ri terminal` → 0 across `workflow/`, `docs/`, `tests/`. (This supersedes the earlier "keep `is_terminal`" note.)
3. **Cutover — no back-compat.** No existing users, so no migration-hint errors: a stray `class: "terminal"` simply fails the generic `class must be one of [resting, working]` enum check.
4. **Sink invariant.** A new validator rule (`_check_closing_states_are_sinks`, ERROR) enforces that a `closes` state has no outgoing transitions — previously implicit (no transition type accepted a terminal *source*), now explicit because closing states are `resting` and `EVENT` is `resting → resting`.
5. **Guard placement.** The same-state co-occurrence guards live in the **validator** (`_check_closes_exclusivity`): `closes` ⊥ `is_initial` / `collects` / `handoff` / `issue_types`, plus `spawns.advance_on`-on-closing. The parser owns only the `closes` object shape — but it must still *relax* its "issue_types required on resting" rule to exempt closing states, and reject `closes` on a `working` state.
6. **Runtime semantics audit.** Because closing states are now `resting`, every place that enumerates resting states is reviewed per-site. Confirmed: inbox/claim discovery excludes them naturally (sinks have no outgoing `CLAIM`); `last-state:` label provisioning (`cli.py`) must explicitly exclude closing states (a closed issue has no origin to return to).
7. **Tests — strict TDD** (red-green) throughout, including renames.
8. **Delivery — feature branch** `adr-0002-closing-states`, phased commits (ADR → model → parser → validator → emitter+backend → examples → docs).

## Alternatives considered

1. **Status quo — keep three classes.** Simplest and most immediately legible; the cost of the change (Principle-1 rewrite, broad migration) is real and the asymmetry with `is_initial` is cosmetic until it bites. This is the default if the symmetry argument doesn't outweigh the churn.
2. **Boolean `terminal: true` instead of a `closes` object.** Smaller diff, but the close metadata (`taxonomy`, `reason`) is mandatory, so it would survive as two loose sibling fields gated on the flag — less cohesive than bundling them into `closes`, and it keeps the "forbidden unless flag set" conditional spread across three fields instead of one.
3. **Promote entry to a class too (symmetry via *more* classes).** Make `is_initial` its own `entry` class so entry and exit are both classes. Rejected: it adds taxonomy rather than removing it, and "entry" is not a distinct ownership state — an entry point is a resting queue that happens to be reachable from outside.
4. **Two ownership classes with *both* entry and exit as annotations** — i.e. this decision. Entry is already an annotation; this finishes the job rather than inventing a new pattern.
