# Temporary production branches and return to main

`main` remains the canonical development branch and hosts the trusted deployment
controller. The repository variable `DEPLOY_BRANCH` selects one application
branch for image publication and deployment; it defaults to `main` when unset.
As of 2026-10-25, the selected branch is `hotfix/security-emudhra-20261023`.
Read the variable before a release rather than treating this dated value as a
permanent setting.

## Branch selection, publication and deployment

AWS OIDC trust permits the configured repository's `main` and `hotfix/*`
subjects to request the publisher role. That is an authorization boundary, not
a release selection: the existing CI publishes only the exact `DEPLOY_BRANCH`.
Changing AWS trust does not start CI, and changing `DEPLOY_BRANCH` alone does
not build, publish or deploy anything.

CI tests and builds the selected source on GitHub runners, publishes both the
application and tunnel images to ECR, and records their immutable digests with
the source commit, run ID and attempt in `deployment.json`. Unchanged images
may be reused by the verified retag path. The VPS pulls the selected image pair
instead of compiling the application or tunnel there.

Successful **push** CI on the selected branch can automatically dispatch
`deploy.yml` from `main`. The dispatcher and deploy controller validate the
exact release and skip obsolete automatic requests. The protected
`vultr-production` executor remains on `main`; the image/configuration source
comes from the selected application branch. A `main` push while a hotfix is
selected does not publish or automatically deploy that main revision.

## Keep features synchronized

1. For ordinary features and fixes, open a PR against `main`, review and merge
   it, then backport the relevant commits through a PR targeting the selected
   hotfix branch. Record the original commits, for example with
   `git cherry-pick -x`, and review conflict resolutions.
2. For urgent production fixes, start from the selected hotfix and merge a
   reviewed fix there first. Promptly forward-port the functional fix through
   a separate PR against `main`, with links to the original change.
3. Keep the temporary CA/runtime safeguards intact when copying features.
   Synchronize focused changes rather than assuming that either branch's
   entire security implementation is appropriate for the other.
4. Keep a supported-upstream replacement PR, such as
   [PR #70](https://github.com/evil-sparkle/moomoo-api-mcp/pull/70), synchronized
   with current `main`. Hotfix-only features do not automatically enter that PR;
   maintain an explicit record of changes still needing forward-porting.

A push or merge into the selected hotfix is a production release trigger.
Review the exact source commit before that push. Currently the primary CI's
`pull_request` filter targets only `main`: a hotfix-target PR does not by itself
guarantee equivalent automatic checks. Where those checks are absent, dispatch
`ci.yml` on the fix branch, verify its exact head and completed checks, and
satisfy repository review/protection requirements before merging. CI on an
unselected branch performs checks without publishing. This guide does not
change workflow triggers or branch protection.

## First release or manual redeployment

To build and publish the selected hotfix without automatically deploying it:

```sh
gh workflow run ci.yml --repo evil-sparkle/moomoo-api-mcp \
  --ref hotfix/security-emudhra-20261023
```

Wait for that exact CI run and attempt to succeed and validate the image-pair
manifest. Then request deployment through the main controller:

```sh
gh workflow run deploy.yml --repo evil-sparkle/moomoo-api-mcp --ref main \
  -f ci_run_id='<successful-selected-branch-CI-run-id>' \
  -f ci_run_attempt='<successful-attempt>' -f automatic=false
```

Manually dispatched CI does **not** trigger the push-only automatic dispatcher.
A successful dispatch only queues the deployment; verify the deployment run,
authenticated MCP, OpenD readiness, tunnel health and a hosted ChatGPT read.
Keep credentials server-side and retain the previous image pair, configuration
snapshot and state volumes for rollback. See the
[Ansible runbook](deploy-ansible.md) for the concrete checks and recovery paths.

## Return production to main

1. Reconcile hotfix and `main` history and forward-port missing features/fixes.
   Account for patch-equivalent cherry-picks when identifying missing changes.
2. Update the supported-upstream replacement PR on current `main`. Verify the
   actual official release binaries/images contain the required security fixes
   and pass trust, compatibility and deployment checks. An upstream source
   toolchain pin alone does not prove a published binary is fixed. Remove
   temporary machinery only after equivalent protection is demonstrated.
3. Review and merge the replacement into `main` while the hotfix remains
   selected. This does not itself move production to main.
4. Coordinate outstanding releases, set `DEPLOY_BRANCH=main`, and manually
   dispatch `ci.yml` with `--ref main`. After successful publication, dispatch
   `deploy.yml` with `--ref main`, that run/attempt and `automatic=false` as
   above. Subsequent successful main-push CI resumes automatic deployment.
5. Verify production and hosted ChatGPT access, then retire the temporary
   branch when it is no longer required for rollback. Preserve release records,
   images and state independently of branch cleanup.

Workflow rollback accepts releases from the currently selected branch. To use
an older hotfix CI run after switching to main, deliberately restore that
branch selection first; the runbook's server-side frozen-configuration rollback
is a separate recovery path. Do not discard the old release while validating
the cutover.
