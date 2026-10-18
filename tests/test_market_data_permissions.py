"""Quote denials remain actionable errors through real MCP HTTP dispatch."""

import json

import pandas as pd
import pytest

from moomoo_mcp.services.quote_errors import (
    QuotePermissionDeniedError,
    raise_quote_error,
)
from tests.mcp_helpers import call, rpc

DENIAL = (
    "No permission to get quotes for SG.XND. "
    "Please check SG Security MarketETFs quote permissions."
)


def diagnostic(result):
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert result["structuredContent"] == payload
    assert payload["error_code"] == "QUOTE_PERMISSION_DENIED"
    assert payload["retryable"] is False
    assert "last_price" not in payload
    return payload


def test_mixed_snapshot_denial_is_request_scoped_and_does_not_poison_other_reads(
    gateway_client,
):
    client, quote_ctx, services = gateway_client
    rows = [{"code": "US.AAPL", "last_price": 150.0}]
    quote_ctx.get_market_snapshot.side_effect = [(-1, DENIAL), (0, pd.DataFrame(rows))]
    payload = diagnostic(
        call(client, "get_market_snapshot", {"codes": ["SG.XND", "US.AAPL"]})
    )
    assert payload["requested_codes"] == ["SG.XND", "US.AAPL"]
    assert payload["denied_codes"] == ["SG.XND"]
    assert payload["provider_message"] == DENIAL
    assert payload["operation"] == "get_market_snapshot"
    assert payload["checked_at"].endswith("Z")
    assert payload["next_action"] == (
        "Use another available market-data tool or web source."
    )
    assert "currency" in " ".join(payload["agent_guidance"])
    assert "timestamp/timezone" in " ".join(payload["agent_guidance"])
    assert "unavailable" in " ".join(payload["agent_guidance"])
    quote_ctx.get_market_snapshot.assert_called_once_with(["SG.XND", "US.AAPL"])

    success = call(client, "get_market_snapshot", {"codes": ["US.AAPL"]})
    assert success["isError"] is False
    assert success["structuredContent"] == {"result": rows}
    assert json.loads(success["content"][0]["text"]) == rows[0]

    services.trade_service.get_assets.return_value = {"cash": 1000.0}
    assets = call(client, "get_assets", {})
    assert assets["isError"] is False
    assert assets["structuredContent"] == {"cash": 1000.0}


def test_existing_json_string_argument_parsing_is_preserved(gateway_client):
    client, quote_ctx, _ = gateway_client
    rows = [{"code": "US.AAPL", "last_price": 150.0}]
    quote_ctx.get_market_snapshot.return_value = (0, pd.DataFrame(rows))
    result = call(client, "get_market_snapshot", {"codes": '["US.AAPL"]'})
    assert result["isError"] is False
    assert result["structuredContent"] == {"result": rows}
    quote_ctx.get_market_snapshot.assert_called_once_with(["US.AAPL"])


@pytest.mark.parametrize("name", ["get_stock_quote", "get_order_book"])
def test_automatic_subscription_denial_does_not_fetch_or_retry(gateway_client, name):
    client, quote_ctx, _ = gateway_client
    quote_ctx.subscribe.return_value = (-1, DENIAL)
    arguments = (
        {"codes": ["SG.XND"]} if name == "get_stock_quote" else {"code": "SG.XND"}
    )
    payload = diagnostic(call(client, name, arguments))
    assert payload["operation"] == "subscribe"
    assert payload["denied_codes"] == ["SG.XND"]
    assert quote_ctx.subscribe.call_count == 1
    quote_ctx.get_stock_quote.assert_not_called()
    quote_ctx.get_order_book.assert_not_called()


@pytest.mark.parametrize(
    ("name", "method", "arguments", "response"),
    [
        ("get_stock_quote", "get_stock_quote", {"codes": ["SG.XND"]}, (-1, DENIAL)),
        ("get_order_book", "get_order_book", {"code": "SG.XND"}, (-1, DENIAL)),
        (
            "get_historical_klines",
            "request_history_kline",
            {"code": "SG.XND"},
            (-1, DENIAL, None),
        ),
        (
            "get_historical_klines_page",
            "request_history_kline",
            {"code": "SG.XND"},
            (-1, DENIAL, None),
        ),
        (
            "get_option_expiration_date",
            "get_option_expiration_date",
            {"code": "SG.XND"},
            (-1, DENIAL),
        ),
        ("get_option_chain", "get_option_chain", {"code": "SG.XND"}, (-1, DENIAL)),
    ],
)
def test_quote_operations_share_the_permission_contract(
    gateway_client, name, method, arguments, response
):
    client, quote_ctx, _ = gateway_client
    query = getattr(quote_ctx, method)
    query.return_value = response
    payload = diagnostic(call(client, name, arguments))
    assert payload["operation"] == method
    assert payload["requested_codes"] == ["SG.XND"]
    assert query.call_count == 1


@pytest.mark.parametrize(
    "message",
    [
        "Subscription quota exceeded. Check quote permissions.",
        "Too many requests. Please retry later.",
        "Unknown stock SG.XND",
        "Connection refused",
    ],
)
def test_other_provider_errors_are_not_entitlement_denials(gateway_client, message):
    client, quote_ctx, _ = gateway_client
    quote_ctx.get_market_snapshot.return_value = (-1, message)
    result = call(client, "get_market_snapshot", {"codes": ["SG.XND"]})
    assert result["isError"] is True
    assert "QUOTE_PERMISSION_DENIED" not in json.dumps(result)
    assert message in result["content"][0]["text"]
    assert quote_ctx.get_market_snapshot.call_count == 1


def test_missing_context_and_invalid_inputs_keep_their_own_errors(gateway_client):
    client, quote_ctx, services = gateway_client
    invalid = call(client, "get_market_snapshot", {"codes": "SG.XND"})
    assert invalid["isError"] is True
    assert "QUOTE_PERMISSION_DENIED" not in json.dumps(invalid)
    quote_ctx.get_market_snapshot.assert_not_called()
    services.market_data_service.quote_ctx = None
    disconnected = call(client, "get_market_snapshot", {"codes": ["SG.XND"]})
    assert disconnected["isError"] is True
    assert disconnected["structuredContent"]["error_code"] == "CONNECTION_UNAVAILABLE"
    assert "QUOTE_PERMISSION_DENIED" not in json.dumps(disconnected)


def test_initialize_and_discovery_expose_fallback_guidance(gateway_client):
    client, _, _ = gateway_client
    result = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-agent", "version": "1"},
        },
    )
    assert "QUOTE_PERMISSION_DENIED" in result["instructions"]
    assert "another available market-data tool or web source" in result["instructions"]
    listing = rpc(client, "tools/list", {})
    tools = {tool["name"]: tool for tool in listing["tools"]}
    for name in ("get_stock_quote", "get_market_snapshot", "get_order_book"):
        assert "QUOTE_PERMISSION_DENIED" in tools[name]["description"]
    # Error payloads must bypass, rather than broaden, the list success schema.
    schema = tools["get_market_snapshot"]["outputSchema"]
    assert schema["properties"]["result"]["type"] == "array"


def test_generic_quote_denial_does_not_invent_a_denied_symbol():
    with pytest.raises(QuotePermissionDeniedError) as caught:
        raise_quote_error("get_market_snapshot", "No quote permission", ["SG.XND"])
    assert caught.value.denied_codes == []


def test_non_string_provider_payload_is_not_classified_by_its_contents():
    with pytest.raises(RuntimeError) as caught:
        raise_quote_error("get_market_snapshot", {"detail": DENIAL}, ["SG.XND"])
    assert not isinstance(caught.value, QuotePermissionDeniedError)
