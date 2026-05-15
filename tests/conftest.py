"""Pytest fixtures for the workflow tool.

Points at the in-repo `examples/.workflow/workflows/` directory so parsers,
validator, planner, and CLI smoke tests run against the canonical example
workflows shipped with the codebase. These same files are what the README's
walkthroughs reference; keeping the tests honest against them prevents the
example from drifting out of sync with the framework.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE_WORKFLOWS = _REPO_ROOT / "examples" / ".workflow" / "workflows"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def shared_resources() -> Path:
    """Legacy alias for the example workflows directory."""
    return _EXAMPLE_WORKFLOWS


@pytest.fixture(scope="session")
def workflow_dir() -> Path:
    """The directory containing every shipped `*-states.json`."""
    return _EXAMPLE_WORKFLOWS


@pytest.fixture
def refinement_workflow_path(workflow_dir: Path) -> Path:
    path = workflow_dir / "refinement-states.json"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path


@pytest.fixture
def inner_loop_workflow_path(workflow_dir: Path) -> Path:
    path = workflow_dir / "inner-loop-states.json"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path


@pytest.fixture
def refinement_hcp_catalog_path(workflow_dir: Path) -> Path:
    return workflow_dir / "refinement-hcps.json"


@pytest.fixture
def inner_loop_hcp_catalog_path(workflow_dir: Path) -> Path:
    return workflow_dir / "inner-loop-hcps.json"


@pytest.fixture
def roles_path(workflow_dir: Path) -> Path:
    path = workflow_dir / "roles.json"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path
