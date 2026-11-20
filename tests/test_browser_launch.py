"""Regression tests for browser launch options.

The first real `musebird login` failed before any browser started:

    launch_persistent_context() got multiple values for keyword argument 'headless'

because `launch_kwargs()` already returns `headless` and the caller passed it again.
These tests pin the contract: exactly one owner sets launch options, and passing a
caller-supplied `headless` reaches Playwright once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from musebird import browsers
from musebird.session.login import browser_launch_args


@pytest.fixture()
def no_system_browser(monkeypatch):
    monkeypatch.setattr(browsers, "find_system_browser", lambda: (None, None))


def test_launch_options_include_headless_exactly_once(no_system_browser):
    kwargs = browser_launch_args(Path("/tmp/profile"))
    assert kwargs["headless"] is False  # login needs a visible window
    assert kwargs["user_data_dir"] == "/tmp/profile"


def test_headless_is_honoured_and_not_duplicated(no_system_browser):
    kwargs = browser_launch_args(Path("/tmp/profile"), headless=True)
    assert kwargs["headless"] is True
    assert list(kwargs).count("headless") == 1


def test_system_chrome_is_preferred_over_a_download(monkeypatch):
    monkeypatch.setattr(browsers, "find_system_browser", lambda: ("chrome", "/usr/bin/google-chrome"))
    kwargs = browser_launch_args(Path("/tmp/p"))
    assert kwargs["channel"] == "chrome"
    assert "executable_path" not in kwargs


def test_plain_chromium_uses_executable_path(monkeypatch):
    monkeypatch.setattr(browsers, "find_system_browser", lambda: (None, "/usr/sbin/chromium"))
    kwargs = browser_launch_args(Path("/tmp/p"))
    assert kwargs["executable_path"] == "/usr/sbin/chromium"


def test_login_never_passes_headless_itself(monkeypatch, tmp_path):
    """Call the real login() against a fake Playwright and assert how it calls it.

    This is the exact failure from the first live run: a duplicate keyword. A fake
    driver that mirrors launch_persistent_context's signature catches it without a
    browser.
    """

    calls: dict[str, Any] = {}

    class FakePage:
        async def goto(self, url, wait_until=None):
            calls["goto"] = url

        async def wait_for_timeout(self, _ms):
            return None

    class FakeContext:
        pages: ClassVar[list] = []

        async def new_page(self):
            return FakePage()

        async def cookies(self, _url):
            return []

        async def close(self):
            calls["closed"] = True

        class request:
            @staticmethod
            async def get(*a, **k):
                raise RuntimeError("no session in this test")

    class FakeChromium:
        # Positional-only-ish signature like Playwright's: one headless keyword.
        async def launch_persistent_context(self, *, user_data_dir, headless=True, **kwargs):
            calls["user_data_dir"] = user_data_dir
            calls["headless"] = headless
            calls["extra"] = kwargs
            return FakeContext()

    class FakeBrowserType:
        chromium = FakeChromium()

    class FakePlaywright:
        async def __aenter__(self):
            return FakeBrowserType()

        async def __aexit__(self, *exc):
            return False

    def fake_async_playwright():
        return FakePlaywright()

    import musebird.session.login as login_module

    monkeypatch.setattr(login_module, "_playwright", lambda: fake_async_playwright)
    monkeypatch.setattr(
        login_module, "browser_launch_args",
        lambda profile, headless=False: {
            "user_data_dir": str(profile),
            "headless": headless,
            "executable_path": "/usr/bin/chromium",
        },
    )

    from musebird.errors import LoginRequired
    from musebird.session.login import login

    with pytest.raises(LoginRequired):
        # Times out looking for cookies; the point is that launch succeeded.
        import asyncio

        asyncio.run(login(paths_=type("P", (), {
            "browser_profile": tmp_path / "profile",
            "ensure": lambda self: None,
        })(), timeout=0.2))

    assert calls.get("headless") is False, calls
    assert calls.get("goto") == "https://muse.ai", calls
    assert calls["extra"].get("executable_path") == "/usr/bin/chromium"


def test_login_refuses_without_a_display(monkeypatch):
    """A headed login on a headless box must say so instead of failing obscurely."""
    import asyncio


    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")

    from musebird.errors import DriverUnavailable
    from musebird.session.login import login

    class Paths:
        browser_profile = Path("/tmp/profile")

        def ensure(self):
            return None

    with pytest.raises(DriverUnavailable) as exc:
        asyncio.run(login(paths_=Paths(), timeout=1))
    assert "no graphical display" in exc.value.message


def test_sub_minute_timeout_is_printed_in_seconds(capsys, monkeypatch):
    """`timeout // 60` printed 'Waiting up to 0 minutes' for short waits."""
    timeout = 45
    wait = f"{timeout // 60} min" if timeout >= 60 else f"{timeout} s"
    print(f"Sign up to {wait}...")
    assert "45 s" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_send_captures_the_real_thread_url(monkeypatch):
    """After the first send the page moves from /thread/new to /thread/<uuid>.

    Resume depends on that URL, so send() must record it.
    """
    from musebird.drivers.base import ThreadRef
    from musebird.drivers.browser.driver import BrowserDriver

    driver = BrowserDriver("t")

    class Composer:
        async def wait_for(self, **k):
            return None

        async def click(self):
            return None

        async def input_value(self):
            return ""

    class Locator:
        first = Composer()

        async def count(self):
            return 0

    class Keyboard:
        async def insert_text(self, text):
            return None

        async def press(self, key):
            return None

    class Page:
        url = "https://muse.ai/thread/new"

        def locator(self, sel):
            return Locator()

        keyboard = Keyboard()

        async def set_input_files(self, *a):
            return None

    page = Page()
    driver._page = page
    driver._read_bubbles = lambda: _async_list([])
    thread = ThreadRef(id="t", url="https://muse.ai/thread/new")

    async def advance(url):
        page.url = url


    original_press = page.keyboard.press

    async def press(key):
        await original_press(key)
        await advance("https://muse.ai/thread/abc-123")

    page.keyboard.press = press
    await driver.send(thread, "hello")
    assert thread.url == "https://muse.ai/thread/abc-123"


async def _async_list(value):
    return value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("out.tar.gz", "out.tar.gz"),
        ("Download the bundle out.tar.gz now", "out.tar.gz"),
        ("MANIFEST.json", "MANIFEST.json"),
        ("src/app.py", "app.py"),
        ("", "attachment"),
        ("!!!", "attachment"),
    ],
)
def test_attachment_filenames_are_safe_and_keep_extensions(raw, expected):
    from musebird.drivers.browser.driver import _safe_name

    assert _safe_name(raw) == expected
