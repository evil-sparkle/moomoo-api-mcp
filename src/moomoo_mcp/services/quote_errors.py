"""Distinguish an explicit quote entitlement denial from other SDK failures."""

from collections.abc import Sequence
from typing import NoReturn

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.health import sanitize_error


class QuotePermissionDeniedError(RuntimeError):
    """The provider refused quote data for this request's current permissions."""

    def __init__(self, operation: str, message: str, codes: Sequence[str]) -> None:
        self.operation = operation
        self.provider_message = sanitize_error(message)
        self.requested_codes = list(codes)
        self.checked_at = utc_now_iso()
        # A batch can fail on one symbol. Identify only a symbol explicitly
        # named by the provider, without inferring rights for the rest.
        remainder = message.casefold().partition("no permission to get quotes for ")[2]
        denied = remainder.split()[0].rstrip(".,;:") if remainder.split() else ""
        self.denied_codes = [code for code in codes if code.casefold() == denied]
        super().__init__(f"{operation} failed: {self.provider_message}")


def raise_quote_error(
    operation: str, message: object, codes: Sequence[str]
) -> NoReturn:
    """Classify only explicit quote denials; preserve other provider errors."""
    if isinstance(message, str):
        lowered = " ".join(message.casefold().split())
        markers = (
            "no permission to get quotes",
            "no quote permission",
            "quote permission denied",
            "quotation permission denied",
            "无行情权限",
            "没有行情权限",
        )
        if any(marker in lowered for marker in markers):
            raise QuotePermissionDeniedError(operation, message, codes)
    raise RuntimeError(f"{operation} failed: {message}")
