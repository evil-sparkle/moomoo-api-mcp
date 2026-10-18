# client-permissions Specification

## Purpose

Scope authenticated MCP clients to explicit tools, trading environments, and accounts independently of deployment-wide trading authority.

## Requirements

### Requirement: Restricted client authority
The server SHALL accept an optional distinct restricted credential whose explicit grants intersect with deployment policy. Defaults SHALL allow market/account reads and paper writes without live writes or gateway control. Unclassified tools SHALL be denied. Operator credentials SHALL remain separate.

#### Scenario: Restricted client on a live backend
- **WHEN** a restricted client requests a REAL mutation or gateway unlock without that grant
- **THEN** dispatch SHALL refuse before broker work even when deployment mode is REAL
- **AND** permitted paper operations SHALL retain their normal journal and account checks

### Requirement: Account disclosure scope
Restricted account grants SHALL apply to fresh reads, account discovery, cached pagination, stored requests, execution evidence and mutation retries. Health SHALL omit evidence outside readable scope. Process initialization SHALL remain independent of the triggering client.

#### Scenario: Replay cannot widen a client scope
- **WHEN** a client reuses a cursor or operation identifier belonging to an unauthorized account
- **THEN** the server SHALL refuse disclosure or mutation without returning that account's evidence
