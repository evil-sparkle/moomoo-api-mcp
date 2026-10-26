## ADDED Requirements

### Requirement: Report Lock At Rest Attempts

Health SHALL expose `gateway_lock_at_rest` with the latest connect/reconnect lock
attempt status and nullable UTC `checked_at`, without broker calls or raw provider
errors. It SHALL describe historical attempts, not current gateway lock state,
and SHALL NOT change connectivity status, execution halt, or trading authority.

#### Scenario: Lock attempt fails then succeeds

- **WHEN** a connect/reconnect lock attempt fails
- **THEN** health SHALL report `failed` and the attempt completion time
- **AND** a later successful attempt SHALL replace it with `succeeded` and its time
- **AND** the read connection SHALL remain available

#### Scenario: No applicable or completed attempt

- **WHEN** lock-at-rest policy does not apply, including SIMULATE
- **THEN** health SHALL report `not_applicable` with null `checked_at`
- **AND** an applicable policy before any completed attempt SHALL report
  `not_attempted` with null `checked_at`

#### Scenario: An attempt is blocked or skipped

- **WHEN** a broker lock request is still pending or a JIT write skips the attempt
- **THEN** health SHALL promptly report the previous observation without waiting
  for the broker or changing the observation
