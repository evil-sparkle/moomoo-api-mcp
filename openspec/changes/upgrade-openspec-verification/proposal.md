# Proposal

## Why

CI pins OpenSpec 1.13.1 while managed integrations were generated with 1.13.0.
The workflow lacks verify and loads a large repository handbook for every change.

## What Changes

- Pin CLI use and CI validation to 1.14.1 and regenerate existing integrations.
- Track a reproducible skills-only custom profile containing the six core workflows plus verify.
- Centralize the scope-aware completion policy in AGENTS.md, with short references
  from config and the development guide.
- Reduce always-loaded context to invariants and pointers; index deeper docs.
- Reformat 43 long requirement bodies in 13 canonical specs for 1.14.1 strict
  validation, retaining every original word and existing scenario.
- Defer GitHub agentic maintenance to a separate maintenance/diagnostics change.

## Capabilities

### New Capabilities

None. This is developer tooling and documentation maintenance.

### Modified Capabilities

None. Canonical spec formatting changes preserve all behavior and normative
obligations. `skip_specs: true` avoids inventing behavioral delta requirements.

## Impact

Managed agent files, CI's OpenSpec pin, regeneration tooling, root agent
instructions, and documentation. No brokerage application, deployment, or
credential changes. The installed local CLI is upgraded to 1.14.1 as requested.
