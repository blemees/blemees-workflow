---
name: Feature request
about: Suggest a change to the workflow engine, CLI, or the state-machine model
title: ""
labels: enhancement
---

## Problem
<!-- What are you trying to do? What's blocking you today? -->

## Proposal
<!-- Concrete change you have in mind. Impact on the state-machine model / schema, if any. -->

## Alternatives considered
<!-- Other approaches and why they're worse. Optional. -->

## Out of scope

`workflow` is the operation mechanism for the state-machine framework defined in
`docs/state-machine-principles.md`. Changes to the principles themselves belong in
an ADR (`docs/adr/`), not a feature request. The engine is tracker-agnostic by
design — backend-specific behavior lives behind the `TrackerBackend` protocol.
