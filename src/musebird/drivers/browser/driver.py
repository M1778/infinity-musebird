"""Playwright browser driver: the default real driver.

Strategy learned from muse2api, but implemented on Playwright for reliability and
maintainability:

1. A persistent browser profile holds the signed-in session.
2. `ensure_session` checks `/api/session`; `wake_vm` POSTs the wake endpoint and
   polls until `vm_state == RUNNING` (a 403 to the wake POST often means the VM is
   already awake, so the response is never trusted on its own).
3. One thread per job: the thread URL is persisted in the job record, so a worker
   restart reattaches instead of creating a new conversation.
4. Completion uses the sentinel in the reply text; DOM "stop button" state is only
   a hint. If the DOM changes, the worker still finishes from `MANIFEST.json`.
5. Files are downloaded through the page: blob URLs are only valid inside the
   document, so the fetch happens in-page and is handed back as base64.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import time
from pathlib import Path
from typing import Any

from musebird.config import paths
from musebird.errors import LoginRequired, UpstreamError
from musebird.session.vault import Vault
from musebird.upstream import muse

from ..base import DriverEvent, MuseDriver, Quota, SandboxFile, SessionState, ThreadRef

TIMEOUT = 60_000


class BrowserDriver(MuseDriver):
    name = "browser"

    def __init__(self, job_id: str, *, vault: Vault | None = None) -> None:
        self.job_id = job_id
        self.vault = vault or Vault(paths())
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self._last_text = ""
        self._bubble_count: int | None = None
        self._stable = 0
        self._profile_lock = None

    # -- lifecycle -------------------------------------------------------
    async def _launch(self) -> None:
        if self._ctx is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            from musebird.errors import DriverUnavailable

            raise DriverUnavailable("browser driver needs Playwright") from exc
        from musebird.browser_lock import ProfileLock
        from musebird.browsers import launch_error, launch_kwargs

        self._pw = await async_playwright().start()
        profile = paths().browser_profile
        profile.mkdir(parents=True, exist_ok=True)
        # Chromium refuses a shared user_data_dir, and two jobs must not race on the
        # same cookie jar: take the profile lock before launching.
        self._profile_lock = ProfileLock(profile, timeout=120)
        try:
            self._profile_lock.__enter__()
        except Exception:
            await self._pw.stop()
            self._pw = None
            raise
        try:
            from musebird.browser_lock import clear_stale_singletons

            clear_stale_singletons(profile)
            self._ctx = await self._pw.chromium.launch_persistent_context(
                **launch_kwargs(profile, headless=True)
            )
        except Exception as exc:
            self._profile_lock.__exit__()
            self._profile_lock = None
            await self._pw.stop()
            self._pw = None
            raise launch_error(exc) from exc
        await self._inject_cookies()
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    async def _inject_cookies(self) -> None:
        """Restore vault cookies into the profile (handles the __Host- prefix rule)."""
        cookies = self.vault.cookies()
        if not cookies:
            return
        payload = []
        for name, value in cookies.items():
            if not value:
                continue
            if name.startswith("__Host-"):
                # __Host- cookies require Secure, Path=/, and *no* Domain. Playwright
                # also rejects an explicit `path` next to `url`, which is why only
                # `url` is set here — mixing the two raises before the cookie lands.
                cookie: dict[str, Any] = {
                    "name": name,
                    "value": value,
                    "url": muse.SITE + "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            else:
                cookie = {
                    "name": name,
                    "value": value,
                    "domain": "muse.ai",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            payload.append(cookie)
        # One bad cookie must not prevent the session cookies from loading.
        for cookie in payload:
            with contextlib.suppress(Exception):
                await self._ctx.add_cookies([cookie])

    async def ensure_session(self) -> SessionState:
        await self._launch()
        resp = await self._page.request.get(muse.SESSION_API, timeout=TIMEOUT)
        if resp.status in (401, 403):
            raise LoginRequired("Muse session expired", hint="Run `musebird login`.")
        try:
            body = await resp.json()
        except Exception:
            return SessionState(valid=resp.ok, detail="non-JSON session response")
        return SessionState(
            valid=str(body.get("status", "")).lower() in ("", "assigned", "ready", "idle"),
            account=body.get("email") or (body.get("user") or {}).get("email"),
            detail=f"vm_state={body.get('vm_state')}",
        )

    async def _session_body(self) -> dict:
        resp = await self._page.request.get(muse.SESSION_API, timeout=TIMEOUT)
        if resp.status in (401, 403):
            raise LoginRequired("Muse session expired", hint="Run `musebird login`.")
        try:
            return await resp.json()
        except Exception:
            return {}

    async def wake_vm(self) -> str:
        body = await self._session_body()
        vm_id = body.get("vm_id")
        state = body.get("vm_state")
        if not vm_id or state == muse.VM_DISABLED:
            return f"vm unavailable ({state or 'no vm_id'})"
        if state == muse.VM_RUNNING:
            return "running"
        payload = {
            "vm_id": vm_id,
            "retry_count": 0,
            "connect_attempt_id": f"musebird-{self.job_id}",
        }
        # A 403 here usually means "already running": the state poll is the truth.
        with contextlib.suppress(Exception):
            await self._page.request.post(muse.VM_WAKE_API, data=payload, timeout=TIMEOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            body = await self._session_body()
            if body.get("vm_state") == muse.VM_RUNNING:
                return "running"
            await asyncio.sleep(2.0)
        return f"wake requested; vm_state={body.get('vm_state')}"

    async def open_thread(self) -> ThreadRef:
        """Open a *side chat*: isolated context, never the account's main chat.

        This is an invariant, not a preference. If the side-chat control cannot be
        found or does not produce a side chat, the driver raises instead of typing
        into the main conversation, because continuing the user's main chat is the
        one outcome that must never happen silently.
        """
        await self._launch()
        await self._page.goto(muse.NEW_THREAD_URL, wait_until="domcontentloaded")
        await self._wait_hydrated()
        if not await self._enter_side_chat():
            raise UpstreamError(
                "could not open a Muse side chat; refusing to run in the main chat",
                hint=(
                    "Muse's side-chat control changed. Update SIDE_CHAT_* in "
                    "src/musebird/upstream/muse.py, then run `musebird doctor --selftest`."
                ),
            )
        return ThreadRef(id=f"browser-{self.job_id}", url=self._page.url)

    async def _enter_side_chat(self) -> bool:
        """Confirm the thread is a side chat, waiting briefly for the shell to settle.

        Verified 2026-11-21: `/thread/new` opens directly into a side chat, so this
        usually just observes the marker. It never clicks "Side chat" blindly: if the
        marker is absent the caller refuses to send, which is the safe direction.
        """
        for _ in range(12):
            if await self._page.locator(muse.SELECTORS["side_chat_marker"]).count():
                return True
            await asyncio.sleep(0.5)
        return False

    async def reopen(self, url: str) -> ThreadRef:
        await self._launch()
        await self._page.goto(url, wait_until="domcontentloaded")
        await self._wait_hydrated()
        if not await page_is_side_chat(self._page):
            raise UpstreamError(
                "refusing to resume: the saved thread is not a side chat",
                hint="Start a new job; the old thread will not be reused.",
            )
        self._last_text = ""
        self._bubble_count = len(await self._read_bubbles())
        return ThreadRef(id=f"browser-{self.job_id}", url=url)

    async def _wait_hydrated(self, timeout_s: float = 30.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            text = (await self._page.inner_text("body"))[:800].lower()
            if any(hint in text for hint in muse.LOGIN_HINTS):
                raise LoginRequired("Muse asked for a sign-in", hint="Run `musebird login`.")
            state = await self._page.evaluate(
                "(sel) => document.querySelector(sel)?.getAttribute("
                "'data-hatch-shell-hydration-state') ?? null",
                muse.SELECTORS["hydration"],
            )
            if state == muse.SELECTORS["hydration_ready"]:
                return
            await asyncio.sleep(0.25)

    async def send(self, thread: ThreadRef, text: str, attachments: list[str] | None = None) -> None:
        if self._page is None:
            await self.open_thread()
        page = self._page
        assert page is not None  # open_thread() either sets it or raised
        if attachments:
            await self._attach(attachments)
        composer = page.locator(muse.SELECTORS["composer"]).first
        await composer.wait_for(state="visible", timeout=TIMEOUT)
        await composer.click()
        await page.keyboard.insert_text(text)
        # Verified 2026-11-21: Enter submits and clears the composer. Use it first;
        # only look for a send button if the composer still holds the text.
        await page.keyboard.press("Enter")
        with contextlib.suppress(Exception):
            await asyncio.sleep(0.6)
            if (await composer.input_value()).strip():
                # Fallback for a future UI that adds an explicit control.
                button = page.locator(muse.SELECTORS["send_button"]).first
                if await button.count():
                    await button.click(timeout=5000)
        # Persist the real thread id: /thread/new is replaced by /thread/<uuid> on
        # the first send, and resume needs the stable URL, not the alias.
        with contextlib.suppress(Exception):
            for _ in range(10):
                if not page.url.rstrip("/").endswith("/thread/new"):
                    thread.url = page.url
                    return
                await asyncio.sleep(0.3)

    async def _attach(self, files: list[str]) -> None:
        try:
            await self._page.set_input_files(muse.SELECTORS["file_input"], files)
        except Exception as exc:
            raise UpstreamError(f"attachment upload failed: {type(exc).__name__}") from exc

    async def _read_bubbles(self) -> list[str]:
        try:
            return await self._page.eval_on_selector_all(
                muse.SELECTORS["agent_bubble"], "els => els.map(e => e.innerText)"
            )
        except Exception:
            return []

    async def _read_assistant_text(self) -> str:
        """Text of the current turn's reply, plus the previous bubble for context.

        Keying on bubble count matters: a resumed thread still renders the old
        question, and diffing against it would hide the new reply.
        """
        bubbles = await self._read_bubbles()
        if not bubbles:
            return ""
        if self._bubble_count is not None and len(bubbles) <= self._bubble_count:
            # Same turn still streaming: only the newest bubble can change.
            return bubbles[-1]
        return "\n".join(bubbles[-2:]) if len(bubbles) > 1 else bubbles[-1]

    async def _generating(self) -> bool:
        try:
            return await self._page.locator(muse.SELECTORS["stop_button"]).count() > 0
        except Exception:
            return False

    async def stream_events(self, thread: ThreadRef, cursor: int = 0) -> tuple[list[DriverEvent], int]:
        if self._page is None:
            return [], cursor
        await asyncio.sleep(muse.POLL_INTERVAL_S)
        bubbles = await self._read_bubbles()
        if bubbles and self._bubble_count is not None and len(bubbles) > self._bubble_count:
            # A new bubble begins: the previous reply is history, not this turn.
            self._bubble_count = len(bubbles)
            self._last_text = ""
        text = await self._read_assistant_text()
        events: list[DriverEvent] = []
        if len(text) > len(self._last_text):
            delta = text[len(self._last_text) :]
            self._last_text = text
            self._stable = 0
            events.append(DriverEvent("text", delta))
        else:
            self._stable += 1
        generating = await self._generating()
        if await self._page.locator(muse.SELECTORS["approval_card"]).count():
            events.append(
                DriverEvent("info", "Muse is asking for approval; denying (privacy default)")
            )
            with contextlib.suppress(Exception):
                await self._page.locator(muse.SELECTORS["approval_deny"]).first.click(timeout=3000)
        if not generating and self._stable >= muse.STABLE_POLLS_DONE and self._last_text:
            from musebird.prompts import parse_sentinel

            kind, payload = parse_sentinel(self._last_text)
            if kind == "done":
                events.append(DriverEvent("done", self._last_text[-400:]))
            elif kind == "needs_input":
                events.append(
                    DriverEvent(
                        "needs_input", f"{muse.NEEDS_INPUT_SENTINEL} {self.job_id}: {payload}"
                    )
                )
            else:
                events.append(
                    DriverEvent("info", "reply finished without a sentinel; will check manifest")
                )
        elif generating:
            # Only report real work. A quiet poll returning "info" would reset the
            # worker's idle timer forever and grow the event log one line per poll.
            events.append(DriverEvent("progress", "Muse is working"))
        return events, cursor + len(events)

    # -- files -----------------------------------------------------------
    async def list_sandbox_files(self, thread: ThreadRef) -> list[SandboxFile]:
        body = await self._session_body()
        vm_id = body.get("vm_id")
        if not vm_id:
            return []
        try:
            resp = await self._page.request.get(
                f"{muse.SITE}/api/hatch/vm/{vm_id}/files", timeout=TIMEOUT
            )
            if not resp.ok:
                return []
            data = await resp.json()
        except Exception:
            return []
        out = []
        for item in data.get("files") or [] if isinstance(data, dict) else []:
            out.append(
                SandboxFile(
                    path=str(item.get("path", "")),
                    size=int(item.get("size", 0)),
                    sha256=str(item.get("sha256", "")),
                    summary=str(item.get("summary", "")),
                )
            )
        return out

    async def _scroll_to_bottom(self) -> None:
        """Older attachment cards are virtualised until scrolled into view."""
        with contextlib.suppress(Exception):
            await self._page.evaluate(
                """() => {
                    for (const e of document.querySelectorAll('*')) {
                        const s = getComputedStyle(e);
                        if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
                            && e.scrollHeight > e.clientHeight + 40) {
                            e.scrollTop = e.scrollHeight;
                        }
                    }
                }"""
            )
            await asyncio.sleep(0.5)

    async def download(self, thread: ThreadRef, dest_dir: str) -> list[str]:
        """Download attachments rendered in the conversation via in-page fetch."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        await self._scroll_to_bottom()
        try:
            blobs = await self._page.eval_on_selector_all(
                muse.SELECTORS["attachment"],
                """els => els.map(e => {
                    // Any element carrying a downloadable URL wins: an <a download>,
                    // a blob: link, or media. Files (tar/zip/manifest) usually attach
                    // as a plain link, not an <img>, so check links before media.
                    const a = e.querySelector('a[download]');
                    if (a && a.href) return {name: a.download || a.innerText, url: a.href};
                    const link = e.querySelector('a[href^="blob:"], a[href^="http"]');
                    if (link) return {name: link.download || link.innerText,
                                      url: link.href};
                    const m = e.querySelector('video, img');
                    if (m && (m.currentSrc || m.src)) {
                        return {name: m.download || e.innerText, url: m.currentSrc || m.src};
                    }
                    return null;
                }).filter(Boolean)""",
            )
        except Exception:
            blobs = []
        written: list[str] = []
        for item in blobs or []:
            name = _safe_name(item.get("name", "attachment"))
            url = item.get("url", "")
            try:
                b64 = await self._page.evaluate(
                    """async (url) => {
                        const r = await fetch(url); const b = await r.arrayBuffer();
                        let s = ''; const u = new Uint8Array(b);
                        for (let i = 0; i < u.length; i += 8192)
                            s += String.fromCharCode(...u.subarray(i, i + 8192));
                        return btoa(s);
                    }""",
                    url,
                )
                data = base64.b64decode(b64)
            except Exception:
                continue
            target = dest / name
            target.write_bytes(data)
            written.append(str(target))
        return written

    async def read_quota(self) -> Quota:
        text = await self._open_usage()
        from musebird.session.quota import parse_usage_text

        parsed = parse_usage_text(text)
        return Quota(
            unit="percent",
            used=parsed.get("weekly_used_pct"),
            limit=100.0,
            resets_at=parsed.get("resets_at"),
            raw=parsed,
        )

    async def _open_usage(self) -> str:
        """Settings -> Usage dialog scrape. Every step is best-effort."""
        try:
            await self._page.keyboard.press("Escape")
            await self._page.keyboard.press("Escape")
            await self._page.locator(muse.SELECTORS["settings_dock"]).first.click(timeout=5000)
            for label in muse.SETTINGS_LABELS:
                item = self._page.get_by_role("menuitem", name=label, exact=False)
                if await item.count():
                    await item.first.click(timeout=3000)
                    break
            dialog = self._page.locator(muse.SELECTORS["dialog"]).first
            await dialog.wait_for(timeout=8000)
            text = await dialog.inner_text()
            await self._page.keyboard.press("Escape")
            return text
        except Exception as exc:
            raise UpstreamError(
                f"could not read quota from the Muse UI: {type(exc).__name__}"
            ) from exc

    async def selftest(self) -> list[dict]:
        checks: list[dict] = []
        try:
            await self._launch()
        except Exception as exc:
            return [{"check": "browser launch", "ok": False, "detail": str(exc)}]
        checks.append({"check": "browser launch", "ok": True, "detail": "ok"})
        try:
            state = await self.ensure_session()
            checks.append({"check": "session", "ok": state.valid, "detail": state.detail})
        except Exception as exc:
            checks.append({"check": "session", "ok": False, "detail": type(exc).__name__})
        await self._page.goto(muse.NEW_THREAD_URL, wait_until="domcontentloaded")
        for name in ("composer", "send_button", "agent_bubble", "hydration"):
            try:
                count = await self._page.locator(muse.SELECTORS[name]).count()
            except Exception:
                count = 0
            checks.append(
                {"check": f"selector:{name}", "ok": count > 0, "detail": f"{count} match(es)"}
            )
        return checks

    async def close(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
            if self._pw is not None:
                await self._pw.stop()
        finally:
            self._ctx = self._pw = self._page = None
            if self._profile_lock is not None:
                self._profile_lock.__exit__()
                self._profile_lock = None


async def page_is_side_chat(page) -> bool:
    """A thread counts as a side chat only when Muse itself marks it as one."""
    try:
        return await page.locator(muse.SELECTORS["side_chat_marker"]).count() > 0
    except Exception:
        return False


_KNOWN_EXT = (
    ".tar.gz", ".tar.zst", ".tar", ".tgz", ".zip", ".json", ".md", ".txt", ".py",
    ".js", ".ts", ".html", ".css", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf",
)


def _safe_name(name: str) -> str:
    """A filesystem-safe filename that keeps a recognisable extension.

    Attachment cards often render a long description rather than a filename, so
    look for a known extension anywhere in the text before falling back.
    """
    text = (name or "").strip()
    lowered = text.lower()
    for ext in _KNOWN_EXT:
        index = lowered.rfind(ext)
        if index != -1:
            # Walk left to the start of the filename token: stop at a path separator
            # or whitespace so a sentence like "Download out.tar.gz now" yields
            # "out.tar.gz", not the whole sentence.
            start = index
            while start > 0 and text[start - 1] not in " \t\n/\\\"'()[]<>|,:;":
                start -= 1
            candidate = text[start : index + len(ext)]
            cleaned = "".join(c for c in candidate if c.isalnum() or c in "._- ")
            if cleaned:
                return cleaned[:120]
    cleaned = "".join(c for c in text if c.isalnum() or c in "._- ")
    cleaned = cleaned.replace("/", "_").replace("\\", "_").strip()
    return cleaned[:120] or "attachment"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["BrowserDriver"]
