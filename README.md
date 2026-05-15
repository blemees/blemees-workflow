# workflow

The canonical operation mechanism for agent-driven state-machine workflows.

This tool implements the framework defined in [`agent-workflow-skill-creator`](../../skills/operator/agent-workflow-skill-creator/). It reads the framework's authoritative artifacts — workflow `.mermaid` files, process docs with HCP catalog rows, trust grants, role mappings — and exposes them as a set of operations agents and humans invoke against work items on the backing issue tracker.

The tool is backend-neutral by design. Today it ships with a GitHub backend; future backends (GitLab, Jira, Linear) implement the same operation surface against their respective trackers.

## Status

Alpha. Implementing the eleven framework operations and the GitHub backend per `hitl-principles.md` § 5 and `backends/github-encoding.md`.

## Installation

The tool is built with [uv](https://docs.astral.sh/uv/). All commands assume uv is on PATH.

```bash
cd tools/workflow-tool

# Development install (creates .venv, installs dev tooling)
uv sync --extra dev

# Or install as a user-wide CLI tool (creates a `workflow` command on PATH)
uv tool install .
```

After either install, the tool is directly invocable — no `python -m`:

```bash
workflow --version
workflow advance --to ready_for_dev --issue 123
```

For development, prefix commands with `uv run`:

```bash
uv run workflow --version
uv run pytest
uv run ruff check
uv run ruff format
```

## Usage

```bash
workflow --help                              # top-level help
workflow <operation> --help                  # per-operation help

workflow --agent-role pm init
workflow setup-labels                                          # creates state:*, wip:*, hitl:* labels on the repo
workflow create --to raw --title "Fix login bug"               # open a new work item in an initial state
workflow inbox                                                 # claimable items + actionable wip for the configured role
workflow search --state ready_for_dev --awaiting-gate '*'      # arbitrary filter combinations
workflow advance --to ready_for_dev --issue 123 --body-from packet.md
workflow approve --gate ready_for_dev --issue 123
workflow reject --gate ready_for_dev --issue 123 --body-from feedback.md
workflow request-input --issue 123 --body-from question.md
workflow respond --issue 123 --body-from response.md
```

The tool resolves the workflow's canonical artifacts at startup from a single `--workflow-dir` (or discovery), validates the contract, and dispatches the operation against the configured backend.

## The framework's eleven operations

Three groups. See `hitl-principles.md` § 5 for full semantics.

**Lifecycle:**

- `advance` — move an issue to a new state.
- `claim` — agent takes responsibility for a resting state.
- `release` — agent gives up the claim.

**Catalogued HITL — block-level:**

- `await-signal` — fire a catalogued gate; pause for human signal.
- `review` — human claims pre-action review.
- `approve` — human authorizes the transition.
- `reject` — human rejects the packet; agent iterates.

**Catalogued HITL — audit-level:**

- `record-action` — agent acts atomically; queue for retroactive review.
- `audit` — human claims post-action audit.
- `confirm` — human confirms post-hoc.
- `revoke` — human triggers remediation.

**Recognized HITL:**

- `request-input` — agent invokes for an unanticipated moment.
- `advise` — human claims response.
- `respond` — human provides input.

## Configuration

The tool's configuration travels with the **agent**, not the OS user. Each agent has its own home directory containing a `.workflow/` folder:

```
<agent-home>/
  .workflow/
    config.json          # agent identity: just `role` for now
    trust-grants/        # the agent's trust grants (per-team or shared)
      refinement/
        ready_for_dev.json
      inner-loop/
        staged.json
```

`config.json` carries *agent identity* — fields that don't change between
invocations. The recognized keys are:

- `agent-role` (required for most operations) — the agent's role id, used
  as the default actor for `claim`, `list`, etc.
- `workflow-dir` (optional) — override for where workflow files live;
  relative paths anchored to the agent home. Defaults to
  `<agent-home>/.workflow/workflows/`.
- `grants-dir` (optional) — override for the trust-grants directory;
  relative paths anchored to the agent home. Defaults to
  `<agent-home>/.workflow/trust-grants/`.

Everything else is either per-invocation or auto-discovered:

- `repo` / `host` — vary per checkout; passed via `--repo`/`--host` flags,
  env vars, or auto-discovered from `git remote get-url origin`.
- `workflow` name — roles span multiple workflows, and the tool
  auto-resolves the right one from the issue's current state via the
  workflow registry (state-name uniqueness is a framework invariant).
- Team — there's no `--team` flag; `--grants-dir` (or `grants-dir` in
  config) is the single knob for selecting which grants apply.
- Backend — github is currently the only backend; no flag to set.

### Parameter resolution

Every resolvable parameter follows the same priority cascade, highest
first:

1. **CLI flag** — explicit, this invocation.
2. **Environment variable** — the flag's default; explicit, this shell.
3. **Agent config** — `<agent-home>/.workflow/config.json`; explicit,
   this agent identity (only for fields that make sense as agent identity).
4. **Discovery** — implicit, from context.

| Parameter | CLI flag | Env var | Config key | Discovery |
|---|---|---|---|---|
| Agent home | `--agent-home` | `AGENT_HOME` | — | walk up cwd for `.workflow/` |
| Agent role | `--agent-role` | `AGENT_ROLE` | `agent-role` | — |
| Repo | `--repo` | `WORKFLOW_REPO` | — | parse `git remote get-url origin` |
| Host (GHES) | `--host` | `WORKFLOW_GH_HOST` | — | parse `git remote get-url origin` |
| Workflows dir | `--workflow-dir` | `WORKFLOW_DIR` | `workflow-dir` | `<agent-home>/.workflow/workflows/` |
| Grants dir | `--grants-dir` | `GRANTS_DIR` | `grants-dir` | `<agent-home>/.workflow/trust-grants/` |

The config-key column is only populated for fields that legitimately belong
to agent identity. `repo` / `host` vary per checkout and are discovered from
the cwd's git remote; agent home is itself discovered. Workflows and grants
directories default to the agent home's own subdirectories, but the config
keys allow pointing at shared / team-wide locations without setting env
vars per invocation.

Config keys with relative path values are anchored to the agent home (so
`{"workflow-dir": "../shared/workflows"}` resolves relative to the
`.workflow/` parent).

### GitHub Enterprise Server (GHES)

The github backend works against `github.com` and any GitHub Enterprise Server.
There's almost nothing to configure: clone the GHES repo, `cd` into it, and run
`workflow list`. The remote URL tells the tool which host and repo to talk to.

You can also be explicit:

```bash
workflow --host ghe.example.com --repo myorg/myrepo list
WORKFLOW_GH_HOST=ghe.example.com workflow list
```

Resolution for `host` (highest priority first):

1. `--host` flag or `WORKFLOW_GH_HOST` env var.
2. Parsed from the cwd's `git remote get-url origin` — except `github.com`,
   which is gh's default and doesn't need to be set explicitly.
3. Unset — `gh` falls back to its own resolution (your exported `GH_HOST`,
   or the host you authenticated against via `gh auth login --hostname …`).

When the tool has a host configured (steps 1 or 2), it sets `GH_HOST` in the
environment of every `gh` subprocess it spawns. You still need to have
authenticated against that host once (`gh auth login --hostname ghe.acme.com`);
the tool doesn't manage credentials.

Supported remote URL shapes for discovery: `https://`, `http://`, `git://`,
`ssh://` (with or without port and embedded credentials), and scp-style
shorthand (`user@host:owner/repo`).

If `--grants-dir` is not supplied, the tool defaults to `<agent-home>/.workflow/trust-grants/`.

### Canonical artifact file names

The tool reads structured data only — no markdown parsing. Every file lives
directly in the `--workflow-dir`:

- `<workflow>-states.json` — the canonical state-machine definition (one
  per workflow). The `<workflow>-states.mermaid` file alongside it is a
  **generated** visualization regenerated by `workflow render-states`. See
  [`docs/workflow-authoring.md`](docs/workflow-authoring.md) for the schema
  and authoring rules, and [`docs/state-machine-principles.md`](docs/state-machine-principles.md)
  for the design principles.
- `<workflow>-hcps.json` — the HCP catalog (one per workflow; optional —
  absence means no catalogued HCPs).
- `roles.json` — the role directory (shared across workflows).
- `<gate>.json` files under `trust-grants/<workflow>/` — per-team
  relaxations. The trust-grants directory is resolved separately (per
  `--grants-dir` or the agent home's `.workflow/trust-grants/` default).

Human-readable prose process docs (`<workflow>-process.md`) may exist
alongside these but are NOT parsed by the tool. They are for human reading
only.

### Example `<agent-home>/.workflow/config.json`

```json
{
  "agent-role": "pm"
}
```

`agent-role` is the only required field (set via
`workflow --agent-role <role> init`). Everything else is per-invocation or
auto-discovered.

An agent that uses a non-default workflows directory and a shared grants
directory might look like:

```json
{
  "agent-role": "pm",
  "workflow-dir": "../shared/workflows",
  "grants-dir": "../shared/grants/refinement"
}
```

Both path keys accept absolute paths too; relative ones are anchored to
the agent home.

## Architecture

```
workflow/
  cli.py                       # argparse / click entry — one sub-command per operation
  core/
    model/                     # dataclasses: StateMachine, State, Transition, HCP, TrustGrant, Role
    parser/                    # mermaid, process doc, trust grant, roles parsers
    validator.py               # cross-reference checks + static rules
    planner.py                 # operation → marker change set
    controller.py              # operation orchestration
    operations/                # one module per framework operation
  backends/
    base.py                    # TrackerBackend protocol
    github.py                  # GitHub implementation (uses gh CLI)
tests/
```

The core is backend-agnostic. The `backends/` layer is the only place that talks to a specific tracker. To support a new backend, implement the `TrackerBackend` protocol; nothing in `core/` changes.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check
uv run ruff format
```

`pyproject.toml` configures both pytest and ruff (target Python 3.10, 100-char line length, rule set: `E F I B UP W`).

## License

MIT
