"""Observed contract fields, not inferred economics or compatibility decisions."""

from datetime import date
from typing import Any

# Aliases describe provider columns, never substitutes between economic fields.
CONTRACT_FIELDS = {
    "is_option": ("option_valid",),
    "option_type": ("option_type",),
    "underlying": ("stock_owner",),
    "expiry": ("strike_time",),
    "strike": ("option_strike_price", "strike_price"),
    "classification": ("stock_type",),
    "lot_size": ("lot_size",),
    "currency": ("currency",),
    "exercise_style": ("option_area_type",),
    "settlement_mode": ("option_settlement_mode",),
    "settlement_cutoff": ("settlement_cutoff",),
    "deliverable_size": ("option_contract_size",),
    "deliverable_description": ("deliverable_description",),
    "monetary_multiplier": ("option_contract_multiplier",),
    "standard_status": ("option_standard_type",),
}


def normalize_contract_field(name: str, value: Any) -> Any:
    """Discard placeholders/invalid types and normalize only representation.

    No currency from a market prefix; no option flag from DRVT; no deliverable
    from lot size or standard status; no AM/PM-to-cutoff conversion.
    """
    from moomoo_mcp.services.instruments import normalize_quote

    if name in {"strike", "lot_size", "deliverable_size", "monetary_multiplier"}:
        return normalize_quote(value)
    if name == "is_option":
        return value if isinstance(value, bool) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.upper() in {"", "N/A", "NONE", "NULL", "UNKNOWN", "NAN", "--"}:
        return None
    if name == "expiry":
        try:
            return text if date.fromisoformat(text).isoformat() == text else None
        except ValueError:
            return None
    if name in {
        "option_type",
        "classification",
        "currency",
        "exercise_style",
        "settlement_mode",
        "standard_status",
    }:
        return text.upper()
    return text


def merge_contract_facts(
    sources: list[tuple[str, dict[str, Any], str | None]],
) -> dict[str, Any]:
    """Keep every usable source/alias observation, including disagreements."""
    facts = {}
    for name, keys in CONTRACT_FIELDS.items():
        observations = []
        for method, record, observed_at in sources:
            for key in keys:
                raw = record.get(key)
                value = normalize_contract_field(name, raw)
                if value is None:
                    continue
                observations.append(
                    {
                        "source_method": method,
                        "source_field": key,
                        "value": value,
                        "raw_value": raw,
                        "observed_at": observed_at,
                    }
                )
        conflict = any(
            item["value"] != observations[0]["value"] for item in observations[1:]
        )
        facts[name] = {
            "state": "CONFLICT" if conflict else "KNOWN" if observations else "UNKNOWN",
            "value": observations[0]["value"]
            if observations and not conflict
            else None,
            "evidence": observations,
        }
    return facts
