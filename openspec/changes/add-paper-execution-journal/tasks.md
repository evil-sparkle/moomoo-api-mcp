# Tasks: Add Paper Execution Journal

## 1. Prerequisite Alignment and Foundation Setup

- [ ] 1.1 Verify prerequisite safeguards merge and reconcile delta specs against landed main specs (`openspec validate --all --strict --no-interactive`)
- [ ] 1.2 Implement configuration parsing for `MOOMOO_SIMULATE_ACC_IDS`, `MOOMOO_EXECUTION_DB_PATH`, and `MOOMOO_EXECUTION_BUSY_TIMEOUT_MS` and verify with unit tests (`pytest tests/test_config.py`)
- [ ] 1.3 Add Docker volume `execution-data` in `docker-compose.yml` and directory provisioning in `Dockerfile` and verify with `docker compose config`

## 2. ExecutionStore Storage and Schema Implementation

- [ ] 2.1 Implement `ExecutionStore` SQLite connection management, WAL mode, pragmas, and schema v1 migrations and verify with unit tests (`pytest tests/test_execution_store_schema.py`)
- [ ] 2.2 Implement request canonicalization and SHA-256 fingerprinting for order placement, modification, and cancellation and verify with unit tests (`pytest tests/test_execution_fingerprint.py`)
- [ ] 2.3 Implement atomic operation reservation (`PENDING_SUBMIT`), duplicate detection, and conflict rejection in `ExecutionStore` and verify with concurrent thread tests (`pytest tests/test_execution_concurrency.py`)
- [ ] 2.4 Implement post-dispatch status updates, transition logging, and broker receipt persistence in `ExecutionStore` and verify with unit tests (`pytest tests/test_execution_transitions.py`)
- [ ] 2.5 Implement storage failure handling (pre-dispatch refusal vs post-dispatch execution halt) in `ExecutionStore` and verify with fault-injection tests (`pytest tests/test_execution_storage_failures.py`)

## 3. Trading Policy and Paper Mode Isolation

- [ ] 3.1 Update `TradingPolicy` to require `MOOMOO_SIMULATE_ACC_IDS` in `SIMULATE` mode, refuse REAL writes without rewrite, and bypass database in `READ_ONLY` mode, verifying with unit tests (`pytest tests/test_trading_policy.py`)
- [ ] 3.2 Update `TradingPolicy` to reject unsupported mutation routes in paper mode (combos, market orders, stop/trailing stop) and verify with unit tests (`pytest tests/test_paper_policy_restrictions.py`)
- [ ] 3.3 Ensure direct service instantiation and default configuration enforce READ_ONLY behavior without creating database files, verifying with isolated unit tests (`pytest tests/test_readonly_isolation.py`)

## 4. Service Integration and Execution Decoupling

- [ ] 4.1 Update `TradeService.place_order` to integrate with `ExecutionStore` (pre-dispatch commit, gateway dispatch without DB lock, post-dispatch status update) and verify with mock broker tests (`pytest tests/test_trade_service_journal.py`)
- [ ] 4.2 Update `TradeService.modify_order` and `cancel_order` to route through `ExecutionStore` and verify that racing fills are recorded factually with mock broker tests (`pytest tests/test_order_modification_journal.py`)
- [ ] 4.3 Implement broker reconciliation queries (`get_orders`, `get_deals`) for operations in `UNKNOWN_OUTCOME` and verify with stateful fake broker tests (`pytest tests/test_reconciliation.py`)
- [ ] 4.4 Extend `HealthService` and `check_health` to probe `ExecutionStore` and report journal status, path, schema version, and unresolved count, verifying with unit tests (`pytest tests/test_health_journal.py`)

## 5. Tool Layer and MCP Schema Updates

- [ ] 5.1 Update `place_order`, `modify_order`, and `cancel_order` tool definitions in `tools/trading.py` to accept mandatory `operation_id` in `SIMULATE` mode and verify with schema tests (`pytest tests/test_trading_tools_schema.py`)
- [ ] 5.2 Validate tool parameter validation, error responses, and duplicate suppression at the MCP tool boundary and verify with FastMCP tool tests (`pytest tests/test_trading_tools_execution.py`)

## 6. Automated Testing and Fault Injection Suite

- [ ] 6.1 Create stateful fake broker test harness simulating network timeouts, dropped responses, and racing fills and verify with harness test suite (`pytest tests/test_broker_harness.py`)
- [ ] 6.2 Implement automated fault injection tests for crashes at all 4 submission/persistence boundaries and verify safe recovery state transitions (`pytest tests/test_fault_injection.py`)
- [ ] 6.3 Implement container recreation and volume persistence tests verifying `execution-data` is retained while `opend-data` remains unaffected (`./scripts/test-volume-persistence.sh`)
- [ ] 6.4 Implement corrupt database, missing database, and schema version mismatch tests verifying fail-closed startup (`pytest tests/test_db_integrity.py`)

## 7. Operational Documentation and Runbooks

- [ ] 7.1 Update `docs/state-and-restarts.md` with execution journal lifecycle, WAL behavior, and restart cost analysis, verified by documentation review
- [ ] 7.2 Update `docs/deploy-vps.md` with volume setup, backup procedures (`.backup`), and operator reconciliation runbook, verified by documentation review

## 8. Staged Manual Verification (Requires Operator Authorization)

- [ ] 8.1 Execute authorized end-to-end paper trading smoke test on real Moomoo simulated account to verify broker limit order placement, modification, cancellation, and receipt capture
- [ ] 8.2 Verify operator-driven reconciliation against real Moomoo simulated account orders and deals after intentional disconnect
