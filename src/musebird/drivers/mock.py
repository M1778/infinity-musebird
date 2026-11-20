"""Deterministic fake Muse, used by every test and by `--driver mock`.

The mock implements the full contract: it wakes, streams progress, asks for input
when told to, writes a manifest + files into a fake sandbox and returns them from
`download`. No network, no browser, no credentials.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from musebird.upstream import muse

from .base import (
    DriverEvent,
    MuseDriver,
    Quota,
    SandboxFile,
    SessionState,
    ThreadRef,
)

DEFAULT_FILES = {
    "out/hello.txt": b"hello from the sandbox\n",
    "out/src/app.py": b"print('built by muse')\n",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MockDriver(MuseDriver):
    name = "mock"

    #: scripted events emitted one per poll after the initial send
    POLL_SCRIPT = (
        [DriverEvent("info", "Sandbox VM ready")]
        + [DriverEvent("progress", f"Working: step {i}/3") for i in (1, 2, 3)]
        + [DriverEvent("text", "Wrote the requested files.")]
    )

    def __init__(
        self,
        job_id: str = "mock-job",
        *,
        scenario: str = "ok",
        quota_used: float = 12.0,
        quota_limit: float = 100.0,
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.job_id = job_id
        self.scenario = scenario
        self._quota = Quota(unit="percent", used=quota_used, limit=quota_limit, resets_at="in 5h")
        self._files = files or dict(DEFAULT_FILES)
        self._thread: ThreadRef | None = None
        self._events: list[DriverEvent] = []
        self._sent = False
        self._rounds = 0
        self._finished = False

    # -- session ---------------------------------------------------------
    async def ensure_session(self) -> SessionState:
        return SessionState(valid=True, account="mock@example.invalid", detail="mock session")

    async def wake_vm(self) -> str:
        if self.scenario == "wake_fail":
            from musebird.errors import UpstreamError

            raise UpstreamError("mock: VM failed to wake")
        return "running"

    async def open_thread(self) -> ThreadRef:
        self._thread = ThreadRef(id=f"mock-thread-{self.job_id}", url="mock://thread")
        return self._thread

    async def reopen(self, url: str) -> ThreadRef:
        """Resume an existing thread; a pending question counts as answered."""
        self._thread = ThreadRef(id=f"mock-thread-{self.job_id}", url=url or "mock://thread")
        self._sent = True
        self._finished = False
        self._rounds = 0
        if self.scenario == "needs_input":
            self.scenario = "ok"
        return self._thread

    async def send(self, thread: ThreadRef, text: str, attachments: list[str] | None = None) -> None:
        self._sent = True
        self._rounds = 0
        self._finished = False
        self._events = [DriverEvent("info", "Prompt accepted")]

    async def stream_events(self, thread: ThreadRef, cursor: int = 0) -> tuple[list[DriverEvent], int]:
        if not self._sent or self._finished:
            return [], cursor
        if self._rounds < len(self.POLL_SCRIPT):
            self._events.append(self.POLL_SCRIPT[self._rounds])
            self._rounds += 1
            return [self._events[-1]], cursor + 1
        self._finished = True
        if self.scenario == "needs_input":
            return [
                DriverEvent(
                    "needs_input",
                    f"{muse.NEEDS_INPUT_SENTINEL} {self.job_id}: Which framework should I use?",
                )
            ], cursor + 1
        if self.scenario == "fail":
            return [DriverEvent("error", "mock: upstream failed")], cursor + 1
        return [DriverEvent("done", f"{muse.DONE_SENTINEL} {self.job_id}")], cursor + 1

    async def list_sandbox_files(self, thread: ThreadRef) -> list[SandboxFile]:
        return [
            SandboxFile(path=rel, size=len(data), sha256=_sha256(data), summary="mock file")
            for rel, data in sorted(self._files.items())
        ]

    async def download(self, thread: ThreadRef, dest_dir: str) -> list[str]:
        root = Path(dest_dir)
        written = []
        for rel, data in sorted(self.files_for_download().items()):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(str(target))
        return written

    def files_for_download(self) -> dict[str, bytes]:
        """Files plus a manifest, matching the contract the prompt asks Muse to follow."""
        files = {
            "out/MANIFEST.json": json.dumps(
                {
                    "files": [
                        {"path": rel.removeprefix("out/"), "sha256": _sha256(data),
                         "size": len(data), "summary": "mock file"}
                        for rel, data in sorted(self._files.items())
                    ]
                },
                indent=2,
            ).encode()
        }
        files.update(self._files)
        if self.scenario == "manifest_mismatch":
            manifest = json.loads(files["out/MANIFEST.json"])
            manifest["files"].append(
                {"path": "src/missing.py", "sha256": "0" * 64, "size": 1,
                 "summary": "never delivered"}
            )
            files["out/MANIFEST.json"] = json.dumps(manifest, indent=2).encode()
        return files

    async def read_quota(self) -> Quota:
        return self._quota

    async def selftest(self) -> list[dict]:
        return [
            {"check": "mock:session", "ok": True, "detail": "always valid"},
            {"check": "mock:quota", "ok": True, "detail": "scripted"},
        ]


def mock_from_env(job_id: str) -> MockDriver:
    import os

    return MockDriver(job_id, scenario=os.environ.get("MUSEBIRD_MOCK_SCENARIO", "ok"))


def sandbox_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="musebird-mock-"))
