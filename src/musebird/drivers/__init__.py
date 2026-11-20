"""Driver factory. `mock` is always available; `browser` needs the optional extra."""

from __future__ import annotations

from musebird.drivers.base import MuseDriver
from musebird.errors import DriverUnavailable


def create_driver(name: str, job_id: str) -> MuseDriver:
    if name == "mock":
        from musebird.drivers.mock import MockDriver

        return MockDriver(job_id)
    if name == "browser":
        try:
            from musebird.drivers.browser.driver import BrowserDriver
        except ImportError as exc:
            raise DriverUnavailable(
                "the browser driver needs Playwright: run `uv tool install 'infinity-musebird[browser]'`"
            ) from exc
        return BrowserDriver(job_id)
    if name == "http" or name == "official_api":
        raise DriverUnavailable(f"the {name} driver is not implemented yet")
    raise DriverUnavailable(f"unknown driver {name!r}")
