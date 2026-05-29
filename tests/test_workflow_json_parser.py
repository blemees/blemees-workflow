"""StateMachine JSON parser tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.core.model.state_machine import (
    ReversibilityClass,
    StateClass,
    TerminalTaxonomy,
    TransitionType,
)
from workflow.core.parser.state_machine import parse_state_machine
from workflow.errors import ParseError


def _minimal() -> dict:
    return {
        "states": {
            "a": {
                "class": "resting",
                "reversibility": "reversible-fast",
                "initial": "in",
            },
            "b": {
                "class": "working",
                "roles": ["product-manager"],
                "issue_types": ["bug"],
            },
            "c": {
                "class": "terminal",
                "reversibility": "reversible-fast",
                "terminal_taxonomy": "shipped",
                "close_reason": "completed",
            },
        },
        "transitions": [
            {"source": "a", "destination": "b", "type": "claim", "label": "pm claims a"},
            {"source": "b", "destination": "c", "type": "advance", "label": "pm ships"},
        ],
    }


def test_parses_minimal_workflow() -> None:
    workflow = parse_state_machine(json.dumps(_minimal()), name="t")
    assert workflow.name == "t"
    assert workflow.states["a"].state_class is StateClass.RESTING
    assert workflow.states["a"].roles == ()
    assert workflow.states["b"].state_class is StateClass.WORKING
    assert workflow.states["b"].roles == ("product-manager",)
    assert workflow.states["c"].state_class is StateClass.TERMINAL
    assert workflow.states["c"].terminal_taxonomy is TerminalTaxonomy.SHIPPED
    assert workflow.states["a"].is_initial is True
    assert workflow.states["a"].initial_label == "in"
    assert len(workflow.transitions) == 2
    assert workflow.transitions[0].transition_type is TransitionType.CLAIM


def test_terminal_requires_taxonomy() -> None:
    bad = _minimal()
    bad["states"]["c"] = {"class": "terminal", "reversibility": "reversible-fast"}
    with pytest.raises(ParseError, match="terminal_taxonomy"):
        parse_state_machine(json.dumps(bad))


def test_resting_state_requires_reversibility() -> None:
    bad = _minimal()
    del bad["states"]["a"]["reversibility"]
    with pytest.raises(ParseError, match="reversibility.*required"):
        parse_state_machine(json.dumps(bad))


def test_terminal_state_requires_reversibility() -> None:
    bad = _minimal()
    del bad["states"]["c"]["reversibility"]
    with pytest.raises(ParseError, match="reversibility.*required"):
        parse_state_machine(json.dumps(bad))


def test_working_state_rejects_reversibility() -> None:
    bad = _minimal()
    bad["states"]["b"]["reversibility"] = "reversible-fast"
    with pytest.raises(ParseError, match="not valid on working"):
        parse_state_machine(json.dumps(bad))


def test_non_terminal_rejects_taxonomy() -> None:
    bad = _minimal()
    bad["states"]["a"]["terminal_taxonomy"] = "shipped"
    with pytest.raises(ParseError, match="only valid for"):
        parse_state_machine(json.dumps(bad))


def test_unknown_state_class_rejected() -> None:
    bad = _minimal()
    bad["states"]["a"]["class"] = "halfway"
    with pytest.raises(ParseError, match="class"):
        parse_state_machine(json.dumps(bad))


def test_unknown_transition_type_rejected() -> None:
    bad = _minimal()
    bad["transitions"][0]["type"] = "wibble"
    with pytest.raises(ParseError, match="type"):
        parse_state_machine(json.dumps(bad))


def test_unknown_endpoint_state_rejected() -> None:
    bad = _minimal()
    bad["transitions"].append(
        {"source": "x", "destination": "b", "type": "advance", "label": "noop"}
    )
    with pytest.raises(ParseError, match="not a declared state"):
        parse_state_machine(json.dumps(bad))


def test_hitl_field_rejected() -> None:
    """`hitl` was merged into `gate` — presence of `gate` is the marker.
    The parser rejects the legacy field outright."""
    bad = _minimal()
    bad["transitions"][1]["hitl"] = True
    with pytest.raises(ParseError, match="`hitl` was removed"):
        parse_state_machine(json.dumps(bad))


def test_human_gate_alone_makes_transition_gated() -> None:
    """Adding `human_gate` to a transition marks it as HITL-gated; no `hitl`
    field needed."""
    spec = _minimal()
    spec["transitions"][1]["human_gate"] = "ship"
    workflow = parse_state_machine(json.dumps(spec))
    advance = workflow.transitions[1]
    assert advance.gate_name == "ship"
    assert advance.is_gated is True


def test_cross_process_transition_type_rejected() -> None:
    """The `cross_process` transition type was removed in favor of
    `handoff: true` on resting states and `spawns: {...}` on working /
    terminal states. The parser rejects authored `cross_process`
    transitions with a clear migration hint."""
    bad = _minimal()
    bad["transitions"].append(
        {"source": "a", "destination": "b", "type": "cross_process",
         "label": "to x", "kind": "shared", "process": "x"}
    )
    with pytest.raises(ParseError, match="cross_process.*removed"):
        parse_state_machine(json.dumps(bad))


def test_kind_field_on_transition_rejected() -> None:
    """The `kind` field was cross_process-only; reject it outright."""
    bad = _minimal()
    bad["transitions"][0]["kind"] = "shared"
    with pytest.raises(ParseError, match="`kind` and `process` fields were"):
        parse_state_machine(json.dumps(bad))


def test_gates_in_legend_built_from_gated_transitions() -> None:
    spec = _minimal()
    # Make b → c a HITL transition by setting `human_gate` (and update
    # reversibility on c).
    spec["states"]["c"]["reversibility"] = "reversible-slow"
    spec["transitions"][1]["human_gate"] = "ship"
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.gates_in_legend == {"ship": ReversibilityClass.REVERSIBLE_SLOW}


def test_parses_real_refinement_workflow(refinement_workflow_path: Path) -> None:
    """The shipped refinement example parses, derives its accepted types
    from its working states, and declares its handoff states with
    inner-loop."""
    workflow = parse_state_machine(refinement_workflow_path)
    assert workflow.name == "refinement"
    assert "raw" in workflow.states
    assert "ready_for_dev" in workflow.states
    assert "ready_bounced" in workflow.states
    assert "wont_fix" in workflow.states
    assert "duplicate" in workflow.states
    # Derived umbrella = union of working states' issue_types.
    assert set(workflow.accepted_issue_types) >= {"bug", "feature"}
    # Shared handover states are flagged, not transitioned via [*].
    assert workflow.states["ready_for_dev"].handoff is True
    assert workflow.states["ready_bounced"].handoff is True


def test_handoff_only_on_resting() -> None:
    spec = _minimal()
    spec["states"]["b"]["handoff"] = True  # b is working
    with pytest.raises(ParseError, match="handoff.*only valid on resting"):
        parse_state_machine(json.dumps(spec))


def test_spawns_on_working_advance_on_optional() -> None:
    """advance_on is now selective and optional — empty/absent means
    'never auto-advance the parent'."""
    spec = _minimal()
    spec["states"]["b"]["spawns"] = {
        "process": "other",
        "issue_type": "bug",
        "initial_state": "queue",
    }
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["b"].spawns is not None
    assert workflow.states["b"].spawns.advance_on == ()


def test_legacy_on_terminal_rejected() -> None:
    """The renamed-from `on_terminal` is rejected with a migration hint."""
    spec = _minimal()
    spec["states"]["b"]["spawns"] = {
        "process": "other",
        "issue_type": "bug",
        "initial_state": "queue",
        "on_terminal": {"done": "raw"},
    }
    with pytest.raises(ParseError, match="renamed to.*advance_on"):
        parse_state_machine(json.dumps(spec))


def test_spawns_on_terminal_forbids_advance_on() -> None:
    spec = _minimal()
    spec["states"]["c"]["spawns"] = {
        "process": "other",
        "issue_type": "bug",
        "initial_state": "queue",
        "advance_on": {"done": "raw"},
    }
    with pytest.raises(ParseError, match="advance_on.*not valid on terminal"):
        parse_state_machine(json.dumps(spec))


def test_spawns_allowed_on_resting() -> None:
    """Resting-state spawns are valid: the parent waits in this state
    while the child runs."""
    spec = _minimal()
    spec["states"]["a"]["spawns"] = {
        "process": "other",
        "issue_type": "bug",
        "initial_state": "queue",
    }
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["a"].spawns is not None
    assert workflow.states["a"].spawns.process == "other"


def test_initial_bool_true_marks_state() -> None:
    spec = _minimal()
    spec["states"]["a"]["initial"] = True
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["a"].is_initial is True
    assert workflow.states["a"].initial_label is None


def test_initial_string_stores_label() -> None:
    spec = _minimal()
    spec["states"]["a"]["initial"] = "alert fires"
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["a"].is_initial is True
    assert workflow.states["a"].initial_label == "alert fires"


def test_initial_false_or_absent_is_default() -> None:
    spec = _minimal()
    spec["states"]["a"]["initial"] = False
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["a"].is_initial is False
    assert workflow.states["a"].initial_label is None


def test_initial_forbidden_on_working() -> None:
    spec = _minimal()
    spec["states"]["b"]["initial"] = True
    with pytest.raises(ParseError, match="initial.*only valid on resting"):
        parse_state_machine(json.dumps(spec))


def test_initial_forbidden_on_terminal() -> None:
    spec = _minimal()
    spec["states"]["c"]["initial"] = True
    with pytest.raises(ParseError, match="initial.*only valid on resting"):
        parse_state_machine(json.dumps(spec))


def test_transition_label_with_colon_rejected() -> None:
    """stateDiagram-v2 treats the first `:` as the label separator and
    would choke on a second one. Parser rejects so the emitter can't
    produce malformed mermaid."""
    spec = _minimal()
    spec["transitions"][0]["label"] = "claim (priority: high)"
    with pytest.raises(ParseError, match="stateDiagram-v2 parser rejects"):
        parse_state_machine(json.dumps(spec))


def test_transition_label_with_semicolon_rejected() -> None:
    spec = _minimal()
    spec["transitions"][0]["label"] = "claim; then notify"
    with pytest.raises(ParseError, match="stateDiagram-v2 parser rejects"):
        parse_state_machine(json.dumps(spec))


def test_initial_label_with_colon_rejected() -> None:
    spec = _minimal()
    spec["states"]["a"]["initial"] = "issue created (source: webhook)"
    with pytest.raises(ParseError, match="stateDiagram-v2"):
        parse_state_machine(json.dumps(spec))


def test_initial_empty_string_rejected() -> None:
    spec = _minimal()
    spec["states"]["a"]["initial"] = ""
    with pytest.raises(ParseError, match="initial.*non-empty"):
        parse_state_machine(json.dumps(spec))


def test_sentinel_source_transition_rejected_with_migration_hint() -> None:
    spec = _minimal()
    spec["transitions"].append(
        {"source": "[*]", "destination": "a", "type": "event", "label": "extra"}
    )
    with pytest.raises(ParseError, match=r"\[\*\]→state.*no longer authored"):
        parse_state_machine(json.dumps(spec))


def test_sentinel_destination_transition_rejected() -> None:
    spec = _minimal()
    spec["transitions"].append(
        {"source": "c", "destination": "[*]", "type": "event", "label": "out"}
    )
    with pytest.raises(ParseError, match=r"state→\[\*\].*implicit"):
        parse_state_machine(json.dumps(spec))


def test_collects_parses_on_resting() -> None:
    spec = _minimal()
    spec["states"]["a"]["collects"] = {
        "process": "pr",
        "from_states": ["staged"],
    }
    workflow = parse_state_machine(json.dumps(spec))
    state_a = workflow.states["a"]
    assert state_a.collects is not None
    assert state_a.collects.process == "pr"
    assert state_a.collects.from_states == ("staged",)


def test_collects_forbidden_on_working() -> None:
    spec = _minimal()
    spec["states"]["b"]["collects"] = {  # b is working
        "process": "pr",
        "from_states": ["staged"],
    }
    with pytest.raises(ParseError, match="collects.*only valid on resting"):
        parse_state_machine(json.dumps(spec))


def test_collects_forbidden_on_terminal() -> None:
    spec = _minimal()
    spec["states"]["c"]["collects"] = {  # c is terminal
        "process": "pr",
        "from_states": ["staged"],
    }
    with pytest.raises(ParseError, match="collects.*only valid on resting"):
        parse_state_machine(json.dumps(spec))


def test_collects_requires_from_states_non_empty() -> None:
    spec = _minimal()
    spec["states"]["a"]["collects"] = {
        "process": "pr",
        "from_states": [],
    }
    with pytest.raises(ParseError, match="from_states.*non-empty"):
        parse_state_machine(json.dumps(spec))


def test_collects_rejects_duplicate_from_states() -> None:
    spec = _minimal()
    spec["states"]["a"]["collects"] = {
        "process": "pr",
        "from_states": ["staged", "staged"],
    }
    with pytest.raises(ParseError, match="duplicate state"):
        parse_state_machine(json.dumps(spec))


def test_mark_pr_ready_forbidden_on_terminal() -> None:
    spec = _minimal()
    spec["states"]["c"]["mark_pr_ready"] = True
    with pytest.raises(ParseError, match="mark_pr_ready.*not valid on terminal"):
        parse_state_machine(json.dumps(spec))


def test_mark_pr_ready_parses_on_resting() -> None:
    spec = _minimal()
    spec["states"]["a"]["mark_pr_ready"] = True
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["a"].mark_pr_ready is True


def test_human_inputs_only_on_working_states() -> None:
    spec = _minimal()
    spec["states"]["a"]["human_inputs"] = ["general"]  # `a` is resting
    with pytest.raises(ParseError, match="human_inputs.*only valid on working"):
        parse_state_machine(json.dumps(spec))


def test_human_inputs_parses_on_working_state() -> None:
    spec = _minimal()
    spec["states"]["b"]["human_inputs"] = ["general", "clarify-scope"]
    workflow = parse_state_machine(json.dumps(spec))
    assert workflow.states["b"].human_inputs == ("general", "clarify-scope")


def test_human_inputs_duplicate_rejected() -> None:
    spec = _minimal()
    spec["states"]["b"]["human_inputs"] = ["general", "general"]
    with pytest.raises(ParseError, match="duplicate topic"):
        parse_state_machine(json.dumps(spec))


def test_pr_process_accepts_pr_issue_type(workflow_dir: Path) -> None:
    """The PR process's working states accept the `pr` type; the derived
    umbrella reflects it."""
    workflow = parse_state_machine(workflow_dir / "pr-states.json")
    assert workflow.name == "pr"
    assert workflow.accepted_issue_types == ["pr"]


def test_top_level_issue_types_rejected() -> None:
    bad = _minimal()
    bad["issue_types"] = ["bug"]
    with pytest.raises(ParseError, match="Top-level.*issue_types"):
        parse_state_machine(json.dumps(bad))


def test_working_state_requires_issue_types() -> None:
    bad = _minimal()
    del bad["states"]["b"]["issue_types"]
    with pytest.raises(ParseError, match="issue_types.*required on working"):
        parse_state_machine(json.dumps(bad))


def test_working_state_requires_roles() -> None:
    bad = _minimal()
    del bad["states"]["b"]["roles"]
    with pytest.raises(ParseError, match="roles.*required on working"):
        parse_state_machine(json.dumps(bad))


def test_terminal_state_requires_close_reason() -> None:
    bad = _minimal()
    del bad["states"]["c"]["close_reason"]
    with pytest.raises(ParseError, match="close_reason.*required on terminal"):
        parse_state_machine(json.dumps(bad))


def test_label_auto_generated_for_claim_transition() -> None:
    spec = _minimal()
    # transitions[0] is the CLAIM (a→b).
    del spec["transitions"][0]["label"]
    workflow = parse_state_machine(json.dumps(spec))
    claim = workflow.transitions[0]
    assert claim.transition_type is TransitionType.CLAIM
    assert claim.label == "product-manager claims a"


def test_label_auto_generated_for_advance_transition() -> None:
    spec = _minimal()
    # transitions[1] is the ADVANCE (b→c).
    del spec["transitions"][1]["label"]
    workflow = parse_state_machine(json.dumps(spec))
    adv = workflow.transitions[1]
    assert adv.transition_type is TransitionType.ADVANCE
    assert adv.label == "product-manager → c"


def test_label_required_on_event() -> None:
    spec = _minimal()
    # _minimal() has no event transitions (entry is now via `initial`);
    # add an a→c event transition without a label to drive the rule.
    spec["transitions"].append(
        {"source": "a", "destination": "c", "type": "event"}
    )
    with pytest.raises(ParseError, match="event transitions"):
        parse_state_machine(json.dumps(spec))


def test_parses_real_inner_loop_workflow(inner_loop_workflow_path: Path) -> None:
    """Inner-loop has one consolidated `implementing` working state that
    accepts every PR-producing issue type (bug/feature/chore/experiment/
    hotfix). Spike is the lone exception — it has its own working state
    because it doesn't spawn a PR."""
    workflow = parse_state_machine(inner_loop_workflow_path)
    assert workflow.name == "inner-loop"
    assert workflow.states["ready_for_dev"].state_class is StateClass.RESTING
    # Role-restriction lives on the working state reached by CLAIM, not on
    # the resting queue.
    assert "developer" in workflow.states["implementing"].roles
    assert "implementing" in workflow.states
    assert "implementing_spike" in workflow.states
    assert "implementing_experiment" not in workflow.states  # consolidated
    assert "implementing_hotfix" not in workflow.states  # consolidated
    # `implementing` accepts every PR-producing type via multi-issue-type.
    impl = workflow.states["implementing"]
    assert set(impl.issue_types) >= {"bug", "feature", "chore", "experiment", "hotfix"}
    # `implementing` declares a subprocess spawn into the pr process.
    assert impl.spawns is not None
    assert impl.spawns.process == "pr"
    assert impl.spawns.issue_type == "pr"
    assert impl.spawns.initial_state == "draft"
    # PR terminal `merged` → parent inner-loop resting state `staged`.
    # (PR's terminal was renamed from `staged` to `merged` so state names
    # don't collide across processes.)
    assert impl.spawns.advance_on == (("merged", "staged"),)
