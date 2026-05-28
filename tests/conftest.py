"""Pytest fixtures for the workflow tool.

Points at the in-repo `examples/workflows/` directory so parsers,
validator, planner, and CLI smoke tests run against the canonical example
workflows shipped with the codebase. The shared `workflows/` folder is
referenced by every per-role agent home under `examples/<role>/.workflow/`
via a `workflow-dir` config entry. Keeping the tests honest against the
shipped files prevents the examples from drifting out of sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE_WORKFLOWS = _REPO_ROOT / "examples" / "workflows"


@pytest.fixture(autouse=True)
def _isolate_capability_cache(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin XDG_CONFIG_HOME at a tmp dir so tests don't read/write the user's
    real capability cache under `~/.config/blemees-workflow/`. Autouse so
    every test gets a clean cache automatically.
    """
    cache_dir = tmp_path_factory.mktemp("xdg-config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cache_dir))


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
    return workflow_dir / "refinement-human-gates.json"


@pytest.fixture
def inner_loop_hcp_catalog_path(workflow_dir: Path) -> Path:
    return workflow_dir / "inner-loop-human-gates.json"


@pytest.fixture
def roles_path(workflow_dir: Path) -> Path:
    path = workflow_dir / "roles.json"
    if not path.exists():
        pytest.skip(f"Sample artifact missing: {path}")
    return path
