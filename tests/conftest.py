"""Pytest fixtures for the workflow tool.

Points at the shared/resources artifacts in this monorepo so parsers and
validators can be exercised against real workflow files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# This file lives in <repo>/tools/workflow-tool/tests/conftest.py.
# `parents[3]` lands on the monorepo root (parents = [tests, workflow-tool,
# tools, <repo-root>]).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_RESOURCES = _REPO_ROOT / "skills" / "workflows" / "shared" / "resources"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def shared_resources() -> Path:
    return _SHARED_RESOURCES


@pytest.fixture(scope="session")
def workflows_dir() -> Path:
    """The directory containing every shipped `*-lifecycle.mermaid`."""
    return _SHARED_RESOURCES


@pytest.fixture
def refinement_lifecycle_path(shared_resources: Path) -> Path:
    path = shared_resources / "refinement-lifecycle.mermaid"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path


@pytest.fixture
def inner_loop_lifecycle_path(shared_resources: Path) -> Path:
    path = shared_resources / "inner-loop-lifecycle.mermaid"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path


@pytest.fixture
def refinement_hcp_catalog_path(shared_resources: Path) -> Path:
    """Path to the refinement HCP catalog JSON.

    The file may not exist on pre-HITL workflows; tests using this fixture
    should handle absence (parsers return empty catalogs)."""
    return shared_resources / "refinement-hcps.json"


@pytest.fixture
def inner_loop_hcp_catalog_path(shared_resources: Path) -> Path:
    return shared_resources / "inner-loop-hcps.json"


@pytest.fixture
def roles_path(shared_resources: Path) -> Path:
    path = shared_resources / "roles.json"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path
