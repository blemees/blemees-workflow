"""Core data model — dataclasses produced by parsers, consumed by validator and operations.

Re-exports the public types so callers can `from workflow.core.model import StateMachine`
without picking specific submodules.
"""

from workflow.core.model.human_gate import (
    HumanGate,
    HumanGateCatalog,
    HumanGateLevel,
    HumanGateType,
)
from workflow.core.model.role import Role, RoleDirectory
from workflow.core.model.state_machine import (
    Collects,
    ReversibilityClass,
    Spawn,
    State,
    StateClass,
    StateMachine,
    TerminalTaxonomy,
    Transition,
    TransitionType,
)
from workflow.core.model.trust_grant import Evidence, TrustGrant, TrustGrantParameters

__all__ = [
    # StateMachine
    "StateMachine",
    "ReversibilityClass",
    "State",
    "StateClass",
    "TerminalTaxonomy",
    "Transition",
    "TransitionType",
    "Spawn",
    "Collects",
    # Human gates
    "HumanGate",
    "HumanGateCatalog",
    "HumanGateLevel",
    "HumanGateType",
    # Trust grants
    "Evidence",
    "TrustGrant",
    "TrustGrantParameters",
    # Roles
    "Role",
    "RoleDirectory",
]
