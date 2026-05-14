"""Internal helpers shared by all operation modules.

Each `run(...)` function is a thin shim: it constructs an `OperationRequest`
and dispatches to `controller.execute(request)`. The shim exists so the CLI
imports a stable per-operation entry point without leaking planner internals.
"""

from __future__ import annotations

from workflow.core.controller import Controller, OperationResult
from workflow.core.planner import OperationRequest


def dispatch(controller: Controller, request: OperationRequest) -> OperationResult:
    return controller.execute(request)
