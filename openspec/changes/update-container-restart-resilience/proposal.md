# Change: Specify Restart Recovery, Stateless Transport and Reconnect Locking as Built

## Why

Three PRs of restart work (`f6d8a92`, `19227f4`, `6e171d5`) changed how the
server behaves when either process restarts, but the specifications were not
updated to match. The result is specs that are wrong in both directions: some
promise less than the code does, and one promised a checksum that no build
checks.

- `container-deployment` said nothing about what a client experiences when
  OpenD or the MCP server restarts. That is now the most operationally important
  property of the deployment.
- The integrity requirement pinned a literal digest (`d38aad77…`) that the build
  never checked. PR #7 replaced it with a reference to the build arg. This change
  restates the requirement as a reviewed pin, so the spec survives an OpenD
  version bump without also relaxing the security property.
- `trade-unlock` covers the READ_ONLY lock at startup only. The code also
  re-asserts it after every SDK reconnect and deliberately tolerates a refused
  lock. Nearby wording also implies that a configured password alone unlocks
  trading or enables REAL access, whatever the configured trading mode. The code
  has not worked that way since the trading-mode policy landed.
- Stateless Streamable HTTP and process-owned gateway connections are
  architectural contracts with no spec at all. `docs/state-and-restarts.md` and
  code comments are the only record of them.

This change documents existing behaviour. It adds no runtime guarantees, and it
changes no code.

## What Changes

- `container-deployment`
  - **MODIFIED** `Binary Download Integrity Verification`: the build verifies
    the archive against the reviewed SHA-256 pin held in version-controlled build
    configuration. Verification happens before extraction, and a mismatch fails
    the build whatever the download source. The digest is deliberately not
    restated; the current values appear below as evidence only.
  - **ADDED** `Recovery From a Gateway Restart`: the MCP server keeps running,
    and gateway access recovers without restarting MCP or reconfiguring the
    client.
  - **ADDED** `Recovery From an MCP Server Restart`: once the server is back,
    authenticated requests work without a stale session blocking them.
  - **ADDED** `Restart Recovery Does Not Replay Trading Commands`: requests that
    overlap downtime may fail. SDK reconnect replay is limited to connection
    state. A lost order response is not evidence either way and does not
    authorize resubmission.
- `trade-unlock`
  - **RENAMED + MODIFIED** `Proactive Lock on Read-Only Startup` → `Read-Only
    Gateway Lock on Connect and Reconnect`. The service issues a lock on the
    initial connection and whenever the SDK re-establishes it. A refused lock is
    logged, and policy enforcement stays in force.
  - **MODIFIED** `Auto-unlock Trade at Startup`, `Manual Unlock Tool` and
    `unlock_trade Tool Environment Variable Fallback`: unlocking depends on
    `MOOMOO_TRADING_MODE=REAL`. A configured credential never changes the mode,
    and "no password" no longer implies "SIMULATE-only".
- `transport-sessions` (new capability)
  - **ADDED** `Stateless Streamable HTTP`: no session id is issued or required,
    a stale one is ignored, and authentication is checked per request.
  - **ADDED** `Process-Owned Gateway Connections`: connections open once per
    process and are shared by every request and client. Sessions do not own
    them, and they close at process exit.

Nothing here is **BREAKING**: every requirement describes behaviour already on
`main`.

## Evidence for the checksum values (not normative)

Recorded so a reviewer can check the build pin against the release it came from.
None of this belongs in the requirement.

| Item | Value | Source |
| --- | --- | --- |
| OpenD version / tag | `10.10.7008` / `v10.10.7008-opend` | `Dockerfile.opend` build args |
| Pinned SHA-256 | `72eaa6e47b5cb8905306427b5e3679d591408243492e3e7acbc3a7d46f09a0aa` | `Dockerfile.opend` `OPEND_SHA256` |
| GitHub-reported digest of `moomoo_OpenD_10.10.7008_Ubuntu18.04.tar.gz` | `sha256:72eaa6e4…09a0aa` (matches) | Releases API, checked 2026-09-18 |
| Asset size | 466,932,458 bytes | Releases API |
| CDN fallback size | 466,932,458 bytes (same) | HTTP `HEAD`, checked 2026-09-18 |

The digest comes from GitHub's release metadata. Nobody downloaded the archive
and re-hashed it independently, and the CDN copy was compared by size only.
Either way the build fails closed: an archive that does not match the pin is
never extracted.

## Impact

- Affected specs: `container-deployment`, `trade-unlock`, `transport-sessions`
  (new)
- Affected code: none. See `design.md` § Evidence for the code and tests each
  requirement rests on, and which ones are unverified.
- Interaction with `refactor-single-container-deployment`: that change
  MODIFIES `Isolated OpenD Gateway Network`, `Session State Persistence` and
  `Non-Root Container Execution`, and ADDS `Paired Process Supervision`. This
  change touches none of those four. Its restart requirements are worded in
  terms of the gateway *process* and the MCP server *process*, so they hold
  under the current two-container layout and under the proposed single
  container. Neither change needs to land first.
- Follow-up work found while drafting (runtime or docs changes, deliberately
  kept out of this documentation-only change) is listed in `tasks.md` § 3.
