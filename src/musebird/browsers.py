"""Finding and launching a browser, shared by `login` and the browser driver.

One place decides which browser to use, so `doctor`, `login`, and jobs cannot
disagree. Preference: a real Chrome/Edge (works with the user's own install and any
managed policies), then a system Chromium, then Playwright's bundled Chromium.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from musebird.errors import DriverUnavailable

# (Playwright channel, POSIX binary, Windows binary) in preference order.
CANDIDATES = (
    ("chrome", "google-chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("msedge", "msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)
CHROMIUM_NAMES = ("chromium", "chromium-browser")


def find_system_browser() -> tuple[str | None, str | None]:
    """Return `(playwright_channel, executable_path)`; both None means use the bundle."""
    for channel, posix, windows in CANDIDATES:
        found = shutil.which(posix)
        if found:
            return channel, found
        if sys.platform == "win32" and Path(windows).exists():
            return channel, windows
    for name in CHROMIUM_NAMES:
        found = shutil.which(name)
        if found:
            return None, found
    return None, None


def launch_kwargs(profile: Path, *, headless: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"user_data_dir": str(profile), "headless": headless}
    channel, executable = find_system_browser()
    if channel:
        kwargs["channel"] = channel
    elif executable:
        kwargs["executable_path"] = executable
    return kwargs


def describe() -> str:
    channel, executable = find_system_browser()
    if channel:
        return f"{channel} ({executable})"
    if executable:
        return f"chromium ({executable})"
    return "playwright bundled chromium"


def playwright_bundle_present() -> bool:
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
        Path.home() / (".cache" if os.name != "nt" else "AppData/Local") / "ms-playwright"
    )
    return Path(cache).is_dir()


def launch_error(exc: Exception) -> DriverUnavailable:
    """Wrap a Playwright launch failure in a message that names the fix."""
    detail = str(exc).splitlines()[0][:300]
    if "Executable doesn't exist" in detail:
        return DriverUnavailable(
            "no usable browser found",
            hint="Install Chrome/Edge, or run: `playwright install chromium`",
        )
    return DriverUnavailable(f"could not launch a browser: {detail}")
