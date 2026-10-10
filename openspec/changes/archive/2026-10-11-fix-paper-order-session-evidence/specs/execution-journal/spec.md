# Spec Delta

## ADDED Requirements

### Requirement: Authoritative Paper Order Session Evidence

Journaled US paper placement SHALL specify the documented RTH session without
an explicit deprecated hours argument. Modifications and cancellations SHALL
require a fresh NORMAL/DAY target observation with explicit RTH before dispatch.
The application SHALL NOT consult the deprecated legacy flag for eligibility.
Missing, unknown, NONE or extended sessions SHALL fail closed. Raw legacy
provider observations MAY be retained without normalization.

#### Scenario: Explicit RTH without legacy data

- **WHEN** the fresh target is NORMAL/DAY with an explicit RTH session
- **THEN** session evidence SHALL permit paper preparation to continue regardless
  of the absence or value of deprecated legacy data
- **AND** all other account, instrument, visibility and limit checks SHALL apply
- **AND** provider fields SHALL NOT be rewritten to force eligibility

#### Scenario: Deprecated data cannot establish unknown-session eligibility

- **WHEN** the target session is NONE, missing, unknown, ETH, OVERNIGHT or ALL
- **THEN** the mutation SHALL be REFUSED with disposition NOT_SENT
- **AND** no dispatch marker or SDK mutation invocation SHALL occur even if
  deprecated legacy data contains false or zero

#### Scenario: Non-DAY or non-NORMAL target remains outside scope

- **WHEN** an RTH target lacks NORMAL order type or DAY validity
- **THEN** the mutation SHALL be REFUSED with disposition NOT_SENT before dispatch

#### Scenario: Stored refusal remains immutable after migration

- **GIVEN** an intent was refused for ineligible target-session evidence
- **WHEN** an identical retry arrives after a fresh broker record becomes eligible
- **THEN** its stored refusal SHALL be returned without another SDK invocation
- **AND** its original identity and epoch SHALL remain unchanged

#### Scenario: Placement uses documented session argument

- **WHEN** an eligible paper placement reaches SDK dispatch
- **THEN** its arguments SHALL include the RTH session and DAY validity
- **AND** SHALL omit the deprecated hours argument
