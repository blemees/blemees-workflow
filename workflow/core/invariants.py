"""Invariant registry — the single source of truth for the framework's invariants.

Each invariant is one row of data: what it guarantees (`statement`), how severe a
violation is (`severity`), which layer enforces it (`layer`), the principle it
derives from (`principle`), and the code symbol that enforces it
(`enforcing_symbol`). Validator rules register themselves via the `@invariant`
decorator, which also stamps the invariant's id onto every finding the rule
emits. The registry drives the generated `docs/invariants.md` and the coverage
test that asserts every registered invariant is exercised.

Guard-placement principle (ADR-0004): an invariant lives in the layer that has
the information to check it —

- **parser** — shape / a single artifact, no cross-references (`ParseError`);
- **validator** — another artifact or the whole graph (`ValidationFinding`,
  the only layer that may emit a WARNING);
- **planner** — the live issue's markers (`OperationError`).

This module owns `Severity` (re-exported from `workflow.core.validator` for
backwards compatibility) so the decorator can be imported by the validator
without a circular dependency.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import TypeVar

LAYERS = ("parser", "validator", "planner")


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Invariant:
    """One row of the registry — a single constraint the framework enforces."""

    id: str
    statement: str
    severity: Severity
    layer: str  # one of LAYERS
    principle: str  # cite, e.g. "state-machine-principles.md#2" or "ADR-0002"
    enforcing_symbol: str  # fully-qualified function the rule lives in


_REGISTRY: dict[str, Invariant] = {}

# Every invariant id that has emitted at least one finding in this process. The
# coverage test uses this to assert each registered invariant is exercised by
# the test suite (it accumulates as tests run).
_emitted_ids: set[str] = set()

F = TypeVar("F", bound=Callable[..., list])


def invariant(
    *,
    id: str,
    statement: str,
    severity: Severity,
    layer: str,
    principle: str,
) -> Callable[[F], F]:
    """Register an invariant and stamp its id onto every finding the rule emits.

    Decorates a check function returning a list of findings. Registration runs
    at import time. The wrapper rewrites each returned finding with
    `invariant_id=id`, so a finding can be attributed to its invariant without
    the check body having to know its own id.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown invariant layer: {layer!r} (expected one of {LAYERS})")

    def deco(fn: F) -> F:
        symbol = f"{fn.__module__}.{fn.__qualname__}"
        if id in _REGISTRY:
            raise ValueError(f"duplicate invariant id: {id!r}")
        _REGISTRY[id] = Invariant(
            id=id,
            statement=statement,
            severity=severity,
            layer=layer,
            principle=principle,
            enforcing_symbol=symbol,
        )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            findings = [replace(f, invariant_id=id) for f in fn(*args, **kwargs)]
            if findings:
                _emitted_ids.add(id)
            return findings

        return wrapper  # type: ignore[return-value]

    return deco


def all_invariants() -> list[Invariant]:
    """Every registered invariant, sorted by (layer, id) for stable output."""
    return sorted(_REGISTRY.values(), key=lambda inv: (inv.layer, inv.id))


def invariants_for_layer(layer: str) -> list[Invariant]:
    """Registered invariants for one layer, sorted by id."""
    return [inv for inv in all_invariants() if inv.layer == layer]


def emitted_invariant_ids() -> frozenset[str]:
    """Invariant ids that have emitted ≥1 finding so far in this process."""
    return frozenset(_emitted_ids)
