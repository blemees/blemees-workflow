"""GitHub label grammar (ADR-0005, label tier).

Single source of truth for the framework's label encoding. One rule:

    <kebab-classifier>/<value>

This module encodes framework markers to labels and parses them back. The
classifiers:

    state/<name>             current workflow state (exactly one)
    claimed/<role>           agent claim (at most one)
    last-state/<name>        origin resting state, set on claim
    type/<id>                framework issue type (label encoding)
    child-of/<id>            spawned-child back-pointer
    collected-by/<id>        fan-in contributor back-pointer
    hitl-blocked/<gate>      block-gated transition awaiting a signal
    hitl-audit/<gate>        audit-pending marker
    hitl-input/<topic>       awaiting human input on a topic (queue + topic in one)
    hitl-claim/<value>       human-claim singleton {reviewing, auditing, advising}
    hitl-signal/<value>      transient outcome {approved, rejected, checked, revoked, resolved}

The module is pure (stdlib only) so any layer that needs to encode a label —
the backend, the planner's spawn `extra_labels`, the CLI's provisioning — can
share one definition rather than hand-rolling f-strings.
"""

from __future__ import annotations

from dataclasses import dataclass

SEP = "/"

# Classifiers.
STATE = "state"
CLAIM = "claimed"
LAST_STATE = "last-state"
TYPE = "type"
CHILD_OF = "child-of"
COLLECTED_BY = "collected-by"
HITL_BLOCKED = "hitl-blocked"
HITL_AUDIT = "hitl-audit"
HITL_INPUT = "hitl-input"
HITL_CLAIM = "hitl-claim"
HITL_SIGNAL = "hitl-signal"

_CLASSIFIERS = frozenset(
    {
        STATE,
        CLAIM,
        LAST_STATE,
        TYPE,
        CHILD_OF,
        COLLECTED_BY,
        HITL_BLOCKED,
        HITL_AUDIT,
        HITL_INPUT,
        HITL_CLAIM,
        HITL_SIGNAL,
    }
)

# hitl-claim/<value> singleton values.
CLAIM_REVIEWING = "reviewing"
CLAIM_AUDITING = "auditing"
CLAIM_ADVISING = "advising"
CLAIM_VALUES = (CLAIM_REVIEWING, CLAIM_AUDITING, CLAIM_ADVISING)

# hitl-signal/<value> outcome values (transient audit-trace; bounded set).
SIGNAL_APPROVED = "approved"
SIGNAL_REJECTED = "rejected"
SIGNAL_CHECKED = "checked"
SIGNAL_REVOKED = "revoked"
SIGNAL_RESOLVED = "resolved"
SIGNAL_VALUES = (
    SIGNAL_APPROVED,
    SIGNAL_REJECTED,
    SIGNAL_CHECKED,
    SIGNAL_REVOKED,
    SIGNAL_RESOLVED,
)

# Per-classifier label colours (used when lazily creating labels).
_COLORS = {
    STATE: "1f6feb",  # blue
    CLAIM: "fbca04",  # yellow
    LAST_STATE: "fef2c0",  # pale yellow — adjacent to claimed
    HITL_BLOCKED: "8957e5",  # purple
    HITL_AUDIT: "8957e5",
    HITL_INPUT: "8957e5",
    HITL_CLAIM: "8957e5",
    HITL_SIGNAL: "8957e5",
}
_DEFAULT_COLOR = "ededed"


def encode(classifier: str, value: str) -> str:
    """Join a classifier and value into a label."""
    return f"{classifier}{SEP}{value}"


def state_label(name: str) -> str:
    return encode(STATE, name)


def claim_label(role: str) -> str:
    return encode(CLAIM, role)


def last_state_label(name: str) -> str:
    return encode(LAST_STATE, name)


def type_label(type_id: str) -> str:
    return encode(TYPE, type_id)


def child_of_label(parent_id: str) -> str:
    return encode(CHILD_OF, parent_id)


def collected_by_label(collector_id: str) -> str:
    return encode(COLLECTED_BY, collector_id)


def hitl_blocked_label(gate: str) -> str:
    return encode(HITL_BLOCKED, gate)


def hitl_audit_label(gate: str) -> str:
    return encode(HITL_AUDIT, gate)


def hitl_input_label(topic: str) -> str:
    return encode(HITL_INPUT, topic)


def hitl_claim_label(which: str) -> str:
    return encode(HITL_CLAIM, which)


def hitl_signal_label(outcome: str) -> str:
    return encode(HITL_SIGNAL, outcome)


def classifier_of(label: str) -> str:
    """Return the classifier segment of a label (the part before the first `/`)."""
    return label.split(SEP, 1)[0]


def color_for(label: str) -> str:
    """Default colour for a label, by classifier. Falls back to neutral grey."""
    return _COLORS.get(classifier_of(label), _DEFAULT_COLOR)


@dataclass(frozen=True)
class ParsedLabel:
    """A label resolved to its framework meaning.

    `kind` is one of the classifier constants; `value` is the value segment.
    """

    kind: str
    value: str


def parse_label(raw: str) -> ParsedLabel | None:
    """Parse a label into (kind, value). Returns None for non-marker labels.

    Partitions on the FIRST `/`, so an id that itself contains `/` (e.g.
    `child-of/owner/repo#1`) survives intact.
    """
    classifier, sep, value = raw.strip().partition(SEP)
    if sep and classifier in _CLASSIFIERS:
        return ParsedLabel(classifier, value)
    return None
