# Local hotfix unlock verification — 2026-10-25

## Scope and authority

The user authorized a local REAL unlock test using an image built from the
hotfix and the existing environment file. No orders, modifications, or
cancellations are part of this test. The remote deployment is outside its scope.

## Provider documentation

The [official Moomoo unlock documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/unlock.html),
checked on 2026-10-25, describes lock/unlock as OpenD-wide: unlocking through one
connection permits transaction calls from other connections. It distinguishes
REAL order operations, which require unlock, from paper operations, which do not.

This resolves the documented scope, but does not itself constitute a live
cross-connection observation against this installation. Read success alone is
not an unlock-scope oracle. No intentionally invalid or real order will be sent
to manufacture an observable difference.

## Build evidence

- Remote hotfix: `hotfix/security-emudhra-20261023`.
- Verified source: `a5893883419af0f1453a74ca938c24fce6203a74`.
- Built locally from an isolated export of that commit's Docker build inputs.
- Image: `moomoo-api-mcp:unlock-test-hotfix-a589388`.
- Image config: `sha256:c8791850615f6fc712a030a16bebb5dc91c0454298d0d1d3e7c3d88b24da5bcb`.
- Build completed with the pinned OpenD archive checksum and hotfix trust
  hardening intact. No image was published.

## Initial preflight outcome

The existing environment file was supplied to Compose without displaying or
opening its contents. A runtime check emitted booleans only: login configuration
present, trade-unlock credential absent. Neither supported trade-password
environment variable was populated. The probe stopped before starting OpenD,
stopping the existing paper container, or issuing a lock/unlock call.

The original local service was confirmed to be SIMULATE before the attempt.
Its container/image were not replaced.

## Prepared test

An isolated temporary runner opens two SDK connections, locks both, unlocks A,
then unlocks B with the same authorized credential and records only success and
whether the provider explicitly reports an already-unlocked condition. Cleanup
re-locks both, closes connections, stops the temporary gateway and restores the
original paper container. A watchdog bounds the probe. No MCP endpoint or host
port is exposed by the temporary container. Even two successful unlock calls
would not independently prove scope unless their responses distinguish it.

## Authorized live retry after configuration update

The user populated the trade password locally and authorized another check.
Preflight then confirmed login and trade credential presence without displaying
either value. The original local paper container was stopped temporarily so
only the isolated hotfix gateway used its saved-login volume.

The first run established two SDK connections, but its initial lock and cleanup
lock requests failed before any unlock. The temporary gateway was terminated
and the paper container restored. Connection establishment alone was insufficient
as a readiness gate; a subsequent probe explicitly waited for REAL account
discovery before attempting locks. The exact cause of the first failure was
not captured, so startup readiness is a hypothesis rather than a proven cause.

The second run completed successfully:

| Step | Result |
| --- | --- |
| Two SDK connections with distinct connection IDs | Passed |
| REAL account discoverable | Passed |
| Initial lock through A | Passed |
| Initial lock through B | Passed |
| Unlock through A using configured trade credential | Passed |
| Unlock through B using configured trade credential | Passed |
| Final lock through A | Passed |
| Final lock through B | Passed |
| Temporary gateway stopped | Passed |
| Original paper container restarted | Passed |
| Restored authenticated health: SIMULATE, quote OK, trade OK | Passed |
| Order placement/modification/cancellation calls | Zero |

Neither unlock response reported an explicit already-unlocked condition. Thus,
this is live evidence for supported password-based unlock and successful relock
on the hotfix image, but is not an independent proof of cross-connection scope.
The official documentation establishes the documented OpenD-wide contract;
archived task 3.12 remains unproven and explicitly deferred, non-blocking, by
user decision on 2026-10-26. The documented OpenD-wide behavior is accepted for
the current occasional-operation design; no further scope experiment is required
for this change to merge or archive. No sensitive values, account IDs, or raw provider errors are recorded.

The first post-restore health call saw quote connectivity with the trade context
still unavailable. A bounded follow-up returned `connected`, `SIMULATE`, quote
`ok`, and trade `ok`. Restoration therefore includes authenticated gateway health,
not merely a running container. The original image remains selected.
