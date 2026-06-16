# Human inputs

Entries agents may invoke `request-input` on. Working states opt-in by listing ids on their `human_inputs` field; states with no list cannot escalate via `request-input`.

## `general` — General

Catch-all for off-catalog questions. Use when none of the specialised topics fits — but prefer a specialised topic when one applies, so the human operator can read the queue at a glance.

- **Declared on**: `config-change.applying_config_change`, `data-change.applying_data_change`, `data-change.creating_backup`, `experimentation.aborting`, `experimentation.analyzing`, `incident-response.diagnosing`, `incident-response.mitigating`, `incident-response.triaging`, `incident-response.verifying`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `mitigation.execute_mitigation`, `mitigation.plan_mitigation`, `postmortem.drafting`, `pr.drafting_pr`, `pr.fixing_qa`, `pr.fixing_review`, `pr.merging`, `pr.reviewing`, `pr.verifying_pr`, `refinement.consulting`, `refinement.refining`, `release.abandoning`, `release.monitoring`, `release.preparing`, `release.reviewing_release`, `release.rolling_back` _(derived)_

## `clarify-scope` — Clarify scope

Boundaries / out-of-scope confirmation. The agent has hit a question about whether the work should include / exclude something the ticket didn't specify.

- **Declared on**: `config-change.applying_config_change`, `data-change.applying_data_change`, `incident-response.mitigating`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `mitigation.execute_mitigation`, `mitigation.plan_mitigation`, `pr.drafting_pr`, `pr.fixing_qa`, `pr.fixing_review`, `pr.reviewing`, `pr.verifying_pr`, `refinement.consulting`, `refinement.refining`, `release.preparing`, `release.rolling_back` _(derived)_

## `needs-arch-review` — Needs architecture review

A cross-module impact or non-trivial design choice the agent isn't comfortable making alone. Prepare an outline of the options considered before invoking.

- **Declared on**: `incident-response.diagnosing`, `incident-response.mitigating`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `mitigation.plan_mitigation`, `pr.fixing_review`, `pr.reviewing`, `refinement.consulting`, `refinement.refining`, `release.rolling_back` _(derived)_

## `needs-security-review` — Needs security review

Auth, secrets, PII, threat-model, or supply-chain implications. Operator escalates to the security engineer.

- **Declared on**: `config-change.applying_config_change`, `data-change.applying_data_change`, `incident-response.diagnosing`, `incident-response.mitigating`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `mitigation.plan_mitigation`, `postmortem.drafting`, `pr.fixing_review`, `pr.reviewing`, `refinement.consulting`, `refinement.refining`, `release.reviewing_release` _(derived)_

## `needs-ux-input` — Needs UX input

User-facing flow or copy that the operator should route to the designer.

- **Declared on**: `experimentation.analyzing`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `refinement.consulting`, `refinement.refining` _(derived)_

## `blocked-on-data` — Blocked on data

The agent needs facts not in the ticket — production traces, customer reports, prior incident notes — that the human operator can fetch.

- **Declared on**: `config-change.applying_config_change`, `data-change.applying_data_change`, `data-change.creating_backup`, `experimentation.analyzing`, `incident-response.diagnosing`, `incident-response.mitigating`, `incident-response.triaging`, `incident-response.verifying`, `inner-loop.implementing`, `inner-loop.implementing_spike`, `mitigation.plan_mitigation`, `postmortem.drafting`, `pr.fixing_qa`, `pr.verifying_pr`, `refinement.consulting`, `refinement.refining`, `release.monitoring`, `release.preparing`, `release.reviewing_release`, `release.rolling_back` _(derived)_
