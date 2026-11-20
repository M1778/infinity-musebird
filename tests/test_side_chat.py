"""Every job must run in a Muse side chat, never the account's main chat.

This is a hard invariant. The driver fails closed rather than falling back to the
main conversation, so these tests exist to keep it that way.
"""

from __future__ import annotations

import pytest

from musebird.drivers.mock import MockDriver
from musebird.errors import UpstreamError
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import Worker
from musebird.upstream import muse


def test_side_chat_surface_is_configured():
    assert muse.SIDE_CHAT_BUTTON
    assert muse.SIDE_CHAT_LABELS
    assert "side_chat_marker" in muse.SELECTORS


async def test_mock_job_runs_in_a_side_chat_thread(tmp_path, project):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=MockDriver(job.id), poll=0.01
    ).run()
    assert final.state == st.DONE
    assert final.thread_url and "side-chat" in final.thread_url


async def test_job_fails_instead_of_using_the_main_chat(tmp_path, project):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=MockDriver(job.id, scenario="no_side_chat"), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert final.error_code == "upstream_error"
    assert "side chat" in (final.error or "")
    assert not final.landing_dir  # nothing was produced, nothing lands


def test_driver_refuses_to_resume_a_non_side_chat(monkeypatch):
    """reopen() must check, not assume, that the saved thread is a side chat."""
    import asyncio

    from musebird.drivers.browser import driver as browser_driver

    class FakePage:
        url = "https://muse.ai/thread/main"

        def __init__(self):
            self.goto_calls = []

        async def goto(self, url, wait_until=None):
            self.goto_calls.append(url)

        def locator(self, selector):
            class Empty:
                async def count(self):
                    return 0
            return Empty()

    async def not_a_side_chat(page):
        return False

    monkeypatch.setattr(browser_driver, "page_is_side_chat", not_a_side_chat)
    d = browser_driver.BrowserDriver("t")

    async def fake_launch():
        d._page = FakePage()
        d._ctx = object()  # mark launched
        d._pw = None

    async def fake_wait(*a, **k):
        return None

    monkeypatch.setattr(d, "_launch", fake_launch)
    monkeypatch.setattr(d, "_wait_hydrated", fake_wait)

    with pytest.raises(UpstreamError) as exc:
        asyncio.run(d.reopen("https://muse.ai/thread/main"))
    assert "not a side chat" in exc.value.message
