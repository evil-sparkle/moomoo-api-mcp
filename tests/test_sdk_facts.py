"""The design's SDK assumptions, re-checked on every test run.

`openspec/changes/harden-trading-safeguards/design.md` builds its decisions on a
list of facts about `moomoo-api`. Carrying them here means an SDK bump that moves
one of them fails CI with the fact's own name, rather than surfacing later as a
mispriced order or a gateway that never relocks.

`scripts/verify_sdk_facts.py` holds the checks; this only runs them.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_sdk_facts.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_sdk_facts = _load("verify_sdk_facts", _SCRIPT)
verify_gateway_facts = _load(
    "verify_gateway_facts", _SCRIPT.with_name("verify_gateway_facts.py")
)

FINDINGS = verify_sdk_facts.run_checks()


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f.fact)
def test_design_fact_still_holds(finding) -> None:
    assert finding.holds, (
        f"{finding.fact}\n  relied on by: {finding.decision}\n"
        f"  evidence: {finding.evidence}"
    )


def test_every_check_ran() -> None:
    """A check that silently disappears would pass by absence."""
    assert len(FINDINGS) == len(verify_sdk_facts.CHECKS)
    assert len(FINDINGS) >= 18


class TestGatewayScriptGuards:
    """The operator script must refuse before it opens a connection.

    The refusal is the whole safety story for the two phases that are not
    read-only, and it is easy to break by reordering `main`. These assert on
    `refusal` alone, so they never touch a gateway.
    """

    def _args(self, phase: str, **overrides) -> argparse.Namespace:
        parser = verify_gateway_facts.build_parser()
        args = parser.parse_args([phase, "--stock", "US.AAPL"])
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_paper_orders_need_authorization(self) -> None:
        why = verify_gateway_facts.refusal(self._args("paper-retention"))
        assert "--i-authorize-paper-orders" in why

    def test_authorized_paper_run_still_needs_an_order(self) -> None:
        why = verify_gateway_facts.refusal(
            self._args("paper-retention", i_authorize_paper_orders=True)
        )
        assert "--order-code" in why

    def test_real_gateway_lock_needs_authorization(self) -> None:
        why = verify_gateway_facts.refusal(self._args("real-reads"))
        assert "--i-authorize-real-gateway-lock" in why

    def test_authorized_real_run_still_needs_a_credential(self) -> None:
        why = verify_gateway_facts.refusal(
            self._args("real-reads", i_authorize_real_gateway_lock=True)
        )
        assert "--password-md5" in why

    def test_read_only_phase_needs_no_authorization(self) -> None:
        assert verify_gateway_facts.refusal(self._args("instruments")) == ""

    def test_no_flag_selects_a_real_trading_environment_for_orders(self) -> None:
        """There is no route from the command line to a REAL order.

        `--trd-env` reaches the positions phase, which only reads. The placement
        path takes no environment argument at all.
        """
        parser = verify_gateway_facts.build_parser()
        args = parser.parse_args(
            [
                "paper-retention",
                "--i-authorize-paper-orders",
                "--order-code",
                "US.AAPL",
                "--order-price",
                "1.00",
            ]
        )
        assert verify_gateway_facts.refusal(args) == ""
        source = _SCRIPT.with_name("verify_gateway_facts.py").read_text()
        body = source.split("def phase_paper_retention(")[1].split("\ndef ")[0]
        assert "trd_env = TrdEnv.SIMULATE" in body
        assert "TrdEnv.REAL" not in body
