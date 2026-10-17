"""Follow synthetic .env settings through Compose into the actual server policy."""

import json
import subprocess
from unittest.mock import patch

import pytest

from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    LegFacts,
    OrderFacts,
    TradingModeConfigError,
    TradingPolicyError,
)
from moomoo_mcp.settings import load_settings
from scripts import deploy_verify
from tests.compose_helpers import PROD_ENV, SERVICE, production_wrapper, render_compose

CONTROLS_PATH = "/var/lib/moomoo/trading-controls/controls.sqlite3"


def trading_environment(mode):
    values = {"MOOMOO_TRADING_MODE": mode, "MCP_AUTH_TOKEN": "synthetic-mcp-token"}
    if mode == "SIMULATE":
        values["MOOMOO_SIMULATED_ACC_IDS"] = "123"
    else:
        values["MOOMOO_REAL_ACC_IDS"] = "456"
    return values


def journal_overlay(mode):
    return (
        "docker-compose.paper.yml" if mode == "SIMULATE" else "docker-compose.real.yml"
    )


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
@pytest.mark.parametrize("production", [False, True])
def test_dotenv_controls_reach_policy_and_keep_storage_separate(
    tmp_path, mode, production
):
    values = trading_environment(mode) | {
        "MOOMOO_SYMBOL_ALLOWLIST": "US.AAPL,US.MSFT",
        "MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:1000",
        "MOOMOO_REQUIRE_TRADE_APPROVAL": "1",
        "MOOMOO_OPERATOR_AUTH_TOKEN": "synthetic-separate-operator-token",
        "MOOMOO_OPERATOR_ID": "test-operator",
        "MOOMOO_CREATE_TRADING_CONTROLS": "1",
        "MOOMOO_REPORTING_CURRENCY": "SGD",
    }
    settings_file = tmp_path / "synthetic.env"
    settings_file.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items())
    )
    overlays = ["docker-compose.yml"]
    if production:
        overlays.append("docker-compose.prod.yml")
    overlays += [journal_overlay(mode), "docker-compose.controls.yml"]
    service = render_compose(*overlays, env=PROD_ENV, env_file=settings_file)[
        "services"
    ][SERVICE]
    settings = load_settings(service["environment"])
    assert settings.policy.require_approval is True
    assert settings.policy.symbol_allowlist == frozenset({"US.AAPL", "US.MSFT"})
    assert settings.policy.max_daily_notional == {"USD": 1000}
    assert settings.operator_auth_token == values["MOOMOO_OPERATOR_AUTH_TOKEN"]
    assert settings.operator_id == "test-operator"
    assert settings.reporting_currency == "SGD"
    assert settings.controls_path == CONTROLS_PATH
    assert settings.create_controls is True
    mounts = {mount["target"]: mount for mount in service["volumes"]}
    assert (
        mounts["/var/lib/moomoo/trading-controls"]["source"] == "trading-controls-data"
    )
    assert mounts["/var/lib/moomoo/trading-controls"]["type"] == "volume"
    assert settings.controls_path not in {
        settings.journal_path,
        settings.real_journal_path,
    }


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_unset_controls_remain_disabled_and_symbol_scope_needs_no_store(mode):
    service = render_compose(
        "docker-compose.yml", journal_overlay(mode), env=trading_environment(mode)
    )["services"][SERVICE]
    settings = load_settings(service["environment"])
    assert not settings.policy.require_approval
    assert not settings.policy.max_daily_notional
    assert not settings.policy.symbol_allowlist
    assert settings.controls_path is None
    service = render_compose(
        "docker-compose.yml",
        journal_overlay(mode),
        env=trading_environment(mode) | {"MOOMOO_SYMBOL_ALLOWLIST": "US.AAPL"},
    )["services"][SERVICE]
    settings = load_settings(service["environment"])
    assert settings.policy.symbol_allowlist == frozenset({"US.AAPL"})
    assert settings.controls_path is None


@pytest.mark.parametrize(
    "variable", ["MOOMOO_SYMBOL_ALLOWLIST", "MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY"]
)
def test_explicitly_empty_control_is_rejected_after_compose(variable):
    service = render_compose("docker-compose.yml", env={variable: ""})["services"][
        SERVICE
    ]
    with pytest.raises(TradingModeConfigError) as failure:
        load_settings(service["environment"])
    assert variable in str(failure.value)


def test_configured_budget_without_storage_fails_instead_of_being_ignored():
    service = render_compose(
        "docker-compose.yml",
        "docker-compose.real.yml",
        env=trading_environment("REAL")
        | {"MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:1000"},
    )["services"][SERVICE]
    with pytest.raises(TradingModeConfigError) as failure:
        load_settings(service["environment"])
    assert "MOOMOO_TRADING_CONTROLS_PATH" in str(failure.value)


def test_operator_credential_is_not_forwarded_to_the_chatgpt_tunnel():
    values = trading_environment("SIMULATE") | {
        "MOOMOO_REQUIRE_TRADE_APPROVAL": "1",
        "MOOMOO_OPERATOR_AUTH_TOKEN": "synthetic-separate-operator-token",
        "MOOMOO_OPERATOR_ID": "test-operator",
        "CHATGPT_TUNNEL_IMAGE": "sha256:" + "a" * 64,
        "CHATGPT_TUNNEL_API_KEY": "synthetic-tunnel-key",
        "CHATGPT_TUNNEL_ID": "tunnel_synthetic",
    }
    services = render_compose(
        "docker-compose.yml",
        "docker-compose.paper.yml",
        "docker-compose.controls.yml",
        "docker-compose.chatgpt.yml",
        env=values,
    )["services"]
    settings = load_settings(services[SERVICE]["environment"])
    assert settings.policy.require_approval
    assert settings.operator_auth_token == values["MOOMOO_OPERATOR_AUTH_TOKEN"]
    tunnel = services["chatgpt-tunnel"]
    assert "MOOMOO_OPERATOR_AUTH_TOKEN" not in tunnel["environment"]
    assert settings.operator_auth_token not in tunnel["environment"].values()
    assert not tunnel.get("volumes")


@pytest.mark.parametrize(
    "configured",
    [
        {"MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:1000"},
        {"MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": ""},
        {"MOOMOO_REQUIRE_TRADE_APPROVAL": "1"},
        {"MOOMOO_OPERATOR_AUTH_TOKEN": "synthetic-operator-token"},
        {"MOOMOO_TRADING_CONTROLS_PATH": CONTROLS_PATH},
        {"MOOMOO_CREATE_TRADING_CONTROLS": "1"},
    ],
)
@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_production_selects_controls_storage_from_resolved_settings(mode, configured):
    base = render_compose(
        "docker-compose.yml", env=trading_environment(mode) | configured
    )
    with patch.object(deploy_verify, "resolve_services", return_value=base["services"]):
        overlays = deploy_verify.execution_overlays()
    assert overlays == [journal_overlay(mode), "docker-compose.controls.yml"]


@pytest.mark.parametrize(
    "tunnel,operation",
    [(False, "up"), (True, "up"), (True, "stop"), (True, "rm")],
)
def test_production_wrapper_keeps_controls_and_permits_tunnel_removal(
    tmp_path, tunnel, operation
):
    if tunnel:
        (tmp_path / ".chatgpt-deploy.json").write_text(
            json.dumps({"version": 2, "image": "sha256:" + "a" * 64, "project": "test"})
        )
    model = {
        "services": {
            SERVICE: {
                "environment": trading_environment("SIMULATE")
                | {"MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:1000"}
            }
        }
    }
    scripts, environment = production_wrapper(tmp_path, model["services"])
    command = ["up", "-d"] if operation == "up" else [operation, "chatgpt-tunnel"]
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), *command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments, _ = json.loads((tmp_path / "docker-call.json").read_text())
    assert arguments.count("docker-compose.paper.yml") == 1
    assert ("docker-compose.controls.yml" in arguments) is (operation == "up")
    assert ("docker-compose.chatgpt.yml" in arguments) is tunnel
    assert arguments[-2:] == command
    assert (tmp_path / "config-seen").exists() is (operation == "up")


def test_local_build_arguments_use_dotenv_overrides_and_preserve_dockerfile_defaults(
    tmp_path,
):
    default = render_compose("docker-compose.yml")["services"][SERVICE]
    assert not default["build"].get("args")
    arguments = {
        "OPEND_VERSION": "synthetic-version",
        "OPEND_TAG": "synthetic-tag",
        "OPEND_URL": "https://example.invalid/synthetic-release.tar.gz",
        "OPEND_SHA256": "a" * 64,
    }
    settings_file = tmp_path / "synthetic-build.env"
    settings_file.write_text(
        "".join(f"{name}={value}\n" for name, value in arguments.items())
    )
    service = render_compose("docker-compose.yml", env_file=settings_file)["services"][
        SERVICE
    ]
    assert service["build"]["args"] == arguments


def order(code, price, *, classification="STOCK", multiplier=1):
    return OrderFacts(
        order_type="NORMAL",
        trd_side="BUY",
        qty=1,
        price=price,
        legs=[
            LegFacts(
                InstrumentFacts(
                    code=code,
                    classification=classification,
                    monetary_multiplier=multiplier,
                )
            )
        ],
    )


def test_compose_real_defaults_accept_small_us_orders_and_count_option_multipliers():
    service = render_compose(
        "docker-compose.yml", "docker-compose.real.yml", env=trading_environment("REAL")
    )["services"][SERVICE]
    policy = load_settings(service["environment"]).policy
    assert policy.max_order_qty is None
    assert policy.max_order_notional == {"USD": 500}
    policy.assess_order("place_order", order("US.AAPL", 100))
    for facts in (
        order("US.AAPL", 600),
        order("US.SYNTHETICOPTION", 6, classification="DRVT", multiplier=100),
    ):
        with pytest.raises(TradingPolicyError) as refusal:
            policy.assess_order("place_order", facts)
        assert "600.00 USD" in str(refusal.value)


def test_non_us_caps_and_reporting_currency_do_not_invent_instrument_currency():
    service = render_compose(
        "docker-compose.yml",
        "docker-compose.real.yml",
        env=trading_environment("REAL")
        | {
            "MOOMOO_REPORTING_CURRENCY": "SGD",
            "MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": "USD:1500,HKD:8000,SGD:1000",
        },
    )["services"][SERVICE]
    settings = load_settings(service["environment"])
    assert settings.reporting_currency == "SGD"
    with pytest.raises(TradingPolicyError) as refusal:
        settings.policy.assess_order("place_order", order("HK.00700", 1))
    assert "cannot establish the currency" in str(refusal.value)
    assert "Adding a currency cap" in str(refusal.value)
