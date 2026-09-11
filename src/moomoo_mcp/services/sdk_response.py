"""Narrowing for the SDK's ``(ret, data)`` responses.

Every moomoo SDK call returns ``(ret, data)``: ``data`` carries the payload when
``ret`` is ``RET_OK`` and an error message otherwise. The SDK types that pair
loosely — ``data`` is declared as a union of ``DataFrame`` with ``str``,
``tuple[str, str]`` and ``datetime`` — so ``ret`` is the only thing that decides
which shape actually arrived, and no type checker can follow that. Every
success-path ``data.to_dict(...)`` therefore reads as an error.

Passing the payload through :func:`as_frame` states the invariant instead. The
success path is unchanged; a response that breaks the invariant raises the same
``RuntimeError`` the failure path raises, naming the call, rather than surfacing
as an ``AttributeError`` from somewhere further down.
"""

from typing import Any

import pandas as pd


def as_frame(operation: str, data: Any) -> pd.DataFrame:
    """Return a successful response's payload as a DataFrame.

    Args:
        operation: SDK call name, used in the error message.
        data: The ``data`` half of the SDK's ``(ret, data)`` tuple, already
            checked to represent success.

    Returns:
        The payload as a DataFrame.

    Raises:
        RuntimeError: If the payload is not a DataFrame.
    """
    if not isinstance(data, pd.DataFrame):
        raise RuntimeError(
            f"{operation} reported success but returned "
            f"{type(data).__name__}, expected a DataFrame"
        )
    return data


def as_dict(operation: str, data: Any) -> dict:
    """Return a successful response's payload as a dict.

    Args:
        operation: SDK call name, used in the error message.
        data: The ``data`` half of the SDK's ``(ret, data)`` tuple, already
            checked to represent success.

    Returns:
        The payload as a dict.

    Raises:
        RuntimeError: If the payload is not a dict.
    """
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{operation} reported success but returned "
            f"{type(data).__name__}, expected a dict"
        )
    return data


def as_list(operation: str, data: Any) -> list:
    """Return a successful response's payload as a list.

    Args:
        operation: SDK call name, used in the error message.
        data: The ``data`` half of the SDK's ``(ret, data)`` tuple, already
            checked to represent success.

    Returns:
        The payload as a list.

    Raises:
        RuntimeError: If the payload is not a list.
    """
    if not isinstance(data, list):
        raise RuntimeError(
            f"{operation} reported success but returned "
            f"{type(data).__name__}, expected a list"
        )
    return data
