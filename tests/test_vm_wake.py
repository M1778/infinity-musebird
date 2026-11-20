"""VM wake contract, pinned from live verification on 2026-11-21.

Two traps this guards against:
  * Muse returns 403 unless a User-Agent and all three Sec-Fetch-* headers are sent.
  * Playwright's `data=<dict>` form-encodes, which Muse rejects with 400; the body
    must be a JSON string.
  * `vm_state` stays UNKNOWN for a cold sandbox, so a 200 acknowledgement is success
    and polling for RUNNING would report a false failure.
"""

from __future__ import annotations

import json

import pytest

from musebird.drivers.browser.driver import BrowserDriver
from musebird.upstream import muse


def test_wake_headers_are_complete():
    for header in ("Origin", "Referer", "Accept", "Sec-Fetch-Site", "Sec-Fetch-Mode",
                   "Sec-Fetch-Dest"):
        assert header in muse.VM_WAKE_HEADERS, header


def test_unknown_is_recognised_as_normal():
    assert muse.VM_UNKNOWN == "UNKNOWN"
    assert muse.VM_WAKE_ACK == "wake_requested"


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
        self.ok = 200 <= status < 300

    async def json(self):
        return self._payload


class _Request:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls: list[dict] = []
        self._session_polls = 0

    async def get(self, url, timeout=None):
        self._session_polls += 1
        # First read sees UNKNOWN; later polls keep UNKNOWN (cold sandbox).
        return _Resp(200, {"status": "assigned", "vm_id": "vm-1", "vm_state": "UNKNOWN"})

    async def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "data": data})
        return _Resp(self.statuses.pop(0) if self.statuses else 200,
                     {"status": "wake_requested"})


class _Page:
    def __init__(self, request):
        self.request = request

    async def evaluate(self, script, arg=None):
        if "navigator.userAgent" in script:
            return "Mozilla/5.0 test"
        return {"status": 200, "text": '{"status":"wake_requested"}'}


@pytest.mark.asyncio
async def test_wake_sends_json_with_browser_headers(monkeypatch):
    driver = BrowserDriver("t")
    request = _Request([200])

    async def fake_launch():
        driver._page = _Page(request)
        driver._ctx = object()

    monkeypatch.setattr(driver, "_launch", fake_launch)

    result = await driver.wake_vm()
    assert "acknowledged" in result
    call = request.calls[0]
    # JSON string body, not a form-encoded dict.
    assert isinstance(call["data"], str)
    assert json.loads(call["data"])["vm_id"] == "vm-1"
    assert call["headers"]["Sec-Fetch-Mode"] == "cors"
    assert call["headers"]["User-Agent"] == "Mozilla/5.0 test"


@pytest.mark.asyncio
async def test_unknown_state_is_not_reported_as_failure(monkeypatch):
    driver = BrowserDriver("t")
    request = _Request([200])

    async def fake_launch():
        driver._page = _Page(request)
        driver._ctx = object()

    monkeypatch.setattr(driver, "_launch", fake_launch)
    result = await driver.wake_vm()
    assert "not acknowledged" not in result


@pytest.mark.asyncio
async def test_403_falls_back_to_an_in_page_fetch(monkeypatch):
    driver = BrowserDriver("t")
    request = _Request([403])
    fetches: list[str] = []

    class Page(_Page):
        async def evaluate(self, script, arg=None):
            if "navigator.userAgent" in script:
                return "Mozilla/5.0 test"
            fetches.append("fetch")
            return {"status": 200, "text": '{"status":"wake_requested"}'}

    async def fake_launch():
        driver._page = Page(request)
        driver._ctx = object()

    monkeypatch.setattr(driver, "_launch", fake_launch)
    result = await driver.wake_vm()
    assert fetches, "expected the in-page fetch fallback"
    assert "acknowledged" in result


@pytest.mark.asyncio
async def test_disabled_vm_is_reported_without_a_post(monkeypatch):
    driver = BrowserDriver("t")
    request = _Request([200])

    async def fake_launch():
        driver._page = _Page(request)
        driver._ctx = object()

    monkeypatch.setattr(driver, "_launch", fake_launch)

    async def disabled():
        return {"status": "assigned", "vm_id": "vm-1", "vm_state": "DISABLED"}

    monkeypatch.setattr(driver, "_session_body", disabled)
    assert "unavailable" in await driver.wake_vm()
    assert request.calls == []
