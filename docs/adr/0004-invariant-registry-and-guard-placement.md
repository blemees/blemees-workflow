# ADR 0004: Invariants have a guard-placement principle and a single generated registry

- **Status**: Accepted
- **Date**: 2026-06-17

## Context

The framework enforces a large body of invariants, spread across three layers of code:

- The **parser** (`workflow/core/parser/`) raises `ParseError` on ~40 shape and local-field rules.
- The **validator** (`workflow/core/validator.py`) emits `ValidationFinding` (ERROR/WARNING) across 21 `_check_*` rule families.
- The **planner** (`workflow/core/planner.py`) raises `OperationError` on ~10 runtime preconditions.

Two problems follow. First, **no document inventories these invariants** — `docs/state-machine-principles.md` explains the *design* and `CONTEXT.md` defines the *vocabulary*, but neither answers "what is checked, where, and at what severity." Several load-bearing invariants (marker drift on release, at-most-one-gate-in-flight, claim-time type-in-destination) exist only in code with no prose statement. Second, **which layer a given invariant belongs in** is a real, consistently-applied rule that has only ever been written down for the single `closes` case (ADR-0002 §5).

This project already fights documentation drift everywhere it matters: the emitters are byte-stable and a CI check verifies the checked-in docs match the JSON. An inventory of invariants that is hand-maintained would drift the same way prose always does.

## Decision

### 1. The guard-placement principle

Every invariant lives in exactly one of three layers, decided by **what the check needs to see**:

| Layer | Needs | Mechanism | Severity |
|---|---|---|---|
| **Parser** | one state / one artifact, no cross-references | `ParseError` | hard stop only |
| **Validator** | another artifact, or the whole graph | `ValidationFinding` | ERROR or **WARNING** |
| **Planner** | the live issue's current markers | `OperationError` | hard stop only |

The dividing line is mechanical: *single-state/single-artifact → parser; needs another artifact or the whole graph → validator; needs the live issue → planner.* Examples that the line resolves cleanly: "`roles` required on working" is one state → **parser**; "every working state has an incoming CLAIM" is whole-graph → **validator**; "the issue isn't already claimed" needs live markers → **planner**.

Two corollaries:

- **Only the validator emits WARNING.** Parse failures and runtime precondition failures are always hard stops; advisory findings (a smell, not a blocker) are a validator-only concept.
- **The JSON schemas in `docs/schemas/` are subordinate to the parser.** They are an editor-time convenience mirror (VS Code `fileMatch`), not the source of truth. Where a schema and the parser disagree, the parser is authoritative and the schema is the bug. (This is why past schema drift — e.g. stale `[*]` authoring — was harmless to runtime but is still a defect.)

### 2. A single invariant registry as source of truth

Introduce one registry that lists every invariant as data:

```
{ id, statement, severity, layer, principle, enforcing_symbol }
```

- `layer` is exactly the guard-placement classification above — the principle is encoded in the data, not just in prose.
- Validator rules are **driven from / keyed to** the registry (the `_check_*` methods already carry docstring + severity + principle cite; they become registry-backed).
- **`docs/invariants.md` is generated** from the registry — byte-stable and covered by the same CI drift check as `generate-docs`. It cross-links `state-machine-principles.md` (the *why*) and the ADRs; it does not duplicate them.
- A **test asserts every registered invariant has a test** — coverage of the invariant set becomes a checked property, not a hope.

`docs/invariants.md` is a new document, organized by the three layers, referencing the principles and ADRs rather than restating them.

## Consequences

**Wins**:
- **Locality of invariant knowledge.** "What rejects this, and at which stage" is answerable in one place — the single biggest AI-navigability win in the codebase, since the rules are otherwise smeared across ~3,000 lines of three modules.
- **Guard placement becomes a decision rule.** New invariants have an unambiguous home, and future architecture reviews don't re-derive the layering.
- **Drift-proof.** The inventory is generated and CI-checked; it cannot silently fall out of sync with the code, and every invariant is provably tested.

**Costs**:
- **Registry-fying the inline parser raises is a real refactor.** ~40 `ParseError` sites are deeply inline; giving each a registry id and routing it through the registry is the bulk of the work.
- **Planner runtime checks need metadata** they don't currently carry (id, principle ref).
- **Upfront investment** beyond a hand-written doc — chosen deliberately over the cheaper hand-written or validator-only-generated options because "define all invariants properly" means the drift-proof end-state, not a snapshot.

## Alternatives considered

1. **Hand-write `docs/invariants.md` once.** Cheapest; drifts like all prose — rejected against this project's anti-drift discipline.
2. **Generate only the validator section** (it is already structured), hand-write parser/planner. Partial drift protection where it is cheapest. Rejected as a half-measure: the planner's runtime invariants are exactly the under-documented ones, so leaving them hand-written misses the point.
3. **Status quo — invariants scattered, no inventory.** Rejected: the documentation gap is the candidate.
