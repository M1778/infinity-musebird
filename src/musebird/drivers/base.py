"""The driver interface: the only layer allowed to talk to Muse.

A driver is a stateful object used by one job worker. All site-specific details
(URLs, cookies, selectors) belong in `musebird.upstream.muse`, never here.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

EventKind = Literal[
    "info", "progress", "text", "file", "needs_input", "done", "completed", "error"
]


@dataclass
class Quota:
    """Usage snapshot. Units are Muse's own; `limit`/`used` may be percentages."""

    unit: str = "percent"
    used: float | None = None
    limit: float | None = None
    resets_at: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def remaining_ratio(self) -> float | None:
        if self.used is None or not self.limit:
            return None
        return max(0.0, min(1.0, 1.0 - (self.used / self.limit)))

    def to_dict(self) -> dict:
        out = {
            "unit": self.unit,
            "used": self.used,
            "limit": self.limit,
            "remaining_ratio": self.remaining_ratio,
            "resets_at": self.resets_at,
        }
        # Keep the parsed details (plan, extra balance) so `muse_status` can show
        # them without scraping Muse again.
        if self.raw:
            out["detail"] = self.raw
        return out


@dataclass
class SessionState:
    valid: bool
    account: str | None = None
    expires_at: str | None = None
    detail: str = ""


@dataclass
class SandboxFile:
    path: str
    size: int = 0
    sha256: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256, "summary": self.summary}


@dataclass
class DriverEvent:
    kind: EventKind
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class ThreadRef:
    id: str
    url: str = ""


class MuseDriver(abc.ABC):
    """Contract every driver implements.

    Lifecycle for one job: `ensure_session` -> `wake_vm` -> `open_thread` ->
    ( `send` -> `stream_events` )* -> `list_sandbox_files` -> `download`.
    """

    name: str = "base"
    #: `Callable[[str], None] | None`, set through set_progress_hook().
    _on_progress: Callable[[str], None] | None = None

    def set_progress_hook(self, hook: Callable[[str], None] | None) -> None:
        """Set (or clear) the callback used by `notify`. The worker sets this once."""
        self._on_progress = hook

    def notify(self, message: str) -> None:
        """Report progress to whoever is driving this job, if anyone is."""
        hook = getattr(self, "_on_progress", None)
        if hook is not None:
            hook(message)

    @abc.abstractmethod
    async def ensure_session(self) -> SessionState: ...

    @abc.abstractmethod
    async def wake_vm(self) -> str:
        """Wake the sandbox VM; returns a human-readable state."""

    @abc.abstractmethod
    async def open_thread(self) -> ThreadRef: ...

    @abc.abstractmethod
    async def send(self, thread: ThreadRef, text: str, attachments: list[str] | None = None) -> None: ...

    @abc.abstractmethod
    async def stream_events(self, thread: ThreadRef, cursor: int = 0) -> tuple[list[DriverEvent], int]:
        """Return `(new_events, next_cursor)`; never blocks longer than a poll interval."""

    @abc.abstractmethod
    async def list_sandbox_files(self, thread: ThreadRef) -> list[SandboxFile]: ...

    @abc.abstractmethod
    async def download(self, thread: ThreadRef, dest_dir: str) -> list[str]:
        """Download the sandbox output bundle into `dest_dir`; returns written paths."""

    @abc.abstractmethod
    async def read_quota(self) -> Quota: ...

    @abc.abstractmethod
    async def selftest(self) -> list[dict]:
        """Return `[{"check": str, "ok": bool, "detail": str}]`."""

    async def close(self) -> None:
        return None
