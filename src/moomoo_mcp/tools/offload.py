"""Run blocking SDK work off the event loop.

Every service call in this package is a synchronous moomoo SDK query that can
block for seconds — longer when the gateway is slow or reconnecting. FastMCP
offers no protection here: an `async def` tool that calls one directly stalls
the event loop for the duration, and a plain `def` tool is no safer, because
FastMCP invokes sync tool functions inline on the loop as well
(`func_metadata.call_fn_with_arg_validation`).

A stalled loop cannot service *any* concurrent request, so one slow option-chain
query delays the health check an operator is running to find out why things are
slow. Routing SDK work through a worker thread keeps the loop free to answer.
"""

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import anyio.to_thread

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Await ``func(*args, **kwargs)`` on a worker thread.

    Concurrency is bounded by anyio's default thread limiter (40 workers), so a
    burst of tool calls queues instead of spawning unbounded threads. The health
    check's own probes run on their own dedicated single-slot workers, so they
    cannot be starved by SDK traffic saturating this pool.

    Args:
        func: Blocking callable, typically a service method.
        *args: Positional arguments for ``func``.
        **kwargs: Keyword arguments for ``func``.

    Returns:
        Whatever ``func`` returns. Exceptions propagate unchanged, so a tool's
        error handling is unaffected by running off the loop.
    """
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))
