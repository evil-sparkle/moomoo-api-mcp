"""Configuration is parsed and validated once, before anything serves.

The point of every case here is the same: a variable that was set and rejected
must produce an error naming it, and must never fall back to a default. A
server that quietly substitutes a default for a value an operator deliberately
set is doing something other than what they asked for.
"""

import pytest

from moomoo_mcp.services.trading_policy import TradingMode, TradingModeConfigError
from moomoo_mcp.settings import (
    ENV_ALLOW_UNAUTHENTICATED_HTTP,
    ENV_AUTH_TOKEN,
    ENV_OPEND_PORT,
    ENV_SECURITY_FIRM,
    ENV_TRADING_MARKET,
    ENV_TRANSPORT,
    check_transport_authentication,
    load_settings,
)

REAL_ENV = {"MOOMOO_TRADING_MODE": "REAL", "MOOMOO_REAL_ACC_IDS": "456"}


class TestDefaults:
    def test_an_empty_environment_is_read_only_on_stdio(self):
        settings = load_settings({})

        assert settings.policy.mode is TradingMode.READ_ONLY
        assert settings.transport == "stdio"
        assert settings.opend_host == "127.0.0.1"
        assert settings.opend_port == 11111
        assert settings.security_firm is None
        assert settings.trading_market == "NONE"
        assert settings.has_trade_credential is False

    def test_host_and_port_are_read(self):
        settings = load_settings(
            {"MOOMOO_OPEND_HOST": "opend.internal", ENV_OPEND_PORT: "11112"}
        )

        assert settings.opend_host == "opend.internal"
        assert settings.opend_port == 11112


class TestInvalidValues:
    """Each rejection names the variable that caused it."""

    @pytest.mark.parametrize("value", ["not-a-port", "11111.5", "-1", "0", "70000"])
    def test_an_unusable_port_is_a_configuration_error(self, value):
        with pytest.raises(TradingModeConfigError, match=ENV_OPEND_PORT):
            load_settings({ENV_OPEND_PORT: value})

    def test_an_unknown_transport_is_a_configuration_error(self):
        with pytest.raises(TradingModeConfigError, match=ENV_TRANSPORT):
            load_settings({ENV_TRANSPORT: "carrier-pigeon"})

    def test_an_unknown_mode_is_a_configuration_error(self):
        with pytest.raises(TradingModeConfigError, match="MOOMOO_TRADING_MODE"):
            load_settings({"MOOMOO_TRADING_MODE": "YOLO"})

    def test_real_mode_without_an_allowlist_is_a_configuration_error(self):
        with pytest.raises(TradingModeConfigError, match="MOOMOO_REAL_ACC_IDS"):
            load_settings({"MOOMOO_TRADING_MODE": "REAL"})

    def test_a_legacy_only_notional_cap_is_a_configuration_error(self):
        with pytest.raises(
            TradingModeConfigError, match="MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY"
        ):
            load_settings({"MOOMOO_MAX_ORDER_NOTIONAL": "25000"})


class TestSecurityFirm:
    def test_a_known_firm_is_accepted_and_upper_cased(self):
        settings = load_settings({ENV_SECURITY_FIRM: "futuinc"})
        assert settings.security_firm == "FUTUINC"

    def test_an_unknown_firm_is_a_configuration_error(self):
        """It used to be dropped silently, routing through the gateway default."""
        with pytest.raises(TradingModeConfigError) as excinfo:
            load_settings({ENV_SECURITY_FIRM: "NOTAFIRM"})

        message = str(excinfo.value)
        assert ENV_SECURITY_FIRM in message
        assert "FUTUINC" in message

    def test_the_not_applicable_placeholder_is_not_a_firm(self):
        """SecurityFirm.NONE means "not applicable", not "this firm"."""
        with pytest.raises(TradingModeConfigError) as excinfo:
            load_settings({ENV_SECURITY_FIRM: "NONE"})

        assert "NONE" not in str(excinfo.value).split("Valid values:")[1]

    def test_an_unset_firm_stays_unset(self):
        assert load_settings({ENV_SECURITY_FIRM: "  "}).security_firm is None


class TestTradingMarket:
    @pytest.mark.parametrize(
        "market",
        ["NONE", "HK", "US", "CN", "HKCC", "SG", "AU", "JP", "MY", "CA"],
    )
    def test_supported_market_is_accepted(self, market):
        assert load_settings({ENV_TRADING_MARKET: market}).trading_market == market

    @pytest.mark.parametrize("value", [" none ", " hk ", "Us"])
    def test_market_is_trimmed_and_uppercased(self, value):
        assert (
            load_settings({ENV_TRADING_MARKET: value}).trading_market
            == value.strip().upper()
        )

    @pytest.mark.parametrize("value", ["USA", "FUTURES", "HKFUND"])
    def test_unknown_market_is_a_configuration_error(self, value):
        with pytest.raises(TradingModeConfigError) as excinfo:
            load_settings({ENV_TRADING_MARKET: value})

        message = str(excinfo.value)
        assert ENV_TRADING_MARKET in message
        assert "NONE, HK, US, CN, HKCC, SG, AU, JP, MY, CA" in message

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_or_blank_market_defaults_to_none(self, value):
        env = {} if value is None else {ENV_TRADING_MARKET: value}
        assert load_settings(env).trading_market == "NONE"


class TestCredentialPrecedence:
    def test_plain_text_wins_when_both_are_set(self):
        settings = load_settings(
            {
                "MOOMOO_TRADE_PASSWORD": "plain",
                "MOOMOO_TRADE_PASSWORD_MD5": "0123456789abcdef",
            }
        )

        assert settings.trade_password == "plain"
        assert settings.trade_password_md5 is None

    def test_the_hash_is_used_when_it_is_the_only_one(self):
        settings = load_settings({"MOOMOO_TRADE_PASSWORD_MD5": "0123456789abcdef"})

        assert settings.trade_password is None
        assert settings.trade_password_md5 == "0123456789abcdef"

    def test_a_blank_credential_is_no_credential(self):
        settings = load_settings(
            {"MOOMOO_TRADE_PASSWORD": "   ", "MOOMOO_TRADE_PASSWORD_MD5": ""}
        )
        assert settings.has_trade_credential is False


class TestLockAtRest:
    """Which deployments assert a lock on connect and reconnect."""

    def test_read_only_locks(self):
        assert load_settings({}).locks_gateway_at_rest is True

    def test_real_with_a_credential_locks(self):
        settings = load_settings({**REAL_ENV, "MOOMOO_TRADE_PASSWORD": "pw"})
        assert settings.locks_gateway_at_rest is True

    def test_real_without_a_credential_does_not_lock(self):
        """It could not unlock again, so locking would strand the operator."""
        assert load_settings(REAL_ENV).locks_gateway_at_rest is False

    def test_simulate_does_not_lock(self):
        settings = load_settings({"MOOMOO_TRADING_MODE": "SIMULATE"})
        assert settings.locks_gateway_at_rest is False


class TestTransportAuthentication:
    """An HTTP endpoint is never unauthenticated outside the explicit opt-out."""

    @pytest.mark.parametrize("transport", ["sse", "streamable-http"])
    def test_http_without_a_token_refuses_to_start(self, transport):
        settings = load_settings({ENV_TRANSPORT: transport})

        with pytest.raises(TradingModeConfigError) as excinfo:
            check_transport_authentication(settings)

        assert ENV_AUTH_TOKEN in str(excinfo.value)

    @pytest.mark.parametrize("transport", ["sse", "streamable-http"])
    def test_http_with_a_token_starts(self, transport):
        settings = load_settings({ENV_TRANSPORT: transport, ENV_AUTH_TOKEN: "secret"})
        check_transport_authentication(settings)

    def test_the_opt_out_is_honoured_in_read_only(self, caplog):
        settings = load_settings(
            {
                ENV_TRANSPORT: "streamable-http",
                ENV_ALLOW_UNAUTHENTICATED_HTTP: "1",
                "MOOMOO_TRADING_MODE": "READ_ONLY",
            }
        )

        with caplog.at_level("WARNING"):
            check_transport_authentication(settings)

        assert "without authentication" in caplog.text

    @pytest.mark.parametrize("env", [REAL_ENV, {"MOOMOO_TRADING_MODE": "SIMULATE"}])
    def test_the_opt_out_is_refused_outside_read_only(self, env):
        """A mode that can write must never serve an unauthenticated endpoint."""
        settings = load_settings(
            {
                **env,
                ENV_TRANSPORT: "streamable-http",
                ENV_ALLOW_UNAUTHENTICATED_HTTP: "1",
            }
        )

        with pytest.raises(TradingModeConfigError) as excinfo:
            check_transport_authentication(settings)

        assert "READ_ONLY" in str(excinfo.value)

    def test_the_opt_out_needs_exactly_one(self):
        """Anything other than "1" is not an opt-out."""
        settings = load_settings(
            {
                ENV_TRANSPORT: "streamable-http",
                ENV_ALLOW_UNAUTHENTICATED_HTTP: "true",
            }
        )

        with pytest.raises(TradingModeConfigError):
            check_transport_authentication(settings)

    def test_stdio_needs_no_token(self):
        check_transport_authentication(load_settings({ENV_TRANSPORT: "stdio"}))

    def test_stdio_needs_no_token_in_real_mode(self):
        check_transport_authentication(
            load_settings({**REAL_ENV, ENV_TRANSPORT: "stdio"})
        )
