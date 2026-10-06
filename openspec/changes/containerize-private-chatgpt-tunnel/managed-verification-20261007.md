# Current verification: CI distribution, shared MCP token and cleanup

The 2026-10-07 follow-up uses the existing GitHub Actions/ECR pipeline for both
images and retires the recurring upstream-client diagnostic suite. The official
v0.0.14 pin, fixed endpoints, authentication, READ_ONLY, secret permissions,
rotation and managed runtime behavior remain the accepted deployment controls.
The owner selected direct reuse of the existing deployment `MCP_AUTH_TOKEN`
through Compose, accepting visibility to trusted Docker/host administrators.

## Implementation

- Main publishes application tags and distinct `tunnel-<commit>` tags to the
  existing `moomoo-api-mcp` ECR repository. PRs build without publishing.
- Both builds use independent cache scopes and change detection; unchanged main
  images are retagged, with a source-build fallback if the baseline is absent.
- The tunnel build uses the same enumerated public-input preparation in CI and
  disposable local builds. Production hosts do not build the tunnel separately.
- Enabled deployment confirms the tunnel commit tag before checkout, saves its
  ECR digest, pulls both services and restores the prior selection on rollback.
  Restarts reuse the saved digest. Application cleanup preserves tunnel tags.
- The diagnostic report job, reproduction runner, 515-case fixture, reporter and
  report-only tests are removed. Actual-image rootful/rootless integration remains
  required for relevant code/workflow changes. Documentation-only changes do not
  rerun the container suite; general CI still validates OpenSpec.
- Prior reports and case snapshots are preserved under
  [the earlier Git commit](https://github.com/evil-sparkle/moomoo-api-mcp/tree/76470318f3d9dbe3eca51ee10902580d50dfad1c/openspec/changes/containerize-private-chatgpt-tunnel).
  Their historical FAIL/BLOCKED statuses are not active release requirements.
- Dated reports are removed from the current tree; no history folder is tracked.
- Compose explicitly injects only the shared MCP token into the tunnel. The
  launcher validates it and derives a header through the official client's
  supported environment reference. The separate bearer mount/staging requirement
  is removed; only the OpenAI runtime key and tunnel ID remain mapped files.
- Bearer rotation updates one deployment token plus authorized local clients and
  recreates both containers. Restart keeps the old container environment; real
  forwarding and old-token refusal are checked in the disposable integration.

## Local verification

- Deployment, selection, build and Compose regressions: **70 passed**, **19 subtests passed**.
- CI filter, tag/retag/fallback and shared build-context checks: **22 passed**.
- Shared-token runtime/Compose checks: **23 passed** before adding the matching
  server token-normalization regression; final full-suite verification pending.
- Ruff lint/format: **PASS**, 97 Python files after diagnostic-code removal.
- Shell syntax and actionlint 1.7.12 for the edited CI workflows: **PASS**.
- Strict OpenSpec after cleanup/token revision: **27 passed, 0 failed**.
- Full basedpyright and full pytest: running; outcomes remain pending.

All checks use synthetic inputs or public source; no actual deployment credential
files are read. Previous runtime/container/hosted results are preserved in the
[earlier managed verification](https://github.com/evil-sparkle/moomoo-api-mcp/tree/76470318f3d9dbe3eca51ee10902580d50dfad1c/openspec/changes/containerize-private-chatgpt-tunnel/managed-verification-20261007.md),
with their original source/image provenance. New hosted CI results are pending.

VPS deployment, real OpenAI access, ChatGPT web and native iPad acceptance remain
**PENDING**. This work performs no production migration, trading or merge.
