"""Server-side trading policy.

The gateway's own unlock state answers "is this user allowed to trade?". This
policy answers a different question: "is this deployment allowed to issue
writes at all?". A read-only analysis deployment and a live-trading deployment
run the same code and can hold the same credentials, so the distinction has to
be configured explicitly rather than inferred from whether a password happens
to be present.

Enforcement lives in the service layer, before any gateway request, so it
applies to direct Python use of ``TradeService`` and not only to MCP calls.
"""

import os
from dataclasses import dataclass
from enum import Enum

ENV_VAR = "MOOMOO_TRADING_MODE"


class TradingMode(str, Enum):
    """What kind of writes a deployment is permitted to issue."""

    READ_ONLY = "READ_ONLY"
    SIMULATE = "SIMULATE"
    REAL = "REAL"


class TradingPolicyError(RuntimeError):
    """A write or unlock was refused by the configured trading mode."""


class TradingModeConfigError(ValueError):
    """`MOOMOO_TRADING_MODE` was set to a value that is not a trading mode."""


# Which order environments each mode may write to. READ_ONLY writes nowhere.
_WRITABLE_ENVIRONMENTS: dict[TradingMode, frozenset[str]] = {
    TradingMode.READ_ONLY: frozenset(),
    TradingMode.SIMULATE: frozenset({"SIMULATE"}),
    TradingMode.REAL: frozenset({"SIMULATE", "REAL"}),
}


@dataclass(frozen=True)
class TradingPolicy:
    """The configured trading mode and the checks that enforce it."""

    # Read-only by default, including for direct construction in scripts and
    # tests: a deployment that never states its intent should not be able to
    # send an order.
    mode: TradingMode = TradingMode.READ_ONLY

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "TradingPolicy":
        """Build a policy from ``MOOMOO_TRADING_MODE``.

        Args:
            environ: Environment mapping to read. Defaults to ``os.environ``.

        Returns:
            The configured policy, or a READ_ONLY policy when the variable is
            unset or empty.

        Raises:
            TradingModeConfigError: If the variable holds an unknown value. A
                misconfigured mode fails startup rather than falling back to a
                permissive one.
        """
        raw = (environ if environ is not None else os.environ).get(ENV_VAR)
        if raw is None or not raw.strip():
            return cls()

        candidate = raw.strip().upper()
        try:
            return cls(TradingMode(candidate))
        except ValueError as exc:
            valid = ", ".join(mode.value for mode in TradingMode)
            raise TradingModeConfigError(
                f"{ENV_VAR} is set to {raw!r}, which is not a trading mode. "
                f"Valid values: {valid}."
            ) from exc

    def check_write(self, operation: str, trd_env: str) -> None:
        """Authorize an order mutation in ``trd_env``, or refuse it.

        The requested environment is never rewritten to one the policy would
        allow: rerouting a REAL order into a simulated account would silently
        do something other than what the caller asked for.

        Args:
            operation: Name of the operation, used in the error message.
            trd_env: Requested trading environment, 'REAL' or 'SIMULATE'.

        Raises:
            TradingPolicyError: If the mode does not permit this write.
        """
        requested = str(trd_env).strip().upper()
        if requested in _WRITABLE_ENVIRONMENTS[self.mode]:
            return

        if self.mode is TradingMode.READ_ONLY:
            detail = (
                "This server is configured read-only, which blocks placing, "
                "modifying, and cancelling orders in every environment."
            )
        elif requested in {"REAL", "SIMULATE"}:
            detail = (
                f"This server is configured for {self.mode.value} trading, "
                f"which does not permit {requested} writes."
            )
        else:
            detail = (
                f"{requested!r} is not a recognized trading environment; "
                "use 'REAL' or 'SIMULATE'."
            )

        raise TradingPolicyError(
            f"{operation} is not permitted: {detail} Set {ENV_VAR} to the "
            "intended mode and restart the server to change this."
        )

    def check_unlock(self, operation: str = "unlock_trade") -> None:
        """Authorize unlocking trade, or refuse it.

        Only REAL mode may unlock. Holding a password does not promote a mode:
        credentials say who the operator is, not what this deployment is for.

        Raises:
            TradingPolicyError: If the mode does not permit unlocking.
        """
        if self.mode is TradingMode.REAL:
            return
        raise TradingPolicyError(
            f"{operation} is not permitted: this server is configured for "
            f"{self.mode.value}, and only REAL mode may unlock trading. A "
            f"configured trade password does not change the mode. Set {ENV_VAR} "
            "to REAL and restart the server to change this."
        )
