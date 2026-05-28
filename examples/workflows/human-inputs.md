# Human inputs

Entries agents may invoke `request-input` on. Working states opt-in by listing ids on their `human_inputs` field; states with no list cannot escalate via `request-input`.

## `general` — General

Catch-all for off-catalog questions. Use when none of the specialised topics fits — but prefer a specialised topic when one applies, so the human operator can read the queue at a glance.

- **Declared on**: `incident-response.diagnosing`, `incident-response.mitigating`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `pr.fixing_review`, `pr.reviewing`, `refinement.consulting`, `refinement.refining` _(derived)_

## `clarify-scope` — Clarify scope

Boundaries / out-of-scope confirmation. The agent has hit a question about whether the work should include / exclude something the ticket didn't specify.

- **Declared on**: `inner-loop.implementing`, `inner-loop.implementing_spike`, `pr.fixing_review`, `refinement.refining` _(derived)_

## `needs-arch-review` — Needs architecture review

A cross-module impact or non-trivial design choice the agent isn't comfortable making alone. Prepare an outline of the options considered before invoking.

- **Declared on**: `inner-loop.implementing`, `inner-loop.implementing_spike`, `refinement.consulting`, `refinement.refining` _(derived)_

## `needs-security-review` — Needs security review

Auth, secrets, PII, threat-model, or supply-chain implications. Operator escalates to the security engineer.

- **Declared on**: `incident-response.mitigating`, `inner-loop.implementing`, `pr.reviewing`, `refinement.consulting`, `refinement.refining` _(derived)_

## `needs-ux-input` — Needs UX input

User-facing flow or copy that the operator should route to the designer.

- **Declared on**: `refinement.refining` _(derived)_

## `blocked-on-data` — Blocked on data

The agent needs facts not in the ticket — production traces, customer reports, prior incident notes — that the human operator can fetch.

- **Declared on**: `incident-response.diagnosing`, `incident-response.mitigating`, `inner-loop.implementing` _(derived)_
