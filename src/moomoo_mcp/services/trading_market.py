"""Supported securities-market filters for trade account discovery."""

from moomoo import TrdMarket

DEFAULT_TRADING_MARKET = "NONE"

# This deliberately excludes non-securities markets the SDK may expose. Adding
# one here is an explicit product decision, not an automatic SDK upgrade effect.
TRADING_MARKET_FILTERS: dict[str, str] = {
    "NONE": TrdMarket.NONE,
    "HK": TrdMarket.HK,
    "US": TrdMarket.US,
    "CN": TrdMarket.CN,
    "HKCC": TrdMarket.HKCC,
    "SG": TrdMarket.SG,
    "AU": TrdMarket.AU,
    "JP": TrdMarket.JP,
    "MY": TrdMarket.MY,
    "CA": TrdMarket.CA,
}

VALID_TRADING_MARKETS = tuple(TRADING_MARKET_FILTERS)
