"""Invariant registry tests — well-formedness, coverage, and doc sync (ADR-0004)."""

from __future__ import annotations

import inspect
from pathlib import Path

from workflow.core.emitter.invariants import emit_invariants_doc
from workflow.core.invariants import (
    LAYERS,
    Severity,
    all_invariants,
    emitted_invariant_ids,
    invariants_for_layer,
)


def test_registry_is_well_formed() -> None:
    invs = all_invariants()
    assert invs, "registry is empty"
    ids = [i.id for i in invs]
    assert len(ids) == len(set(ids)), "duplicate invariant ids"
    for inv in invs:
        assert inv.id and inv.id.isupper(), f"id not SCREAMING_SNAKE: {inv.id!r}"
        assert inv.statement.strip(), f"{inv.id}: empty statement"
        assert inv.layer in LAYERS, f"{inv.id}: bad layer {inv.layer!r}"
        assert isinstance(inv.severity, Severity)
        assert inv.principle.strip(), f"{inv.id}: empty principle"
        assert inv.enforcing_symbol, f"{inv.id}: no enforcing symbol"


def test_only_validator_layer_may_warn() -> None:
    # Guard-placement (ADR-0004): parser and planner are hard stops; WARNING is
    # an advisory severity that only the validator layer may emit.
    for inv in all_invariants():
        if inv.severity is Severity.WARNING:
            assert inv.layer == "validator", f"{inv.id}: WARNING outside the validator layer"


def test_generated_invariants_doc_is_in_sync() -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "invariants.md"
    assert doc.read_text(encoding="utf-8") == emit_invariants_doc(), (
        "docs/invariants.md is stale — regenerate with `python -m workflow.core.emitter.invariants`"
    )


def test_every_validator_invariant_is_exercised_by_a_test() -> None:
    """ADR-0004: every registered invariant has a test.

    Drive every zero-argument test in `test_validator.py`, then assert each
    validator invariant emitted at least one finding (findings are id-stamped by
    the @invariant decorator). Running the tests here makes the check
    order-independent rather than relying on suite execution order.
    """
    import tests.test_validator as tv

    for name, fn in vars(tv).items():
        if not name.startswith("test_") or not callable(fn):
            continue
        if inspect.signature(fn).parameters:
            continue  # needs a fixture — skip; not a plain trigger
        fn()

    registered = {i.id for i in invariants_for_layer("validator")}
    missing = registered - emitted_invariant_ids()
    assert not missing, f"validator invariants with no exercising test: {sorted(missing)}"
