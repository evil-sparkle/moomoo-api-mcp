"""Independent checks for fail-closed paper configuration boundaries."""

import pytest

from moomoo_mcp.services.trading_policy import TradingModeConfigError
from moomoo_mcp.settings import load_settings


def paper_settings(**overrides: str) -> dict[str, str]:
    return {
        "MOOMOO_TRADING_MODE": "SIMULATE",
        "MOOMOO_TRADING_MARKET": "US",
        "MOOMOO_SIMULATED_ACC_IDS": "123456",
        "MOOMOO_JOURNAL_PATH": "/nonexistent-test-only/execution.db",
        **overrides,
    }


@pytest.mark.parametrize("value", ["0", "-1", "123456,", ",123456", "123456,,789"])
def test_paper_allowlist_refuses_placeholders_and_empty_members(value: str) -> None:
    with pytest.raises(TradingModeConfigError) as error:
        load_settings(paper_settings(MOOMOO_SIMULATED_ACC_IDS=value))
    assert "MOOMOO_SIMULATED_ACC_IDS" in str(error.value)


def test_operator_capability_cannot_equal_agent_credential() -> None:
    with pytest.raises(TradingModeConfigError) as error:
        load_settings(
            paper_settings(
                MCP_AUTH_TOKEN="same-test-only-token",
                MCP_OPERATOR_TOKEN="same-test-only-token",
            )
        )
    assert "MCP_OPERATOR_TOKEN" in str(error.value)


def test_read_only_ignores_journal_location_without_touching_disk(tmp_path) -> None:
    target = tmp_path / "uncreated" / "execution.db"
    settings = load_settings(
        {
            "MOOMOO_TRADING_MODE": "READ_ONLY",
            "MOOMOO_JOURNAL_PATH": str(target),
            "MOOMOO_CREATE_JOURNAL": "1",
        }
    )
    assert settings.journal_path is None
    assert not target.parent.exists()


@pytest.mark.parametrize(
    "variable,value",
    [
        ("MOOMOO_SIMULATED_ACC_IDS", ""),
        ("MOOMOO_JOURNAL_PATH", ""),
        ("MOOMOO_CREATE_JOURNAL", "yes"),
        ("MOOMOO_JOURNAL_LOCK_WAIT_MS", "0"),
        ("MOOMOO_JOURNAL_LOCK_WAIT_MS", "-1"),
        ("MOOMOO_JOURNAL_LOCK_WAIT_MS", "60001"),
        ("MOOMOO_JOURNAL_LOCK_WAIT_MS", "1.5"),
        ("MOOMOO_TRADING_MARKET", "HK"),
    ],
)
def test_invalid_paper_configuration_names_the_variable(variable, value):
    with pytest.raises(TradingModeConfigError) as refusal:
        load_settings(paper_settings(**{variable: value}))
    assert variable in str(refusal.value)


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_both_writable_modes_keep_the_same_paper_path(mode):
    settings = load_settings(
        paper_settings(
            MOOMOO_TRADING_MODE=mode,
            MOOMOO_REAL_ACC_IDS="987654",
        )
    )
    assert settings.journal_path == "/nonexistent-test-only/execution.db"
    assert settings.simulated_account_allowlist == frozenset({123456})
