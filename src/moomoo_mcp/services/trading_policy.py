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
ENV_MAX_ORDER_QTY = "MOOMOO_MAX_ORDER_QTY"
ENV_MAX_ORDER_NOTIONAL = "MOOMOO_MAX_ORDER_NOTIONAL"


class TradingMode(str, Enum):
    """What kind of writes a deployment is permitted to issue."""

    READ_ONLY = "READ_ONLY"
    SIMULATE = "SIMULATE"
    REAL = "REAL"


class TradingPolicyError(RuntimeError):
    """A write or unlock was refused by the configured trading mode or guardrails."""


class TradingModeConfigError(ValueError):
    """A trading configuration variable was set to an invalid value."""


# Which order environments each mode may write to. READ_ONLY writes nowhere.
_WRITABLE_ENVIRONMENTS: dict[TradingMode, frozenset[str]] = {
    TradingMode.READ_ONLY: frozenset(),
    TradingMode.SIMULATE: frozenset({"SIMULATE"}),
    TradingMode.REAL: frozenset({"SIMULATE", "REAL"}),
}


@dataclass(frozen=True)
class TradingPolicy:
    """The configured trading mode, safety limits, and checks that enforce them."""

    # Read-only by default, including for direct construction in scripts and
    # tests: a deployment that never states its intent should not be able to
    # send an order.
    mode: TradingMode = TradingMode.READ_ONLY
    max_order_qty: float | None = None
    max_order_notional: float | None = None

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "TradingPolicy":
        """Build a policy from environment variables.

        Reads ``MOOMOO_TRADING_MODE``, ``MOOMOO_MAX_ORDER_QTY``, and
        ``MOOMOO_MAX_ORDER_NOTIONAL``.

        Args:
            environ: Environment mapping to read. Defaults to ``os.environ``.

        Returns:
            The configured policy, defaulting to READ_ONLY mode.

        Raises:
            TradingModeConfigError: If any configuration variable holds an unknown
                or invalid value.
        """
        env = environ if environ is not None else os.environ

        # 1. Parse trading mode
        raw_mode = env.get(ENV_VAR)
        mode = TradingMode.READ_ONLY
        if raw_mode is not None and raw_mode.strip():
            candidate = raw_mode.strip().upper()
            try:
                mode = TradingMode(candidate)
            except ValueError as exc:
                valid = ", ".join(m.value for m in TradingMode)
                raise TradingModeConfigError(
                    f"{ENV_VAR} is set to {raw_mode!r}, which is not a trading mode. "
                    f"Valid values: {valid}."
                ) from exc

        # 2. Parse optional max order quantity limit
        raw_qty = env.get(ENV_MAX_ORDER_QTY)
        max_order_qty: float | None = None
        if raw_qty is not None and raw_qty.strip():
            try:
                max_order_qty = float(raw_qty.strip())
                if max_order_qty <= 0:
                    raise ValueError("Must be greater than 0")
            except ValueError as exc:
                raise TradingModeConfigError(
                    f"{ENV_MAX_ORDER_QTY} must be a positive number, got {raw_qty!r}."
                ) from exc

        # 3. Parse optional max order notional value limit
        raw_notional = env.get(ENV_MAX_ORDER_NOTIONAL)
        max_order_notional: float | None = None
        if raw_notional is not None and raw_notional.strip():
            try:
                max_order_notional = float(raw_notional.strip())
                if max_order_notional <= 0:
                    raise ValueError("Must be greater than 0")
            except ValueError as exc:
                raise TradingModeConfigError(
                    f"{ENV_MAX_ORDER_NOTIONAL} must be a positive number, "
                    f"got {raw_notional!r}."
                ) from exc

        return cls(
            mode=mode,
            max_order_qty=max_order_qty,
            max_order_notional=max_order_notional,
        )

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

    def check_order_limits(
        self,
        operation: str,
        qty: float | int,
        price: float | int | None = None,
    ) -> None:
        """Enforce safety limits on order quantity and estimated notional value.

        Args:
            operation: Name of the operation (e.g. 'place_order').
            qty: Quantity requested.
            price: Order price (if limit or known).

        Raises:
            TradingPolicyError: If the order exceeds configured safety limits.
        """
        if self.max_order_qty is not None and qty > self.max_order_qty:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: order quantity {qty} "
                f"exceeds configured maximum limit of {self.max_order_qty}."
            )

        if (
            self.max_order_notional is not None
            and price is not None
            and price > 0
            and qty > 0
        ):
            notional = float(qty) * float(price)
            if notional > self.max_order_notional:
                raise TradingPolicyError(
                    f"{operation} rejected by trading guardrail: estimated "
                    f"order notional ${notional:,.2f} exceeds configured maximum "
                    f"limit of ${self.max_order_notional:,.2f}."
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
