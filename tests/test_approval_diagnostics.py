"""Approval handling and failure diagnostics, from the 2026-11-22 live failure.

Live evidence (job_20261122_072110_f6f857): 374 events for one 8-agent job, of which
**128 were the identical line** "Muse is asking for approval; denying (privacy
defaut)". The job looked healthy for ten minutes and then failed with no output. The
cause was `[data-testid="hatch-inline-approval-card"]` also matching *resolved* cards
left in the transcript, so every poll "found" an approval and logged a denial without
proving the click worked.

These tests pin the repaired behaviour at the driver level, because the mock-driver
`needs_input` tests never reached this branch.
"""

from __future__ import annotations

import json

import pytest

from musebird.drivers.browser.driver import BrowserDriver
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import Worker
from musebird.upstream import muse


def _driver(page) -> BrowserDriver:
    d = BrowserDriver("t")
    d._page = page
    d._ctx = object()
    return d


class _NoApprovalsPage:
    """A page with no cards at all."""

    def __init__(self):
        self.evaluations: list[str] = []

    async def evaluate(self, script, arg=None):
        self.evaluations.append(script)
        return []


@pytest.mark.asyncio
async def test_no_cards_means_no_approval_events():
    page = _NoApprovalsPage()
    events = await _driver(page)._handle_approvals()
    assert events == []


@pytest.mark.asyncio
async def test_a_pending_card_is_reported_once_and_denied():
    calls: list[tuple] = []

    class Page:
        async def evaluate(self, script, arg=None):
            if script == muse.APPROVAL_SCAN:
                return [{"key": "card-1:Run bash ls", "text": "Run bash ls", "hasDeny": True}]
            calls.append(tuple(arg))
            return {"clicked": True}

    d = _driver(Page())
    first = await d._handle_approvals()
    assert len(first) == 1
    assert first[0].kind == "info"
    assert "denied a Muse approval request" in first[0].text
    assert "Run bash ls" in first[0].text
    # The click was scoped to the identified card, not "first on the page".
    assert calls and calls[0][1] == "Run bash ls"

    # Unchanged card: silent on every later poll (this is what flooded the log).
    second = await d._handle_approvals()
    assert second == []


@pytest.mark.asyncio
async def test_a_failed_deny_asks_the_user_instead_of_looping():
    class Page:
        async def evaluate(self, script, arg=None):
            if script == muse.APPROVAL_SCAN:
                return [{"key": "card-2:Publish results", "text": "Publish results"}]
            return {"clicked": False, "reason": "no-enabled-deny"}

    events = await _driver(Page())._handle_approvals()
    assert len(events) == 1
    event = events[0]
    # A blocker must become a question, never silent background progress.
    assert event.kind == "needs_input"
    assert muse.NEEDS_INPUT_SENTINEL in event.text
    assert "no-enabled-deny" in event.text
    assert "Publish results" in event.text


@pytest.mark.asyncio
async def test_two_distinct_cards_are_both_actionable():
    class Page:
        async def evaluate(self, script, arg=None):
            if script == muse.APPROVAL_SCAN:
                return [
                    {"key": "a:first", "text": "first"},
                    {"key": "b:second", "text": "second"},
                ]
            return {"clicked": True}

    events = await _driver(Page())._handle_approvals()
    assert len(events) == 2
    assert all(e.kind == "info" for e in events)


@pytest.mark.asyncio
async def test_a_scan_failure_is_not_treated_as_an_approval():
    class Page:
        async def evaluate(self, script, arg=None):
            raise RuntimeError("page navigated")

    assert await _driver(Page())._handle_approvals() == []


def test_approval_selectors_live_in_upstream_only():
    source = (
        __import__("pathlib").Path("src/musebird/drivers/browser/driver.py").read_text()
    )
    assert "hatch-inline-approval-card" not in source, (
        "Muse selectors must stay in upstream/muse.py so a UI change is a one-file edit"
    )
    assert "approval_card" in source


def test_the_scan_script_excludes_resolved_cards():
    """The regression itself: a resolved card has no enabled decision control."""
    assert "querySelectorAll" in muse.APPROVAL_SCAN
    assert "hasDeny" in muse.APPROVAL_SCAN
    assert "getClientRects" in muse.APPROVAL_SCAN
    # It must require a live deny control, or resolved cards match again.
    assert "disabled" in muse.APPROVAL_SCAN
    assert "APPROVAL_DENY_IN_CARD" in dir(muse)


# ---------------------------------------------------------------- issue 2
async def test_mcp_status_exposes_error_code_and_thread(tmp_path, project, sandbox_home):
    """A failed job must be classifiable from the MCP tool alone."""
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    class NoFiles:
        async def download(self, thread, dest_dir):
            return []

        def files_for_download(self):
            return {}

    from musebird.drivers.mock import MockDriver

    class NoOutput(MockDriver):
        def files_for_download(self):
            return {}

        async def download(self, thread, dest_dir):
            return []

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=NoOutput(job.id), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert final.error_code == "no_output"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "musebird", "mcp"],
        env={**os.environ, "MUSEBIRD_HOME": str(sandbox_home.root)},
    )
    async with (
        stdio_client(params) as (r, w),
        ClientSession(r, w) as session,
    ):
        await session.initialize()
        res = await session.call_tool("muse_job_status", {"job_id": job.id})
        payload = json.loads(res.content[0].text)
    assert payload["error_code"] == "no_output"
    assert "thread_url" in payload
    assert payload["next_step"], "a failure needs an actionable next step"
    assert "retrievable" in payload["next_step"].lower()
    # The failed job DID cost quota, and the status says so.
    assert payload["quota_charged"] is True
    assert payload["quota_after"]["used"] == 12.0


async def test_failure_path_records_quota_when_available(tmp_path, project):
    """An expensive failed run must not report quota as unknown-and-zero."""
    from musebird.drivers.mock import MockDriver

    class NoOutputWithQuota(MockDriver):
        def files_for_download(self):
            return {}

        async def download(self, thread, dest_dir):
            return []

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=NoOutputWithQuota(job.id), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert final.quota_after is not None, "the failure path must still read quota"
    assert final.quota_after["used"] == 12.0


async def test_retrieval_attempts_are_recorded(tmp_path, project):
    from musebird.drivers.mock import MockDriver

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    await Worker(job.id, registry=reg, driver=MockDriver(job.id), poll=0.01).run()
    events, _ = reg.events_since(job.id, 0)
    texts = [e.get("text", "") for e in events]
    assert any("retrieval: download returned" in t for t in texts), texts


async def test_a_blocked_job_is_not_kept_alive_by_generic_progress(tmp_path, project):
    """Repeating 'Muse is working' must not reset the no-progress deadline."""
    import musebird.jobs.worker as worker_module
    from musebird.drivers.base import DriverEvent
    from musebird.drivers.mock import MockDriver

    class StuckDriver(MockDriver):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.calls = 0

        async def stream_events(self, thread, cursor=0):
            self.calls += 1
            # Endless "working" with no output and no terminal event.
            if self.calls > 2:
                return [DriverEvent("progress", "Muse is working")], cursor + 1
            return await super().stream_events(thread, cursor)

    original = worker_module.IDLE_TIMEOUT
    worker_module.IDLE_TIMEOUT = 0.5
    try:
        reg = JobRegistry()
        job = reg.create(prompt="x", project=str(project), driver="mock")
        final = await Worker(
            job.id, registry=reg, driver=StuckDriver(job.id), poll=0.05
        ).run()
    finally:
        worker_module.IDLE_TIMEOUT = original
    assert final.state == st.FAILED, "generic progress masked a blocked job"
    assert "went quiet" in (final.error or "")
