"""Configuration loader — resolves workflow artifacts, trust grants, and agent-level config.

## Discovery model

The tool operates against a **workflows directory** (where the canonical
`*-states.json`, `*-hcps.json`, and `roles.json` files live) and,
separately, an **agent home** — a directory on the user's filesystem that
represents one agent's identity. Each agent has its own home; configuration
that lives there does not bleed across agents.

The agent home contains a `.workflow/` directory:

    <agent-home>/
      .workflow/
        config.json         # agent identity: role, team, backend
        trust-grants/       # this agent's team-specific trust grants
          refinement/
            ready_for_dev.json
          inner-loop/
            staged.json

### Agent home discovery (priority order)

1. `AGENT_HOME` environment variable — if set and contains a `.workflow/` directory.
2. Walk up from cwd looking for a directory that contains `.workflow/`.
3. None — operations that depend on agent-level config fail with `ConfigError`.

### Workflows directory discovery (priority order)

1. Explicit `workflow_dir` param (typically from CLI `--workflow-dir`).
2. `WORKFLOW_DIR` env var.
3. `<agent-home>/.workflow/workflows/` — workflows colocated with the agent.

Discovery does NOT walk up cwd. Workflows are agent-scoped or
explicit, never inferred from the user's current checkout.

### Trust grants

If `grants_dir` is not specified explicitly, the loader defaults to
`<agent-home>/.workflow/trust-grants/` when an agent home is discovered.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workflow.backends.base import TrackerBackend
from workflow.core.model.hcp import HCPCatalog
from workflow.core.model.role import RoleDirectory
from workflow.core.model.state_machine import StateMachine
from workflow.core.model.trust_grant import TrustGrant
from workflow.core.parser import (
    load_team_grants,
    parse_hcp_catalog,
    parse_role_directory,
    parse_state_machine,
)
from workflow.errors import ConfigError

logger = logging.getLogger(__name__)

_AGENT_CONFIG_DIR = ".workflow"
_AGENT_CONFIG_FILE = "config.json"
_AGENT_GRANTS_SUBDIR = "trust-grants"
_AGENT_WORKFLOWS_SUBDIR = "workflows"


@dataclass
class Process:
    process_name: str
    state_machine: StateMachine
    catalog: HCPCatalog | None = None
    role_directory: RoleDirectory | None = None
    grants: dict[str, TrustGrant] = field(default_factory=dict)
    backend: TrackerBackend | None = None
    workflow_dir: Path | None = None  # dir containing *-states.json files
    agent_home: Path | None = None
    agent_config: dict[str, Any] = field(default_factory=dict)
    agent_role: str | None = None  # from agent_config["role"]; used for defaults and validation


def discover_agent_home(start: Path | None = None) -> Path | None:
    """Discover the agent home directory.

    1. `AGENT_HOME` env var if set and contains a `.workflow/` subdir.
    2. Walk up from `start` (default: cwd) looking for any dir containing `.workflow/`.
    3. Return None if no agent home is found.
    """
    env = os.environ.get("AGENT_HOME")
    if env:
        candidate = Path(env).expanduser().resolve()
        if (candidate / _AGENT_CONFIG_DIR).is_dir():
            return candidate
        logger.warning("AGENT_HOME=%s but %s/ directory not found there.", env, _AGENT_CONFIG_DIR)

    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / _AGENT_CONFIG_DIR).is_dir():
            return candidate
    return None


def load_agent_config(agent_home: Path) -> dict[str, Any]:
    """Load `<agent-home>/.workflow/config.json` if it exists.

    Returns an empty dict if the file is absent. Raises ConfigError on parse failure
    (invalid JSON fails loudly; we never silently degrade an agent's contract).
    """
    config_path = agent_home / _AGENT_CONFIG_DIR / _AGENT_CONFIG_FILE
    if not config_path.is_file():
        return {}
    try:
        with config_path.open() as f:
            text = f.read()
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Failed to parse {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"{config_path} must be a JSON object at the top level (got {type(data).__name__})."
        )
    return data


def default_grants_dir(agent_home: Path | None) -> Path | None:
    """Return the conventional trust-grants directory inside the agent home, if it exists."""
    if agent_home is None:
        return None
    candidate = agent_home / _AGENT_CONFIG_DIR / _AGENT_GRANTS_SUBDIR
    return candidate if candidate.is_dir() else None


def default_workflows_dir(agent_home: Path | None) -> Path | None:
    """Return the conventional workflows directory inside the agent home, if it exists."""
    if agent_home is None:
        return None
    candidate = agent_home / _AGENT_CONFIG_DIR / _AGENT_WORKFLOWS_SUBDIR
    return candidate if candidate.is_dir() else None


def discover_workflows_dir(
    agent_home: Path | None = None,
    agent_config: dict[str, Any] | None = None,
) -> Path | None:
    """Locate the directory containing the workflow files.

    Resolution order:

    1. `WORKFLOW_DIR` env var — used directly if set.
    2. Agent config `workflow-dir` key — relative paths anchored to the
       agent home.
    3. `<agent-home>/.workflow/workflows/` — the default location when the
       config key is absent.

    Returns None if no source produces an existing directory. Discovery
    does NOT walk up cwd; workflows are agent-scoped, not checkout-scoped.
    """
    env = os.environ.get("WORKFLOW_DIR")
    if env:
        candidate = Path(env).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        logger.warning("WORKFLOW_DIR=%s but directory does not exist.", env)
        return None

    if agent_config and "workflow-dir" in agent_config:
        resolved = _resolve_agent_path(agent_home, agent_config["workflow-dir"])
        if resolved is not None and resolved.is_dir():
            return resolved
        logger.warning(
            "config workflow-dir=%r but resolved path does not exist.",
            agent_config["workflow-dir"],
        )
        return None

    return default_workflows_dir(agent_home)


def discover_grants_dir(
    agent_home: Path | None = None,
    agent_config: dict[str, Any] | None = None,
) -> Path | None:
    """Locate the trust-grants directory.

    Resolution order:

    1. `GRANTS_DIR` env var — used directly if set.
    2. Agent config `grants-dir` key — relative paths anchored to the
       agent home.
    3. `<agent-home>/.workflow/trust-grants/` — the default when the config
       key is absent.

    Returns None if no source produces an existing directory.
    """
    env = os.environ.get("GRANTS_DIR")
    if env:
        candidate = Path(env).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        logger.warning("GRANTS_DIR=%s but directory does not exist.", env)
        return None

    if agent_config and "grants-dir" in agent_config:
        resolved = _resolve_agent_path(agent_home, agent_config["grants-dir"])
        if resolved is not None and resolved.is_dir():
            return resolved
        logger.warning(
            "config grants-dir=%r but resolved path does not exist.",
            agent_config["grants-dir"],
        )
        return None

    return default_grants_dir(agent_home)


def load_process(
    process_name: str | None = None,
    workflow_dir: Path | None = None,
    grants_dir: Path | None = None,
    backend: TrackerBackend | None = None,
    agent_home: Path | None = None,
) -> Process:
    """Resolve and parse the workflow's canonical artifacts.

    `process_name` is the name of the workflow to load (e.g., `refinement`);
    the corresponding files are read from `<workflow_dir>/<name>-states.json`
    and `<workflow_dir>/<name>-hcps.json`. `roles.json` is read from the
    same directory.

    Discovery: if `workflow_dir` is not supplied, it is found via
    `discover_workflows_dir` (env var `WORKFLOW_DIR`, then walking up from
    cwd). The agent home, if discovered, contributes defaults from
    `.workflow/config.json` and the default location for `trust-grants/`.
    """
    # Discover agent home and load its config.
    agent_home = agent_home or discover_agent_home()
    agent_config: dict[str, Any] = load_agent_config(agent_home) if agent_home is not None else {}

    # NOTE: `workflow`, `repo`, `host`, `team`, and `backend` are
    # intentionally NOT read from agent config. State-name uniqueness routes
    # the workflow per-invocation; --repo/--host are per-checkout; team
    # selection is subsumed by --grants-dir; only one backend exists.

    workflow_dir = workflow_dir or discover_workflows_dir(
        agent_home=agent_home, agent_config=agent_config
    )
    if workflow_dir is None:
        raise ConfigError(
            "Cannot find a workflows directory. Pass --workflow-dir, set "
            "WORKFLOW_DIR, or run from inside a repo containing "
            "`skills/workflows/shared/resources/` (or a directory with "
            "`*-states.json` files)."
        )
    if process_name is None:
        raise ConfigError("load_process requires a process_name to resolve the workflow file.")

    state_machine_path = workflow_dir / f"{process_name}-states.json"
    if not state_machine_path.exists():
        raise ConfigError(
            f"StateMachine file not found: {state_machine_path}. "
            f"Expected `<workflow_dir>/{process_name}-states.json`."
        )
    state_machine = parse_state_machine(state_machine_path)

    # HCP catalog (optional — pre-HITL workflows have none).
    catalog: HCPCatalog | None = None
    hcp_catalog_path = workflow_dir / f"{process_name}-hcps.json"
    if hcp_catalog_path.exists():
        catalog = parse_hcp_catalog(hcp_catalog_path)

    # Role directory (optional, shared across workflows).
    role_directory: RoleDirectory | None = None
    roles_path = workflow_dir / "roles.json"
    if roles_path.exists():
        role_directory = parse_role_directory(roles_path)

    # Trust grants — priority: explicit grants_dir > env > agent config > default.
    grants: dict[str, TrustGrant] = {}
    if grants_dir is None:
        grants_dir = discover_grants_dir(agent_home=agent_home, agent_config=agent_config)
    if grants_dir is not None and grants_dir.exists():
        grants = load_team_grants(grants_dir)

    agent_role = agent_config.get("agent-role")
    if agent_role is not None and not isinstance(agent_role, str):
        raise ConfigError(
            f"agent config `agent-role` must be a string (got {type(agent_role).__name__})."
        )
    if isinstance(agent_role, str):
        agent_role = agent_role.strip("{}").strip() or None

    return Process(
        process_name=process_name or (state_machine.name or "unnamed"),
        state_machine=state_machine,
        catalog=catalog,
        role_directory=role_directory,
        grants=grants,
        backend=backend,
        workflow_dir=workflow_dir,
        agent_home=agent_home,
        agent_config=agent_config,
        agent_role=agent_role,
    )


def _resolve_agent_path(agent_home: Path | None, value: str) -> Path | None:
    """Resolve a path string from agent config.

    Absolute paths are used as-is; relative paths are anchored to the agent home.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if agent_home is None:
        return (Path.cwd() / path).resolve()
    return (agent_home / path).resolve()


# --------------------------------------------------------------------------- #
# Multi-workflow registry
#
# A role often participates in multiple workflows (a PM works on refinement,
# postmortem, prioritization; a developer works on inner-loop, hotfix, etc.).
# The agent's config should not pin them to a single workflow; instead the
# tool discovers all workflows in the repo and routes operations by the
# state-name uniqueness invariant (no state name appears in two workflows).
#
# The registry lazily loads each workflow's workflow + catalog. Queries
# that span all workflows (e.g., `list --role`) iterate the registry.
# Per-issue operations resolve the workflow from the issue's current state.


@dataclass
class Workflow:
    """Discovered workflows in a workflows directory, indexed by name and state.

    Lazy-loaded: each workflow's workflow and catalog are parsed on first
    access (or all-at-once via `load_all`). The state-to-workflow index is
    built incrementally as workflows load.
    """

    workflow_dir: Path
    agent_home: Path | None = None
    agent_config: dict[str, Any] = field(default_factory=dict)
    backend: TrackerBackend | None = None
    grants_dir: Path | None = None
    _contexts: dict[str, Process] = field(default_factory=dict)
    _state_to_process: dict[str, str] = field(default_factory=dict)
    _discovered_processes: list[str] | None = None

    def discovered_processes(self) -> list[str]:
        """List every workflow name discoverable in the workflows directory."""
        if self._discovered_processes is None:
            self._discovered_processes = _discover_processes(self.workflow_dir)
        return list(self._discovered_processes)

    def get_process(self, name: str) -> Process:
        """Load (or return cached) context for the named workflow."""
        if name in self._contexts:
            return self._contexts[name]
        context = load_process(
            process_name=name,
            workflow_dir=self.workflow_dir,
            grants_dir=self.grants_dir,
            backend=self.backend,
            agent_home=self.agent_home,
        )
        self._contexts[name] = context
        # Update state-to-workflow index.
        for state_name in context.state_machine.states:
            self._state_to_process.setdefault(state_name, name)
        return context

    def find_process_for_state(self, state_name: str) -> str | None:
        """Return the workflow name whose workflow contains the given state,
        or None if no workflow does. Loads workflows on demand to populate
        the index."""
        if state_name in self._state_to_process:
            return self._state_to_process[state_name]
        # Force-load all undiscovered workflows.
        for name in self.discovered_processes():
            if name not in self._contexts:
                try:
                    self.get_process(name)
                except (ConfigError, Exception):
                    logger.debug("Skipping malformed workflow %r during state lookup.", name)
                    continue
                if state_name in self._state_to_process:
                    return self._state_to_process[state_name]
        return None

    def load_all(self) -> dict[str, Process]:
        """Eagerly load every discovered workflow. Returns the full map."""
        for name in self.discovered_processes():
            if name not in self._contexts:
                try:
                    self.get_process(name)
                except (ConfigError, Exception) as exc:
                    logger.warning("Failed to load workflow %r: %s", name, exc)
        return dict(self._contexts)


def _discover_processes(workflow_dir: Path) -> list[str]:
    """Scan a directory for `*-states.json` and return process names."""
    if not workflow_dir.is_dir():
        return []
    names: list[str] = []
    for path in sorted(workflow_dir.glob("*-states.json")):
        stem = path.stem
        if stem.endswith("-states"):
            names.append(stem[: -len("-states")])
    return names


def build_registry(
    agent_home: Path | None = None,
    workflow_dir: Path | None = None,
    backend: TrackerBackend | None = None,
    grants_dir: Path | None = None,
) -> Workflow | None:
    """Build a registry for the current invocation context.

    Returns None if no `workflow_dir` can be discovered — multi-workflow
    queries aren't possible without a workflow source tree.
    """
    agent_home = agent_home or discover_agent_home()
    agent_config = load_agent_config(agent_home) if agent_home is not None else {}
    workflow_dir = workflow_dir or discover_workflows_dir(
        agent_home=agent_home, agent_config=agent_config
    )
    if workflow_dir is None:
        return None
    if grants_dir is None:
        grants_dir = discover_grants_dir(agent_home=agent_home, agent_config=agent_config)
    return Workflow(
        workflow_dir=workflow_dir,
        agent_home=agent_home,
        agent_config=agent_config,
        backend=backend,
        grants_dir=grants_dir,
    )
