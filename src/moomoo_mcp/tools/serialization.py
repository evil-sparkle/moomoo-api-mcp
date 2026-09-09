"""Field-aware identifier serialization for the MCP response boundary.

Moomoo account, position, and combo identifiers are 64-bit values around 3e18.
A client that parses JSON numbers as IEEE-754 doubles — which a JavaScript-based
MCP client does — silently rounds anything above 2**53. The corruption is
undetectable downstream because the result is still a plausible integer, so a
request built from a rounded id targets a different account or position.
Validating the input on the way back cannot recover precision already lost in
transit; the value has to leave as a string in the first place.

Conversion happens here, at the serialization boundary, so in-process callers of
the services keep exact Python integers.
"""

import math
import numbers
from typing import Any

# Identifiers only. Quantities, prices, and balances stay numeric: they are used
# in arithmetic by clients, and their magnitudes are nowhere near 2**53.
IDENTIFIER_FIELDS = frozenset({"acc_id", "position_id", "combo_id"})


class IdentifierSerializationError(ValueError):
    """An identifier reached the boundary in a form that cannot be trusted."""


def _serialize_identifier(field: str, value: Any) -> Any:
    """Convert one identifier value, or refuse it."""
    if value is None:
        return None

    # bool is a subclass of int, so it must be rejected before the Integral
    # check: str(int(True)) is "1", a perfectly valid-looking account id.
    if isinstance(value, bool):
        raise IdentifierSerializationError(
            f"Field '{field}' has a boolean value {value!r}. Identifiers must be "
            "exact integers or decimal strings."
        )

    # numbers.Integral covers Python ints and the NumPy integers pandas produces.
    if isinstance(value, numbers.Integral):
        return str(int(value))

    if isinstance(value, str):
        # Already a string: pass it through untouched so a value that made the
        # trip once is not re-encoded or re-validated into a different shape.
        return value

    if isinstance(value, float):
        # pandas widens an integer column to float64 when a row is missing a
        # value, and represents that gap as NaN. That is a missing identifier,
        # not a corrupted one, so it is preserved as null.
        if math.isnan(value):
            return None
        raise IdentifierSerializationError(
            f"Field '{field}' has a floating-point value {value!r}. A float cannot "
            "represent a 64-bit identifier exactly, so the precision is already "
            "lost; emitting it as an id would hide that. Fetch the identifier "
            "again from a tool that returns it as a decimal string."
        )

    raise IdentifierSerializationError(
        f"Field '{field}' has an unsupported value of type {type(value).__name__}: "
        f"{value!r}. Identifiers must be exact integers or decimal strings."
    )


def serialize_identifiers(payload: Any) -> Any:
    """Return ``payload`` with every known identifier field as a decimal string.

    Dictionaries and lists are traversed recursively, so identifiers nested in a
    composed response — an account summary embedding its positions, for example —
    are covered as well as top-level rows.

    Missing fields stay missing, nulls stay null, existing strings are untouched,
    and no non-identifier field is modified. Source objects are never mutated:
    the caller's service result keeps its exact integers.

    Args:
        payload: Any JSON-shaped structure returned by a service.

    Returns:
        A converted copy of ``payload``.

    Raises:
        IdentifierSerializationError: If an identifier arrives as a boolean, a
            non-NaN float, or another type that cannot be an exact identifier.
    """
    if isinstance(payload, dict):
        return {
            key: (
                _serialize_identifier(key, value)
                if key in IDENTIFIER_FIELDS
                else serialize_identifiers(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        # Both become a JSON array on the wire, so a list is the honest result.
        # Rebuilding the original type would also break on a namedtuple, which
        # takes positional fields rather than one iterable.
        return [serialize_identifiers(item) for item in payload]
    return payload
