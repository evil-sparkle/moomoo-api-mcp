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
  - **ADDED** `Recovery From a Gateway Restart`: while the gateway is restarted
    in place within the supervisor's retry budget, the MCP server keeps
    running, and gateway access recovers without restarting MCP or
    reconfiguring the client. Once the budget is exhausted, the supervisor
    exits and Docker's restart policy restarts the container instead.
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
| OpenD version / tag | `10.10.7008` / `v10.10.7008-opend` | `Dockerfile:31-32` build args |
| Pinned SHA-256 | `72eaa6e47b5cb8905306427b5e3679d591408243492e3e7acbc3a7d46f09a0aa` | `Dockerfile:39` `OPEND_SHA256` |
| GitHub-reported digest of `moomoo_OpenD_10.10.7008_Ubuntu18.04.tar.gz` | `sha256:72eaa6e4…09a0aa` (matches) | Releases API, checked 2026-09-18 |
| Asset size | 466,932,458 bytes | Releases API |
| CDN fallback size | 466,932,458 bytes (same) | HTTP `HEAD`, checked 2026-09-18 |

When this was drafted (`ef4acc4`), the pins lived in `Dockerfile.opend`. PR #9
moved them unchanged into the combined `Dockerfile` and deleted that file.

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
  change edits none of those four, but the two changes still interact.
  Separate requirement names do not rule out contradictory behaviour. As first
  drafted, "the MCP server SHALL NOT be restarted as a consequence" of a gateway
  restart contradicted `Paired Process Supervision`, which stops the MCP server
  once the gateway exhausts its retry budget. `Recovery From a Gateway Restart`
  is now scoped to in-place restarts within that budget and defers to
  `Paired Process Supervision` beyond it.
- Ordering: `refactor-single-container-deployment` is implemented on `main`
  (PR #9), and this change now names its `Paired Process Supervision`
  requirement. Archive that change first, so the requirement exists in the
  canonical spec before this one refers to it.
- Follow-up work found while drafting (runtime or docs changes, deliberately
  kept out of this documentation-only change) is listed in `tasks.md` § 3.
