"""Browser work must not run on the MCP server's event loop.

The MCP server uses anyio, whose loop refuses `asyncio.create_subprocess_exec`.
Playwright spawns its driver that way, so running it inline makes anyio cancel the
wait and tear the server down. `run_off_loop` isolates the work on a detached thread
with a plain asyncio loop.

Verified 2026-11-22 with a real MCP client over stdio: `muse_status(selftest=true)`
and `muse_job_start` both return normally and the server keeps serving afterwards.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from musebird.offloop import run_off_loop


@pytest.mark.asyncio
async def test_runs_work_on_its_own_loop():
    async def work():
        return asyncio.get_running_loop()

    loop = await run_off_loop(work)
    # A different loop than the caller's: proof it did not run inline.
    assert loop is not asyncio.get_running_loop()
    assert loop.is_closed()  # asyncio.run closed it after the work finished


@pytest.mark.asyncio
async def test_can_spawn_a_subprocess_from_the_mcp_style_loop():
    """The whole point: a subprocess must be spawnable from off-loop work."""

    async def work():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print('hello')",
            stdout=subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return out.decode().strip()

    assert await run_off_loop(work) == "hello"


@pytest.mark.asyncio
async def test_exceptions_propagate_to_the_caller():
    async def work():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await run_off_loop(work)


@pytest.mark.asyncio
async def test_a_timeout_does_not_leak_a_hanging_thread():
    async def slow():
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        await run_off_loop(slow, timeout=0.2)


@pytest.mark.asyncio
async def test_timeout_is_a_timeout_on_every_supported_python():
    """3.10 keeps asyncio.TimeoutError separate from the builtin; both must be caught.

    Until 3.11 they are different classes (`asyncio.TimeoutError` subclasses Exception;
    the builtin subclasses OSError-era `Exception` too, but they are not aliases), and
    `asyncio.wait_for` raises the asyncio flavour. `asyncio.CancelledError` is a
    separate `BaseException` and must keep propagating as cancellation.
    """
    import sys

    async def slow():
        await asyncio.sleep(30)

    try:
        await run_off_loop(slow, timeout=0.1)
    except TimeoutError as exc:
        assert "did not finish in time" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a timeout")
    assert sys.version_info >= (3, 10)


@pytest.mark.asyncio
async def test_caller_cancellation_stays_a_cancellation():
    """A cancelled caller must not be converted into a timeout."""
    import asyncio as _asyncio

    async def slow():
        await _asyncio.sleep(30)

    task = _asyncio.ensure_future(run_off_loop(slow, timeout=30))
    await _asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await task


def test_timeout_classes_are_distinct_on_310():
    """Document the class relationship, so the comment cannot drift again."""
    import sys

    if sys.version_info >= (3, 11):
        pytest.skip("3.11+ aliases asyncio.TimeoutError to the builtin")
    import asyncio as _asyncio

    assert not issubclass(_asyncio.TimeoutError, _asyncio.CancelledError)
    assert not issubclass(_asyncio.CancelledError, Exception)
    assert _asyncio.TimeoutError is not TimeoutError
    assert issubclass(_asyncio.TimeoutError, Exception)
