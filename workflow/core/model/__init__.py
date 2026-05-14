"""Core data model — dataclasses produced by parsers, consumed by validator and operations.

Re-exports the public types so callers can `from workflow.core.model import Lifecycle`
without picking specific submodules.
"""

from workflow.core.model.hcp import HCP, HCPCatalog, HCPLevel, HCPType
from workflow.core.model.lifecycle import (
    Lifecycle,
    ReversibilityClass,
    State,
    StateClass,
    TerminalTaxonomy,
    Transition,
    TransitionType,
)
from workflow.core.model.role import Role, RoleDirectory
from workflow.core.model.trust_grant import Evidence, TrustGrant, TrustGrantParameters

__all__ = [
    # Lifecycle
    "Lifecycle",
    "ReversibilityClass",
    "State",
    "StateClass",
    "TerminalTaxonomy",
    "Transition",
    "TransitionType",
    # HCP
    "HCP",
    "HCPCatalog",
    "HCPLevel",
    "HCPType",
    # Trust grants
    "Evidence",
    "TrustGrant",
    "TrustGrantParameters",
    # Roles
    "Role",
    "RoleDirectory",
]
