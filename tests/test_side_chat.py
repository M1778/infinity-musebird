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
    # Verified live 2026-11-21: /thread/new is a side chat; the marker is what we
    # assert on, so a renamed control fails closed instead of landing in main chat.
    assert muse.SIDE_CHAT_MARKERS
    assert "Back to main chat" in muse.SIDE_CHAT_MARKERS[0]
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


def test_profile_lock_is_exclusive(tmp_path):
    """Two browser sessions must not share one profile: the second waits/fails."""
    from musebird.browser_lock import ProfileLock

    profile = tmp_path / "browser-profile"
    profile.mkdir()
    first = ProfileLock(profile, timeout=1)
    first.__enter__()
    try:
        second = ProfileLock(profile, timeout=0.2, poll=0.05)
        with pytest.raises(Exception) as exc:
            second.__enter__()
        assert "browser profile is busy" in str(exc.value)
    finally:
        first.__exit__()
    # released: a new lock acquires immediately
    third = ProfileLock(profile, timeout=1)
    third.__enter__()
    third.__exit__()


def test_stale_singleton_from_a_dead_pid_is_cleared(tmp_path, requires_symlinks):
    import os

    from musebird.browser_lock import clear_stale_singletons

    profile = tmp_path / "p"
    profile.mkdir()
    dead_pid = 999_999_999
    os.symlink(f"host-{dead_pid}", profile / "SingletonLock")
    removed = clear_stale_singletons(profile)
    assert "SingletonLock" in removed
    assert not (profile / "SingletonLock").exists() or not (profile / "SingletonLock").is_symlink()


def test_live_singleton_is_left_alone(tmp_path, requires_symlinks):
    import os

    from musebird.browser_lock import clear_stale_singletons

    profile = tmp_path / "p2"
    profile.mkdir()
    os.symlink(f"host-{os.getpid()}", profile / "SingletonLock")
    clear_stale_singletons(profile)
    assert (profile / "SingletonLock").is_symlink()


def test_lock_status_reports_holder(tmp_path):
    from musebird.browser_lock import ProfileLock, lock_status

    profile = tmp_path / "bp"
    profile.mkdir()
    status = lock_status(profile)
    assert status["held"] is False

    held = ProfileLock(profile, timeout=1)
    held.__enter__()
    try:
        status = lock_status(profile)
        assert status["held"] is True
        assert status["pid"] == __import__("os").getpid()
    finally:
        held.__exit__()


def test_force_unlock_refuses_a_live_holder(tmp_path):
    """Never steal a profile from a running process; only break dead locks."""
    from musebird.browser_lock import ProfileLock, force_unlock
    from musebird.errors import DriverUnavailable

    profile = tmp_path / "bp2"
    profile.mkdir()
    held = ProfileLock(profile, timeout=1)
    held.__enter__()
    try:
        with pytest.raises(DriverUnavailable) as exc:
            force_unlock(profile)
        assert "still running" in exc.value.message
    finally:
        held.__exit__()


def test_force_unlock_clears_a_dead_lock(tmp_path, requires_symlinks):
    import os

    from musebird.browser_lock import force_unlock

    profile = tmp_path / "bp3"
    profile.mkdir()
    os.symlink("host-999999999", profile / "SingletonLock")
    cleared = force_unlock(profile)
    assert "SingletonLock" in cleared


def test_lock_owner_is_written_beside_the_lock_not_inside_it(tmp_path):
    """Windows locks a byte range; truncating that byte is not portable.

    The holder's pid therefore lives in a sibling `.owner` file, written only after
    the lock is held, and removed on release.
    """
    from musebird.browser_lock import ProfileLock

    profile = tmp_path / "bp-owner"
    profile.mkdir()
    lock = ProfileLock(profile, timeout=1)
    lock.__enter__()
    try:
        # The lock file itself holds no pid, so a locked byte is never rewritten.
        assert lock.path.read_bytes() in (b"", b"\x00") or len(lock.path.read_bytes()) <= 1
        assert lock.owner_path.read_text().strip() == str(__import__("os").getpid())
    finally:
        lock.__exit__()
    assert not lock.owner_path.exists(), "the owner file must be cleaned up"


def test_a_rejected_attempt_does_not_clear_the_holders_pid(tmp_path):
    from musebird.browser_lock import ProfileLock, lock_status

    profile = tmp_path / "bp-clear"
    profile.mkdir()
    held = ProfileLock(profile, timeout=1)
    held.__enter__()
    try:
        contender = ProfileLock(profile, timeout=0.15, poll=0.05)
        from musebird.errors import DriverUnavailable

        with pytest.raises(DriverUnavailable):
            contender.__enter__()
        # The failed attempt must leave the holder identifiable.
        assert lock_status(profile)["pid"] == __import__("os").getpid()
    finally:
        held.__exit__()
