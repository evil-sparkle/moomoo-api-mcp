## 0. Proposal and Contract Review

- [x] 0.1 Review requirements R1-R8 and approve scope, especially the default
  READ_ONLY policy and response-type migration.
- [x] 0.2 Reconcile the account-ID proposal with implemented string inputs and
  plan archival order with the completed combo and market-data changes.
- [ ] 0.3 Verify SDK timeout, cursor, permission, and quota contracts against the
  installed minimum-supported SDK; record any resulting interface adjustments.

## 1. Foundation — R1, R2, R3

- [x] 1.1 R1: Implement active quote/trade health probes, bounded execution,
  partial-startup cleanup, and availability of health during downstream failure.
- [x] 1.2 R1: Test healthy, quote-only failure, trade-only failure, total failure,
  timeout, repeated timeout, and gateway failure after successful initialization.
- [x] 1.3 R2: Apply shared field-aware ID serialization to every covered account
  tool, including nested summaries; preserve exact service values.
- [x] 1.4 R2: Test actual MCP text and structured responses through an IEEE-754
  roundtrip, using synthetic large IDs, nulls, strings, invalid IDs, and nested rows.
- [x] 1.5 R3: Add policy configuration and service guards for every order mutation
  and unlock path; align startup, health reporting, and agent guidance.
- [x] 1.6 R3: Test the full mode/environment matrix through tools and direct service
  calls; assert denied operations make zero gateway calls and never change mode.
- [x] 1.7 Publish migration documentation for string IDs and explicit trading mode.

## 2. Options Workflow — R4, R5

- [x] 2.1 R4: Implement combo preview using shared leg validation/account selection
  and the SDK tradability query; return timestamped account-impact fields.
- [x] 2.2 R4: Test valid opening/closing legs, exact IDs, validation failures,
  account selection, unavailable values, and SDK rejection. Assert no placement,
  modification, cancellation, or unlock call occurs during preview.
- [x] 2.3 R5: Add option expiration and chain services/tools with date and enum
  validation and documented empty-result behavior.
- [x] 2.4 R5: Test exact symbol preservation, call/put filtering, date boundaries,
  empty chains, and unsupported instruments or permission errors.
- [x] 2.5 Exercise discovery -> selected contracts -> preview using actual MCP
  tool calls and mocked SDK responses. Add examples without live identifiers.

## 3. Market Data Completion — R6, R7, R8

- [x] 3.1 R6: Add market-state and trading-calendar tools with explicit date/time
  semantics. Test holidays, session transitions, partial sessions, and SDK errors.
- [x] 3.2 R7: Add the paginated candle tool with lossless filter-bound cursors;
  retain the existing list-returning tool and document its one-page behavior.
- [x] 3.3 R7: Test multiple pages, terminal and empty intermediate pages, corrupt
  cursors, filter mismatch, later-page errors, and exact candle ordering.
- [ ] 3.4 R8: Expose current-connection subscription inspection and explicit release.
- [ ] 3.5 R8: Test subscription reuse, quota reporting, explicit release, failed
  early release, and isolation from other connections.

## 4. Release Gates

- [ ] 4.1 Run the complete pytest suite and the new MCP workflow tests. Confirm
  compatibility with Python >=3.10 and the minimum-supported SDK.
- [ ] 4.2 Check changed files with ruff and compare existing-file diagnostics with
  the baseline; do not hide new errors behind the repository's existing lint debt.
- [ ] 4.3 Validate this proposal strictly and verify each requirement scenario has
  a test or a documented read-only smoke-test procedure.
- [ ] 4.4 Update README, generated/runtime tool schemas, migration instructions,
  and examples. Ensure health and preview never claim trading authorization.
- [ ] 4.5 If an authorized gateway is available, run read-only health, discovery,
  calendar, and preview smoke checks. Record unavailable broker features as limits.
  These checks must not submit a live order.
- [ ] 4.6 Review each slice, select the release version, and archive approved
  changes only after deployment in the dependency order described in the proposal.
