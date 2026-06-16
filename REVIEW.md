# Module review — 2026-06-10

Full-codebase review (core engine, backend/CLI, docs/tests/examples). All findings were
verified against the code at `main` (b6ef8e2); none are covered by existing tests unless
noted. Items marked **[fixed]** were fixed on the `review-fixes` branch with regression
tests; everything else is open.

**Issue tracker mapping** (every open finding below is filed):
- §1: state-name routing #16 · cascade close #9 · duplicate parent-advance #8
- §2: claim-less claim #17 · advance-over-CLAIM #11 · release/approve gate gaps #12, #24 ·
  cascade re-fire #18 · trust-grant keying #19 · gated CLAIM + EVENT + verdict legend #25
- §3: atomicity #20 · claim race #21 · PR/closed visibility #22 · native type readback #12 ·
  unassign #23 · limit/dry-run/exit-codes/collect-into #26 · spawn-issue #14
- §4: legend sync + dead checks + record-action #14 · approve --body + singletons #24 ·
  verdict legend #25
- §5: examples validate #3 · CI gate #4 · doc drift #13 · backend hardening #27
- Fixed on `review-fixes` (close when the PR lands): #7, #10, and the UnboundLocalError
  bullet of #12; the agent-role cascade and emitter-repr fixes were never filed separately.

## 1. Critical — engine correctness

- **State-name routing is unvalidated and broken by the shipped examples.**
  `Workflow.find_process_for_state` (workflow/config.py:398-401) resolves duplicate state
  names to whichever process loads first (alphabetical). The framework invariant
  ("state-name uniqueness") has no validator rule, and the examples violate it for
  non-handoff states: `verifying` (incident-response + pr), `drafting` (postmortem + pr).
  A PR in `state:drafting` routes to the *postmortem* state machine. Fix: validator rule —
  a state name in ≥2 processes must be `handoff: true` in all; registry raises on
  non-handoff collisions.

- **Cascade auto-advance into a closing state never closes the tracker issue.**
  workflow/core/cascade.py:220-224 and :321-324 build `MarkerChange(set_state=...)` without
  `close_issue`/`close_reason` (and without `set_pr_ready`), unlike every planner path
  (`_closing_close_info`, planner.py:155). Violates ADR-0002: contributor gets
  `state:shipped` but the GitHub issue stays open. Fix: share the planner's close-info
  helper in cascade.

- **Parent auto-advance is implemented twice with conflicting semantics.**
  `Controller.execute` runs `cascade_after_state_change` (controller.py:144-151,
  wait-for-all semantics), then `_do_advance_issue` *also* calls
  `_propagate_to_parent_on_closing` (cli.py:1386-1387, :1395-1497) which advances the
  parent on the first matching `advance_on` rule with no wait-for-all — defeating the
  invariant the validator enforces, and able to chain-fire after the cascade already
  advanced the parent. It also greps `extras` for a `parent-of` key the backend never
  populates and falls back to private `backend._gh` calls. Fix: delete the CLI helper;
  the controller cascade is the single implementation.

## 2. Major — engine semantics

- **`claim-issue` from a state with no CLAIM transition silently "succeeds"**, including
  closing states on closed issues: planner.py:497-518 falls through with `transition=None`
  and stamps `wip:`/`wip-from:` markers pointing at the current state (planner.py:544-551),
  which `release-issue` then rejects as marker drift. Should raise `OperationError`.
- **Ungated CLAIM reachable via `advance-issue` enters a working state without claim
  markers** (planner.py:307-353 never sets `set_agent_claim`/`set_last_state`). Reject
  CLAIM-type transitions in advance with "use claim-issue".
- **`release-issue` ignores in-flight gate markers** (stale `hitl:awaiting-<gate>` rides
  back to the resting state), and **`approve-blocked` never re-checks the issue is at the
  gate's source state** (planner.py:680-740 vs the check `_plan_await_signal` does at
  :625-630).
- **Cascade triggers are level-based, not edge-based** — controller runs cascades after
  *every* op; collector-side `collects:` labels are never cleared, so a collector sitting
  at a trigger state re-fires the rule on any later op, yanking contributors that have
  moved on (cascade.py:286-299). Clear `collects:` on fire or trigger only on state change.
- **Trust grants are keyed by gate name only** — `TrustGrant.workflow` is parsed and never
  consulted; `load_team_grants` (parser/trust_grant.py:157-183) flattens all processes and
  drops cross-process duplicates. Key by `(workflow, control_point)`.
- **Gated CLAIM transitions are schema-legal but broken end-to-end** (no triggering roles
  derivable from a resting source; approve clears the claim that was never set). Forbid
  `human_gate` on CLAIM/EVENT in the validator until claim semantics exist in approve.
- **EVENT transitions are agent-fireable through the core** — the "must be event" guard
  lives only in `_do_event_fired` (cli.py:1518-1533); `advance-issue --to <event-dest>`
  bypasses it. Belongs in the planner.

## 3. Major — backend / CLI

- ~~`view-issue --json` crashes (AttributeError) on any issue with next actions~~ **[fixed]**
  (cli.py `_next_actions_to_dict` read `triggering_role`; field is `triggering_roles`).
- ~~`--agent-role` flag/env never reached `claim-issue`/`advance-issue`~~ **[fixed]** — they
  read `context.agent_role` (config.json only), so the flag errored on claim and silently
  *skipped role validation* on advance (None actor). Now resolved via `_resolve_agent_role`.
- ~~`view-issue` UnboundLocalError when the workflow can't be resolved~~ **[fixed]**
  (`context` unbound on the WorkflowError path, dereferenced in the next-actions block).
- **"Atomic apply" is a 4–8 subprocess sequence with no rollback.** `apply_marker_change`
  (backends/github.py:410-461): label creates → audit comment → label edit → assign →
  close → pr-ready. Failure between label swap and close leaves a closing-state label on
  an open issue; audit comment can record a transition that never happened. The
  `TrackerBackend` protocol's atomicity contract (base.py:217-228) and CONTEXT.md's
  "same atomic apply step" overstate this. Reorder (labels before comment), emit explicit
  "partially applied — repair with X" errors, fix the docs; longer-term single GraphQL call.
- **Claim has no concurrency control** — read-modify-write; two concurrent claims both
  land `wip:` labels. Practical fix: verify-after-write, self-revert on conflict.
- **PRs are invisible to every query path** — `list_issues` uses `gh issue list`, which
  excludes PRs (github.py:167-232), so `view-inbox`/`search-issues`/cascade child lookups
  never see the pr process's items. Merge `gh pr list` results or use the search API.
- **Closed issues are invisible to `search-issues`** — `gh issue list` defaults to open;
  searching a closing state's label returns nothing (github.py:183). Pass `--state all`
  at least for closing states.
- **Native issue-type encoding is write-only** — `read_issue` doesn't request `issueType`,
  so under native encoding `IssueState.issue_type` is always None and claim-time type
  checks silently skip (github.py:392-408). Map the GitHub type back to the framework id.
- **`unassign` fires unconditionally while `assign` never does** (resolve_role TODO,
  github.py:531-540) — releases/advances remove assignments humans made in the UI. Make
  unassign a no-op until role→handle mapping exists.
- **Post-fetch filtering breaks `--limit`** — wildcard gate/audit filters applied in
  Python after `gh issue list --limit 50`; collect-candidate checks hardcode 200
  (github.py:185-223, cli.py:2327,2581). Over-fetch/paginate or warn at the cap.
- **`spawn-issue` / `collect-into` apply multi-step label changes non-atomically with no
  repair guidance on midway failure** (cli.py:1717-1732, :2621-2642); `spawn-issue` also
  uses private `backend._gh`.

## 4. Validator / emitter

- ~~"Audit grant on irreversible gate" ERROR could never fire~~ **[fixed]** — loop-variable
  shadowing (validator.py:401) passed a `HumanGate` where a gate-name str was expected.
- ~~Generated docs rendered the full `HumanGate(...)` repr in the transitions table~~
  **[fixed]** — same shadowing pattern in emitter/docs.py:822-835.
- `_check_legend_catalog_sync` is half-implemented: identical ternary branches,
  `marker_gates` built from destinations and never compared (validator.py:271-275) — the
  documented three-way legend↔catalog↔markers check doesn't exist.
- `_check_reversibility_declared_on_legend_states` (validator.py:242-262) can never fire
  given parser rules; dead code.
- Verdict-gate legend reversibility is last-destination-wins in the parser
  (parser/state_machine.py:257-266) vs worst-case in the model — emitted legend can
  disagree with docs depending on JSON order.
- `_plan_record_action` diverges from `_advance_audit_gated`: missing `set_pr_ready`;
  binary gate with no destination proceeds with `set_state=None` instead of erroring
  (planner.py:791-820).
- `approve-blocked --body` is silently discarded (planner never reads it).
- Human-claim singletons (`reviewing`/`auditing`/`advising`) are never required by
  approve/reject/respond — the claim verbs are decorative.
- `initial_label` is parsed and character-validated but never emitted (mermaid.py:88-91).

## 5. Docs / examples / CI

- **`examples/workflows` fails its own validator: 9 ERRORS across 10 processes**
  (spawns.process mismatches in experimentation ×2 + postmortem; `initial`+spawn-target
  conflicts on inner-loop `ready_for_dev` and refinement `raw`; refinement role-actions
  out of resting states ×2; release `monitoring → rolling_back` lands in a working state
  with no CLAIM into it). The smoke test masks it: `test_validate_against_shipped_workflows`
  accepts `rc in (0, 1)` while its docstring says "no errors". Fix the examples, then
  tighten to `rc == 0`.
- **README front door is broken**: `cd tools/workflow-tool` (repo is standalone);
  `workflow ... init` → command is `init-agent`; `workflow list` ×3 → no such command;
  "eleven operations" lists 14; architecture tree describes parsers that don't exist
  (mermaid/process-doc) and omits emitter/inspector/cascade/capability_cache; broken
  upstream links (`hitl-principles.md`, `backends/github-encoding.md`,
  `agent-workflow-skill-creator` relative path). Undocumented commands: `event-fired`,
  `spawn-issue`, `collect-into`, `view-issue`, `post-comment`, `edit-issue`,
  `validate-workflow`, `doctor-workflow`.
- **docs/workflow-authoring.md's "minimum valid file" does not parse** (missing
  `issue_types` on resting states) — same bug in parser/state_machine.py's module
  docstring example. Doc also claims the examples "validate clean" (they don't) and
  references `[*]` authoring / `cross_process` transitions the parser now rejects
  (also stale in states.schema.json:184-190 and state-machine-principles.md §9).
- **Zero HITL anywhere in examples** — no `human_gate`, no `*-human-gates.json`, no trust
  grants in any of the 11 agent homes. The framework's differentiator has no example or
  end-to-end test coverage (cli.py 44% coverage, github.py 60%, 12 of 14 operation
  wrappers' `run()` bodies never executed).
- **`more_examples/` is dead weight** — 15 tracked files, pre-ADR-0001 vocabulary,
  referenced nowhere. Port `backport` + `progressive-rollout` or delete.
- **CI gaps**: no `validate-workflow` run on examples, no `generate-docs` drift check
  (authoring doc prescribes both); test job ignores `uv.lock` (`uv pip install` instead of
  `uv sync --locked`); lint floats ruff (`uvx --from "ruff>=0.6"`); no uv caching; no
  coverage report; Python 3.13 absent.
- CONTEXT.md: stale `--packet-from` reference (:98), literal "GHES ≥ ??" placeholder
  (:162). docs/schemas/README.md says "five authored file types" — there are six
  (human-inputs missing from table and VS Code fileMatch); `.vscode/settings.json` is
  claimed to ship but is gitignored.
- Stale pre-ADR-0002 comments: `iterated` taxonomy / open-after-close in
  backends/base.py:113-118, :272-281; assorted terminal→closing rename debris in
  model/state_machine.py docstrings; planner error strings use pre-rename verbs.

## Inert-but-parsed features (decide: implement or cut)

- Trust-grant runtime parameters: `timeout`/`on_timeout`/`escalate_to` (block),
  `cadence` (audit) — parsed, statically validated, no runtime machinery.
- `reject-audit` remediation: `parameters.on_revoke` never read; comment cites catalog
  rationale as a proxy.
- `TrustGrant.workflow` (see grants keying above), `TrustGrant.is_expired` (unused),
  `initial_label` (never emitted).

## Suggested sequencing

1. **PR: surgical fixes** (done on `review-fixes`): the five [fixed] items + 6 regression tests.
2. **PR: examples + CI honesty** — fix the 9 validator errors, smoke test `rc == 0`, CI
   `validate-workflow` + `generate-docs` drift check, `uv sync --locked`, pin ruff, delete
   or port `more_examples/`.
3. **PR: engine semantics** — state-name uniqueness rule, cascade close-info, delete
   `_propagate_to_parent_on_closing`, claim/advance/release/approve guards (§2).
4. **PR: backend robustness** — apply ordering + partial-failure messages, claim
   verify-after-write, PR + closed-issue visibility, native type readback, unassign no-op.
5. **PR: docs overhaul + first HITL example** — README/authoring/schema fixes plus one
   gated process with a catalog + trust grant in examples, with end-to-end tests over the
   human-side operations.
