"""Tests for auto-unlock trade at startup functionality."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moomoo_mcp.server import _auto_unlock_trade, app_lifespan


class TestAutoUnlockTrade:
    """Tests for _auto_unlock_trade function."""

    def test_auto_unlock_with_plain_password(self) -> None:
        """Test auto-unlock when MOOMOO_TRADE_PASSWORD is set."""
        mock_trade_service = MagicMock()
        mock_trade_service.get_accounts.return_value = [{"acc_id": 123}]

        with patch.dict(os.environ, {"MOOMOO_TRADE_PASSWORD": "test_password"}, clear=False):
            _auto_unlock_trade(mock_trade_service)

        # get_accounts must be called first to initialize account context
        mock_trade_service.get_accounts.assert_called_once()
        mock_trade_service.unlock_trade.assert_called_once_with(password="test_password")

    def test_auto_unlock_with_md5_password(self) -> None:
        """Test auto-unlock when only MOOMOO_TRADE_PASSWORD_MD5 is set."""
        mock_trade_service = MagicMock()

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

        # Create a clean environment without the password vars
        clean_env = {k: v for k, v in os.environ.items() 
                     if k not in ("MOOMOO_TRADE_PASSWORD", "MOOMOO_TRADE_PASSWORD_MD5")}
        
        with patch.dict(os.environ, clean_env, clear=True):
            _auto_unlock_trade(mock_trade_service)

        mock_trade_service.unlock_trade.assert_not_called()

    def test_graceful_failure_on_unlock_error(self) -> None:
        """Test that unlock failure is handled gracefully (no exception raised)."""
        mock_trade_service = MagicMock()
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
