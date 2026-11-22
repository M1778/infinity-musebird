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
import json
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
        self._filenames: list[str] = []
        self._settled = False
        self._approvals_seen: set[str] = set()

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

    async def _refresh_vault_from_profile(self) -> None:
        """Copy the profile's current cookies back into the vault.

        Muse rotates `hatch_sess` during a session, so a vault captured at login
        time goes stale while the persistent profile stays valid. The profile is the
        source of truth; the vault is a durable copy of it.
        """
        with contextlib.suppress(Exception):
            jar = await self._ctx.cookies([muse.SITE])
            cookies = {
                str(c.get("name")): str(c.get("value"))
                for c in jar
                if c.get("name") and c.get("value")
            }
            if all(n in cookies for n in muse.SESSION_COOKIES):
                meta = (self.vault.load() or {}).get("meta") or {}
                self.vault.save(cookies, meta)

    async def ensure_session(self) -> SessionState:
        await self._launch()
        resp = await self._page.request.get(muse.SESSION_API, timeout=TIMEOUT)
        if resp.ok:
            await self._refresh_vault_from_profile()
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

    async def _ensure_on_site(self) -> None:
        """Make sure the page origin is muse.ai.

        The VM wake POST is rejected with 403 unless the request looks same-origin
        (Sec-Fetch-Site: same-origin), which is only true once a muse.ai document is
        loaded. A freshly launched context sits on about:blank.
        """
        page = self._page
        if page is None:
            return
        with contextlib.suppress(Exception):
            if "muse.ai" not in (page.url or ""):
                await page.goto(muse.LOGIN_URL, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)

    async def _session_body(self) -> dict:
        resp = await self._page.request.get(muse.SESSION_API, timeout=TIMEOUT)
        if resp.status in (401, 403):
            raise LoginRequired("Muse session expired", hint="Run `musebird login`.")
        try:
            return await resp.json()
        except Exception:
            return {}

    async def wake_vm(self) -> str:
        """Ask Muse to start the sandbox VM.

        Verified live 2026-11-21, two traps:

        * The POST needs a User-Agent and all three `Sec-Fetch-*` headers or Muse
          answers 403, and Playwright's `data=` form-encodes a dict (Muse then says
          400 "Invalid retry_count"), so the body must be a JSON string.
        * `vm_state` stays UNKNOWN for a cold sandbox even after a confirmed
          200 `wake_requested`. A 200 acknowledgement *is* the success signal; polling
          for RUNNING would waste a minute and then report a false failure.
        """
        await self._launch()
        await self._ensure_on_site()
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
            **muse.VM_WAKE_BODY_EXTRA,
        }
        headers = dict(muse.VM_WAKE_HEADERS)
        with contextlib.suppress(Exception):
            headers["User-Agent"] = await self._page.evaluate("navigator.userAgent")

        acked = False
        try:
            resp = await self._page.request.post(
                muse.VM_WAKE_API,
                headers=headers,
                data=json.dumps(payload),
                timeout=TIMEOUT,
            )
            acked = resp.ok
        except Exception:
            acked = False
        if not acked:
            # 403 or a transport error: retry as a fetch issued by the page itself.
            # Chrome supplies every fetch-metadata header Muse demands, including
            # the ones we cannot reliably fake from the request context.
            with contextlib.suppress(Exception):
                result = await self._page.evaluate(
                    """async (body) => {
                        const r = await fetch('https://muse.ai/api/hatch/vm/wake', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(body),
                            credentials: 'include',
                        });
                        return {status: r.status, text: await r.text()};
                    }""",
                    payload,
                )
                acked = bool(result and result.get("status") == 200)

        # Short confirmation window only; UNKNOWN is expected and is not a failure.
        deadline = time.time() + muse.VM_WAKE_CONFIRM_S
        while time.time() < deadline:
            body = await self._session_body()
            if body.get("vm_state") == muse.VM_RUNNING:
                return "running"
            await asyncio.sleep(1.0)
        if acked:
            return "wake acknowledged (VM starts on the first turn)"
        return f"wake not acknowledged; vm_state={body.get('vm_state')}"

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
        # Take the baseline only once the conversation stops changing. Reading it
        # during hydration can count transient placeholder bubbles (5 observed where
        # the settled DOM had 3), which then hides every later reply.
        await self._settle_bubbles()
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
        # Muse rewrites /thread/new once the first message is accepted. Poll long
        # enough to catch it (a slow first turn can take several seconds), then keep
        # the alias only if it truly never changed.
        with contextlib.suppress(Exception):
            for _ in range(40):
                if not page.url.rstrip("/").endswith("/thread/new"):
                    thread.url = page.url
                    return
                await asyncio.sleep(0.5)

    async def _attach(self, files: list[str]) -> None:
        try:
            await self._page.set_input_files(muse.SELECTORS["file_input"], files)
        except Exception as exc:
            raise UpstreamError(f"attachment upload failed: {type(exc).__name__}") from exc

    async def _settle_bubbles(self, timeout_s: float = 10.0) -> None:
        """Wait until the bubble count is stable for a few consecutive reads."""
        last = -1
        stable = 0
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            count = len(await self._read_bubbles())
            if count == last:
                stable += 1
                if stable >= 3:
                    return
            else:
                last = count
                stable = 0
            await asyncio.sleep(0.4)

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

    async def _handle_approvals(self) -> list[DriverEvent]:
        """Deny pending approval cards, once each, and report honestly.

        Live failure this replaces (2026-11-22 job_20261122_072110_f6f857): the card
        selector also matches *resolved* cards, so the old code logged "denying" on
        every 5s poll for 10+ minutes — 128 identical events — while the job looked
        healthy. Nothing said what was being approved, whether the click worked, or
        that a human decision was actually needed.

        Rules now:
          * only genuinely pending cards count (visible, with an enabled deny control)
          * each distinct card is reported once; repeats are silent
          * the deny click is scoped to that card and its outcome is reported
          * if denying keeps failing, ask the user instead of looping forever
        """
        events: list[DriverEvent] = []
        try:
            cards = await self._page.evaluate(muse.APPROVAL_SCAN, muse.SELECTORS["approval_card"])
        except Exception:
            return events
        for card in cards or []:
            key = str(card.get("key", ""))
            if not key or key in self._approvals_seen:
                continue
            self._approvals_seen.add(key)
            summary = str(card.get("text", "")).replace("\n", " ").strip()[:200]
            result = {"clicked": False, "reason": "not attempted"}
            for _attempt in range(1, 3):
                try:
                    result = await self._page.evaluate(
                        muse.APPROVAL_DENY_IN_CARD, [muse.SELECTORS["approval_card"], card.get("text", "")]
                    ) or result
                except Exception as exc:
                    result = {"clicked": False, "reason": type(exc).__name__}
                if result.get("clicked"):
                    break
                await asyncio.sleep(0.5)
            if result.get("clicked"):
                self.notify(f"denied an approval request: {summary[:120]}")
                events.append(
                    DriverEvent(
                        "info",
                        f"denied a Muse approval request: {summary[:160]}",
                    )
                )
            else:
                # A card we cannot dismiss is a real blocker, not background work.
                self.notify(f"approval could not be denied: {result.get('reason')}")
                events.append(
                    DriverEvent(
                        "needs_input",
                        f"{muse.NEEDS_INPUT_SENTINEL} {self.job_id}: Muse is waiting on an "
                        f"approval this tool declines by policy ({result.get('reason')}). "
                        f"Request: {summary[:160]}. Approve or deny it in the Muse thread, "
                        f"then reply here.",
                    )
                )
        return events

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
        if bubbles and self._bubble_count is not None and len(bubbles) != self._bubble_count:
            # A new bubble begins, or the list re-rendered: either way the previous
            # reply is history, not this turn.
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
        events.extend(await self._handle_approvals())
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
            elif not self._settled:
                # Emit once. Repeating this every poll flooded the event log and made
                # the worker look busy forever instead of moving to the manifest check.
                self._settled = True
                events.append(
                    DriverEvent("completed", "reply finished without a sentinel")
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
        """Retrieve produced files by driving Muse's file cards.

        Verified live 2026-11-21: an agent-produced file renders as a clickable card
        (`hatch-chat-file-card-<hex path>`) with no href. Clicking opens an in-app
        editor whose Download control fires a real Playwright download event. The
        `blob:`/media `fetch()` trick only works for image/video attachments, so it
        stays as a secondary path, not the primary one.
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        await self._scroll_to_bottom()

        written: list[str] = []
        written.extend(await self._download_file_cards(dest))
        written.extend(await self._download_media(dest))
        if written:
            self._filenames = [Path(p).name for p in written]
        return written

    async def _download_file_cards(self, dest: Path) -> list[str]:
        """Click each file card, save its download, close the editor."""
        page = self._page
        written: list[str] = []
        cards = page.locator(muse.SELECTORS["file_card"])
        count = 0
        # Cards can render a moment after the completion sentence, so give them a
        # short window instead of concluding "no files" from the first look.
        for _ in range(15):
            try:
                count = await cards.count()
            except Exception:
                count = 0
            if count:
                break
            await asyncio.sleep(0.4)
        if not count:
            return written
        for index in range(count):
            # Re-resolve each iteration: the editor overlays the list between saves.
            card = page.locator(muse.SELECTORS["file_card"]).nth(index)
            name = await self._card_filename(card) or f"file-{index}"
            try:
                await card.click(timeout=10_000)
                await page.locator(muse.SELECTORS["file_editor"]).first.wait_for(
                    timeout=10_000
                )
                button = page.locator(muse.SELECTORS["file_editor_download"]).first
                if not await button.count():
                    # Text files have no direct Download: it lives in the more menu.
                    await page.locator(muse.SELECTORS["file_editor_more"]).first.click(
                        timeout=5_000
                    )
                    button = page.get_by_role("menuitem", name="Download").first
                async with page.expect_download(timeout=muse.DOWNLOAD_TIMEOUT_MS) as info:
                    await button.click()
                download = await info.value
                target = dest / _safe_name(download.suggested_filename or name)
                await download.save_as(target)
                written.append(str(target))
                self.notify(f"downloaded {target.name}")
            except Exception as exc:
                # One unreadable card must not lose the others.
                self.notify(f"could not download {name}: {type(exc).__name__}")
            finally:
                with contextlib.suppress(Exception):
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
        return written

    async def _card_filename(self, card) -> str | None:
        """The sandbox filename encoded in the card's data-testid, else its text."""
        with contextlib.suppress(Exception):
            testid = await card.get_attribute("data-testid") or ""
            suffix = testid.removeprefix(muse.FILE_CARD_PREFIX)
            if suffix:
                try:
                    path = bytes.fromhex(suffix).decode("utf-8")
                    return Path(path).name
                except (ValueError, UnicodeDecodeError):
                    pass
        with contextlib.suppress(Exception):
            text = (await card.inner_text()).strip().splitlines()[0]
            if text:
                return text
        return None


    async def _download_media(self, dest: Path) -> list[str]:
        """Images/videos: fetch the media URL inside the page and decode it."""
        page = self._page
        try:
            blobs = await page.eval_on_selector_all(
                muse.SELECTORS["attachment"],
                """els => els.map(e => {
                    // File cards render a static icon (/_next/static/...png) next to
                    // the filename. Fetching that icon produced a PNG saved as
                    // "out.tar.gz", so cards are handled only by the card path.
                    if (e.querySelector('[data-testid^="hatch-chat-file-card-"]')) return null;
                    const m = e.querySelector('video, a[href^="blob:"], img');
                    if (!m) return null;
                    const url = m.currentSrc || m.src || m.href || '';
                    if (!url || url.includes('/_next/static/')) return null;
                    return {name: (m.download || e.innerText || 'media').trim(), url};
                }).filter(Boolean)""",
            )
        except Exception:
            return []
        written: list[str] = []
        for item in blobs or []:
            url = item.get("url", "")
            if not url:
                continue
            try:
                b64 = await page.evaluate(
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
            target = dest / _safe_name(item.get("name", "media"))
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
        """Open Settings and return the text of the usage panel.

        Verified live 2026-08-21: opening the dock menu creates a second dialog-like
        node (the menu itself), so `[role=dialog]`.first is the *menu*, not the
        settings panel. The reliable rule is to pick the dialog whose text actually
        contains the Usage section.
        """
        page = self._page
        try:
            for _ in range(2):
                await page.keyboard.press("Escape")
            await page.locator(muse.SELECTORS["settings_dock"]).first.click(timeout=5000)
            clicked = False
            for label in muse.SETTINGS_LABELS:
                item = page.get_by_role("menuitem", name=label, exact=False)
                if await item.count():
                    await item.first.click(timeout=5000)
                    clicked = True
                    break
            if not clicked:
                raise UpstreamError("could not find the Settings menu item")

            # The panel paints in two stages: the nav list first (no numbers), then the
            # content. Wait for a percentage so we never return the half-rendered text.
            best = ""
            for _ in range(30):
                panels = page.locator(muse.SELECTORS["dialog"])
                for index in range(await panels.count()):
                    text = await panels.nth(index).inner_text()
                    if "%" in text or "％" in text:
                        with contextlib.suppress(Exception):
                            await page.keyboard.press("Escape")
                        return text
                    if "Usage" in text and len(text) > len(best):
                        best = text
                await asyncio.sleep(0.5)
            if best:
                # No percentage at all: return what we saw so the parser can explain.
                with contextlib.suppress(Exception):
                    await page.keyboard.press("Escape")
                return best
            raise UpstreamError("the Settings panel never showed a Usage section")
        except UpstreamError:
            raise
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
        await asyncio.sleep(3)
        # Only selectors that must exist on a *fresh* thread are checked here.
        # `agent_bubble` and `file_card` appear once there is output, and `send_button`
        # does not exist at all (Enter submits), so testing them here would always
        # fail and teach the user to ignore the report.
        for name in ("composer", "hydration", "side_chat_marker", "file_input"):
            try:
                count = await self._page.locator(muse.SELECTORS[name]).count()
            except Exception:
                count = 0
            checks.append(
                {"check": f"selector:{name}", "ok": count > 0, "detail": f"{count} match(es)"}
            )
        # Side-chat isolation is an invariant, so it is asserted explicitly.
        checks.append(
            {
                "check": "side-chat isolation",
                "ok": await page_is_side_chat(self._page),
                "detail": "thread is isolated from the main conversation",
            }
        )
        # Quota is read from a dialog; a failure here is informative, not fatal.
        try:
            quota = await self.read_quota()
            checks.append(
                {
                    "check": "quota readable",
                    "ok": quota.used is not None,
                    "detail": f"{quota.used}% used, resets {quota.resets_at or 'unknown'}",
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "check": "quota readable",
                    "ok": False,
                    "detail": getattr(exc, "message", None) or type(exc).__name__,
                }
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
