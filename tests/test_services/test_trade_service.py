"""Unit tests for TradeService."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy

# These tests exercise order writes, so they state that intent explicitly:
# a service constructed without a policy is read-only and refuses them.
REAL_POLICY = TradingPolicy(TradingMode.REAL)


@pytest.fixture
def mock_trade_ctx():
    """Create a mock OpenSecTradeContext."""
    return MagicMock()


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

        mock_ctx_class.assert_called_once_with(host="localhost", port=12345)
        assert service.trade_ctx is not None

    def test_close_clears_context(self, trade_service_with_mock, mock_trade_ctx):
        """Test close() closes and clears trade context."""
        trade_service_with_mock.close()

        mock_trade_ctx.close.assert_called_once()
        assert trade_service_with_mock.trade_ctx is None


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


class TestJitTradeUnlock:
    """Tests for _jit_trade_unlock context manager."""

    def test_jit_unlock_real_with_md5(
        self, trade_service_with_mock, mock_trade_ctx, monkeypatch
    ):
        """Test JIT unlock on REAL environment with password MD5."""
        monkeypatch.setenv("MOOMOO_TRADE_PASSWORD_MD5", "hash123")
        monkeypatch.delenv("MOOMOO_TRADE_PASSWORD", raising=False)
        mock_trade_ctx.unlock_trade.return_value = (0, None)

        with trade_service_with_mock._jit_trade_unlock(trd_env="REAL"):
            mock_trade_ctx.unlock_trade.assert_called_once_with(
                password=None, password_md5="hash123", is_unlock=True
            )

        # After exiting context, should have called lock (is_unlock=False)
        assert mock_trade_ctx.unlock_trade.call_count == 2
        mock_trade_ctx.unlock_trade.assert_called_with(is_unlock=False)

    def test_jit_unlock_always_relocks_on_exception(
        self, trade_service_with_mock, mock_trade_ctx, monkeypatch
    ):
        """Test JIT unlock guarantees relock even when order raises exception."""
        monkeypatch.setenv("MOOMOO_TRADE_PASSWORD_MD5", "hash123")
        mock_trade_ctx.unlock_trade.return_value = (0, None)

        with (
            pytest.raises(ValueError, match="Order exploded"),
            trade_service_with_mock._jit_trade_unlock(trd_env="REAL"),
        ):
            raise ValueError("Order exploded")

        # Must have re-locked
        assert mock_trade_ctx.unlock_trade.call_count == 2
        mock_trade_ctx.unlock_trade.assert_called_with(is_unlock=False)

    def test_jit_unlock_skipped_for_simulate(
        self, trade_service_with_mock, mock_trade_ctx, monkeypatch
    ):
        """Test JIT unlock is skipped for SIMULATE environment."""
        monkeypatch.setenv("MOOMOO_TRADE_PASSWORD_MD5", "hash123")

        with trade_service_with_mock._jit_trade_unlock(trd_env="SIMULATE"):
            pass

        mock_trade_ctx.unlock_trade.assert_not_called()

    def test_jit_unlock_noop_when_no_credentials(
        self, trade_service_with_mock, mock_trade_ctx, monkeypatch
    ):
        """Test JIT unlock is a no-op when credentials are not configured."""
        monkeypatch.delenv("MOOMOO_TRADE_PASSWORD", raising=False)
        monkeypatch.delenv("MOOMOO_TRADE_PASSWORD_MD5", raising=False)

        with trade_service_with_mock._jit_trade_unlock(trd_env="REAL"):
            pass

        mock_trade_ctx.unlock_trade.assert_not_called()


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

        with pytest.raises(RuntimeError, match="place_order failed"):
            trade_service_with_mock.place_order(
                code="US.AAPL",
                price=150.0,
                qty=100,
                trd_side="BUY",
            )

    def test_place_order_no_context(self):
        """Test error when context not connected."""
        service = TradeService(policy=REAL_POLICY)

        with pytest.raises(RuntimeError, match="Trade context not connected"):
            service.place_order(
                code="US.AAPL",
                price=150.0,
                qty=100,
                trd_side="BUY",
            )

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

        with pytest.raises(RuntimeError, match="modify_order failed"):
            trade_service_with_mock.modify_order(
                order_id="123456",
                modify_order_op="NORMAL",
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

        with pytest.raises(RuntimeError, match="cancel_order failed"):
            trade_service_with_mock.cancel_order(order_id="123456")


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

        with pytest.raises(ValueError, match="missing 'qty_ratio'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
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

        with pytest.raises(ValueError, match="non-integer 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_boolean_position_id(self, trade_service_with_mock, mock_trade_ctx):
        """Test a boolean position_id is refused rather than coerced to 1."""
        legs = self._legs()
        legs[0]["position_id"] = True

        with pytest.raises(ValueError, match="boolean 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_float_position_id(self, trade_service_with_mock, mock_trade_ctx):
        """Test a float position_id is refused rather than truncated."""
        legs = self._legs()
        legs[0]["position_id"] = 123.75

        with pytest.raises(ValueError, match="non-integer 'position_id'"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
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
        with pytest.raises(ValueError, match="at least two legs"):
            trade_service_with_mock.place_combo_order(
                combo_legs=self._legs()[:1],
                price=2.5,
                qty=1,
                acc_id=123,
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_invalid_trd_side(self, trade_service_with_mock, mock_trade_ctx):
        """Test an unsupported trade side is refused."""
        legs = self._legs()
        legs[0]["trd_side"] = "SHORT"

        with pytest.raises(ValueError, match="Invalid trd_side"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_mixed_markets(self, trade_service_with_mock, mock_trade_ctx):
        """Test legs from different markets are refused."""
        legs = self._legs()
        legs[1]["code"] = "HK.00700"

        with pytest.raises(ValueError, match="same market"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_non_positive_qty_ratio(
        self, trade_service_with_mock, mock_trade_ctx
    ):
        """Test a zero quantity ratio is refused."""
        legs = self._legs()
        legs[0]["qty_ratio"] = 0

        with pytest.raises(ValueError, match="qty_ratio"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_rejects_missing_code(self, trade_service_with_mock, mock_trade_ctx):
        """Test a leg without a code is refused."""
        legs = self._legs()
        legs[0]["code"] = ""

        with pytest.raises(ValueError, match="code"):
            trade_service_with_mock.place_combo_order(
                combo_legs=legs, price=2.5, qty=1, acc_id=123
            )

        mock_trade_ctx.place_combo_order.assert_not_called()

    def test_place_combo_order_error(self, trade_service_with_mock, mock_trade_ctx):
        """Test gateway rejection surfaces as RuntimeError."""
        mock_trade_ctx.place_combo_order.return_value = (-1, "Combo rejected")

        with pytest.raises(RuntimeError, match="place_combo_order failed"):
            trade_service_with_mock.place_combo_order(
                combo_legs=self._legs(),
                price=2.5,
                qty=1,
                acc_id=123,
            )

    def test_place_combo_order_no_context(self):
        """Test error when context not connected."""
        service = TradeService(policy=REAL_POLICY)

        with pytest.raises(RuntimeError, match="Trade context not connected"):
            service.place_combo_order(
                combo_legs=self._legs(),
                price=2.5,
                qty=1,
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
