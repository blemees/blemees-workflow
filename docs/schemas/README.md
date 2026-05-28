# JSON Schemas

Structural schemas for the five authored file types in `blemees-workflow`. **Editor-side feedback only** — the canonical validation is the Python parsers in `workflow/core/parser/` and the cross-reference checks in `workflow/core/validator.py`, surfaced via `workflow validate-workflow`. The schemas mostly cover type / enum constraints; cross-field rules (terminal-needs-taxonomy, gate-only-when-hitl, current_level-in-allowed_levels, etc.) stay in Python.

## Files

| Schema | Validates |
|---|---|
| [`states.schema.json`](states.schema.json) | `<process>-states.json` — the state machine for one process. |
| [`human-gates.schema.json`](human-gates.schema.json) | `<process>-human-gates.json` — the human-gate catalog for one process. |
| [`roles.schema.json`](roles.schema.json) | `roles.json` — the shared role directory. |
| [`issue-types.schema.json`](issue-types.schema.json) | `issue-types.json` — the shared issue-type directory. |
| [`trust-grant.schema.json`](trust-grant.schema.json) | `trust-grants/<process>/<gate>.json` — one trust grant per file. |

## Editor setup

### Per-file via `$schema`

Reference the schema from any file you author:

```json
{
  "$schema": "../../docs/schemas/states.schema.json",
  "states": { … }
}
```

VS Code, JetBrains IDEs, and most JSON-aware editors pick this up automatically. The Python parsers ignore unknown top-level keys, so the field is safe to leave in.

### Workspace-wide via `.vscode/settings.json`

To avoid adding `$schema` to every file, configure VS Code globally:

```json
{
  "json.schemas": [
    {
      "fileMatch": ["**/*-states.json"],
      "url": "./docs/schemas/states.schema.json"
    },
    {
      "fileMatch": ["**/*-human-gates.json"],
      "url": "./docs/schemas/human-gates.schema.json"
    },
    {
      "fileMatch": ["**/roles.json"],
      "url": "./docs/schemas/roles.schema.json"
    },
    {
      "fileMatch": ["**/issue-types.json"],
      "url": "./docs/schemas/issue-types.schema.json"
    },
    {
      "fileMatch": ["**/trust-grants/**/*.json"],
      "url": "./docs/schemas/trust-grant.schema.json"
    }
  ]
}
```

## Why schemas + Python validation

JSON Schema is good at: enum membership, required-field presence, type checks, basic structural assertions — the things an editor can squiggle in real time. It struggles with: conditional requiredness across fields, cross-file references, date-relative checks, principle citations. Those live in `workflow/core/validator.py`, which produces actionable findings with file/line context and principle cites.

The schemas duplicate a sliver of the parser's contract for authoring ergonomics; the parser remains the source of truth.
