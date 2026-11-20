"""`musebird login`: a real headed browser, one sign-in, cookies captured and sealed.

The browser is a persistent profile under `~/.infinity-musebird/browser-profile`, so
later headless jobs reuse it. Cookies are copied into the vault and the profile is
left alone; nothing is ever printed except cookie *names*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from musebird.config import Paths, paths
from musebird.errors import DriverUnavailable, LoginRequired, UpstreamError
from musebird.upstream import muse

from .vault import Vault

LOGIN_TIMEOUT_S = 600
POLL_S = 2.0


@dataclass
class LoginResult:
    ok: bool
    account: str | None
    cookies: list[str]
    detail: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "account": self.account,
            "cookie_names": self.cookies,
            "detail": self.detail,
        }


def _display_available() -> bool:
    """True when a headed browser can actually draw a window."""
    import os
    import sys

    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise DriverUnavailable(
            "login needs Playwright: run `uv tool install 'infinity-musebird[browser]'`"
        ) from exc
    return async_playwright


def browser_launch_args(profile: Path, *, headless: bool = False) -> dict:
    """Prefer a system Chrome/Edge, fall back to Playwright's bundled Chromium.

    This is the only place that decides launch options. Callers must not pass
    `headless` themselves: doing so previously produced a duplicate-keyword
    TypeError before the browser ever started.
    """
    from musebird.browsers import launch_kwargs

    return launch_kwargs(profile, headless=headless)


async def login(*, paths_: Paths | None = None, timeout: int = LOGIN_TIMEOUT_S,
                headless: bool = False) -> LoginResult:
    p = paths_ or paths()
    p.ensure()
    async_playwright = _playwright()
    vault = Vault(p)

    if not headless and not _display_available():
        raise DriverUnavailable(
            "no graphical display detected, so no sign-in window can open",
            hint=(
                "Run `musebird login` from a desktop session, or over SSH with "
                "`ssh -X`. On WSL, make sure WSLg is running."
            ),
        )

    from musebird.browser_lock import ProfileLock

    # Held synchronously around the async block: the lock is a file lock, not a
    # coroutine, and it must cover the whole browser lifetime.
    with ProfileLock(p.browser_profile, timeout=60):
        return await _login_with_browser(async_playwright, p, vault, timeout, headless)


async def _login_with_browser(async_playwright, p, vault, timeout, headless) -> LoginResult:
    async with async_playwright() as pw:
        kwargs = browser_launch_args(p.browser_profile, headless=headless)
        try:
            ctx = await pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            from musebird.browsers import launch_error

            raise launch_error(exc) from exc
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(muse.LOGIN_URL, wait_until="domcontentloaded")
            wait = f"{timeout // 60} min" if timeout >= 60 else f"{timeout} s"
            print(f"Sign in to Muse in the browser window. Waiting up to {wait}...", flush=True)
            deadline = time.time() + timeout
            found: dict[str, str] = {}
            while time.time() < deadline:
                cookies = {str(c.get("name")): str(c.get("value")) for c in await ctx.cookies(muse.SITE)}
                found = {n: cookies[n] for n in muse.SESSION_COOKIES if n in cookies}
                if len(found) == len(muse.SESSION_COOKIES) and await _verified(ctx):
                    meta = {
                        "account": await _account(ctx),
                        "saved_at": time.time(),
                        "expires_at": time.time() + 30 * 86400,
                    }
                    vault.save(cookies, meta)
                    return LoginResult(
                        ok=True,
                        account=meta["account"],
                        cookies=sorted(cookies),
                        detail="session captured and stored",
                    )
                # cookies exist but the API is not ready yet: keep waiting
                await page.wait_for_timeout(int(POLL_S * 1000))
            missing = [n for n in muse.SESSION_COOKIES if n not in found]
            raise LoginRequired(
                "sign-in did not complete; missing cookies: " + ", ".join(missing),
                hint=(
                    "Run `musebird login` again and stay on the window until Muse stops "
                    "loading: accept any consent screen and finish 2FA."
                ),
            )
        finally:
            await ctx.close()


async def _verified(ctx) -> bool:
    """Ask Muse directly: /api/session status == 'assigned'."""
    try:
        response = await ctx.request.get(muse.SESSION_API, timeout=15000)
    except Exception:
        return False
    if response.status in (401, 403):
        return False
    if not response.ok:
        return False
    try:
        body = await response.json()
    except Exception:
        return True  # non-JSON 200 means the app answered; cookies are live
    return str(body.get("status", "")).lower() in ("", "assigned", "ready", "idle")


async def _account(ctx) -> str | None:
    try:
        body = await (await ctx.request.get(muse.SESSION_API, timeout=15000)).json()
    except Exception:
        return None
    for key in ("email", "account", "user", "name"):
        value = body.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("email"), str):
            return value["email"]
    return None


async def logout(*, paths_: Paths | None = None, keep_profile: bool = False) -> bool:
    """Delete the stored session, and by default the browser profile too.

    The profile holds a live Chromium cookie jar. Leaving it behind after a
    "logout" would mean the next job is still signed in, so it is removed unless
    the caller explicitly keeps it.
    """
    import shutil

    p = paths_ or paths()
    removed = Vault(p).delete()
    profile = p.browser_profile
    if profile.exists() and not keep_profile:
        shutil.rmtree(profile, ignore_errors=True)
        removed = True
    return removed


def require_session(paths_: Paths | None = None) -> dict[str, str]:
    cookies = Vault(paths_ or paths()).cookies()
    missing = [n for n in muse.SESSION_COOKIES if n not in cookies]
    if missing:
        raise LoginRequired(
            "no stored Muse session",
            hint="Run `musebird login` on this machine.",
        )
    return cookies


__all__ = ["LoginResult", "UpstreamError", "login", "logout", "require_session"]
