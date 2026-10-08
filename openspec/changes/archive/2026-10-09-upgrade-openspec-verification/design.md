# Design

## Context

See proposal.md for scope. OpenSpec 1.14.1 selects workflows through global
configuration, and strict validation now rejects requirement text over 500
characters ([release notes](https://github.com/Fission-AI/OpenSpec/releases/tag/v1.14.1)).
Existing integrations are Codex, Antigravity, Claude, and OpenCode.

## Goals / Non-Goals

Keep regeneration repeatable across machines and preserve every trading safeguard.
Do not install unrelated expanded workflows, change brokerage behavior, modify
archived history, or introduce agentic GitHub Actions in this change.

## Decisions

1. Track `openspec/profile.json` and use a short regeneration script with a
   temporary `XDG_CONFIG_HOME`, with skills-only delivery across the four tools.
   This removes duplicate commands/workflows; invoke the skills instead. Explicit
   `init --tools` refreshes all four integrations, including Antigravity after its
   workflow-based detection disappears. The exact 1.14.1 version matches CI. Personal
   global profile edits would be less reproducible and affect unrelated projects.
2. Keep the full completion policy in `AGENTS.md`; config and the README contributor
   section reference it. The policy distinguishes a selected OpenSpec change from
   ordinary maintenance. Generated files remain upstream output, so regeneration
   cannot erase local policy.
3. Keep architectural requirements in `openspec/specs/`, checks in
   [README contributor guidance](../../../README.md#contributing), and operator
   procedures in [the deployment runbook](../../../docs/deploy-vps.md). The
   documentation consolidation supersedes the earlier separate guide/index layout.
4. Keep each long requirement's concise opening and insert a named scenario with
   a specific trigger before its detailed obligations. Preserve original wording,
   requirement names, and existing scenarios. Strict-mode relaxation or deleting
   clauses would weaken verification; rewriting trading behavior is out of scope.

## Risks / Trade-offs

- Bare `openspec update` uses the personal profile and may remove verify: document
  and use the pinned repository script.
- New scenario boundaries could lose normative clauses: verify each old
  requirement's non-whitespace text is preserved after removing only inserted
  framing; independently run strict validation.
- Instructions guide agent behavior but are not a sandbox or runtime enforcement
  mechanism. Report evidence and blockers rather than promising automatic success.

## Migration Plan

Regenerate integrations, validate inventory and repeatability, run the full test
suite and applicable tooling checks, then execute the installed verify workflow.
Reverting this maintenance change restores prior repository behavior; the local
CLI may be explicitly downgraded separately if needed. No service rollout occurs.
