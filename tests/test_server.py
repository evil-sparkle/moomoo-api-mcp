"""Tests for auto-unlock trade at startup functionality."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moomoo_mcp.server import _auto_unlock_trade, app_lifespan
from moomoo_mcp.services.trading_policy import (
    ENV_VAR,
    TradingMode,
    TradingModeConfigError,
    TradingPolicy,
)

REAL_POLICY = TradingPolicy(TradingMode.REAL)


class TestAutoUnlockTrade:
    """Tests for _auto_unlock_trade function."""

    def test_auto_unlock_with_plain_password(self) -> None:
        """Test auto-unlock when MOOMOO_TRADE_PASSWORD is set."""
        mock_trade_service = MagicMock()
        mock_trade_service.policy = REAL_POLICY
        mock_trade_service.get_accounts.return_value = [{"acc_id": 123}]

        with patch.dict(os.environ, {"MOOMOO_TRADE_PASSWORD": "test_password"}, clear=False):
            _auto_unlock_trade(mock_trade_service)

        # get_accounts must be called first to initialize account context
        mock_trade_service.get_accounts.assert_called_once()
        mock_trade_service.unlock_trade.assert_called_once_with(password="test_password")

    def test_auto_unlock_with_md5_password(self) -> None:
        """Test auto-unlock when only MOOMOO_TRADE_PASSWORD_MD5 is set."""
        mock_trade_service = MagicMock()
        mock_trade_service.policy = REAL_POLICY

        env_vars = {"MOOMOO_TRADE_PASSWORD_MD5": "md5_hash_value"}
        with patch.dict(os.environ, env_vars, clear=False):
            # Ensure plain password is not set
            if "MOOMOO_TRADE_PASSWORD" in os.environ:
                del os.environ["MOOMOO_TRADE_PASSWORD"]
            _auto_unlock_trade(mock_trade_service)

        mock_trade_service.unlock_trade.assert_called_once_with(password_md5="md5_hash_value")

    def test_plain_password_takes_precedence(self) -> None:
        """Test that plain password takes precedence over MD5."""
        mock_trade_service = MagicMock()
        mock_trade_service.policy = REAL_POLICY

        env_vars = {
            "MOOMOO_TRADE_PASSWORD": "plain_password",
            "MOOMOO_TRADE_PASSWORD_MD5": "md5_hash",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            _auto_unlock_trade(mock_trade_service)

        # Should use plain password, not MD5
        mock_trade_service.unlock_trade.assert_called_once_with(password="plain_password")

    def test_skip_unlock_when_no_env_vars(self) -> None:
        """Test that unlock is skipped when no env vars are set."""
        mock_trade_service = MagicMock()
        mock_trade_service.policy = REAL_POLICY

        # Create a clean environment without the password vars
        clean_env = {k: v for k, v in os.environ.items() 
                     if k not in ("MOOMOO_TRADE_PASSWORD", "MOOMOO_TRADE_PASSWORD_MD5")}
        
        with patch.dict(os.environ, clean_env, clear=True):
            _auto_unlock_trade(mock_trade_service)

        mock_trade_service.unlock_trade.assert_not_called()

    def test_graceful_failure_on_unlock_error(self) -> None:
        """Test that unlock failure is handled gracefully (no exception raised)."""
        mock_trade_service = MagicMock()
        mock_trade_service.policy = REAL_POLICY
        mock_trade_service.unlock_trade.side_effect = RuntimeError("Invalid password")

        with patch.dict(os.environ, {"MOOMOO_TRADE_PASSWORD": "wrong_password"}, clear=False):
            # Should not raise an exception
            _auto_unlock_trade(mock_trade_service)

        mock_trade_service.unlock_trade.assert_called_once()


class TestLifespanResilience:
    """MCP must start, and stay diagnosable, when OpenD is unreachable (R1)."""

    @staticmethod
    def _patched_services(quote_error=None, trade_error=None):
        """Patch the service classes used by app_lifespan with mocks."""
        moomoo_service = MagicMock()
        trade_service = MagicMock()

        def connect_quote():
            if quote_error:
                raise quote_error
            moomoo_service.quote_ctx = MagicMock()

        def connect_trade():
            if trade_error:
                trade_service.trade_ctx = None
                raise trade_error
            trade_service.trade_ctx = MagicMock()

        moomoo_service.quote_ctx = None
        trade_service.trade_ctx = None
        moomoo_service.connect.side_effect = connect_quote
        trade_service.connect.side_effect = connect_trade
        return moomoo_service, trade_service

    @pytest.mark.asyncio
    async def test_startup_survives_total_gateway_failure(self) -> None:
        moomoo_service, trade_service = self._patched_services(
            quote_error=OSError("connection refused"),
            trade_error=OSError("connection refused"),
        )

        with (
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", return_value=trade_service),
            patch("moomoo_mcp.server.MarketDataService") as market_data_cls,
        ):
            async with app_lifespan(MagicMock()) as app_context:
                assert app_context.moomoo_service is moomoo_service
                assert app_context.trade_service is trade_service

        # A failed trade connection must not trigger an unlock attempt.
        trade_service.unlock_trade.assert_not_called()
        market_data_cls.assert_called_once_with(quote_ctx=None)
        trade_service.close.assert_called_once()
        moomoo_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_startup_still_closes_the_quote_context(self) -> None:
        moomoo_service, trade_service = self._patched_services(
            trade_error=OSError("trade svr not ready")
        )

        with (
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", return_value=trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass

        assert moomoo_service.quote_ctx is not None
        moomoo_service.close.assert_called_once()
        trade_service.close.assert_called_once()


class TestStartupTradingMode:
    """MOOMOO_TRADING_MODE governs startup behavior (R3)."""

    @staticmethod
    def _mock_services():
        moomoo_service = MagicMock()
        trade_service = MagicMock()
        moomoo_service.connect.side_effect = lambda: setattr(
            moomoo_service, "quote_ctx", MagicMock()
        )
        trade_service.connect.side_effect = lambda: setattr(
            trade_service, "trade_ctx", MagicMock()
        )
        return moomoo_service, trade_service

    async def _start(self, env):
        moomoo_service, trade_service = self._mock_services()
        captured = {}

        def make_trade_service(**kwargs):
            captured.update(kwargs)
            trade_service.policy = kwargs["policy"]
            return trade_service

        with (
            patch.dict(os.environ, env, clear=True),
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", side_effect=make_trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass
        return captured, trade_service

    @pytest.mark.asyncio
    async def test_absent_mode_configures_read_only(self) -> None:
        captured, _ = await self._start({})

        assert captured["policy"].mode is TradingMode.READ_ONLY

    @pytest.mark.asyncio
    async def test_configured_mode_reaches_the_service(self) -> None:
        captured, _ = await self._start({ENV_VAR: "SIMULATE"})

        assert captured["policy"].mode is TradingMode.SIMULATE

    @pytest.mark.asyncio
    async def test_unknown_mode_fails_startup(self) -> None:
        with pytest.raises(TradingModeConfigError):
            await self._start({ENV_VAR: "PAPER"})

    @pytest.mark.asyncio
    async def test_password_does_not_unlock_in_read_only(self) -> None:
        _, trade_service = await self._start(
            {"MOOMOO_TRADE_PASSWORD": "hunter2"}
        )

        trade_service.unlock_trade.assert_not_called()
        assert trade_service.policy.mode is TradingMode.READ_ONLY

    @pytest.mark.asyncio
    async def test_password_does_not_unlock_in_simulate(self) -> None:
        _, trade_service = await self._start(
            {ENV_VAR: "SIMULATE", "MOOMOO_TRADE_PASSWORD": "hunter2"}
        )

        trade_service.unlock_trade.assert_not_called()
        assert trade_service.policy.mode is TradingMode.SIMULATE

    @pytest.mark.asyncio
    async def test_real_mode_with_password_unlocks(self) -> None:
        _, trade_service = await self._start(
            {ENV_VAR: "REAL", "MOOMOO_TRADE_PASSWORD": "hunter2"}
        )

        trade_service.unlock_trade.assert_called_once_with(password="hunter2")

    @pytest.mark.asyncio
    async def test_failed_unlock_does_not_change_the_mode(self) -> None:
        moomoo_service, trade_service = self._mock_services()
        trade_service.unlock_trade.side_effect = RuntimeError("bad password")

        def make_trade_service(**kwargs):
            trade_service.policy = kwargs["policy"]
            return trade_service

        env = {ENV_VAR: "REAL", "MOOMOO_TRADE_PASSWORD": "wrong"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", side_effect=make_trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass

        # Startup survives, the mode is untouched, and nothing is retried.
        assert trade_service.policy.mode is TradingMode.REAL
        assert trade_service.unlock_trade.call_count == 1
        trade_service.place_order.assert_not_called()
