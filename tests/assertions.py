"""Explicit exception-message matching without regular expressions."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

import pytest

ExceptionType = TypeVar("ExceptionType", bound=BaseException)


@contextmanager
def raises_message(
    exception: type[ExceptionType],
    message: str,
) -> Iterator[pytest.ExceptionInfo[ExceptionType]]:
    with pytest.raises(exception) as error:
        yield error
    assert message in str(error.value)
