"""Run browser work without breaking the MCP server's event loop.

The MCP server runs on **anyio**, and anyio's event loop refuses
`asyncio.create_subprocess_exec`. Playwright spawns its driver process that way, so
calling it from inside an MCP tool makes anyio cancel the subprocess wait — which
tears down the whole stdio server and closes the client connection.

Observed live on 2026-11-22: Claude Code called `muse_status(selftest=true)`, the
process exited, and the session ended with no result. Reproduced with a raw MCP
client in under a second.

Why a thread alone is not enough: `loop.run_in_executor` keeps the work attached to
the caller's cancel scope, so anyio still cancels it. The work therefore runs on a
**plain `threading.Thread`** with its own `asyncio.run`, and the caller waits on a
`concurrent.futures.Future` bridged back with `asyncio.wrap_future`. Nothing in the
browser path can then observe anyio's cancellation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine
from typing import TypeVar

T = TypeVar("T")


def _run_detached(
    work: Callable[[], Coroutine[object, object, T]],
    future: concurrent.futures.Future[T],
) -> None:
    """Thread body: a private event loop, isolated from anyio's cancel scopes."""
    try:
        future.set_result(asyncio.run(work()))
    except BaseException as exc:  # propagate to the awaiting coroutine
        future.set_exception(exc)


async def run_off_loop(
    work: Callable[[], Coroutine[object, object, T]], *, timeout: float = 300.0
) -> T:
    """Await `work()` on a detached thread with its own event loop.

    `work` is a zero-argument callable returning a coroutine. It is invoked inside
    the thread, so no part of it runs on the MCP server's loop.
    """
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    thread = threading.Thread(target=_run_detached, args=(work, future), daemon=True)
    thread.start()
    wrapped = asyncio.wrap_future(future)
    try:
        return await asyncio.wait_for(wrapped, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        # Two distinct timeout classes until 3.11:
        #   * 3.10 has `asyncio.TimeoutError` as its own `Exception` subclass, separate
        #     from the builtin `TimeoutError` (they are not aliases).
        #   * 3.11+ aliases `asyncio.TimeoutError` to the builtin.
        # `asyncio.wait_for` raises the asyncio flavour, so catching only the builtin
        # missed it on 3.10 and the timeout escaped as an unhandled exception.
        # `asyncio.CancelledError` is unrelated: it is a `BaseException` and is *not*
        # caught here, so genuine cancellation still propagates as cancellation.
        raise TimeoutError(
            "the browser did not finish in time; run `musebird doctor --selftest` in a "
            "terminal to see what it is waiting on"
        ) from exc
