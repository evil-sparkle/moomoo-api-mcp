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

``run_blocking`` is for starting blocking work; ``await_futures`` is for waiting
on work already running elsewhere. Using the former for the latter is what let a
saturated worker pool delay health past its own deadline.
"""

import asyncio
import contextlib
import functools
import inspect
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from typing import Any, TypeVar

import anyio.to_thread

from moomoo_mcp.services.order_errors import OrderNotSentError, not_sent_message
from moomoo_mcp.services.rate_limit import ProviderRateLimitError

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Await ``func(*args, **kwargs)`` on a worker thread.

    Concurrency is bounded by anyio's default thread limiter (40 workers), so a
    burst of tool calls queues instead of spawning unbounded threads. That queue
    is why health never passes through here: its probes run on dedicated workers
    and are awaited with ``await_futures``, so a saturated pool cannot delay it.

    Args:
        func: Blocking callable, typically a service method.
        *args: Positional arguments for ``func``.
        **kwargs: Keyword arguments for ``func``.

    Returns:
        Whatever ``func`` returns. Exceptions propagate unchanged, so a tool's
        error handling is unaffected by running off the loop.
    """
    owner = getattr(func, "__self__", None)
    prepare = getattr(type(owner), "prepare_admission", None)
    if owner is not None and prepare is not None:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        original_arguments = dict(bound.arguments)
        plan = await anyio.to_thread.run_sync(
            functools.partial(prepare, owner, func.__name__, bound.arguments)
        )
        try:
            return await owner.dispatcher.run_async(
                plan.requests, func, **bound.arguments
            )
        except ProviderRateLimitError as exc:
            if plan.write_operation:
                recover = getattr(type(owner), "recover_admission", None)
                if recover is not None:
                    result = await anyio.to_thread.run_sync(
                        functools.partial(
                            recover, owner, func.__name__, original_arguments
                        )
                    )
                    if result is not None:
                        return result
                raise OrderNotSentError(
                    not_sent_message(plan.write_operation, str(exc))
                ) from exc
            raise
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))


async def await_futures(futures: Iterable[Future[Any]], timeout: float) -> None:
    """Wait up to ``timeout`` seconds for worker futures, occupying no thread.

    ``run_blocking`` is the wrong tool for waiting on work that is already
    running elsewhere. It would take a slot in the shared limiter, so under load
    the wait would not even begin until 40 other queries had finished — a health
    check would blow its deadline queueing for permission to look at a result
    that was ready the whole time. Awaiting on the event loop costs nothing and
    starts immediately.

    Futures that are still pending when the timeout expires are left running:
    the SDK calls behind them are not cancellable, and the caller reports them
    as timed out.

    Args:
        futures: Worker futures to wait on concurrently.
        timeout: Seconds to wait. Non-positive returns without waiting.
    """
    pending = [f for f in futures if not f.done()]
    if not pending or timeout <= 0:
        return

    loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    outstanding = len(pending)
    detached = False

    def tick() -> None:
        nonlocal outstanding
        outstanding -= 1
        if outstanding == 0:
            finished.set()

    def on_done(_worker: Future[Any]) -> None:
        # Runs on the worker thread. Once this call has stopped waiting, or the
        # loop is gone, it must do nothing: an abandoned SDK call can finish
        # long after the server that started it has shut down, and waking a
        # closed loop from a stray thread only produces a spurious error.
        if detached or loop.is_closed():
            return
        # The loop can close between the check above and this call.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(tick)

    for worker in pending:
        worker.add_done_callback(on_done)
    try:
        await asyncio.wait_for(finished.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        detached = True
