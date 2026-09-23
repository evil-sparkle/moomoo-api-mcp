"""Unit tests for TradeService."""

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK, OpenSecTradeContext

from moomoo_mcp.services.instruments import InstrumentLookupError
from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderOutcomeUnknownError,
    OrderReceiptUnreadableError,
)
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_market import TRADING_MARKET_FILTERS
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
)

# These tests exercise order writes, so they state that intent explicitly:
# a service constructed without a policy is read-only and refuses them.
# REAL mode also needs its account allowlist; 123 is the account these tests use.
REAL_POLICY = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123, 456}))
ACCOUNT_READ_ENDPOINTS = [
    ("get_assets", {"trd_env": "simulate", "acc_id": "0"}, "accinfo_query"),
    (
        "get_positions",
        {"trd_env": "simulate", "acc_id": "0"},
        "position_list_query",
    ),
    (
        "get_max_tradable",
        {
            "order_type": "NORMAL",
            "code": "US.AAPL",
            "price": 1.0,
            "trd_env": "simulate",
            "acc_id": "0",
        },
        "acctradinginfo_query",
    ),
    ("get_cash_flow", {"trd_env": "simulate", "acc_id": "0"}, "get_acc_cash_flow"),
    ("get_orders", {"trd_env": "simulate", "acc_id": "0"}, "order_list_query"),
    ("get_deals", {"trd_env": "simulate", "acc_id": "0"}, "deal_list_query"),
    (
        "get_history_orders",
        {"trd_env": "simulate", "acc_id": "0"},
        "history_order_list_query",
    ),
    (
        "get_history_deals",
        {"trd_env": "simulate", "acc_id": "0"},
        "history_deal_list_query",
    ),
]


@pytest.fixture
def mock_trade_ctx():
    """Create a mock OpenSecTradeContext."""
    ctx = MagicMock()
    ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "SIMULATE"},
                {"acc_id": 456, "trd_env": "REAL"},
            ]
        ),
    )
    # A NORMAL modification is assessed as the order that would result, so it
    # reads the existing order before dispatching anything.
    ctx.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "code": "US.AAPL",
                    "trd_side": "BUY",
                    "order_type": "NORMAL",
                    "qty": 100,
                    "price": 150.0,
                    "aux_price": 0.0,
                }
            ]
        ),
    )
    return ctx


@pytest.fixture
def trade_service_with_mock(mock_trade_ctx):
    """Create a write-enabled TradeService with a mocked context."""
    service = TradeService(policy=REAL_POLICY)
    service.trade_ctx = mock_trade_ctx
    return service


class TestTradeServiceConnection:
    """Tests for connection lifecycle."""

    @patch("moomoo_mcp.services.trade_service.OpenSecTradeContext")
    def test_connect_creates_context(self, mock_ctx_class):
        """Test connect() initializes OpenSecTradeContext."""
        service = TradeService(host="localhost", port=12345)
        service.connect()

        mock_ctx_class.assert_called_once_with(
            host="localhost",
            port=12345,
            filter_trdmarket=TRADING_MARKET_FILTERS["NONE"],
        )
        assert service.trade_ctx is not None

    @pytest.mark.parametrize("market", ["NONE", "US", "HK"])
    def test_market_filter_reaches_sdk_context(self, market):
        ctx = MagicMock()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            return_value=ctx,
        ) as context_class:
            service = TradeService(trading_market=market)
            service.connect()

        context_class.assert_called_once_with(
            host="127.0.0.1",
            port=11111,
            filter_trdmarket=TRADING_MARKET_FILTERS[market],
        )
        assert service.trading_market == market

    def test_close_clears_context(self, trade_service_with_mock, mock_trade_ctx):
        """Test close() closes and clears trade context."""
        trade_service_with_mock.close()

        mock_trade_ctx.close.assert_called_once()
        assert trade_service_with_mock.trade_ctx is None


class TestReadOnlyGatewayLock:
    """A READ_ONLY deployment keeps the gateway locked across reconnects.

    The SDK reconnects by itself and keeps the same context object, so a lock
    asserted only once at startup is gone as soon as OpenD restarts. Nothing
    below re-reads the gateway's unlock state, so the lock has to be re-issued
    on every connection the SDK establishes.
    """

    @staticmethod
    def _connected(policy, lock_result=(RET_OK, "")):
        """Connect a service against a mock context that answers the lock.

        Returns the SDK's original reconnect callback alongside the context:
        connecting replaces that attribute with the service's wrapper, so a
        test that wants the inner one has to hold it from before.
        """
        ctx = MagicMock()
        ctx.unlock_trade.return_value = lock_result
        # Stands in for the SDK's own post-reconnect work (replaying a cached
        # unlock, re-subscribing), so a test can show it still runs.
        sdk_reconnect = ctx.on_api_socket_reconnected
        sdk_reconnect.return_value = (RET_OK, "")

        service = TradeService(policy=policy)
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            return_value=ctx,
        ):
            service.connect(timeout=5)
        return service, ctx, sdk_reconnect

    def test_connecting_locks_the_gateway(self):
        service, ctx, _ = self._connected(TradingPolicy(TradingMode.READ_ONLY))

        ctx.unlock_trade.assert_called_once_with(is_unlock=False)
        ctx.close.assert_not_called()
        assert service.trade_ctx is ctx

    def test_reconnect_keeps_the_configured_market_context(self):
        ctx = MagicMock()
        ctx.unlock_trade.return_value = (RET_OK, "")
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            return_value=ctx,
        ) as context_class:
            service = TradeService(
                trading_market="US",
                policy=TradingPolicy(TradingMode.SIMULATE),
            )
            service.connect()
            ctx.on_api_socket_reconnected()

        context_class.assert_called_once_with(
            host="127.0.0.1",
            port=11111,
            filter_trdmarket=TRADING_MARKET_FILTERS["US"],
        )
        assert service.trade_ctx is ctx

    def test_reconnecting_locks_the_gateway_again(self):
        """OpenD restarting must not leave a READ_ONLY server unlocked."""
        service, ctx, _ = self._connected(TradingPolicy(TradingMode.READ_ONLY))
        ctx.unlock_trade.reset_mock()

        # What the SDK calls on the reconnect thread once the socket is ready.
        ctx.on_api_socket_reconnected()

        ctx.unlock_trade.assert_called_once_with(is_unlock=False)
        assert service.trade_ctx is ctx

    def test_the_sdk_reconnect_work_still_runs_and_is_reported(self):
        """The hook wraps the SDK's own callback; it does not replace it."""
        _, ctx, sdk_reconnect = self._connected(TradingPolicy(TradingMode.READ_ONLY))
        sdk_reconnect.return_value = (RET_ERROR, "init connect failed")

        result = ctx.on_api_socket_reconnected()

        sdk_reconnect.assert_called_once_with()
        assert result == (RET_ERROR, "init connect failed")

    def test_a_refused_lock_does_not_break_the_reconnect(self):
        """Raising here would cost the SDK the connection it just made."""
        _, ctx, _sdk = self._connected(
            TradingPolicy(TradingMode.READ_ONLY),
            lock_result=(RET_ERROR, "trade svr not ready"),
        )
        ctx.unlock_trade.reset_mock()

        assert ctx.on_api_socket_reconnected() == (RET_OK, "")
        ctx.unlock_trade.assert_called_once_with(is_unlock=False)

    def test_a_refused_lock_on_initial_connect_is_logged_and_keeps_the_connection(
        self, caplog: pytest.LogCaptureFixture
    ):
        """A refused lock at connect must not fail connection or discard context."""
        with caplog.at_level(logging.WARNING):
            service, ctx, _ = self._connected(
                TradingPolicy(TradingMode.READ_ONLY),
                lock_result=(RET_ERROR, "trade svr not ready"),
            )

        ctx.unlock_trade.assert_called_once_with(is_unlock=False)
        ctx.close.assert_not_called()
        assert service.trade_ctx is ctx
        assert "Failed to lock the trade gateway after connecting" in caplog.text
        assert "trade svr not ready" in caplog.text

    @pytest.mark.parametrize("mode", [TradingMode.SIMULATE, TradingMode.REAL])
    def test_other_modes_are_left_alone(self, mode):
        """Only READ_ONLY asserts a lock; REAL relies on just-in-time unlock."""
        _, ctx, _sdk = self._connected(TradingPolicy(mode))
        ctx.unlock_trade.assert_not_called()

        ctx.on_api_socket_reconnected()

        ctx.unlock_trade.assert_not_called()

    def test_the_real_sdk_class_allows_the_hook(self):
        """The SDK resolves the callback on the instance, and permits shadowing.

        Wrapping an attribute holds only while the vendored class defines no
        ``__slots__`` and does not expose the callback as a read-only property.
        Those are facts about the SDK rather than about this server, so they are
        asserted against the real class, not a mock that would accept anything.
        """
        ctx = object.__new__(OpenSecTradeContext)
        service = TradeService(policy=TradingPolicy(TradingMode.READ_ONLY))

        service._watch_reconnects(ctx)

        assert ctx.__dict__["on_api_socket_reconnected"] is (
            ctx.on_api_socket_reconnected
        )
        assert (
            ctx.on_api_socket_reconnected
            is not OpenSecTradeContext.on_api_socket_reconnected
        )

    def test_a_context_abandoned_by_shutdown_is_not_locked(self):
        """close() won the race: the context is closed, not talked to."""
        release = threading.Event()
        ctx = MagicMock()
        ctx.unlock_trade.return_value = (RET_OK, "")

        def slow_connect(**_):
            release.wait(30)
            return ctx

        service = TradeService(policy=TradingPolicy(TradingMode.READ_ONLY))
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=slow_connect,
        ):
            service.connect(timeout=0.1)
            service.close()
            release.set()
            time.sleep(0.3)

        ctx.close.assert_called_once()
        ctx.unlock_trade.assert_not_called()


class TestGetAccounts:
    """Tests for get_accounts."""

    def test_get_accounts_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful account list retrieval."""
        df = pd.DataFrame([{"acc_id": 123, "trd_env": "REAL"}])
        mock_trade_ctx.get_acc_list.return_value = (0, df)  # RET_OK = 0

        result = trade_service_with_mock.get_accounts()

        assert len(result) == 1
        assert result[0]["acc_id"] == 123
        mock_trade_ctx.get_acc_list.assert_called_once()

    def test_get_accounts_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test error handling for failed account list."""
        mock_trade_ctx.get_acc_list.return_value = (-1, "API Error")

        with pytest.raises(RuntimeError, match="get_acc_list failed"):
            trade_service_with_mock.get_accounts()

    def test_get_accounts_no_context(self):
        """Test error when context not connected."""
        service = TradeService()  # reads need no write policy

        with pytest.raises(RuntimeError, match="Trade context not connected"):
            service.get_accounts()

    @staticmethod
    def _account_rows(mock_trade_ctx):
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "acc_id": 101,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["HK"],
                    },
                    {
                        "acc_id": 202,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["US"],
                    },
                    {
                        "acc_id": 303,
                        "trd_env": "REAL",
                        "trdmarket_auth": ["HK", "US"],
                    },
                    {"acc_id": 404, "trd_env": "SIMULATE"},
                ]
            ),
        )

    @pytest.mark.parametrize("market", [None, "NONE", " none "])
    def test_omitted_null_or_none_market_retains_provider_list(
        self, trade_service_with_mock, mock_trade_ctx, market
    ):
        self._account_rows(mock_trade_ctx)

        result = trade_service_with_mock.get_accounts(market=market)

        assert [account["acc_id"] for account in result] == [101, 202, 303, 404]
        mock_trade_ctx.get_acc_list.assert_called_once_with()

    def test_named_market_matches_membership_once_and_excludes_missing_metadata(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        self._account_rows(mock_trade_ctx)

        result = trade_service_with_mock.get_accounts(market=" us ")

        assert [account["acc_id"] for account in result] == [202, 303]

    def test_market_and_environment_filters_intersect(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        self._account_rows(mock_trade_ctx)

        result = trade_service_with_mock.get_accounts(market="US", trd_env="simulate")

        assert [account["acc_id"] for account in result] == [202]

    def test_valid_filter_without_matches_returns_empty_list(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        self._account_rows(mock_trade_ctx)

        result = trade_service_with_mock.get_accounts(market="SG")

        assert result == []

    @pytest.mark.parametrize(
        ("kwargs", "field"),
        [
            ({"market": "USA"}, "market"),
            ({"market": ""}, "market"),
            ({"trd_env": "PAPER"}, "trd_env"),
            ({"trd_env": " "}, "trd_env"),
        ],
    )
    def test_invalid_filters_fail_before_provider_query(
        self, trade_service_with_mock, mock_trade_ctx, kwargs, field
    ):
        with pytest.raises(ValueError) as excinfo:
            trade_service_with_mock.get_accounts(**kwargs)

        assert field in str(excinfo.value)
        mock_trade_ctx.get_acc_list.assert_not_called()


class TestReadAccountResolution:
    @staticmethod
    def _set_accounts(mock_trade_ctx, accounts):
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(accounts),
        )

    def test_zero_resolves_when_exactly_one_account_matches(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        self._set_accounts(
            mock_trade_ctx,
            [
                {"acc_id": 456, "trd_env": "SIMULATE"},
                {"acc_id": 789, "trd_env": "REAL"},
            ],
        )

        assert trade_service_with_mock.resolve_read_account("simulate", "0") == (
            "SIMULATE",
            456,
        )

    def test_multiple_accounts_fail_with_masked_candidates(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        first = 2222222222222222222
        second = 3333333333333333333
        self._set_accounts(
            mock_trade_ctx,
            [
                {"acc_id": first, "trd_env": "SIMULATE"},
                {"acc_id": second, "trd_env": "SIMULATE"},
            ],
        )

        with pytest.raises(ValueError) as excinfo:
            trade_service_with_mock.resolve_read_account("SIMULATE", 0)

        message = str(excinfo.value)
        assert "...2222" in message
        assert "...3333" in message
        assert str(first) not in message
        assert str(second) not in message
        assert "get_accounts" in message

    def test_no_candidate_fails_without_a_default(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        self._set_accounts(
            mock_trade_ctx,
            [{"acc_id": 789, "trd_env": "REAL"}],
        )

        with pytest.raises(ValueError) as excinfo:
            trade_service_with_mock.resolve_read_account("SIMULATE", "0")

        assert "No SIMULATE account" in str(excinfo.value)

    def test_explicit_large_account_id_is_exact_and_not_allowlist_gated(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        account_id = 9007199254740993
        self._set_accounts(
            mock_trade_ctx,
            [{"acc_id": account_id, "trd_env": "REAL"}],
        )

        assert trade_service_with_mock.resolve_read_account(
            "REAL", str(account_id)
        ) == ("REAL", account_id)

    @pytest.mark.parametrize("acc_id", [True, 1.0, "", "bad", "-1", "1.0"])
    def test_malformed_explicit_ids_are_rejected_before_discovery(
        self, trade_service_with_mock, mock_trade_ctx, acc_id
    ):
        with pytest.raises(ValueError):
            trade_service_with_mock.resolve_read_account("SIMULATE", acc_id)

        mock_trade_ctx.get_acc_list.assert_not_called()

    @pytest.mark.parametrize(
        ("accounts", "trd_env", "acc_id"),
        [
            (
                [{"acc_id": 456, "trd_env": "SIMULATE"}],
                "SIMULATE",
                "1111111111111111789",
            ),
            (
                [{"acc_id": 456, "trd_env": "SIMULATE"}],
                "REAL",
                "2222222222222222456",
            ),
        ],
    )
    def test_unknown_or_wrong_environment_id_is_rejected(
        self,
        trade_service_with_mock,
        mock_trade_ctx,
        accounts,
        trd_env,
        acc_id,
    ):
        self._set_accounts(mock_trade_ctx, accounts)

        with pytest.raises(ValueError) as excinfo:
            trade_service_with_mock.resolve_read_account(trd_env, acc_id)

        assert f"...{acc_id[-4:]}" in str(excinfo.value)
        assert acc_id not in str(excinfo.value)

    def test_invalid_environment_is_rejected(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        with pytest.raises(ValueError) as excinfo:
            trade_service_with_mock.resolve_read_account("PAPER", "0")

        assert "trd_env" in str(excinfo.value)
        mock_trade_ctx.get_acc_list.assert_not_called()

    def test_discovery_failure_propagates(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        mock_trade_ctx.get_acc_list.return_value = (RET_ERROR, "gateway unavailable")

        with pytest.raises(RuntimeError) as excinfo:
            trade_service_with_mock.resolve_read_account("SIMULATE", "0")

        assert "get_acc_list failed: gateway unavailable" in str(excinfo.value)


@pytest.mark.parametrize(
    ("service_method", "arguments", "sdk_method"),
    ACCOUNT_READ_ENDPOINTS,
)
def test_every_account_read_resolves_zero_to_a_concrete_id(
    trade_service_with_mock,
    mock_trade_ctx,
    service_method,
    arguments,
    sdk_method,
):
    getattr(mock_trade_ctx, sdk_method).return_value = (RET_OK, pd.DataFrame())

    getattr(trade_service_with_mock, service_method)(**arguments)

    sdk_arguments = getattr(mock_trade_ctx, sdk_method).call_args.kwargs
    assert sdk_arguments["acc_id"] == 123
    assert sdk_arguments["trd_env"] == "SIMULATE"


@pytest.mark.parametrize(
    ("service_method", "arguments", "sdk_method"),
    ACCOUNT_READ_ENDPOINTS,
)
def test_every_account_read_refuses_ambiguous_zero_before_query(
    trade_service_with_mock,
    mock_trade_ctx,
    service_method,
    arguments,
    sdk_method,
):
    mock_trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "SIMULATE"},
                {"acc_id": 789, "trd_env": "SIMULATE"},
            ]
        ),
    )

    with pytest.raises(ValueError):
        getattr(trade_service_with_mock, service_method)(**arguments)

    getattr(mock_trade_ctx, sdk_method).assert_not_called()


@pytest.mark.parametrize(
    ("service_method", "arguments", "sdk_method"),
    ACCOUNT_READ_ENDPOINTS,
)
def test_every_account_read_propagates_discovery_failure_before_query(
    trade_service_with_mock,
    mock_trade_ctx,
    service_method,
    arguments,
    sdk_method,
):
    mock_trade_ctx.get_acc_list.return_value = (RET_ERROR, "discovery unavailable")

    with pytest.raises(RuntimeError) as excinfo:
        getattr(trade_service_with_mock, service_method)(**arguments)

    assert "discovery unavailable" in str(excinfo.value)
    getattr(mock_trade_ctx, sdk_method).assert_not_called()


@pytest.mark.parametrize(
    ("service_method", "arguments", "sdk_method"),
    ACCOUNT_READ_ENDPOINTS,
)
def test_every_account_read_forwards_exact_explicit_id_and_environment(
    trade_service_with_mock,
    mock_trade_ctx,
    service_method,
    arguments,
    sdk_method,
):
    account_id = 9007199254740993
    mock_trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame([{"acc_id": account_id, "trd_env": "SIMULATE"}]),
    )
    getattr(mock_trade_ctx, sdk_method).return_value = (RET_OK, pd.DataFrame())
    explicit_arguments = {**arguments, "acc_id": str(account_id)}

    getattr(trade_service_with_mock, service_method)(**explicit_arguments)

    sdk_arguments = getattr(mock_trade_ctx, sdk_method).call_args.kwargs
    assert sdk_arguments["acc_id"] == account_id
    assert sdk_arguments["trd_env"] == "SIMULATE"


class TestGetAssets:
    """Tests for get_assets."""

    def test_get_assets_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful assets retrieval."""
        df = pd.DataFrame([{"cash": 10000.0, "market_val": 5000.0}])
        mock_trade_ctx.accinfo_query.return_value = (0, df)

        result = trade_service_with_mock.get_assets(trd_env="SIMULATE")

        assert result["cash"] == 10000.0
        mock_trade_ctx.accinfo_query.assert_called_once()

    def test_get_assets_empty(self, trade_service_with_mock, mock_trade_ctx):
        """Test empty assets result."""
        df = pd.DataFrame([])
        mock_trade_ctx.accinfo_query.return_value = (0, df)

        result = trade_service_with_mock.get_assets()

        assert result == {}


class TestGetPositions:
    """Tests for get_positions."""

    def test_get_positions_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful positions retrieval."""
        df = pd.DataFrame(
            [
                {"code": "US.AAPL", "qty": 100},
                {"code": "US.TSLA", "qty": 50},
            ]
        )
        mock_trade_ctx.position_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_positions()

        assert len(result) == 2
        assert result[0]["code"] == "US.AAPL"

    def test_get_positions_with_code_filter(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test positions with code filter."""
        df = pd.DataFrame([{"code": "US.AAPL", "qty": 100}])
        mock_trade_ctx.position_list_query.return_value = (0, df)

        trade_service_with_mock.get_positions(code="US.AAPL")

        mock_trade_ctx.position_list_query.assert_called_once()
        call_kwargs = mock_trade_ctx.position_list_query.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"


class TestGetMaxTradable:
    """Tests for get_max_tradable."""

    def test_get_max_tradable_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test max tradable quantity retrieval."""
        df = pd.DataFrame([{"max_cash_buy": 100, "max_sell_short": 50}])
        mock_trade_ctx.acctradinginfo_query.return_value = (0, df)

        result = trade_service_with_mock.get_max_tradable(
            order_type="NORMAL", code="US.AAPL", price=150.0
        )

        assert result["max_cash_buy"] == 100
        mock_trade_ctx.acctradinginfo_query.assert_called_once()
        _, kwargs = mock_trade_ctx.acctradinginfo_query.call_args
        assert kwargs["order_id"] is None

    def test_get_max_tradable_custom_order_id(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test max tradable when order_id is provided."""
        df = pd.DataFrame([{"max_cash_buy": 100}])
        mock_trade_ctx.acctradinginfo_query.return_value = (0, df)

        trade_service_with_mock.get_max_tradable(
            order_type="NORMAL", code="US.AAPL", price=150.0, order_id="ORDER123"
        )

        _, kwargs = mock_trade_ctx.acctradinginfo_query.call_args
        assert kwargs["order_id"] == "ORDER123"


class TestGetMarginRatio:
    """Tests for get_margin_ratio."""

    def test_get_margin_ratio_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test margin ratio retrieval."""
        df = pd.DataFrame([{"code": "US.AAPL", "im_factor": 0.25}])
        mock_trade_ctx.get_margin_ratio.return_value = (0, df)

        result = trade_service_with_mock.get_margin_ratio(["US.AAPL"])

        assert len(result) == 1
        assert result[0]["im_factor"] == 0.25


class TestGetCashFlow:
    """Tests for get_cash_flow."""

    def test_get_cash_flow_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test cash flow retrieval."""
        df = pd.DataFrame([{"trade_date": "2025-01-01", "amount": 1000.0}])
        mock_trade_ctx.get_acc_cash_flow.return_value = (0, df)

        result = trade_service_with_mock.get_cash_flow(clearing_date="2025-01-01")

        assert len(result) == 1
        assert result[0]["amount"] == 1000.0


class TestUnlockTrade:
    """Tests for unlock_trade."""

    def test_unlock_trade_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful trade unlock."""
        mock_trade_ctx.unlock_trade.return_value = (0, None)

        trade_service_with_mock.unlock_trade(password="testpass")

        mock_trade_ctx.unlock_trade.assert_called_once_with(
            password="testpass", password_md5=None, is_unlock=True
        )

    def test_unlock_trade_with_md5(self, trade_service_with_mock, mock_trade_ctx):
        """Test trade unlock with MD5 password."""
        mock_trade_ctx.unlock_trade.return_value = (0, None)

        trade_service_with_mock.unlock_trade(password_md5="abc123")

        mock_trade_ctx.unlock_trade.assert_called_once_with(
            password=None, password_md5="abc123", is_unlock=True
        )

    def test_unlock_trade_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test unlock trade error handling."""
        mock_trade_ctx.unlock_trade.return_value = (-1, "Invalid password")

        with pytest.raises(RuntimeError, match="unlock_trade failed"):
            trade_service_with_mock.unlock_trade(password="wrongpass")


class TestLockTrade:
    """Tests for lock_trade."""

    def test_lock_trade_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful trade lock."""
        mock_trade_ctx.unlock_trade.return_value = (0, None)

        trade_service_with_mock.lock_trade()

        mock_trade_ctx.unlock_trade.assert_called_once_with(is_unlock=False)

    def test_lock_trade_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test trade lock error handling."""
        mock_trade_ctx.unlock_trade.return_value = (-1, "Gateway busy")

        with pytest.raises(RuntimeError, match="lock_trade failed"):
            trade_service_with_mock.lock_trade()


def _credentialed_service(ctx: MagicMock) -> TradeService:
    """A REAL service with a stored credential: the lifecycle under test."""
    service = TradeService(policy=REAL_POLICY, trade_password_md5="hash123")
    service.trade_ctx = ctx
    return service


def _place(service: TradeService, **overrides):
    kwargs = {
        "code": "US.AAPL",
        "price": 150.0,
        "qty": 1,
        "trd_side": "BUY",
        "trd_env": "REAL",
        "acc_id": 123,
    }
    kwargs.update(overrides)
    return service.place_order(**kwargs)


class _LockArrival:
    """Wraps the service's just-in-time lock to report a thread arriving at it.

    A concurrency test needs to know that the second writer has crossed its
    early halt check and reached the lock — the exact point the race turns on.
    Sleeping for a while only assumes it. This sets ``arrived`` from inside the
    named thread, immediately before the acquire, so the test can wait on the
    fact instead of on a duration.
    """

    def __init__(self, lock, thread_name: str, arrived: threading.Event):
        self._lock = lock
        self._thread_name = thread_name
        self._arrived = arrived

    def __enter__(self):
        if threading.current_thread().name == self._thread_name:
            self._arrived.set()
        return self._lock.__enter__()

    def __exit__(self, *exc_info):
        return self._lock.__exit__(*exc_info)


class TestJustInTimeUnlock:
    """Unlock, dispatch, relock — and what each failure means."""

    def test_a_real_write_unlocks_then_relocks(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = _credentialed_service(mock_trade_ctx)

        _place(service)

        assert mock_trade_ctx.unlock_trade.call_args_list[0].kwargs == {
            "password": None,
            "password_md5": "hash123",
            "is_unlock": True,
        }
        assert mock_trade_ctx.unlock_trade.call_args_list[1].kwargs == {
            "is_unlock": False
        }

    def test_the_relock_is_attempted_even_when_the_write_fails(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.place_order.side_effect = ValueError("Order exploded")
        service = _credentialed_service(mock_trade_ctx)

        with pytest.raises(OrderOutcomeUnknownError):
            _place(service)

        mock_trade_ctx.unlock_trade.assert_called_with(is_unlock=False)

    def test_an_unlock_failure_sends_nothing(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_ERROR, "wrong password")
        service = _credentialed_service(mock_trade_ctx)

        with pytest.raises(OrderNotSentError) as excinfo:
            _place(service)

        assert "no order was sent" in str(excinfo.value)
        mock_trade_ctx.place_order.assert_not_called()

    def test_an_unlock_reported_as_unnecessary_is_a_success(self, mock_trade_ctx):
        """The gateway returns RET_OK with an explanatory message when it is
        already unlocked. Reading that as a failure would refuse every write."""
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, "already unlocked")
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = _credentialed_service(mock_trade_ctx)

        assert _place(service)["order_id"] == "1"

    def test_simulate_does_not_unlock(self, mock_trade_ctx):
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = _credentialed_service(mock_trade_ctx)

        _place(service, trd_env="SIMULATE")

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_real_without_a_credential_does_not_unlock(self, mock_trade_ctx):
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        _place(service)

        mock_trade_ctx.unlock_trade.assert_not_called()


class TestDispatchOutcomes:
    """Each post-boundary case, and which outcome it maps to."""

    def test_a_readable_success_is_the_receipt(self, mock_trade_ctx):
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}]),
        )
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        result = _place(service, trd_env="SIMULATE")

        assert result["order_id"] == "1"
        assert result["acc_id"] == 123
        assert result["trd_env"] == "SIMULATE"
        assert "gateway_relock_error" not in result

    def test_a_gateway_error_code_is_an_unknown_outcome(self, mock_trade_ctx):
        """Not a rejection: the text cannot tell a broker refusal from a
        timeout, and guessing "rejected" would tell the caller nothing was
        sent when something may have been."""
        mock_trade_ctx.place_order.return_value = (RET_ERROR, "gateway said no")
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderOutcomeUnknownError) as excinfo:
            _place(service, trd_env="SIMULATE")

        message = str(excinfo.value)
        assert "may have been sent" in message
        assert "outcome is unknown" in message
        assert "gateway said no" in message
        assert "reached" not in message

    def test_a_raising_sdk_call_is_an_unknown_outcome(self, mock_trade_ctx):
        mock_trade_ctx.place_order.side_effect = TimeoutError("socket timeout")
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderOutcomeUnknownError, match="socket timeout"):
            _place(service, trd_env="SIMULATE")

    def test_an_unreadable_success_forbids_a_resend(self, mock_trade_ctx):
        """The order exists and its identifier is lost. Resending would double
        it; reporting an unknown outcome would understate what is known."""
        mock_trade_ctx.place_order.return_value = (RET_OK, "not a frame")
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderReceiptUnreadableError) as excinfo:
            _place(service, trd_env="SIMULATE")

        message = str(excinfo.value)
        assert "acknowledged" in message
        assert "do not resend" in message.lower()
        assert "outcome is unknown" not in message


class TestRelockAlwaysAttempted:
    """The gateway is unlocked right now; nothing may leave it that way."""

    def test_an_unexpected_failure_still_relocks(self, mock_trade_ctx):
        """A conversion that raises something the boundary does not classify.

        The relock lives in a `finally` precisely so that a path nobody
        anticipated cannot skip it and leave the gateway open.
        """
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = _credentialed_service(mock_trade_ctx)

        def exploding_convert(_data):
            raise KeyboardInterrupt("operator interrupted")

        with pytest.raises(KeyboardInterrupt):
            service._dispatch_write(
                "REAL",
                "place_order",
                lambda: mock_trade_ctx.place_order(),
                exploding_convert,
                adds_exposure=True,
            )

        mock_trade_ctx.unlock_trade.assert_called_with(is_unlock=False)

    def test_the_relock_runs_before_the_failure_propagates(self, mock_trade_ctx):
        order = []
        mock_trade_ctx.unlock_trade.side_effect = lambda **kwargs: (
            order.append("lock" if kwargs.get("is_unlock") is False else "unlock"),
            (RET_OK, None),
        )[1]
        mock_trade_ctx.place_order.return_value = (RET_ERROR, "gateway said no")
        service = _credentialed_service(mock_trade_ctx)

        with pytest.raises(OrderOutcomeUnknownError):
            _place(service)

        assert order == ["unlock", "lock"]


class TestExecutionHalt:
    """A failed relock halts execution until someone locks the gateway."""

    @staticmethod
    def _halted_service(ctx: MagicMock) -> TradeService:
        """A service that has already halted, via a real relock failure."""
        ctx.unlock_trade.side_effect = [
            (RET_OK, None),  # the write's unlock
            (RET_ERROR, "lock refused"),  # the relock that halts it
        ]
        ctx.place_order.return_value = (RET_OK, pd.DataFrame([{"order_id": "1"}]))
        service = _credentialed_service(ctx)
        _place(service)
        assert service.execution_state["execution_halted"] is True
        ctx.unlock_trade.side_effect = None
        ctx.reset_mock()
        return service

    def test_an_acknowledged_write_keeps_its_receipt_when_the_relock_fails(
        self, mock_trade_ctx
    ):
        """Losing an order identifier to a lock failure would be strictly worse
        than reporting both facts."""
        mock_trade_ctx.unlock_trade.side_effect = [
            (RET_OK, None),
            (RET_ERROR, "lock refused"),
        ]
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = _credentialed_service(mock_trade_ctx)

        result = _place(service)

        assert result["order_id"] == "1"
        assert result["execution_halted"] is True
        assert "lock refused" in result["gateway_relock_error"]
        assert service.execution_state["execution_halted"] is True

    def test_a_failed_write_reports_the_relock_failure_alongside_its_own(
        self, mock_trade_ctx
    ):
        mock_trade_ctx.unlock_trade.side_effect = [
            (RET_OK, None),
            (RET_ERROR, "lock refused"),
        ]
        mock_trade_ctx.place_order.return_value = (RET_ERROR, "gateway said no")
        service = _credentialed_service(mock_trade_ctx)

        with pytest.raises(OrderOutcomeUnknownError) as excinfo:
            _place(service)

        message = str(excinfo.value)
        assert "may have been sent" in message
        assert "lock refused" in message

    def test_a_halt_refuses_new_exposure(self, mock_trade_ctx):
        service = self._halted_service(mock_trade_ctx)

        with pytest.raises(OrderNotSentError) as excinfo:
            _place(service)

        message = str(excinfo.value)
        assert "halted" in message
        assert "lock_trade" in message
        mock_trade_ctx.place_order.assert_not_called()

    @pytest.mark.parametrize("op", ["NORMAL", "ENABLE"])
    def test_a_halt_refuses_exposing_modifications(self, mock_trade_ctx, op):
        service = self._halted_service(mock_trade_ctx)

        with pytest.raises(OrderNotSentError, match="halted"):
            service.modify_order(
                order_id="123456",
                modify_order_op=op,
                price=1.0,
                trd_env="REAL",
                acc_id=123,
            )

        mock_trade_ctx.modify_order.assert_not_called()

    @pytest.mark.parametrize("op", ["CANCEL", "DISABLE", "DELETE"])
    def test_a_halt_allows_reducing_exposure(self, mock_trade_ctx, op):
        """An operator facing a halt must still be able to pull orders."""
        service = self._halted_service(mock_trade_ctx)
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )

        service.modify_order(
            order_id="123456", modify_order_op=op, trd_env="REAL", acc_id=123
        )

        mock_trade_ctx.modify_order.assert_called_once()

    def test_cancellation_is_allowed_while_halted(self, mock_trade_ctx):
        service = self._halted_service(mock_trade_ctx)
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )

        service.cancel_order(order_id="123456", trd_env="REAL", acc_id=123)

        mock_trade_ctx.modify_order.assert_called_once()

    def test_a_cancellations_successful_relock_does_not_clear_the_halt(
        self, mock_trade_ctx
    ):
        """That relock only undoes the unlock this server made a moment ago.

        It says nothing about why the earlier one failed.
        """
        service = self._halted_service(mock_trade_ctx)
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )

        service.cancel_order(order_id="123456", trd_env="REAL", acc_id=123)

        assert service.execution_state["execution_halted"] is True

    def test_a_reconnect_lock_does_not_clear_the_halt(self, mock_trade_ctx):
        """A reconnect lock happens without anyone seeing the halt."""
        service = self._halted_service(mock_trade_ctx)
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)

        service._enforce_gateway_lock(mock_trade_ctx, "reconnecting")

        assert service.execution_state["execution_halted"] is True

    def test_a_health_check_neither_clears_nor_probes(self, mock_trade_ctx):
        service = self._halted_service(mock_trade_ctx)

        first = service.execution_state
        second = service.execution_state

        assert first["execution_halted"] is True
        assert second == first

    def test_a_successful_lock_trade_clears_the_halt(self, mock_trade_ctx):
        service = self._halted_service(mock_trade_ctx)
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)

        result = service.lock_trade()

        assert result == {
            "status": "locked",
            "execution_halted": False,
            "halt_cleared": True,
        }
        assert service.execution_state["execution_halted"] is False

    def test_a_failed_lock_trade_keeps_the_halt_and_its_start_time(
        self, mock_trade_ctx
    ):
        service = self._halted_service(mock_trade_ctx)
        started = service.execution_state["halted_since"]
        mock_trade_ctx.unlock_trade.return_value = (RET_ERROR, "still refused")

        with pytest.raises(RuntimeError, match="lock_trade failed"):
            service.lock_trade()

        state = service.execution_state
        assert state["execution_halted"] is True
        assert state["halted_since"] == started
        assert "still refused" in state["halt_error"]

    def test_lock_trade_reports_no_clear_when_already_armed(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        service = _credentialed_service(mock_trade_ctx)

        result = service.lock_trade()

        assert result["halt_cleared"] is False
        assert result["execution_halted"] is False

    def test_lock_trade_waits_for_an_in_flight_write(self, mock_trade_ctx):
        """It must never lock the gateway inside another write's unlock window."""
        released = threading.Event()
        lock_issued = threading.Event()

        def slow_write(**_kwargs):
            released.wait(timeout=2)
            return RET_OK, pd.DataFrame([{"order_id": "1"}])

        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.place_order.side_effect = slow_write
        service = _credentialed_service(mock_trade_ctx)

        writer = threading.Thread(target=lambda: _place(service))
        writer.start()
        time.sleep(0.05)

        locker = threading.Thread(
            target=lambda: (service.lock_trade(), lock_issued.set())
        )
        locker.start()

        assert not lock_issued.wait(timeout=0.2), "lock_trade ran during the write"
        released.set()
        writer.join(timeout=2)
        locker.join(timeout=2)
        assert lock_issued.is_set()


class TestHaltRefusesBeforeAnyGatewayRequest:
    """The contract says "before any gateway request", so the read counts too.

    Resolving the default ``acc_id="0"`` asks the gateway for the account list.
    Checked after that resolution, a halted write still put a request on the
    connection before being turned away — no mutation, but not what the spec
    says either.
    """

    @staticmethod
    def _halted_with_accounts(ctx: MagicMock) -> TradeService:
        service = TestExecutionHalt._halted_service(ctx)
        ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                [{"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]}]
            ),
        )
        return service

    def test_a_halted_placement_resolves_no_account(self, mock_trade_ctx):
        service = self._halted_with_accounts(mock_trade_ctx)

        with pytest.raises(OrderNotSentError, match="halted"):
            _place(service, acc_id="0")

        mock_trade_ctx.get_acc_list.assert_not_called()

    def test_a_halted_combo_placement_resolves_no_account(self, mock_trade_ctx):
        service = self._halted_with_accounts(mock_trade_ctx)

        with pytest.raises(OrderNotSentError, match="halted"):
            service.place_combo_order(
                combo_legs=[
                    {"code": "US.A", "trd_side": "BUY", "qty_ratio": 1},
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                price=1.0,
                qty=1,
                trd_env="REAL",
                acc_id="0",
            )

        mock_trade_ctx.get_acc_list.assert_not_called()

    @pytest.mark.parametrize("op", ["NORMAL", "ENABLE"])
    def test_a_halted_exposing_modification_resolves_no_account(
        self, mock_trade_ctx, op
    ):
        service = self._halted_with_accounts(mock_trade_ctx)

        with pytest.raises(OrderNotSentError, match="halted"):
            service.modify_order(
                order_id="123456",
                modify_order_op=op,
                price=1.0,
                trd_env="REAL",
                acc_id="0",
            )

        mock_trade_ctx.get_acc_list.assert_not_called()
        mock_trade_ctx.order_list_query.assert_not_called()

    def test_a_permitted_write_still_resolves_the_default_account(self, mock_trade_ctx):
        """The refusal moved; resolution itself did not."""
        service = self._halted_with_accounts(mock_trade_ctx)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)

        service.cancel_order(order_id="123456", trd_env="REAL", acc_id="0")

        mock_trade_ctx.get_acc_list.assert_called_once()
        assert mock_trade_ctx.modify_order.call_args.kwargs["acc_id"] == 123


class TestHaltIsScopedToRealWrites:
    """The halt guards the REAL gateway, so it is REAL writes it refuses.

    A failed relock says the REAL gateway may still be unlocked. A SIMULATE
    write neither unlocks it nor can add live exposure, so refusing one would
    take away the only environment still safe to use while an operator works
    out what went wrong — and the accepted contract scopes the refusal to REAL.
    """

    def test_a_simulate_placement_is_allowed_while_halted(self, mock_trade_ctx):
        service = TestExecutionHalt._halted_service(mock_trade_ctx)

        result = _place(service, trd_env="SIMULATE", acc_id=999)

        assert result["order_id"] == "1"
        mock_trade_ctx.place_order.assert_called_once()

    def test_a_permitted_simulate_write_does_not_touch_the_gateway_lock(
        self, mock_trade_ctx
    ):
        """It is not a just-in-time write, so there is nothing to unlock."""
        service = TestExecutionHalt._halted_service(mock_trade_ctx)

        _place(service, trd_env="SIMULATE", acc_id=999)

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_a_permitted_simulate_write_leaves_the_halt_in_place(self, mock_trade_ctx):
        """Only lock_trade clears it; letting a paper order through is not a
        sign that the gateway can be locked again."""
        service = TestExecutionHalt._halted_service(mock_trade_ctx)

        _place(service, trd_env="SIMULATE", acc_id=999)

        assert service.execution_state["execution_halted"] is True

    @pytest.mark.parametrize("op", ["NORMAL", "ENABLE"])
    def test_a_simulate_exposing_modification_is_allowed_while_halted(
        self, mock_trade_ctx, op
    ):
        service = TestExecutionHalt._halted_service(mock_trade_ctx)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )

        service.modify_order(
            order_id="123456",
            modify_order_op=op,
            price=1.0,
            trd_env="SIMULATE",
            acc_id=999,
        )

        mock_trade_ctx.modify_order.assert_called_once()

    def test_a_real_placement_is_still_refused_while_halted(self, mock_trade_ctx):
        """The narrowing must not reach the writes the halt exists for."""
        service = TestExecutionHalt._halted_service(mock_trade_ctx)

        with pytest.raises(OrderNotSentError, match="halted"):
            _place(service)

        mock_trade_ctx.place_order.assert_not_called()

    def test_the_in_lock_check_lets_a_simulate_write_through(self, mock_trade_ctx):
        """The authoritative check, exercised directly."""
        service = _credentialed_service(mock_trade_ctx)
        service._execution.record_relock_failure("lock refused")

        receipt, relock_error = service._dispatch_write(
            "SIMULATE",
            "place_order",
            lambda: (RET_OK, pd.DataFrame([{"order_id": "1"}])),
            lambda _data: {"order_id": "1"},
            adds_exposure=True,
        )

        assert receipt == {"order_id": "1"}
        assert relock_error is None


class TestHaltIsCheckedInsideTheLock:
    """A write queued behind another cannot slip past a halt it caused.

    The pre-dispatch halt check alone is not enough: two writes can both read
    ARMED, and the first one's relock can then fail and halt the service while
    the second is still queued for the just-in-time lock. Without a re-check
    inside that lock, the second one dispatches new exposure after the halt
    began — which is exactly what the halt exists to stop.
    """

    def test_a_queued_write_is_refused_after_the_first_one_halts(self, mock_trade_ctx):
        service = _credentialed_service(mock_trade_ctx)
        first_is_dispatching = threading.Event()
        release_first = threading.Event()
        second_is_at_the_lock = threading.Event()
        unlock_calls: list[bool] = []

        def unlock_trade(**kwargs):
            is_unlock = kwargs.get("is_unlock", True)
            unlock_calls.append(is_unlock)
            if is_unlock:
                return RET_OK, None
            # The relock: fail it, which halts the service.
            return RET_ERROR, "lock refused"

        def slow_place_order(**_kwargs):
            first_is_dispatching.set()
            release_first.wait(timeout=2)
            return RET_OK, pd.DataFrame([{"order_id": "A"}])

        mock_trade_ctx.unlock_trade.side_effect = unlock_trade
        mock_trade_ctx.place_order.side_effect = slow_place_order
        service._jit_lock = _LockArrival(  # type: ignore[assignment]
            service._jit_lock, "writer-b", second_is_at_the_lock
        )

        first: dict = {}
        second: dict = {}

        def run_first():
            try:
                first["result"] = _place(service)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                first["error"] = exc

        def run_second():
            try:
                second["result"] = _place(service)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                second["error"] = exc

        writer_a = threading.Thread(target=run_first, name="writer-a")
        writer_a.start()
        assert first_is_dispatching.wait(timeout=2)

        writer_b = threading.Thread(target=run_second, name="writer-b")
        writer_b.start()

        # Wait for proof, not for a duration: B has passed its own pre-dispatch
        # checks — the service was ARMED when it looked — and has reached the
        # lock A holds. A sleep here only assumed that, so on a loaded runner B
        # could instead start after the halt and be turned away by the early
        # check, leaving the test green with the in-lock check deleted.
        assert second_is_at_the_lock.wait(timeout=2)

        release_first.set()
        writer_a.join(timeout=2)
        writer_b.join(timeout=2)

        # A is acknowledged, with the halt reported alongside its receipt.
        assert first["result"]["order_id"] == "A"
        assert first["result"]["execution_halted"] is True
        assert service.execution_state["execution_halted"] is True

        # B is refused, and never reached the gateway.
        assert isinstance(second.get("error"), OrderNotSentError)
        assert "halted" in str(second["error"])
        assert mock_trade_ctx.place_order.call_count == 1

    def test_a_queued_cancellation_still_goes_through(self, mock_trade_ctx):
        """Reducing exposure stays permitted, which is the point of the gate."""
        service = _credentialed_service(mock_trade_ctx)
        service._execution.record_relock_failure("lock refused")
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        mock_trade_ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456"}]),
        )

        service.cancel_order(order_id="123456", trd_env="REAL", acc_id=123)

        mock_trade_ctx.modify_order.assert_called_once()

    def test_the_in_lock_check_refuses_before_unlocking(self, mock_trade_ctx):
        """A halted write must not even unlock the gateway."""
        service = _credentialed_service(mock_trade_ctx)
        service._execution.record_relock_failure("lock refused")
        mock_trade_ctx.reset_mock()

        with pytest.raises(OrderNotSentError, match="halted"):
            service._dispatch_write(
                "REAL",
                "place_order",
                lambda: (RET_OK, pd.DataFrame([{"order_id": "1"}])),
                lambda _data: {"order_id": "1"},
                adds_exposure=True,
            )

        mock_trade_ctx.unlock_trade.assert_not_called()


class TestAcknowledgedButEmptyReceipt:
    """RET_OK with no rows is not a placed order anyone can find."""

    def test_an_empty_payload_is_an_unreadable_receipt(self, mock_trade_ctx):
        """It used to return a result holding only the routing, which reads as a
        successful order with no identifier in it."""
        mock_trade_ctx.place_order.return_value = (RET_OK, pd.DataFrame([]))
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderReceiptUnreadableError) as excinfo:
            _place(service, trd_env="SIMULATE")

        message = str(excinfo.value)
        assert "acknowledged" in message
        assert "do not resend" in message.lower()
        assert "get_orders" in message

    def test_an_empty_combo_payload_is_an_unreadable_receipt(self, mock_trade_ctx):
        mock_trade_ctx.place_combo_order.return_value = (RET_OK, pd.DataFrame([]))
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderReceiptUnreadableError, match="Do not resend"):
            service.place_combo_order(
                combo_legs=[
                    {"code": "US.A", "trd_side": "BUY", "qty_ratio": 1},
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                price=2.5,
                qty=1,
                trd_env="SIMULATE",
                acc_id=123,
            )

    def test_an_empty_modification_payload_is_an_unreadable_receipt(
        self, mock_trade_ctx
    ):
        mock_trade_ctx.modify_order.return_value = (RET_OK, pd.DataFrame([]))
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(OrderReceiptUnreadableError, match="Do not resend"):
            service.cancel_order(order_id="123456", trd_env="SIMULATE", acc_id=123)


class TestLockAtRest:
    """Who locks on connect and reconnect, and who must not."""

    def test_real_with_a_credential_locks(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        service = _credentialed_service(mock_trade_ctx)

        service._enforce_gateway_lock(mock_trade_ctx, "connecting")

        mock_trade_ctx.unlock_trade.assert_called_once_with(is_unlock=False)

    def test_real_without_a_credential_does_not_lock(self, mock_trade_ctx):
        """It could not unlock again, so locking would strand the operator."""
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        service._enforce_gateway_lock(mock_trade_ctx, "connecting")

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_simulate_does_not_lock(self, mock_trade_ctx):
        service = TradeService(
            policy=TradingPolicy(TradingMode.SIMULATE), trade_password_md5="hash123"
        )
        service.trade_ctx = mock_trade_ctx

        service._enforce_gateway_lock(mock_trade_ctx, "connecting")

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_a_reconnect_during_a_write_issues_no_lock(self, mock_trade_ctx):
        """Locking underneath a write would make it fail on a locked gateway.

        The write's own relock covers the window instead.
        """
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        service = _credentialed_service(mock_trade_ctx)
        skipped = threading.Event()

        def write_holding_the_lock(**_kwargs):
            # The reconnect runs on the SDK's own thread, not this one.
            reconnect = threading.Thread(
                target=lambda: (
                    service._enforce_gateway_lock(mock_trade_ctx, "reconnecting"),
                    skipped.set(),
                )
            )
            reconnect.start()
            reconnect.join(timeout=2)
            return RET_OK, pd.DataFrame([{"order_id": "1"}])

        mock_trade_ctx.place_order.side_effect = write_holding_the_lock

        _place(service)

        assert skipped.is_set()
        # Exactly the write's own unlock and relock: no third call from the
        # reconnect.
        assert mock_trade_ctx.unlock_trade.call_count == 2

    def test_a_successful_lock_at_rest_does_not_change_the_execution_state(
        self, mock_trade_ctx
    ):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        service = _credentialed_service(mock_trade_ctx)

        service._enforce_gateway_lock(mock_trade_ctx, "connecting")

        assert service.execution_state["execution_halted"] is False


class TestManualUnlockRefusal:
    """A stored credential makes manual unlocking the wrong tool."""

    def test_unlock_is_refused_when_a_credential_is_stored(self, mock_trade_ctx):
        service = _credentialed_service(mock_trade_ctx)

        with pytest.raises(TradingPolicyError) as excinfo:
            service.unlock_trade(password="pw")

        assert "unlock just in time" in str(excinfo.value)
        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_unlock_without_a_credential_requires_a_password(self, mock_trade_ctx):
        """There is no environment fallback left to fall back to."""
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(ValueError, match="requires a password"):
            service.unlock_trade()

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_unlock_with_a_password_reaches_the_gateway(self, mock_trade_ctx):
        mock_trade_ctx.unlock_trade.return_value = (RET_OK, None)
        service = TradeService(policy=REAL_POLICY)
        service.trade_ctx = mock_trade_ctx

        service.unlock_trade(password="pw")

        mock_trade_ctx.unlock_trade.assert_called_once_with(
            password="pw", password_md5=None, is_unlock=True
        )


class TestInstrumentLookupIntegration:
    """When the adapter runs, and what its failures look like from outside."""

    @staticmethod
    def _capped_service(ctx: MagicMock, lookup) -> TradeService:
        service = TradeService(
            policy=TradingPolicy(
                TradingMode.REAL,
                max_order_notional={"USD": 1000.0},
                real_acc_ids=frozenset({123}),
            ),
            instrument_lookup=lookup,
        )
        service.trade_ctx = ctx
        return service

    def test_no_cap_means_no_lookup(self, mock_trade_ctx):
        """An unconfigured deployment pays no quote latency for limits."""
        lookup = MagicMock()
        service = TradeService(policy=REAL_POLICY, instrument_lookup=lookup)
        service.trade_ctx = mock_trade_ctx
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )

        _place(service, trd_env="SIMULATE")

        lookup.assert_not_called()

    def test_a_cap_makes_the_lookup_run(self, mock_trade_ctx):
        lookup = MagicMock(
            return_value=[
                InstrumentFacts(
                    code="US.AAPL",
                    classification="STOCK",
                    monetary_multiplier=1.0,
                    last_price=150.0,
                )
            ]
        )
        mock_trade_ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1"}]),
        )
        service = self._capped_service(mock_trade_ctx, lookup)

        _place(service, trd_env="SIMULATE", price=150.0, qty=1)

        lookup.assert_called_once_with(["US.AAPL"])

    def test_an_adapter_failure_is_not_sent_never_unknown(self, mock_trade_ctx):
        """The adapter sits inside the pre-dispatch boundary, so a missing
        valuation fact can never surface as an order that may exist."""
        lookup = MagicMock(side_effect=InstrumentLookupError("quote server down"))
        service = self._capped_service(mock_trade_ctx, lookup)

        with pytest.raises(OrderNotSentError) as excinfo:
            _place(service, trd_env="SIMULATE")

        assert "no order was sent" in str(excinfo.value)
        assert "quote server down" in str(excinfo.value)
        mock_trade_ctx.place_order.assert_not_called()

    def test_no_lookup_wired_with_a_cap_refuses(self, mock_trade_ctx):
        service = self._capped_service(mock_trade_ctx, None)

        with pytest.raises(OrderNotSentError, match="no instrument lookup"):
            _place(service, trd_env="SIMULATE")

        mock_trade_ctx.place_order.assert_not_called()


class TestModificationAssessment:
    """A modification is checked as the order that would result."""

    @staticmethod
    def _service(ctx: MagicMock, existing: dict) -> TradeService:
        ctx.order_list_query.return_value = (RET_OK, pd.DataFrame([existing]))
        ctx.modify_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "123456", "order_status": "MODIFIED"}]),
        )
        service = TradeService(
            policy=TradingPolicy(
                TradingMode.REAL,
                max_order_notional={"USD": 1000.0},
                real_acc_ids=frozenset({123}),
            ),
            instrument_lookup=lambda codes: [
                InstrumentFacts(
                    code=codes[0],
                    classification="STOCK",
                    monetary_multiplier=1.0,
                    last_price=50.0,
                )
            ],
        )
        service.trade_ctx = ctx
        return service

    OPEN_ORDER = {
        "order_id": "123456",
        "code": "US.AAPL",
        "trd_side": "BUY",
        "order_type": "NORMAL",
        "qty": 10,
        "price": 50.0,
        "aux_price": 0.0,
    }

    def test_a_price_only_change_is_checked_against_the_full_order(
        self, mock_trade_ctx
    ):
        """10 x 500 = 5,000 USD.

        The old check passed qty=0 when only the price changed, so the notional
        came out as zero and every price change passed.
        """
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))

        with pytest.raises(OrderNotSentError) as excinfo:
            service.modify_order(
                order_id="123456",
                modify_order_op="NORMAL",
                price=500.0,
                trd_env="REAL",
                acc_id=123,
            )

        assert "5,000.00 USD" in str(excinfo.value)
        mock_trade_ctx.modify_order.assert_not_called()

    def test_a_quantity_only_change_is_checked_against_the_full_order(
        self, mock_trade_ctx
    ):
        """100 x 50 = 5,000 USD."""
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))

        with pytest.raises(OrderNotSentError, match="5,000.00 USD"):
            service.modify_order(
                order_id="123456",
                modify_order_op="NORMAL",
                qty=100,
                trd_env="REAL",
                acc_id=123,
            )

    def test_a_permitted_modification_is_dispatched(self, mock_trade_ctx):
        """10 x 90 = 900 USD, under the cap."""
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))

        result = service.modify_order(
            order_id="123456",
            modify_order_op="NORMAL",
            price=90.0,
            trd_env="REAL",
            acc_id=123,
        )

        assert result["order_status"] == "MODIFIED"
        mock_trade_ctx.modify_order.assert_called_once()

    def test_enable_is_checked_because_it_restores_exposure(self, mock_trade_ctx):
        over_cap = dict(self.OPEN_ORDER, qty=100, price=500.0)
        service = self._service(mock_trade_ctx, over_cap)

        with pytest.raises(OrderNotSentError, match="exceeds"):
            service.modify_order(
                order_id="123456",
                modify_order_op="ENABLE",
                trd_env="REAL",
                acc_id=123,
            )

        mock_trade_ctx.modify_order.assert_not_called()

    def test_an_unknown_order_is_refused(self, mock_trade_ctx):
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))
        mock_trade_ctx.order_list_query.return_value = (RET_OK, pd.DataFrame([]))

        with pytest.raises(OrderNotSentError, match="was not found"):
            service.modify_order(
                order_id="999",
                modify_order_op="NORMAL",
                price=1.0,
                trd_env="REAL",
                acc_id=123,
            )

        mock_trade_ctx.modify_order.assert_not_called()

    def test_a_failing_order_lookup_is_refused(self, mock_trade_ctx):
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))
        mock_trade_ctx.order_list_query.return_value = (RET_ERROR, "query failed")

        with pytest.raises(OrderNotSentError, match="could not retrieve order"):
            service.modify_order(
                order_id="123456",
                modify_order_op="NORMAL",
                price=1.0,
                trd_env="REAL",
                acc_id=123,
            )

    def test_the_existing_order_is_fetched_with_a_fresh_cache(self, mock_trade_ctx):
        """The value at risk depends on the broker's current order, not on a
        cached copy of it."""
        service = self._service(mock_trade_ctx, dict(self.OPEN_ORDER))

        service.modify_order(
            order_id="123456",
            modify_order_op="NORMAL",
            price=90.0,
            trd_env="REAL",
            acc_id=123,
        )

        kwargs = mock_trade_ctx.order_list_query.call_args.kwargs
        assert kwargs["order_id"] == "123456"
        assert kwargs["refresh_cache"] is True

    def test_an_absent_trigger_price_does_not_refuse_the_modification(
        self, mock_trade_ctx
    ):
        """A plain limit order has no trigger, and the SDK reports that as 'N/A'.

        Validating the sentinel as a number would refuse every modification of
        every limit order the gateway reports this way.
        """
        without_trigger = dict(self.OPEN_ORDER, aux_price="N/A")
        service = self._service(mock_trade_ctx, without_trigger)

        result = service.modify_order(
            order_id="123456",
            modify_order_op="NORMAL",
            price=90.0,
            trd_env="REAL",
            acc_id=123,
        )

        assert result["order_status"] == "MODIFIED"

    @pytest.mark.parametrize("op", ["CANCEL", "DISABLE", "DELETE"])
    def test_exposure_reducing_operations_skip_the_assessment(self, mock_trade_ctx, op):
        over_cap = dict(self.OPEN_ORDER, qty=100, price=500.0)
        service = self._service(mock_trade_ctx, over_cap)

        service.modify_order(
            order_id="123456", modify_order_op=op, trd_env="REAL", acc_id=123
        )

        mock_trade_ctx.modify_order.assert_called_once()
        mock_trade_ctx.order_list_query.assert_not_called()


class TestPlaceOrder:
    """Tests for place_order."""

    def test_place_order_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful order placement."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "SUBMITTED",
                    "code": "US.AAPL",
                    "qty": 100,
                    "price": 150.0,
                }
            ]
        )
        mock_trade_ctx.place_order.return_value = (0, df)

        result = trade_service_with_mock.place_order(
            code="US.AAPL",
            price=150.0,
            qty=100,
            trd_side="BUY",
            order_type="NORMAL",
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_id"] == "123456"
        mock_trade_ctx.place_order.assert_called_once()

    def test_place_order_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test order placement failure."""
        # Setup mock for smart account selection
        acc_df = pd.DataFrame(
            [{"acc_id": 123, "trd_env": "SIMULATE", "market_auth": ["US"]}]
        )
        mock_trade_ctx.get_acc_list.return_value = (0, acc_df)
        mock_trade_ctx.place_order.return_value = (-1, "Order rejected")

        # A gateway error code cannot be told apart from a timeout, so the
        # outcome is unknown rather than a rejection.
        with pytest.raises(OrderOutcomeUnknownError) as excinfo:
            trade_service_with_mock.place_order(
                code="US.AAPL",
                price=150.0,
                qty=100,
                trd_side="BUY",
                trd_env="SIMULATE",
            )

        message = str(excinfo.value)
        assert "may have been sent" in message
        assert "Order rejected" in message

    def test_place_order_no_context(self):
        """Test error when context not connected."""
        service = TradeService(policy=REAL_POLICY)

        with pytest.raises(OrderNotSentError) as excinfo:
            service.place_order(
                code="US.AAPL",
                price=150.0,
                qty=100,
                trd_side="BUY",
                trd_env="SIMULATE",
            )

        assert "no order was sent" in str(excinfo.value)
        assert "Trade context not connected" in str(excinfo.value)

    def test_place_order_with_time_in_force_gtc(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test order placement with GTC time in force."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "SUBMITTED",
                    "code": "US.AAPL",
                    "qty": 100,
                    "price": 150.0,
                    "time_in_force": "GTC",
                }
            ]
        )
        mock_trade_ctx.place_order.return_value = (0, df)

        result = trade_service_with_mock.place_order(
            code="US.AAPL",
            price=150.0,
            qty=100,
            trd_side="BUY",
            order_type="NORMAL",
            time_in_force="GTC",
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_id"] == "123456"
        mock_trade_ctx.place_order.assert_called_once()
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["time_in_force"] == "GTC"

    def test_place_order_with_time_in_force_day(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test order placement with DAY time in force (default)."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "SUBMITTED",
                    "code": "US.AAPL",
                    "qty": 100,
                    "price": 150.0,
                    "time_in_force": "DAY",
                }
            ]
        )
        mock_trade_ctx.place_order.return_value = (0, df)

        result = trade_service_with_mock.place_order(
            code="US.AAPL",
            price=150.0,
            qty=100,
            trd_side="BUY",
            order_type="NORMAL",
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_id"] == "123456"
        mock_trade_ctx.place_order.assert_called_once()
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        # Default should be DAY
        assert call_kwargs["time_in_force"] == "DAY"

    def test_place_order_with_special_limit_order_type(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test order placement with SPECIAL_LIMIT order type."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "SUBMITTED",
                    "code": "HK.00700",
                    "qty": 100,
                    "price": 300.0,
                }
            ]
        )
        mock_trade_ctx.place_order.return_value = (0, df)

        result = trade_service_with_mock.place_order(
            code="HK.00700",
            price=300.0,
            qty=100,
            trd_side="BUY",
            order_type="SPECIAL_LIMIT",
            time_in_force="DAY",
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_id"] == "123456"
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["order_type"] == "SPECIAL_LIMIT"


class TestModifyOrder:
    """Tests for modify_order."""

    def test_modify_order_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful order modification."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "MODIFIED",
                }
            ]
        )
        mock_trade_ctx.modify_order.return_value = (0, df)

        result = trade_service_with_mock.modify_order(
            order_id="123456",
            modify_order_op="NORMAL",
            qty=200,
            price=155.0,
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_status"] == "MODIFIED"
        mock_trade_ctx.modify_order.assert_called_once()

    def test_modify_order_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test order modification failure."""
        mock_trade_ctx.modify_order.return_value = (-1, "Modification failed")

        with pytest.raises(OrderOutcomeUnknownError, match="may have been sent"):
            trade_service_with_mock.modify_order(
                order_id="123456",
                modify_order_op="NORMAL",
                trd_env="SIMULATE",
                acc_id=123,
            )


class TestCancelOrder:
    """Tests for cancel_order."""

    def test_cancel_order_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful order cancellation."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123456",
                    "order_status": "CANCELLED",
                }
            ]
        )
        mock_trade_ctx.modify_order.return_value = (0, df)

        result = trade_service_with_mock.cancel_order(
            order_id="123456",
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_status"] == "CANCELLED"
        # Verify CANCEL operation is passed
        call_kwargs = mock_trade_ctx.modify_order.call_args.kwargs
        assert call_kwargs["modify_order_op"] == "CANCEL"
        assert call_kwargs["qty"] == 0
        assert call_kwargs["price"] == 0

    def test_cancel_order_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test order cancellation failure."""
        mock_trade_ctx.modify_order.return_value = (-1, "Cancel failed")

        with pytest.raises(OrderOutcomeUnknownError, match="may have been sent"):
            trade_service_with_mock.cancel_order(
                order_id="123456", trd_env="SIMULATE", acc_id=123
            )


class TestGetOrders:
    """Tests for get_orders."""

    def test_get_orders_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful order list retrieval."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "123",
                    "code": "US.AAPL",
                    "qty": 100,
                    "order_status": "SUBMITTED",
                },
                {
                    "order_id": "456",
                    "code": "US.TSLA",
                    "qty": 50,
                    "order_status": "FILLED",
                },
            ]
        )
        mock_trade_ctx.order_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_orders(trd_env="SIMULATE", acc_id=123)

        assert len(result) == 2
        assert result[0]["order_id"] == "123"

    def test_get_orders_with_code_filter(self, trade_service_with_mock, mock_trade_ctx):
        """Test order list with code filter."""
        df = pd.DataFrame([{"order_id": "123", "code": "US.AAPL"}])
        mock_trade_ctx.order_list_query.return_value = (0, df)

        trade_service_with_mock.get_orders(code="US.AAPL")

        call_kwargs = mock_trade_ctx.order_list_query.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"

    def test_get_orders_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test order list retrieval failure."""
        mock_trade_ctx.order_list_query.return_value = (-1, "Query failed")

        with pytest.raises(RuntimeError, match="order_list_query failed"):
            trade_service_with_mock.get_orders()


class TestGetDeals:
    """Tests for get_deals."""

    def test_get_deals_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful deal list retrieval."""
        df = pd.DataFrame(
            [
                {"deal_id": "D123", "code": "US.AAPL", "qty": 100, "price": 150.0},
            ]
        )
        mock_trade_ctx.deal_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_deals(trd_env="SIMULATE", acc_id=123)

        assert len(result) == 1
        assert result[0]["deal_id"] == "D123"

    def test_get_deals_with_code_filter(self, trade_service_with_mock, mock_trade_ctx):
        """Test deal list with code filter."""
        df = pd.DataFrame([{"deal_id": "D123", "code": "US.AAPL"}])
        mock_trade_ctx.deal_list_query.return_value = (0, df)

        trade_service_with_mock.get_deals(code="US.AAPL")

        call_kwargs = mock_trade_ctx.deal_list_query.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"

    def test_get_deals_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test deal list retrieval failure."""
        mock_trade_ctx.deal_list_query.return_value = (-1, "Query failed")

        with pytest.raises(RuntimeError, match="deal_list_query failed"):
            trade_service_with_mock.get_deals()


class TestGetHistoryOrders:
    """Tests for get_history_orders."""

    def test_get_history_orders_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful history order retrieval."""
        df = pd.DataFrame(
            [
                {"order_id": "H123", "code": "US.AAPL", "order_status": "FILLED"},
            ]
        )
        mock_trade_ctx.history_order_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_history_orders(
            trd_env="SIMULATE",
            acc_id=123,
            start="2025-01-01",
            end="2025-01-15",
        )

        assert len(result) == 1
        assert result[0]["order_id"] == "H123"

    def test_get_history_orders_with_filters(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test history orders with code and status filters."""
        from moomoo import OrderStatus

        df = pd.DataFrame([])
        mock_trade_ctx.history_order_list_query.return_value = (0, df)

        trade_service_with_mock.get_history_orders(
            code="US.AAPL",
            status_filter_list=["FILLED_ALL"],
        )

        call_kwargs = mock_trade_ctx.history_order_list_query.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"
        # Status strings should be converted to OrderStatus enum values
        assert call_kwargs["status_filter_list"] == [OrderStatus.FILLED_ALL]

    def test_get_history_orders_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test history order retrieval failure."""
        mock_trade_ctx.history_order_list_query.return_value = (-1, "Query failed")

        with pytest.raises(RuntimeError, match="history_order_list_query failed"):
            trade_service_with_mock.get_history_orders()


class TestGetHistoryDeals:
    """Tests for get_history_deals."""

    def test_get_history_deals_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful history deal retrieval."""
        df = pd.DataFrame(
            [
                {"deal_id": "HD123", "code": "US.AAPL", "qty": 100, "price": 150.0},
            ]
        )
        mock_trade_ctx.history_deal_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_history_deals(
            trd_env="SIMULATE",
            acc_id=123,
            start="2025-01-01",
            end="2025-01-15",
        )

        assert len(result) == 1
        assert result[0]["deal_id"] == "HD123"

    def test_get_history_deals_with_code_filter(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test history deals with code filter."""
        df = pd.DataFrame([])
        mock_trade_ctx.history_deal_list_query.return_value = (0, df)

        trade_service_with_mock.get_history_deals(code="US.AAPL")

        call_kwargs = mock_trade_ctx.history_deal_list_query.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"

    def test_get_history_deals_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test history deal retrieval failure."""
        mock_trade_ctx.history_deal_list_query.return_value = (-1, "Query failed")

        with pytest.raises(RuntimeError, match="history_deal_list_query failed"):
            trade_service_with_mock.get_history_deals()


class TestStatusFilterConversion:
    """Tests for status_filter_list string to enum conversion."""

    def test_get_orders_converts_status_filter_to_enum(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test that get_orders converts string status values to OrderStatus enums."""
        from moomoo import OrderStatus

        df = pd.DataFrame([{"order_id": "123", "code": "US.AAPL"}])
        mock_trade_ctx.order_list_query.return_value = (0, df)

        trade_service_with_mock.get_orders(
            status_filter_list=["SUBMITTED", "FILLED_ALL"],
        )

        call_kwargs = mock_trade_ctx.order_list_query.call_args.kwargs
        assert call_kwargs["status_filter_list"] == [
            OrderStatus.SUBMITTED,
            OrderStatus.FILLED_ALL,
        ]

    def test_get_orders_handles_empty_result(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test that get_orders returns empty list when no orders found."""
        # Test with None data
        mock_trade_ctx.order_list_query.return_value = (0, None)
        result = trade_service_with_mock.get_orders(code="HK.00100")
        assert result == []

        # Test with empty DataFrame
        df = pd.DataFrame([])
        mock_trade_ctx.order_list_query.return_value = (0, df)
        result = trade_service_with_mock.get_orders(code="HK.00100")
        assert result == []

    def test_get_history_orders_handles_empty_result(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test that get_history_orders returns empty list when no orders found."""
        # Test with None data
        mock_trade_ctx.history_order_list_query.return_value = (0, None)
        result = trade_service_with_mock.get_history_orders()
        assert result == []

        # Test with empty DataFrame
        df = pd.DataFrame([])
        mock_trade_ctx.history_order_list_query.return_value = (0, df)
        result = trade_service_with_mock.get_history_orders()
        assert result == []

    def test_get_orders_invalid_status_raises_error(self, trade_service_with_mock):
        """Test that invalid status string raises ValueError with helpful message."""
        with pytest.raises(ValueError, match="Invalid order status: 'INVALID_STATUS'"):
            trade_service_with_mock.get_orders(
                status_filter_list=["INVALID_STATUS"],
            )

    def test_status_filter_none_passes_empty_list(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test that None status_filter_list becomes empty list for SDK."""
        df = pd.DataFrame([{"order_id": "123", "code": "US.AAPL"}])
        mock_trade_ctx.order_list_query.return_value = (0, df)

        trade_service_with_mock.get_orders(status_filter_list=None)

        call_kwargs = mock_trade_ctx.order_list_query.call_args.kwargs
        assert call_kwargs["status_filter_list"] == []


class TestPositionStrategyView:
    """Tests for the option strategy view used to obtain closing position IDs."""

    def test_strategy_view_forwarded_to_sdk(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test show_option_strategy_view reaches position_list_query."""
        df = pd.DataFrame(
            [
                {
                    "code": "US.XYZ260101C100000/105000",
                    "position_id": 1111111111111111111,
                    "combo_id": 1111111111111111111,
                    "position_type": "COMBINED",
                    "qty": 1,
                }
            ]
        )
        mock_trade_ctx.position_list_query.return_value = (0, df)

        result = trade_service_with_mock.get_positions(
            trd_env="SIMULATE", acc_id=123, show_option_strategy_view=True
        )

        kwargs = mock_trade_ctx.position_list_query.call_args.kwargs
        assert kwargs["show_option_strategy_view"] is True
        assert result[0]["position_id"] == 1111111111111111111

    def test_strategy_view_defaults_off(self, trade_service_with_mock, mock_trade_ctx):
        """Test the flat view remains the default."""
        df = pd.DataFrame([{"code": "US.AAPL", "qty": 10}])
        mock_trade_ctx.position_list_query.return_value = (0, df)

        trade_service_with_mock.get_positions(trd_env="SIMULATE", acc_id=123)

        kwargs = mock_trade_ctx.position_list_query.call_args.kwargs
        assert kwargs["show_option_strategy_view"] is False


class TestPlaceComboOrder:
    """Tests for place_combo_order."""

    @staticmethod
    def _legs():
        """Two legs of a vertical call spread, sold as a package."""
        return [
            {"code": "US.XYZ260101C100000", "trd_side": "SELL", "qty_ratio": 1},
            {"code": "US.XYZ260101C105000", "trd_side": "BUY", "qty_ratio": 1},
        ]

    def test_rejects_missing_qty_ratio(self, trade_service_with_mock, mock_trade_ctx):
        """Test an omitted qty_ratio is refused rather than defaulted to 1.

        qty_ratio multiplies the order quantity, so assuming a value would
        silently submit a different strategy.
        """
        legs = self._legs()
        del legs[0]["qty_ratio"]

        with pytest.raises(OrderNotSentError, match="missing 'qty_ratio'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_position_id_passed_through_when_closing(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test position_id reaches the gateway, as required for closing orders."""
        df = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        mock_trade_ctx.place_combo_order.return_value = (0, df)
        legs = self._legs()
        legs[0]["position_id"] = 1111111111111111111
        legs[1]["position_id"] = "2222222222222222222"

        trade_service_with_mock.place_combo_order(
            combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
        )

        sent = mock_trade_ctx.place_combo_order.call_args.kwargs["combo_leg_list"]
        assert [leg.position_id for leg in sent] == [
            1111111111111111111,
            2222222222222222222,
        ]

    def test_position_id_omitted_when_opening(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test legs without position_id leave it unset rather than inventing one."""
        df = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        mock_trade_ctx.place_combo_order.return_value = (0, df)

        trade_service_with_mock.place_combo_order(
            combo_legs=self._legs(), price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
        )

        sent = mock_trade_ctx.place_combo_order.call_args.kwargs["combo_leg_list"]
        assert all(leg.position_id is None for leg in sent)

    def test_rejects_non_numeric_position_id(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test a malformed position_id is refused."""
        legs = self._legs()
        legs[0]["position_id"] = "not-an-id"

        with pytest.raises(OrderNotSentError, match="non-integer 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_boolean_position_id(self, trade_service_with_mock, mock_trade_ctx):
        """Test a boolean position_id is refused rather than coerced to 1."""
        legs = self._legs()
        legs[0]["position_id"] = True

        with pytest.raises(OrderNotSentError, match="boolean 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_float_position_id(self, trade_service_with_mock, mock_trade_ctx):
        """Test a float position_id is refused rather than truncated."""
        legs = self._legs()
        legs[0]["position_id"] = 123.75

        with pytest.raises(OrderNotSentError, match="non-integer 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_large_position_id_string_kept_exact(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test a decimal string ID survives transport without precision loss."""
        df = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        mock_trade_ctx.place_combo_order.return_value = (0, df)
        legs = self._legs()
        legs[0]["position_id"] = "9007199254740993"  # beyond float64 exact range
        legs[1]["position_id"] = 3333333333333333333

        trade_service_with_mock.place_combo_order(
            combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
        )

        sent = mock_trade_ctx.place_combo_order.call_args.kwargs["combo_leg_list"]
        assert [leg.position_id for leg in sent] == [
            9007199254740993,
            3333333333333333333,
        ]

    def test_place_combo_order_success(self, trade_service_with_mock, mock_trade_ctx):
        """Test successful combo order placement."""
        df = pd.DataFrame(
            [
                {
                    "order_id": "998877",
                    "order_status": "SUBMITTED",
                    "qty": 1,
                    "price": 2.5,
                }
            ]
        )
        mock_trade_ctx.place_combo_order.return_value = (0, df)

        result = trade_service_with_mock.place_combo_order(
            combo_legs=self._legs(),
            price=2.5,
            qty=1,
            trd_env="SIMULATE",
            acc_id=123,
        )

        assert result["order_id"] == "998877"
        mock_trade_ctx.place_combo_order.assert_called_once()

    def test_legs_mapped_to_combo_leg_objects(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test dict legs are converted to ComboLeg with the right attributes."""
        df = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        mock_trade_ctx.place_combo_order.return_value = (0, df)

        trade_service_with_mock.place_combo_order(
            combo_legs=self._legs(),
            price=2.5,
            qty=1,
            trd_env="SIMULATE",
            acc_id=123,
        )

        sent = mock_trade_ctx.place_combo_order.call_args.kwargs["combo_leg_list"]
        assert [leg.code for leg in sent] == [
            "US.XYZ260101C100000",
            "US.XYZ260101C105000",
        ]
        assert [leg.trd_side for leg in sent] == ["SELL", "BUY"]
        assert [leg.qty_ratio for leg in sent] == [1, 1]

    def test_rejects_single_leg(self, trade_service_with_mock, mock_trade_ctx):
        """Test a one-leg combo is refused and never reaches the gateway."""
        with pytest.raises(OrderNotSentError, match="at least two legs"):
            trade_service_with_mock.place_combo_order(
                combo_legs=self._legs()[:1],
                price=2.5,
                qty=1,
                trd_env="SIMULATE",
                acc_id=123,
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_invalid_trd_side(self, trade_service_with_mock, mock_trade_ctx):
        """Test an unsupported trade side is refused."""
        legs = self._legs()
        legs[0]["trd_side"] = "SHORT"

        with pytest.raises(OrderNotSentError, match="Invalid trd_side"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_mixed_markets(self, trade_service_with_mock, mock_trade_ctx):
        """Test legs from different markets are refused."""
        legs = self._legs()
        legs[1]["code"] = "HK.00700"

        with pytest.raises(OrderNotSentError, match="same market"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_non_positive_qty_ratio(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test a zero quantity ratio is refused."""
        legs = self._legs()
        legs[0]["qty_ratio"] = 0

        with pytest.raises(OrderNotSentError, match="qty_ratio"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_missing_code(self, trade_service_with_mock, mock_trade_ctx):
        """Test a leg without a code is refused."""
        legs = self._legs()
        legs[0]["code"] = ""

        with pytest.raises(OrderNotSentError, match="code"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, trd_env="SIMULATE", acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_place_combo_order_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test a gateway error code surfaces as an unknown outcome."""
        mock_trade_ctx.place_combo_order.return_value = (-1, "Combo rejected")

        with pytest.raises(OrderOutcomeUnknownError, match="may have been sent"):
            trade_service_with_mock.place_combo_order(
                combo_legs=self._legs(),
                price=2.5,
                qty=1,
                trd_env="SIMULATE",
                acc_id=123,
            )

    def test_place_combo_order_no_context(self):
        """Test error when context not connected."""
        service = TradeService(policy=REAL_POLICY)

        with pytest.raises(OrderNotSentError, match="Trade context not connected"):
            service.place_combo_order(
                combo_legs=self._legs(),
                price=2.5,
                qty=1,
                trd_env="SIMULATE",
                acc_id=123,
            )

    def test_resolves_account_from_leg_market(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test default acc_id is resolved using the market of the legs."""
        acc_df = pd.DataFrame(
            [{"acc_id": 456, "trd_env": "SIMULATE", "market_auth": ["US"]}]
        )
        mock_trade_ctx.get_acc_list.return_value = (0, acc_df)
        df = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        mock_trade_ctx.place_combo_order.return_value = (0, df)

        trade_service_with_mock.place_combo_order(
            combo_legs=self._legs(),
            price=2.5,
            qty=1,
            trd_env="SIMULATE",
        )

        assert mock_trade_ctx.place_combo_order.call_args.kwargs["acc_id"] == 456


class TestAutomaticAccountSelection:
    """Tests for automatic account resolution.

    Resolves account based on market and trading environment.
    """

    def test_get_market_from_code(self, trade_service_with_mock):
        assert trade_service_with_mock._get_market_from_code("US.AAPL") == "US"
        assert trade_service_with_mock._get_market_from_code("JP.8058") == "JP"
        assert trade_service_with_mock._get_market_from_code("HK.00700") == "HK"
        assert trade_service_with_mock._get_market_from_code("INVALID") is None

    def test_a_single_eligible_account_resolves(self, trade_service_with_mock):
        accounts = [
            {"acc_id": 1, "trd_env": "SIMULATE", "trdmarket_auth": ["HK", "US"]},
            {"acc_id": 2, "trd_env": "SIMULATE", "trdmarket_auth": ["JP"]},
            {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["JP"]},
        ]
        with patch.object(
            trade_service_with_mock, "get_accounts", return_value=accounts
        ):
            assert trade_service_with_mock._resolve_account("SIMULATE", "JP", 0) == 2
            assert trade_service_with_mock._resolve_account("SIMULATE", "US", 0) == 1
            assert trade_service_with_mock._resolve_account("REAL", "JP", 0) == 123

    def test_two_eligible_accounts_are_refused_not_chosen_between(
        self, trade_service_with_mock
    ):
        """The old behaviour took the first match, silently.

        Two accounts authorized for the same market can hold very different
        amounts of money, and acc_id='0' was the default.
        """
        accounts = [
            {"acc_id": 11112222, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            {"acc_id": 33334444, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
        ]
        with (
            patch.object(
                trade_service_with_mock, "get_accounts", return_value=accounts
            ),
            pytest.raises(ValueError) as excinfo,
        ):
            trade_service_with_mock._resolve_account("SIMULATE", "US", 0)

        message = str(excinfo.value)
        assert "2 SIMULATE accounts" in message
        assert "acc_id" in message

    def test_candidates_are_named_by_their_last_four_digits_only(
        self, trade_service_with_mock
    ):
        accounts = [
            {"acc_id": 11112222, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            {"acc_id": 33334444, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
        ]
        with (
            patch.object(
                trade_service_with_mock, "get_accounts", return_value=accounts
            ),
            pytest.raises(ValueError) as excinfo,
        ):
            trade_service_with_mock._resolve_account("SIMULATE", "US", 0)

        message = str(excinfo.value)
        assert "...2222" in message
        assert "...4444" in message
        assert "11112222" not in message
        assert "33334444" not in message

    def test_no_eligible_account_is_refused(self, trade_service_with_mock):
        accounts = [
            {"acc_id": 1, "trd_env": "SIMULATE", "trdmarket_auth": ["HK"]},
        ]
        with (
            patch.object(
                trade_service_with_mock, "get_accounts", return_value=accounts
            ),
            pytest.raises(ValueError, match="No SIMULATE account"),
        ):
            trade_service_with_mock._resolve_account("SIMULATE", "JP", 0)

    def test_no_account_in_the_environment_is_refused(self, trade_service_with_mock):
        accounts = [{"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["JP"]}]
        with (
            patch.object(
                trade_service_with_mock, "get_accounts", return_value=accounts
            ),
            pytest.raises(ValueError, match="No SIMULATE account"),
        ):
            trade_service_with_mock._resolve_account("SIMULATE", "JP", 0)

    def test_a_failing_account_list_is_refused(self, trade_service_with_mock):
        with (
            patch.object(
                trade_service_with_mock,
                "get_accounts",
                side_effect=RuntimeError("API error"),
            ),
            pytest.raises(ValueError, match="Could not retrieve the account list"),
        ):
            trade_service_with_mock._resolve_account("SIMULATE", "JP", 0)

    def test_modify_and_cancel_resolve_without_a_market(self, trade_service_with_mock):
        """They name an order, and the order already knows its instrument."""
        accounts = [
            {"acc_id": 7, "trd_env": "SIMULATE", "trdmarket_auth": ["HK"]},
        ]
        with patch.object(
            trade_service_with_mock, "get_accounts", return_value=accounts
        ):
            assert trade_service_with_mock._resolve_account("SIMULATE", None, 0) == 7

    def test_an_explicit_account_needs_no_gateway_call(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        assert trade_service_with_mock._resolve_account("SIMULATE", "US", 555) == 555
        mock_trade_ctx.get_acc_list.assert_not_called()

    def test_an_explicit_account_may_be_a_string(self, trade_service_with_mock):
        assert trade_service_with_mock._resolve_account("SIMULATE", "US", "555") == 555


class TestRealAccountAllowlistRouting:
    """REAL writes may only reach the accounts the deployment configured."""

    def test_an_unlisted_explicit_real_account_is_refused(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        with pytest.raises(ValueError, match="MOOMOO_REAL_ACC_IDS"):
            trade_service_with_mock._resolve_account("REAL", "US", 999)

        # Checked against configuration, so it costs no gateway call.
        mock_trade_ctx.get_acc_list.assert_not_called()

    def test_a_listed_explicit_real_account_is_used(self, trade_service_with_mock):
        assert trade_service_with_mock._resolve_account("REAL", "US", 123) == 123

    def test_an_unlisted_real_account_is_not_eligible_for_resolution(
        self, trade_service_with_mock
    ):
        """Two accounts authorized for US, but only one allowlisted."""
        accounts = [
            {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
            {"acc_id": 999, "trd_env": "REAL", "trdmarket_auth": ["US"]},
        ]
        with patch.object(
            trade_service_with_mock, "get_accounts", return_value=accounts
        ):
            assert trade_service_with_mock._resolve_account("REAL", "US", 0) == 123

    def test_real_mode_with_no_allowlist_refuses(self, mock_trade_ctx):
        service = TradeService(policy=TradingPolicy(TradingMode.REAL))
        service.trade_ctx = mock_trade_ctx

        with pytest.raises(ValueError, match="No REAL accounts are configured"):
            service._resolve_account("REAL", "US", 999)

    def test_simulate_is_unaffected_by_the_allowlist(self, trade_service_with_mock):
        """999 is not allowlisted, and SIMULATE does not care."""
        assert trade_service_with_mock._resolve_account("SIMULATE", "US", 999) == 999


class TestAutoSelectionThroughPlaceOrder:
    """The resolver, reached the way an order reaches it."""

    def test_place_order_auto_select_account(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        mock_accounts = [
            {"acc_id": 999, "trd_env": "SIMULATE", "trdmarket_auth": ["JP"]},
        ]
        mock_trade_ctx.place_order.return_value = (
            0,
            pd.DataFrame([{"order_id": "1", "code": "JP.8058"}]),
        )

        with patch.object(
            trade_service_with_mock, "get_accounts", return_value=mock_accounts
        ):
            trade_service_with_mock.place_order(
                code="JP.8058",
                price=1000,
                qty=100,
                trd_side="BUY",
                trd_env="SIMULATE",
                acc_id=0,
            )

            kwargs = mock_trade_ctx.place_order.call_args[1]
            assert kwargs["acc_id"] == 999
            assert kwargs["code"] == "JP.8058"

    def test_visible_hk_and_us_accounts_are_selected_by_order_market(self):
        ctx = MagicMock()
        ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "acc_id": 101,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["HK"],
                    },
                    {
                        "acc_id": 202,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["US"],
                    },
                ]
            ),
        )
        ctx.place_order.return_value = (
            RET_OK,
            pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}]),
        )
        service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
        service.trade_ctx = ctx

        service.place_order(
            code="US.AAPL",
            price=1.0,
            qty=1,
            trd_side="BUY",
            trd_env="SIMULATE",
        )
        service.place_order(
            code="HK.00700",
            price=1.0,
            qty=1,
            trd_side="BUY",
            trd_env="SIMULATE",
        )

        assert [call.kwargs["acc_id"] for call in ctx.place_order.call_args_list] == [
            202,
            101,
        ]

    @pytest.mark.parametrize("operation", ["modify", "cancel"])
    def test_modify_and_cancel_refuse_hk_us_account_ambiguity(self, operation):
        ctx = MagicMock()
        ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "acc_id": 101,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["HK"],
                    },
                    {
                        "acc_id": 202,
                        "trd_env": "SIMULATE",
                        "trdmarket_auth": ["US"],
                    },
                ]
            ),
        )
        service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
        service.trade_ctx = ctx

        with pytest.raises(OrderNotSentError) as excinfo:
            if operation == "modify":
                service.modify_order(
                    order_id="1",
                    modify_order_op="CANCEL",
                    trd_env="SIMULATE",
                )
            else:
                service.cancel_order(order_id="1", trd_env="SIMULATE")

        assert isinstance(excinfo.value.__cause__, ValueError)
        assert "2 SIMULATE accounts" in str(excinfo.value.__cause__)
        ctx.modify_order.assert_not_called()
