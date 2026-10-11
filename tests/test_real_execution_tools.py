"""MCP selects independent journals and carries REAL caller identity unchanged."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from moomoo_mcp.services.base_service import HealthCheck


def execution_fixture(environment):
    row = {"operation_id": "shared-token", "state": "ACKNOWLEDGED"}
    result = {**row, "trd_env": environment, "admission_epoch": environment + "-epoch"}
    return SimpleNamespace(
        store=SimpleNamespace(lookup=MagicMock(return_value=row)),
        result=MagicMock(return_value=result),
        reconcile=MagicMock(return_value=result),
    )


@pytest.fixture
def distinct_journals(mock_trade_service):
    mock_trade_service.paper = execution_fixture("SIMULATE")
    mock_trade_service.real = execution_fixture("REAL")
    return mock_trade_service.paper, mock_trade_service.real


@pytest.mark.parametrize("tool", ["get_execution", "reconcile_execution"])
@pytest.mark.parametrize("environment", [None, "SIMULATE", "REAL", " real "])
@pytest.mark.asyncio
async def test_execution_tools_select_environment_without_crossing_journals(
    call_tool, distinct_journals, tool, environment
):
    paper, real = distinct_journals
    arguments = {"operation_id": "shared-token"}
    if environment is not None:
        arguments["trd_env"] = environment
    result = await call_tool(tool, arguments)
    expected = (
        "REAL" if environment and environment.strip().upper() == "REAL" else "SIMULATE"
    )
    assert result.json["trd_env"] == expected
    selected, untouched = (real, paper) if expected == "REAL" else (paper, real)
    if tool == "get_execution":
        selected.store.lookup.assert_called_once_with("shared-token")
    else:
        selected.reconcile.assert_called_once_with("shared-token")
    untouched.store.lookup.assert_not_called()
    untouched.reconcile.assert_not_called()


@pytest.mark.parametrize("tool", ["get_execution", "reconcile_execution"])
@pytest.mark.asyncio
async def test_execution_tools_refuse_invalid_environment_before_journal_lookup(
    call_tool, distinct_journals, tool
):
    with pytest.raises(ToolError) as refusal:
        await call_tool(tool, {"operation_id": "shared-token", "trd_env": "UNKNOWN"})
    assert "trd_env must be SIMULATE or REAL" in str(refusal.value)
    for journal in distinct_journals:
        journal.store.lookup.assert_not_called()
        journal.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_real_combo_tokens_cross_actual_mcp_schema(call_tool, mock_trade_service):
    mock_trade_service.place_combo_order.return_value = {"state": "ACKNOWLEDGED"}
    await call_tool(
        "place_combo_order",
        {
            "combo_legs": [
                {"code": "US.XYZ261216C100000", "trd_side": "SELL", "qty_ratio": 1},
                {"code": "US.XYZ261216C105000", "trd_side": "BUY", "qty_ratio": 1},
            ],
            "price": -2.5,
            "qty": 1,
            "trd_env": "REAL",
            "operation_id": "caller-combo",
            "admission_epoch": "original-real-epoch",
        },
    )
    kwargs = mock_trade_service.place_combo_order.call_args.kwargs
    assert kwargs["operation_id"] == "caller-combo"
    assert kwargs["admission_epoch"] == "original-real-epoch"
    assert kwargs["price"] == -2.5


@pytest.mark.asyncio
async def test_health_reports_real_gate_independently_from_connectivity_and_paper(
    call_tool, mock_moomoo_service, mock_trade_service
):
    check = MagicMock(spec=HealthCheck)
    check.futures = []
    check.remaining.return_value = 5.0
    check.result.return_value = {"status": "connected"}
    mock_moomoo_service.start_health_check.return_value = check
    paper = {"state": "READY", "admission_epoch": "paper-epoch"}
    real = {
        "state": "JOURNAL_BLOCKED",
        "admission_epoch": "real-epoch",
        "pending_operation_ids": ["uncertain-real"],
    }
    for prefix, payload in (("", paper), ("real_", real)):
        future = Future()
        future.set_result(payload)
        getattr(
            mock_trade_service, "start_" + prefix + "journal_health"
        ).return_value = future
        getattr(
            mock_trade_service, "collect_" + prefix + "journal_health"
        ).return_value = payload
    result = await call_tool("check_health")
    assert result.structured["status"] == "connected"
    assert result.structured["execution_journal"] == paper
    assert result.structured["real_execution_journal"] == real
