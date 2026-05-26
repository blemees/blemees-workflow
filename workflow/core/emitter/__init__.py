"""Emitters — render canonical model objects to presentation formats.

The JSON sources are the canonical contract; everything in this module is
regenerated. `workflow generate-docs` invokes these emitters to materialise:

- `<process>-states.mermaid` — state diagrams (`mermaid.emit_mermaid`).
- `<process>.md` — per-process reference docs (`docs.emit_process_doc`).
- `roles.md`, `issue-types.md`, `input-topics.md`, `README.md` — shared / index docs.

Pre-commit hooks or CI checks should verify these files are up-to-date.
"""

from workflow.core.emitter.docs import (
    ProcessDocInput,
    emit_index_doc,
    emit_input_topics_doc,
    emit_issue_types_doc,
    emit_process_doc,
    emit_process_map,
    emit_process_map_doc,
    emit_roles_doc,
)
from workflow.core.emitter.mermaid import emit_mermaid

__all__ = [
    "emit_mermaid",
    "emit_process_doc",
    "emit_roles_doc",
    "emit_issue_types_doc",
    "emit_input_topics_doc",
    "emit_process_map",
    "emit_process_map_doc",
    "emit_index_doc",
    "ProcessDocInput",
]
