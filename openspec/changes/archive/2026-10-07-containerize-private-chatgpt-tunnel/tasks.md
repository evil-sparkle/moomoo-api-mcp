# Tasks

The current plan reflects the owner's final deployment requirements. Original diagnostic reports and earlier verification are retained in Git history. Current validation results belong in PR #38 rather than tracked dated reports.

## 1. Preserve official runtime and HTTP controls

- [x] 1.1 Preserve official v0.0.14 integrity/base pins and public build-input allowlist; verify image construction and binary provenance.
- [x] 1.2 Preserve exact Docker Host/destination opt-ins, bearer/Origin checks and server READ_ONLY enforcement; verify transport and mutation-refusal tests.
- [x] 1.3 Preserve authenticated bounded startup, filtered child environment, safe diagnostics and independent child/liveness/signal recovery; verify actual-image integration.

## 2. Deploy from environment configuration

- [x] 2.1 Inject the limited OpenAI key, tunnel ID and existing MCP token explicitly through Compose; remove mounted-secret requirements and validate missing/invalid settings without printing values.
- [x] 2.2 Update only the authorized `.env.example` template and verify its optional settings match the runtime and default-off deployment.
- [x] 2.3 Make deploy.sh enable/disable/update the optional service and image selection without extra operator scripts, preserving the existing project; cover first enable, disable, missing images/credentials and rollback in disposable tests.
- [x] 2.4 Keep main-only CI publication/retagging of both images in the existing ECR repository; verify distinct tags, independent caches, missing-baseline fallback and PR builds.

## 3. Remove superseded migration and diagnostic artifacts

- [x] 3.1 Remove host tunnel unit/config, credential staging/provisioning helpers and obsolete host-permission tests; verify no active references remain.
- [x] 3.2 Remove recurring direct-client diagnostic job/code and dated reports without a history folder; verify they remain available only in earlier Git commits.
- [x] 3.3 Reconcile active specs, comments, runbooks and repository context with the one-script, environment-based official-client approach; scan for drift and run strict OpenSpec validation.

## 4. Validate and hand off

- [x] 4.1 Update actual-client rootful/rootless fixtures for environment credentials and both rotations; verify normal/negative forwarding, restart retaining old environment, recreation adopting new values, isolation and recovery.
- [x] 4.2 Run required unit/deployment, lint/format/types, workflow and strict OpenSpec checks; push/update PR #38 with actual results and any remaining live-acceptance limits.
- [x] 4.3 Verify existing server container/project/state access read-only and deliver the migration Markdown in chat plus a file outside the repository; preserve project/state during any separately authorized pre-merge trial and the post-merge deployment.

## 5. Retire OpenD password-MD5 startup

- [x] 5.1 Remove the retired gateway login setting from the public template, Compose and supervisor argument selection; preserve interactive and remembered login and separate trade-unlock credentials.
- [x] 5.2 Reconcile deployment runbooks, startup errors, comments and project context with the supported login flow; keep migration details outside Git and OpenSpec active until final approval.
- [x] 5.3 Verify obsolete hash values cannot override remembered login or start a gateway without remembered state; update synthetic fixtures and run focused supervisor/topology checks, lint/types and strict OpenSpec validation without retrying live brokerage login.

After final PR approval in chat, archive/sync this OpenSpec change. After merge and CI image publication, perform the external migration guide with existing permissions/credentials, escalating only missing credentials or permissions. These post-approval actions are not claimed complete by repository implementation or synthetic CI.
