# Roles

This workflow defines these roles:

## `product-owner` — Product Owner

Final say on product direction, priorities, business trade-offs, and experiment verdicts

- **Participates in**: `experimentation`, `release` _(derived from working-state `roles`)_
- **Does not**: write code, run the inner loop, refine issues, make architectural calls without consulting architect

## `product-manager` — Product Manager

Owns refinement; turns raw issues into ready tickets that developers can safely pick up

- **Participates in**: `postmortem`, `refinement` _(derived from working-state `roles`)_
- **Does not**: decide architecture, UX, or security questions, implement tickets, self-refine bounced-back issues without addressing the specific gap, coordinate active incidents, diagnose root cause, post experiment verdicts

## `architect` — Architect

Owns architecture, reliability, performance, and cost decisions

- **Participates in**: `refinement` _(derived from working-state `roles`)_
- **Does not**: decide UX or security questions, implement feature tickets (though they implement spike investigations)

## `designer` — Designer

Owns user flows, accessibility, and design-system contributions

- **Participates in**: `refinement` _(derived from working-state `roles`)_
- **Does not**: decide architecture or security questions, implement non-UI tickets

## `security` — Security Engineer

Owns authentication, secrets, PII, threat modeling, and supply-chain concerns

- **Does not**: decide architecture (beyond security implications) or UX questions

## `developer` — Developer

Implements changes end-to-end: code, tests, PR, merge

- **Participates in**: `backport`, `inner-loop`, `pr`, `progressive-rollout`, `release` _(derived from working-state `roles`)_
- **Does not**: self-refine issues (bounces back instead), review their own PRs, make medium-design decisions mid-implementation

## `peer-reviewer` — Peer Reviewer

Reviews PRs for design, intent, readability, and risk

- **Participates in**: `pr` _(derived from working-state `roles`)_
- **Does not**: review their own code, do QA verification, rubber-stamp

## `tester` — Tester

Verifies PRs behaviorally on preview environments

- **Participates in**: `pr` _(derived from working-state `roles`)_
- **Does not**: code review, fix the bug they found (report only), approve on the basis of 'the code looks fine'

## `release-manager` — Release Manager

Owns release mechanics: cadence, cut, release notes, RC tagging, deploy coordination

- **Participates in**: `release` _(derived from working-state `roles`)_
- **Does not**: decide what's in the release (that's pm), give the go/no-go (that's product-owner or incident-commander for patch trains), monitor post-deploy (that's on-call developer), declare or respond to incidents

## `incident-commander` — Incident Commander

Owns the incident lifecycle: triage, coordination, communication, postmortem creation

- **Participates in**: `incident-response`, `progressive-rollout`, `release` _(derived from working-state `roles`)_
- **Does not**: diagnose technical root causes, write code, merge PRs, draft or own the postmortem (that's pm), make business-level decisions (escalates to product-owner)

## `incident-responder` — Incident Responder

Owns incident diagnosis and mitigation execution: root-cause investigation, rollback/toggle/hotfix

- **Participates in**: `incident-response`, `mitigation`, `progressive-rollout` _(derived from working-state `roles`)_
- **Does not**: declare or assign severity (IC), communicate with stakeholders (IC), decide mitigation strategy (recommends to IC, IC decides), draft or own the postmortem (that's pm; responder contributes root-cause findings), file follow-up issues (pm)
