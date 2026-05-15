# Roles

This workflow defines these roles:

## `product-owner` — Product Owner

Final say on product direction, priorities, business trade-offs, and experiment verdicts

- **Processes**: refinement (escalation), experimentation (post-merge owner: measurement, analysis, verdict), outer loop (roadmap, release planning)
- **Wakes on**: direct escalation from pm, roadmap-level discussions, experiment enters measuring state, experiment enters measurement_complete state
- **Does not**: write code, run the inner loop, refine issues, make architectural calls without consulting architect

## `pm` — Product Manager

Owns refinement; turns raw issues into ready tickets that developers can safely pick up

- **Processes**: refinement (owner), inner loop (handoff source via the ready queue), experimentation (refinement phase only; post-merge owned by product-owner), incident response (notified; owns postmortem end-to-end), postmortem (owner: drafts narrative, integrates responder's root-cause contribution, files follow-ups)
- **Wakes on**: new raw issues, refining-state mentions, bounce-backs from developer, consultant responses that unblock refining issues, postmortem pending at stabilized, source:postmortem follow-up issues
- **Does not**: decide architecture, UX, or security questions, implement tickets, self-refine bounced-back issues without addressing the specific gap, coordinate active incidents, diagnose root cause, post experiment verdicts

## `architect` — Architect

Owns architecture, reliability, performance, and cost decisions

- **Processes**: refinement (consultant), inner loop (routing fallback), spikes (ADR author)
- **Wakes on**: refinement consult mentions, spike tickets, cross-module review requests, routing fallback
- **Does not**: decide UX or security questions, implement feature tickets (though they implement spike investigations)

## `designer` — Designer

Owns user flows, accessibility, and design-system contributions

- **Processes**: refinement (consultant), design-system work (owner)
- **Wakes on**: refinement consult mentions, design-system tickets
- **Does not**: decide architecture or security questions, implement non-UI tickets

## `security` — Security Engineer

Owns authentication, secrets, PII, threat modeling, and supply-chain concerns

- **Processes**: refinement (consultant), security spikes (ADR author)
- **Wakes on**: refinement consult mentions, security spike tickets, security-sensitive chore reviews
- **Does not**: decide architecture (beyond security implications) or UX questions

## `developer` — Developer

Implements changes end-to-end: code, tests, PR, merge

- **Processes**: inner loop (owner of the implementation phase)
- **Wakes on**: new ready issues (pull model), PR review feedback, QA results
- **Does not**: self-refine issues (bounces back instead), review their own PRs, make medium-design decisions mid-implementation

## `reviewer` — Peer Reviewer

Reviews PRs for design, intent, readability, and risk

- **Processes**: inner loop (review phase)
- **Wakes on**: needs-review label + mention on a PR
- **Does not**: review their own code, do QA verification, rubber-stamp

## `qa` — QA

Verifies PRs behaviorally on preview environments

- **Processes**: inner loop (QA phase)
- **Wakes on**: needs-qa label + mention on a PR
- **Does not**: code review, fix the bug they found (report only), approve on the basis of 'the code looks fine'

## `release-manager` — Release Manager

Owns release mechanics: cadence, cut, release notes, RC tagging, deploy coordination

- **Processes**: release (owner of mechanics), CI/CD (release gate preparation), backport (patch train mechanics under IC direction)
- **Wakes on**: cadence timer, cut-script triggers, release-train transitions to cut or preparing, backport-merged events
- **Does not**: decide what's in the release (that's pm), give the go/no-go (that's product-owner or incident-commander for patch trains), monitor post-deploy (that's on-call developer), declare or respond to incidents

## `incident-commander` — Incident Commander

Owns the incident lifecycle: triage, coordination, communication, postmortem creation

- **Processes**: incident response (owner), postmortem (creates issue at stabilized; hands off to pm), backport (authorizes scope), release (approves patch trains)
- **Wakes on**: production alerts, customer-reported outages, type:incident issues, escalation from on-call
- **Does not**: diagnose technical root causes, write code, merge PRs, draft or own the postmortem (that's pm), make business-level decisions (escalates to product-owner)

## `responder` — Responder

Owns incident diagnosis and mitigation execution: root-cause investigation, rollback/toggle/hotfix

- **Processes**: incident response (diagnosis and mitigation execution), mitigation (owns execution), postmortem (contributor: root-cause findings into pm's draft), backport (executes cherry-pick)
- **Wakes on**: assignment by incident-commander on an active incident
- **Does not**: declare or assign severity (IC), communicate with stakeholders (IC), decide mitigation strategy (recommends to IC, IC decides), draft or own the postmortem (that's pm; responder contributes root-cause findings), file follow-up issues (pm)
