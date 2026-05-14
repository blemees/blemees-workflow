"""Mermaid lifecycle parser.

Parses a stateDiagram-v2 .mermaid file into a `Lifecycle`. Extracts states,
transitions, the comment-block legend (canonical catalog path + HITL gate
listing per `hitl-principles.md` principle 9), reversibility-class
declarations from notes, and terminal taxonomy tags.

The parser is permissive — it accepts pre-HITL artifacts (no `[hitl]`
markers, no legend) and simply leaves the corresponding fields empty. The
validator catches missing HITL annotations as warnings.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from workflow.core.model.lifecycle import (
    Lifecycle,
    ReversibilityClass,
    State,
    StateClass,
    TerminalTaxonomy,
    Transition,
    TransitionType,
)

logger = logging.getLogger(__name__)


# stateDiagram-v2 has two relevant line shapes:
#   <src> --> <dst>: <label>
#   <src> --> <dst>
# Sources or destinations may be `[*]` (start/end marker — skipped from State
# set but still relevant for terminal classification).
_TRANSITION_RE = re.compile(
    r"^\s*(?P<src>\[\*\]|[A-Za-z_][\w]*)\s*-->\s*(?P<dst>\[\*\]|[A-Za-z_][\w]*)\s*(?::\s*(?P<label>.+?))?\s*$"
)

# Legend comment lines look like:
#   %% HITL gates (canonical: <path>):
#   %%   <gate_name> <reversibility-class>
# We're lenient about whitespace inside the comment payload.
_LEGEND_HEADER_RE = re.compile(
    r"HITL\s+gates\s*\(canonical:\s*(?P<path>[^)]+?)\)\s*:?\s*$",
    re.IGNORECASE,
)
_LEGEND_ENTRY_RE = re.compile(
    r"^(?P<gate>[A-Za-z_][\w-]*)\s+(?P<rev>irreversible|reversible-fast|reversible-slow|mixed-reversibility)"
    r"(?:\s+\(.*\))?\s*$",
    re.IGNORECASE,
)

# Notes embedded in mermaid may carry reversibility hints like
#   note right of staged: irreversible
# We also pick up terminal taxonomy via `terminal (<tag>)` markers commonly
# used on terminal transitions (e.g., `wont_fix --> [*]: terminal (abandoned)`).
_TERMINAL_TAXONOMY_RE = re.compile(
    r"terminal\s*\(\s*(?P<tag>shipped|reverted|abandoned|deduplicated|iterated|aborted|stabilized|resolved)\s*\)",
    re.IGNORECASE,
)
_REVERSIBILITY_RE = re.compile(
    r"\b(irreversible|reversible-fast|reversible-slow)\b",
    re.IGNORECASE,
)
# Notes may declare which role claims a state, e.g.:
#   note left of raw: claim-role=pm
#   note right of ready_for_dev: claim-role="developer"
_CLAIM_ROLE_RE = re.compile(
    r"\bclaim-role\s*=\s*\{?\s*[\"']?(?P<role>[A-Za-z_][\w-]*)[\"']?\s*\}?",
    re.IGNORECASE,
)


def parse_lifecycle(source: str | Path, name: str | None = None) -> Lifecycle:
    """Parse a .mermaid file (path or raw text) into a `Lifecycle`.

    `name` defaults to the filename stem (`refinement-lifecycle.mermaid` →
    `refinement-lifecycle`) or `"unnamed"` if not derivable.
    """
    source_path: str | None = None
    if isinstance(source, Path) or (
        isinstance(source, str) and "\n" not in source and Path(source).exists()
    ):
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        source_path = str(path)
        if name is None:
            name = path.stem
    else:
        text = str(source)
        if name is None:
            name = "unnamed"

    return _parse_text(text, name, source_path)


def _parse_text(text: str, name: str, source_path: str | None) -> Lifecycle:
    lifecycle = Lifecycle(name=name, source_path=source_path)

    # Pass 1: scan top-of-file legend comments + collect note blocks.
    notes_by_state: dict[str, list[str]] = {}
    in_note_block = False
    current_note_state: str | None = None
    current_note_lines: list[str] = []

    lines = text.splitlines()
    in_legend = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        # ---- Legend extraction (%% comments) ----
        if stripped.startswith("%%"):
            payload = stripped[2:].strip()
            header_match = _LEGEND_HEADER_RE.search(payload)
            if header_match:
                in_legend = True
                lifecycle.canonical_catalog_path = header_match.group("path").strip()
                continue
            if in_legend:
                # Subsequent comment lines are legend entries until a blank
                # comment or a non-comment line breaks the block.
                if not payload:
                    in_legend = False
                    continue
                entry_match = _LEGEND_ENTRY_RE.match(payload)
                if entry_match:
                    gate = entry_match.group("gate")
                    rev_raw = entry_match.group("rev").lower()
                    rev = _map_reversibility(rev_raw)
                    if rev is not None:
                        lifecycle.gates_in_legend[gate] = rev
                    else:
                        # `mixed-reversibility` is a documentation form; we
                        # do not encode it in the model — the worst-case
                        # destination's class governs (per principle 8).
                        logger.debug(
                            "Mixed-reversibility legend entry %r; "
                            "actual class derives from destinations.",
                            gate,
                        )
                else:
                    # Not an entry; legend has ended.
                    in_legend = False
            # Other %% comments are ignored.
            continue

        # ---- Note blocks (`note <side> of <state>` … `end note`) ----
        if not in_note_block:
            note_open_match = re.match(
                r"^note\s+(?:right|left|above|below|over)\s+of\s+(?P<state>[A-Za-z_][\w]*)\b.*$",
                stripped,
            )
            if note_open_match:
                in_note_block = True
                current_note_state = note_open_match.group("state")
                current_note_lines = []
                # Inline note: `note left of X: <text>`
                inline_match = re.match(
                    r"^note\s+(?:right|left|above|below|over)\s+of\s+[A-Za-z_][\w]*\s*:\s*(.+)$",
                    stripped,
                )
                if inline_match:
                    current_note_lines.append(inline_match.group(1))
                    notes_by_state.setdefault(current_note_state, []).extend(current_note_lines)
                    in_note_block = False
                    current_note_state = None
                    current_note_lines = []
                continue
        else:
            if stripped.startswith("end note"):
                if current_note_state is not None:
                    notes_by_state.setdefault(current_note_state, []).extend(current_note_lines)
                in_note_block = False
                current_note_state = None
                current_note_lines = []
                continue
            current_note_lines.append(stripped)
            continue

        # ---- Transition extraction ----
        match = _TRANSITION_RE.match(line)
        if not match:
            continue
        src = match.group("src")
        dst = match.group("dst")
        label = (match.group("label") or "").strip()
        _register_states(lifecycle, src, dst)
        _register_transition(lifecycle, src, dst, label)

    # Pass 2: classify states + apply note-derived metadata.
    _classify_states(lifecycle, notes_by_state)
    _apply_legend_to_states(lifecycle)

    return lifecycle


def _map_reversibility(raw: str) -> ReversibilityClass | None:
    lowered = raw.lower()
    match lowered:
        case "irreversible":
            return ReversibilityClass.IRREVERSIBLE
        case "reversible-fast":
            return ReversibilityClass.REVERSIBLE_FAST
        case "reversible-slow":
            return ReversibilityClass.REVERSIBLE_SLOW
        case _:
            return None


def _register_states(lifecycle: Lifecycle, src: str, dst: str) -> None:
    for name in (src, dst):
        if name == "[*]":
            continue
        if name not in lifecycle.states:
            # Default to RESTING; pass 2 reclassifies.
            lifecycle.states[name] = State(
                name=name,
                state_class=StateClass.RESTING,
            )


def _register_transition(lifecycle: Lifecycle, src: str, dst: str, label: str) -> None:
    is_gated = "[hitl]" in label.lower()
    cleaned_label = re.sub(r"\s*\[hitl\]\s*$", "", label, flags=re.IGNORECASE).strip()
    transition_type = _infer_transition_type(cleaned_label)
    # `[*] --> X` is a process-entry edge; treat as cross-process for legend
    # purposes when the label says so, else external.
    if src == "[*]" or dst == "[*]":
        if "to process" in cleaned_label.lower() or "from process" in cleaned_label.lower():
            transition_type = TransitionType.CROSS_PROCESS
        elif transition_type is TransitionType.ROLE_ACTION:
            transition_type = TransitionType.EXTERNAL
    lifecycle.transitions.append(
        Transition(
            source=src,
            destination=dst,
            label=cleaned_label,
            is_gated=is_gated,
            transition_type=transition_type,
        )
    )


def _infer_transition_type(label: str) -> TransitionType:
    lowered = label.lower()
    if "claims" in lowered or " claim " in lowered or lowered.endswith(" claim"):
        return TransitionType.CLAIM
    if "(external)" in lowered or "(time)" in lowered:
        return TransitionType.EXTERNAL
    if "to process" in lowered or "from process" in lowered:
        return TransitionType.CROSS_PROCESS
    return TransitionType.ROLE_ACTION


def _classify_states(lifecycle: Lifecycle, notes_by_state: dict[str, list[str]]) -> None:
    """Classify states as RESTING / WORKING / TERMINAL using outgoing/incoming
    transitions, the `[*]` sink, and label-derived heuristics."""

    # A state with no outgoing transitions to other states (and at least one
    # incoming) is terminal. Also: a state with an outgoing transition to `[*]`
    # whose label is `terminal (...)` or similar is terminal.
    terminal_state_names: set[str] = set()
    working_state_names: set[str] = set()

    for state_name in list(lifecycle.states.keys()):
        outgoing = [t for t in lifecycle.transitions if t.source == state_name]
        non_sink_outgoing = [t for t in outgoing if t.destination != "[*]"]
        sink_outgoing = [t for t in outgoing if t.destination == "[*]"]

        if not non_sink_outgoing:
            # All outgoing edges (if any) go to [*], or there are none — terminal.
            if sink_outgoing or not outgoing:
                terminal_state_names.add(state_name)

        # Heuristic: states whose name ends in `-ing` and which are
        # destinations of claim transitions are working.
        incoming = [t for t in lifecycle.transitions if t.destination == state_name]
        is_claim_destination = any(t.transition_type is TransitionType.CLAIM for t in incoming)
        if is_claim_destination and state_name.endswith("ing"):
            working_state_names.add(state_name)

    # Rebuild the state dict with corrected classes + reversibility + taxonomy.
    # Working wins over terminal: a state classified as working by the
    # claim-destination heuristic isn't terminal even if it has no outgoing
    # transitions yet (e.g., a minimal example or an incomplete lifecycle).
    new_states: dict[str, State] = {}
    for name, existing in lifecycle.states.items():
        if name in working_state_names:
            state_class = StateClass.WORKING
        elif name in terminal_state_names:
            state_class = StateClass.TERMINAL
        else:
            state_class = StateClass.RESTING

        reversibility = existing.reversibility
        taxonomy = existing.terminal_taxonomy
        claim_role = existing.claim_role
        notes = list(notes_by_state.get(name, []))

        # Pull reversibility from notes if not already set.
        if reversibility is None:
            for line in notes:
                m = _REVERSIBILITY_RE.search(line)
                if m:
                    reversibility = _map_reversibility(m.group(1))
                    if reversibility is not None:
                        break

        # Pull claim role from notes if not already set.
        if claim_role is None:
            for line in notes:
                m = _CLAIM_ROLE_RE.search(line)
                if m:
                    claim_role = m.group("role").strip()
                    break

        # Pull terminal taxonomy from outgoing-to-[*] transition labels OR notes.
        if state_class is StateClass.TERMINAL and taxonomy is None:
            for t in lifecycle.transitions:
                if t.source == name and t.destination == "[*]":
                    m = _TERMINAL_TAXONOMY_RE.search(t.label)
                    if m:
                        try:
                            taxonomy = TerminalTaxonomy(m.group("tag").lower())
                        except ValueError:
                            taxonomy = None
                        if taxonomy is not None:
                            break
            if taxonomy is None:
                for line in notes:
                    m = _TERMINAL_TAXONOMY_RE.search(line)
                    if m:
                        try:
                            taxonomy = TerminalTaxonomy(m.group("tag").lower())
                        except ValueError:
                            taxonomy = None
                        if taxonomy is not None:
                            break

        new_states[name] = State(
            name=name,
            state_class=state_class,
            reversibility=reversibility,
            terminal_taxonomy=taxonomy,
            claim_role=claim_role,
            notes=notes,
        )

    lifecycle.states = new_states


def _apply_legend_to_states(lifecycle: Lifecycle) -> None:
    """When the legend names a gate that resolves to a destination state, copy
    the reversibility class onto that state if it wasn't already set."""
    for gate, rev in lifecycle.gates_in_legend.items():
        if gate in lifecycle.states and lifecycle.states[gate].reversibility is None:
            old = lifecycle.states[gate]
            lifecycle.states[gate] = State(
                name=old.name,
                state_class=old.state_class,
                reversibility=rev,
                terminal_taxonomy=old.terminal_taxonomy,
                claim_role=old.claim_role,
                notes=old.notes,
            )
