# Design

## Context

The initial fix accepted explicit RTH with exact legacy N/A. The Mac activation
and original-order cancellation are recorded in runtime-verification.md. The
operator now requests removal of the deprecated application parameter and use
of the documented session API, followed by a local Mac test image replacement.

Moomoo's [placement documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html)
deprecates the legacy argument in favor of session. Its
[session definitions](https://openapi.moomoo.com/moomoo-api-doc/en/quote/quote.html#session)
define RTH as regular hours and NONE as unknown. The installed 10.10.7008 SDK
still supplies the legacy default and returns raw legacy columns; removing the
application argument does not alter that dependency's wire implementation.

## Goals / Non-Goals

**Goals:** Explicit RTH placement/target evidence, no application dependency on
the deprecated flag, exactly-once journal behavior and a verified local image.

**Non-Goals:** Additional sessions, SDK patching/upgrades, provider-field
normalization, journal migration, production deployment or completion claims
for the remaining live lifecycle/fault/retry/visibility tests.

## Decisions

1. Use `Session.RTH` for placement and compare the fresh target's session against
   that same value. Do not pass, read, normalize or fall back to the deprecated
   flag. Legacy columns may remain in raw SDK query results and stored evidence.
2. Require NORMAL/DAY and explicit RTH. NONE plus a false legacy flag is no
   longer sufficient. Extended, missing, null and unrecognized sessions remain
   refused with NOT_SENT and marker zero, independent of legacy values.
3. Keep this inside serialized preparation. Existing account, instrument,
   notional, prior-modification visibility and durable dispatch checks remain.
   An old refused intent stays immutable even after the source/image changes.
4. Exercise modification/cancellation through stateful doubles and SQLite.
   Prove explicit RTH works when legacy data is absent, null, malformed, true,
   false or unavailable. Prove the legacy flag cannot admit unknown sessions.
5. Stage only allowlisted source/build inputs into a private temporary build
   context. Record source HEAD, patch/file hashes, image identity and platform.
   Activate the immutable local image ID in only the existing Mac project.

## Risks / Trade-offs

- Rejecting NONE may refuse older ambiguous observations. This is intentional:
  the current US scope requires positively reported regular-hours evidence.
- The SDK retains its deprecated default internally. Do not patch dependencies
  or claim the underlying SDK protocol no longer contains the legacy field.
- Recreation changes the admission epoch. Preserve all stored prior epochs,
  caller markers and operation rows; compare complete row/evidence hashes.
- Coordinate with the existing hourly lock throughout development/activation.
  No second brokerage executor or unattended deployment is enabled.

## Migration Plan

No storage migration. Run focused/full checks, build a distinct linux/amd64
local image and verify packaged source before activation. Preserve the original
three-operation journal, named volumes, remembered login, SIMULATE/US exact
allowlist, USD 336 cap and loopback port. Authenticate agent/operator MCP and
verify health, SDK/OpenD, orders/positions and row hashes without mutations.
Record updated image/epoch and continuation state. Retain the previous image
and evidence for rollback; never reset the journal or replay stored intents.
