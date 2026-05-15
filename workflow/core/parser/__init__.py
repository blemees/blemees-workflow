"""Parsers — read framework artifacts into the core data model.

All structured artifacts are JSON — workflows, catalogs, roles, trust grants.
The `.mermaid` file is no longer authored: it is generated from the canonical
`<name>-states.json` for visualization (see
`workflow.core.emitter.mermaid`). The legacy mermaid parser is kept around
under `parser.mermaid` for migration tooling and historical tests, but the
runtime always reads JSON.

- `state_machine` — workflow `.json` files into `StateMachine`.
- `hcp_catalog` — `<workflow>-hcps.json` into `HCPCatalog`.
- `trust_grant` — trust-grant `.json` files into `TrustGrant`.
- `role_directory` — `roles.json` into `RoleDirectory`.
- `issue_type_directory` — `issue-types.json` into `IssueTypeDirectory`.

Each parser fails loudly on malformed input: structured-data parsers raise
`ParseError` on JSON errors or schema violations. Missing files are
distinguished from missing fields — absence of an optional artifact is fine
(empty result), but a present-but-malformed artifact is a hard error.
"""

from workflow.core.parser.hcp_catalog import parse_hcp_catalog
from workflow.core.parser.issue_type_directory import parse_issue_type_directory
from workflow.core.parser.role_directory import parse_role_directory
from workflow.core.parser.state_machine import parse_state_machine
from workflow.core.parser.trust_grant import (
    load_team_grants,
    parse_trust_grant,
)

__all__ = [
    "parse_state_machine",
    "parse_hcp_catalog",
    "parse_trust_grant",
    "load_team_grants",
    "parse_role_directory",
    "parse_issue_type_directory",
]
