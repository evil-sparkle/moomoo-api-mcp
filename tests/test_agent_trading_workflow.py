"""Agent workflows through authenticated MCP and isolated, actual trade journals.

The SDK constructor is forbidden. Every account, order and receipt comes from
an in-memory broker double; journal and approval files live under tmp_path.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import RET_OK, OpenSecTradeContext
from starlette.testclient import TestClient

import moomoo_mcp.server as server
import moomoo_mcp.services.clock as observation_clock
from examples.trading_client import (
    AttemptAlreadyStarted,
    load_intent,
    persist_authorized_intent,
    submit_once,
)
from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.execution_store import ExecutionStore, ExecutionStoreError
from moomoo_mcp.services.trade_preparation import bound_parameters
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_controls import TradingControls
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    TradingMode,
    TradingPolicy,
)
from tests.mcp_helpers import TOKEN, call
from tests.paper_helpers import Broker

OPERATOR_TOKEN = "isolated-workflow-operator"


class WorkflowBroker(Broker):
    """The paper journal double, with the actual service's read interfaces."""

    def __init__(self, path, environment):
        super().__init__(path)
        self.environment = environment
        self.accounts = [
            {"acc_id": 123, "trd_env": environment, "trdmarket_auth": ["US"]}
        ]
        self.reads = []

    def get_acc_list(self):
        self.reads.append("accounts")
        return RET_OK, pd.DataFrame(self.accounts)

    def acctradinginfo_query(self, **_parameters):
        self.reads.append("maximum")
        return RET_OK, pd.DataFrame([{"max_cash_buy": 100}])

    def order_list_query(self, **parameters):
        self.reads.append("orders")
        return super().order_list_query(**parameters)

    def history_order_list_query(self, **parameters):
        self.reads.append("history")
        return super().history_order_list_query(**parameters)

    def position_list_query(self, **parameters):
        self.reads.append("positions")
        return RET_OK, pd.DataFrame(
            [
                {
                    "code": parameters["code"],
                    "qty": 2,
                    "acc_id": 123,
                    "trd_env": self.environment,
                }
            ]
        )

    def close(self):
        pass


@pytest.fixture(params=["SIMULATE", "REAL"])
def workflow(request, tmp_path, monkeypatch, mcp_app_context):
    environment = request.param
    path = tmp_path / environment / "execution.db"
    store = ExecutionStore(path, create=True, environment=environment)
    broker = WorkflowBroker(path, environment)
    controls = TradingControls(tmp_path / "controls.db", create=True)
    metadata = {"multiplier": 1}
    constructor = MagicMock(side_effect=AssertionError("SDK construction forbidden"))
    monkeypatch.setattr(
        "moomoo_mcp.services.trade_service.OpenSecTradeContext", constructor
    )
    policy = TradingPolicy(TradingMode(environment), real_acc_ids=frozenset({123, 456}))
    service = TradeService(
        policy=policy,
        execution_store=store if environment == "SIMULATE" else None,
        real_execution_store=store if environment == "REAL" else None,
        simulated_account_allowlist=frozenset({123, 456}),
        trading_controls=controls,
        dispatcher=BrokerRequestDispatcher(clock=broker.clock),
        instrument_lookup=lambda codes: [
            InstrumentFacts(
                code=code,
                classification="STOCK",
                monetary_multiplier=metadata["multiplier"],
                last_price=10,
                lot_size=1,
            )
            for code in codes
        ],
    )
    service.trade_ctx = cast(OpenSecTradeContext, broker)
    mcp_app_context.trade_service = service
    monkeypatch.setattr(server, "get_services", lambda: mcp_app_context)
    security = server.mcp.settings.transport_security
    server.mcp._session_manager = None
    app = server.create_streamable_http_app(
        TOKEN,
        operator_auth_token=OPERATOR_TOKEN,
        operator_id="fixture-operator",
    )
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                service=service,
                store=store,
                broker=broker,
                controls=controls,
                metadata=metadata,
                environment=environment,
                path=path,
                parameters={
                    "code": "US.AAPL",
                    "qty": 2,
                    "price": "10.00" if environment == "SIMULATE" else 10.0,
                    "trd_side": "BUY",
                    "trd_env": environment,
                    "acc_id": "123",
                    "operation_id": "original-operation",
                    "admission_epoch": store.epoch,
                    "session": "RTH",
                },
            )
    finally:
        service.close()
        server.mcp._session_manager = None
        server.mcp.settings.transport_security = security
        constructor.assert_not_called()


def decoded(result, *, error=False):
    assert result.get("isError", False) is error, result
    data = json.loads(result["content"][0]["text"])
    if error or "structuredContent" in result:
        assert result["structuredContent"] == data
    return data


def submit(workflow, **changes):
    return call(workflow.client, "place_order", workflow.parameters | changes)


def execution(workflow):
    return decoded(
        call(
            workflow.client,
            "get_execution",
            {
                "operation_id": workflow.parameters["operation_id"],
                "trd_env": workflow.environment,
            },
        )
    )


def reopen_journal(workflow):
    executor = (
        workflow.service.paper
        if workflow.environment == "SIMULATE"
        else workflow.service.real
    )
    assert executor is not None
    executor.recovery.stop()
    workflow.store.close()
    workflow.store = ExecutionStore(workflow.path, environment=workflow.environment)
    executor.store = workflow.store
    workflow.store.review()


def prepare_and_approve(workflow, parameters):
    prepared = decoded(
        call(
            workflow.client,
            "prepare_trade_request",
            {"method": "place_order", "parameters": parameters},
        )
    )
    decision = workflow.client.post(
        "/operator/approvals/" + prepared["request_id"],
        headers={"Authorization": "Bearer " + OPERATOR_TOKEN},
        json={"decision": "APPROVED", "reason": "Reviewed this exact fixture order"},
    )
    assert decision.status_code == 200
    return prepared


@pytest.mark.parametrize("field", ["operation_id", "admission_epoch"])
def test_oversized_token_is_unadmitted_and_does_not_disclose_value(workflow, field):
    invalid = "synthetic-sensitive-marker-" + "x" * 38 + "z"
    assert len(invalid) == 66
    result = decoded(submit(workflow, **{field: invalid}), error=True)
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["validation_phase"] == "INPUT_VALIDATION"
    assert result["journal_record_created"] is False
    assert result["journal_record_scope"] == "THIS_INVOCATION"
    assert result["identity_status"] == "UNKNOWN"
    assert result["prior_attempt_outcome"] == "UNKNOWN"
    assert result["mutation_outcome"] == "NOT_SENT"
    assert result["retryable"] is False
    issue = next(item for item in result["validation_issues"] if item["field"] == field)
    assert issue["constraint"] == "max_length"
    assert issue["min_length"] == 1
    assert issue["max_length"] == 64
    assert issue["actual_length"] == 66
    assert invalid not in json.dumps(result)
    assert workflow.store.lookup(workflow.parameters["operation_id"]) is None
    assert workflow.store.lookup(invalid) is None
    assert workflow.broker.calls == []
    assert workflow.broker.reads == []


async def test_authorized_unadmitted_correction_uses_recipe_and_submits_once(
    workflow, tmp_path
):
    invalid = "x" * 66
    rejected = decoded(submit(workflow, operation_id=invalid), error=True)
    assert rejected["validation_phase"] == "INPUT_VALIDATION"
    assert rejected["journal_record_created"] is False
    assert rejected["journal_record_scope"] == "THIS_INVOCATION"
    # This fixture has no previous invocation: verify its isolated journal and
    # broker too. The error's invocation-scoped evidence alone is insufficient.
    assert workflow.store.lookup(invalid) is None
    assert workflow.broker.calls == []
    parameters = dict(workflow.parameters)
    parameters.pop("operation_id")
    path = tmp_path / "authorized-correction.json"
    intent = persist_authorized_intent(path, "place_order", parameters)
    transport_calls = []

    async def transport(method, arguments):
        transport_calls.append((method, dict(arguments)))
        return call(workflow.client, method, arguments)

    submitted = decoded(await submit_once(path, transport))
    assert submitted["state"] == "ACKNOWLEDGED"
    assert submitted["operation_id"] == intent["parameters"]["operation_id"]
    assert submitted["admission_epoch"] == parameters["admission_epoch"]
    assert submitted["acc_id"] == parameters["acc_id"]
    assert submitted["trd_env"] == parameters["trd_env"]
    assert len(submitted["operation_id"]) == 32
    assert load_intent(path) == intent
    with pytest.raises(AttemptAlreadyStarted):
        await submit_once(path, transport)
    assert len(transport_calls) == 1
    assert len(workflow.broker.calls) == 1


@pytest.mark.parametrize("outcome", ["input_error", "unknown", "transport_loss"])
async def test_single_invocation_recipe_never_retries_actual_mcp_result(
    workflow, tmp_path, outcome
):
    parameters = dict(workflow.parameters)
    parameters.pop("operation_id")
    if outcome == "input_error":
        parameters["qty"] = 0
    elif outcome == "unknown":
        workflow.broker.failure = "lost_response"
    path = tmp_path / "single-invocation.json"
    intent = persist_authorized_intent(path, "place_order", parameters)
    transport_calls = []

    async def transport(method, arguments):
        transport_calls.append((method, dict(arguments)))
        response = call(workflow.client, method, arguments)
        if outcome == "transport_loss":
            raise TimeoutError("Fixture dropped the MCP response after dispatch")
        return response

    if outcome == "transport_loss":
        with pytest.raises(TimeoutError):
            await submit_once(path, transport)
    else:
        response = decoded(
            await submit_once(path, transport), error=outcome == "input_error"
        )
        if outcome == "unknown":
            assert response["state"] == "UNKNOWN_OUTCOME"
        else:
            assert response["mutation_outcome"] == "NOT_SENT"
    with pytest.raises(AttemptAlreadyStarted):
        await submit_once(path, transport)
    assert load_intent(path) == intent
    assert len(transport_calls) == 1
    assert len(workflow.broker.calls) == (0 if outcome == "input_error" else 1)


async def test_recipe_submits_exact_operator_approved_request_once(workflow, tmp_path):
    workflow.service.policy = replace(workflow.service.policy, require_approval=True)
    # Model a complete authorized request, including every served default.
    parameters = bound_parameters(
        workflow.service, "place_order", workflow.parameters | {"session": "N/A"}
    )
    parameters.pop("operation_id")
    path = tmp_path / "approved-intent.json"
    intent = persist_authorized_intent(path, "place_order", parameters)
    prepared = prepare_and_approve(workflow, intent["parameters"])
    approved = decoded(
        call(
            workflow.client,
            "get_prepared_trade",
            {"request_id": prepared["request_id"]},
        )
    )
    transport_calls = []

    async def transport(method, arguments):
        transport_calls.append((method, dict(arguments)))
        return call(workflow.client, method, arguments)

    submitted = decoded(await submit_once(path, transport, prepared_trade=approved))
    assert submitted["state"] == "ACKNOWLEDGED"
    assert submitted["operation_id"] == intent["parameters"]["operation_id"]
    assert transport_calls == [
        (
            "place_order",
            approved["parameters"] | {"approval_id": prepared["request_id"]},
        )
    ]
    assert workflow.controls.get(prepared["request_id"])["status"] == "CONSUMED"
    assert len(workflow.broker.calls) == 1


@pytest.mark.parametrize("problem", ["timeout", "lost_response"])
def test_uncertain_result_retains_identity_and_repeat_never_dispatches(
    workflow, problem
):
    workflow.broker.failure = problem
    first = decoded(submit(workflow))
    assert first["state"] == "UNKNOWN_OUTCOME"
    assert first["disposition"] == "POSSIBLY_SENT"
    assert first["recovery"]["original_operation_replay_allowed"] is False
    assert first["operation_id"] == workflow.parameters["operation_id"]
    assert first["admission_epoch"] == workflow.parameters["admission_epoch"]
    assert first["trd_env"] == workflow.environment
    assert first["acc_id"] == "123"
    assert execution(workflow) == first
    assert decoded(submit(workflow)) == first
    conflict = decoded(submit(workflow, qty=3), error=True)
    assert conflict["retryable"] is False
    assert workflow.store.lookup(workflow.parameters["operation_id"])["marker"] == 1
    assert len(workflow.broker.calls) == 1


def test_failed_journal_lookup_does_not_prove_original_absent(workflow, monkeypatch):
    workflow.broker.failure = "lost_response"
    first = decoded(submit(workflow))

    def unavailable(_operation_id):
        raise ExecutionStoreError("isolated lookup unavailable")

    with monkeypatch.context() as local:
        local.setattr(workflow.store, "lookup", unavailable)
        read = decoded(
            call(
                workflow.client,
                "get_execution",
                {
                    "operation_id": first["operation_id"],
                    "trd_env": workflow.environment,
                },
            ),
            error=True,
        )
        repeated = decoded(submit(workflow), error=True)
    for result in (read, repeated):
        assert result["retryable"] is False
        # A repeat may be unsent in this invocation while its original dispatch
        # remains unknown. Neither failure establishes global journal absence.
        assert result["journal_record_created"] is None
        assert result["identity_status"] == "UNKNOWN"
        assert result["prior_attempt_outcome"] == "UNKNOWN"
        assert "new operation" in result["next_action"].lower() or (
            "original" in result["next_action"].lower()
        )
    assert decoded(submit(workflow)) == first
    assert len(workflow.broker.calls) == 1


def test_bad_input_after_uncertainty_does_not_erase_prior_dispatch(workflow):
    workflow.broker.failure = "lost_response"
    first = decoded(submit(workflow))
    rejected = decoded(submit(workflow, operation_id="x" * 66), error=True)
    assert rejected["journal_record_created"] is False
    assert rejected["journal_record_scope"] == "THIS_INVOCATION"
    assert rejected["prior_attempt_outcome"] == "UNKNOWN"
    assert rejected["identity_status"] == "UNKNOWN"
    assert rejected["retryable"] is False
    assert execution(workflow) == first
    assert decoded(submit(workflow)) == first
    assert len(workflow.broker.calls) == 1


def test_reopened_journal_keeps_original_epoch_and_unknown_operation(workflow):
    workflow.broker.failure = "lost_response"
    first = decoded(submit(workflow))
    original_epoch = workflow.parameters["admission_epoch"]
    reopen_journal(workflow)
    assert workflow.store.epoch != original_epoch
    for result in (execution(workflow), decoded(submit(workflow))):
        assert result["state"] == "UNKNOWN_OUTCOME"
        assert result["operation_id"] == first["operation_id"]
        assert result["admission_epoch"] == original_epoch
        assert result["order_tag"] == first["order_tag"]
        assert result["recovery"]["original_operation_replay_allowed"] is False
    conflict = decoded(submit(workflow, qty=3), error=True)
    assert conflict["retryable"] is False
    stale = decoded(submit(workflow, operation_id="unadmitted-operation"), error=True)
    assert stale["retryable"] is False
    assert workflow.store.lookup("unadmitted-operation") is None
    assert len(workflow.broker.calls) == 1


def test_stale_unknown_epoch_does_not_admit_or_refresh(workflow):
    stale = "previous-process-epoch"
    result = decoded(submit(workflow, admission_epoch=stale), error=True)
    assert result["retryable"] is False
    assert result["journal_record_created"] is None
    assert workflow.store.lookup(workflow.parameters["operation_id"]) is None
    assert workflow.parameters["admission_epoch"] == workflow.store.epoch
    assert workflow.broker.calls == []
    assert workflow.broker.reads == []


def test_admitted_refusal_is_immutable_even_after_metadata_repair(workflow):
    workflow.service.instrument_lookup = lambda codes: [InstrumentFacts(code=codes[0])]
    first = decoded(submit(workflow), error=True)
    assert first["mutation_outcome"] == "NOT_SENT"
    assert first["validation_phase"] == "ADMITTED"
    assert first["journal_record_created"] is True
    refused = execution(workflow)
    assert refused["state"] == "REFUSED"
    assert refused["disposition"] == "NOT_SENT"
    assert workflow.store.lookup(workflow.parameters["operation_id"])["marker"] == 0
    workflow.service.instrument_lookup = lambda codes: [
        InstrumentFacts(code=codes[0], classification="STOCK", lot_size=1)
    ]
    assert decoded(submit(workflow)) == refused
    changed = decoded(submit(workflow, qty=3), error=True)
    assert changed["retryable"] is False
    assert execution(workflow) == refused
    assert workflow.broker.calls == []


def test_unreadable_receipt_keeps_acknowledgement_and_original_identity(
    workflow, monkeypatch
):
    def unreadable(_kind, _data):
        raise ValueError("isolated receipt conversion failure")

    monkeypatch.setattr(workflow.service, "_first_record", unreadable)
    first = decoded(submit(workflow))
    assert first["state"] == "ACKNOWLEDGED"
    assert first["receipt"]["order_id"] == "9007199254740993"
    assert "conversion" in first["error"]
    assert first["operation_id"] == workflow.parameters["operation_id"]
    assert first["admission_epoch"] == workflow.parameters["admission_epoch"]
    assert first["recovery"]["original_operation_replay_allowed"] is False
    assert decoded(submit(workflow)) == first
    assert len(workflow.broker.calls) == 1


def test_post_dispatch_mcp_serialization_failure_recovers_original_execution(
    workflow, monkeypatch
):
    import moomoo_mcp.tools.trading as trading_tools

    def broken_output(_result):
        raise ValueError("isolated output conversion failure")

    with monkeypatch.context() as local:
        local.setattr(trading_tools, "serialize_identifiers", broken_output)
        failed = decoded(submit(workflow), error=True)
    assert failed["retryable"] is False
    assert failed["mutation_outcome"] == "UNKNOWN"
    assert "Do not resend" in failed["next_action"]
    original = execution(workflow)
    assert original["state"] == "ACKNOWLEDGED"
    assert original["operation_id"] == workflow.parameters["operation_id"]
    assert original["admission_epoch"] == workflow.parameters["admission_epoch"]
    assert decoded(submit(workflow)) == original
    assert len(workflow.broker.calls) == 1


def test_same_identity_during_dispatch_returns_pending_without_second_write(workflow):
    entered, release = threading.Event(), threading.Event()
    workflow.broker.entered, workflow.broker.release = entered, release
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(submit, workflow)
        try:
            assert entered.wait(5)
            repeated = decoded(submit(workflow))
            assert repeated["status"] == "IN_FLIGHT"
            assert repeated["state"] == "DISPATCHING"
            assert repeated["operation_id"] == workflow.parameters["operation_id"]
            assert repeated["receipt"] == {}
            assert len(workflow.broker.calls) == 1
        finally:
            release.set()
        submitted = decoded(pending.result(timeout=5))
    assert submitted["state"] == "ACKNOWLEDGED"
    assert submitted["receipt"]["order_status"] == "SUBMITTED"
    assert submitted["receipt"]["dealt_qty"] == 0
    assert len(workflow.broker.calls) == 1


def test_broker_fill_is_distinct_from_original_submission_acknowledgement(workflow):
    submitted = decoded(submit(workflow))
    identity = submitted["receipt"]["order_id"]
    assert submitted["receipt"]["order_status"] == "SUBMITTED"
    assert submitted["receipt"]["dealt_qty"] == 0
    workflow.broker.orders[identity].update(order_status="FILLED_ALL", dealt_qty=2)
    observed = decoded(
        call(
            workflow.client,
            "get_order",
            {"order_id": identity, "acc_id": "123", "trd_env": workflow.environment},
        )
    )
    assert observed["found"] is True
    assert observed["order"]["order_status"] == "FILLED_ALL"
    assert observed["order"]["dealt_qty"] == 2
    assert execution(workflow) == submitted
    assert len(workflow.broker.calls) == 1


@pytest.mark.parametrize("selection", ["unallowlisted", "ambiguous", "wrong_env"])
def test_account_or_environment_refusal_cannot_fall_back(workflow, selection):
    changes = {}
    if selection == "unallowlisted":
        changes["acc_id"] = "999"
    elif selection == "ambiguous":
        workflow.broker.accounts.append(
            {"acc_id": 456, "trd_env": workflow.environment, "trdmarket_auth": ["US"]}
        )
        changes["acc_id"] = "0"
    else:
        changes["trd_env"] = "SIMULATE" if workflow.environment == "REAL" else "REAL"
    refused = decoded(submit(workflow, **changes), error=True)
    assert refused["retryable"] is False
    assert workflow.store.lookup(workflow.parameters["operation_id"]) is None
    assert workflow.broker.calls == []


@pytest.mark.parametrize("workflow", ["SIMULATE"], indirect=True)
@pytest.mark.parametrize("session", ["ETH", "OVERNIGHT", "ALL"])
def test_paper_wider_session_refuses_through_mcp_without_admission(workflow, session):
    result = decoded(submit(workflow, session=session), error=True)
    assert result["retryable"] is False
    assert workflow.store.lookup(workflow.parameters["operation_id"]) is None
    assert workflow.broker.calls == []


def test_outside_rth_clock_does_not_block_mock_rth_submission(workflow, monkeypatch):
    class ClosedMarketTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 18, 6, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(observation_clock, "datetime", ClosedMarketTime)
    assert observation_clock.utc_now_iso() == "2026-10-18T06:00:00Z"
    submitted = decoded(submit(workflow))
    assert submitted["state"] == "ACKNOWLEDGED"
    assert submitted["receipt"]["order_status"] == "SUBMITTED"
    assert submitted["receipt"]["dealt_qty"] == 0
    assert workflow.broker.calls[0][1]["session"] == "RTH"
    assert len(workflow.broker.calls) == 1


@pytest.mark.parametrize("change", ["operation_id", "admission_epoch", "contract"])
def test_exact_operator_approval_cannot_transfer_to_changed_request(workflow, change):
    workflow.service.policy = replace(workflow.service.policy, require_approval=True)
    prepared = prepare_and_approve(workflow, workflow.parameters)
    changes = {"approval_id": prepared["request_id"]}
    if change == "contract":
        workflow.metadata["multiplier"] = 2
    elif change == "admission_epoch":
        reopen_journal(workflow)
        changes[change] = workflow.store.epoch
    else:
        changes[change] = "changed-" + workflow.parameters[change]
    refused = decoded(submit(workflow, **changes), error=True)
    assert refused["retryable"] is False
    assert refused["validation_phase"] == "ADMITTED"
    assert refused["journal_record_created"] is True
    review = decoded(
        call(
            workflow.client,
            "get_prepared_trade",
            {"request_id": prepared["request_id"]},
        )
    )
    assert review["status"] == "APPROVED"
    assert review["parameters"] == prepared["parameters"]
    assert review["fingerprint"] == prepared["fingerprint"]
    assert workflow.broker.calls == []
