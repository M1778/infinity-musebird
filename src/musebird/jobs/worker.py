"""One job worker process.

Started detached by the MCP server (`musebird job-run <id>`), it owns the driver for
the job's lifetime. All progress is written to the registry; the MCP server and CLI
only ever read files. If the worker dies, the job stays `running` and `musebird
doctor` reports it as stale so it can be resumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from musebird.audit import AuditLog, prompt_hash
from musebird.config import Paths, paths
from musebird.drivers import create_driver
from musebird.drivers.base import MuseDriver, ThreadRef
from musebird.errors import MuseBirdError, UpstreamError
from musebird.guard.paths import Caps
from musebird.jobs import state as st
from musebird.jobs.registry import Job, JobRegistry
from musebird.prompts import build_prompt, parse_sentinel
from musebird.sync import landing, manifest
from musebird.upstream import muse

POLL_INTERVAL = 2.0
IDLE_TIMEOUT = 60 * 45


class Worker:
    def __init__(
        self,
        job_id: str,
        *,
        registry: JobRegistry | None = None,
        driver: MuseDriver | None = None,
        paths_: Paths | None = None,
        poll: float = POLL_INTERVAL,
    ) -> None:
        self.registry = registry or JobRegistry(paths_)
        self.paths = paths_ or paths()
        self.job_id = job_id
        self.driver = driver
        self.poll = poll
        self.audit = AuditLog(self.paths)
        self.sent_messages = 0
        self._pending_sends: list[asyncio.Task[None]] = []
        self._sent = False

    async def run(self) -> Job:
        job = self.registry.get(self.job_id)
        self.audit.write("job_start", job=job.id, mode=job.mode, agents=job.agents,
                         prompt_sha256=prompt_hash(job.prompt), files=job.upload_paths)
        driver = self.driver or create_driver(job.driver, job.id)
        try:
            session = await driver.ensure_session()
            if not session.valid:
                raise MuseBirdError("Muse session is not valid", hint="Run `musebird login`.")
            self.registry.update(job.id, state=st.WAKING)
            vm = await driver.wake_vm()
            self.registry.append_event(job.id, f"VM: {vm}")
            self._check_cancel()
            quota_before = await driver.read_quota()
            self.registry.update(job.id, state=st.RUNNING, quota_before=quota_before.to_dict())
            thread = await self._resume_or_open(driver)
            if not self._sent:
                prompt = build_prompt(job.prompt, job.id, mode=job.mode, agents=job.agents)
                await driver.send(thread, prompt, job.upload_paths or None)
                self._sent = True
                self.registry.append_event(job.id, "prompt sent to Muse")
            finished = await self._pump(driver, thread)
            if not finished:
                # needs_input or cancelled: keep the record, land nothing.
                return self.registry.get(job.id)
            return await self._land(driver, thread)
        except asyncio.CancelledError:
            self.registry.update(job.id, state=st.CANCELLED, allow_terminal=True)
            self.audit.write("job_cancel", job=job.id)
            raise
        except MuseBirdError as exc:
            self._fail(job, exc.message, exc.code)
        except Exception as exc:  # keep the record usable even for a bug
            self._fail(job, f"{type(exc).__name__}: {exc}", "internal_error")
        finally:
            for task in self._pending_sends:
                task.cancel()
            with contextlib.suppress(Exception):
                await driver.close()
            self.registry.clear_cancel(job.id)
        return self.registry.get(job.id)

    # -- internals -------------------------------------------------------
    def _fail(self, job: Job, message: str, code: str) -> None:
        state = self.registry.get(job.id).state
        if st.is_terminal(state):
            return
        self.registry.append_event(job.id, message, kind="error")
        self.registry.update(job.id, state=st.FAILED, error=message, error_code=code)
        self.audit.write("job_failed", job=job.id, code=code)

    def _check_cancel(self) -> None:
        if self.registry.cancel_requested(self.job_id):
            raise asyncio.CancelledError

    async def _resume_or_open(self, driver: MuseDriver) -> ThreadRef:
        job = self.registry.get(self.job_id)
        if job.thread_url and hasattr(driver, "reopen"):
            thread = await driver.reopen(job.thread_url)  # type: ignore[attr-defined]
            self._sent = True
        else:
            thread = await driver.open_thread()
            self.registry.update(self.job_id, thread_url=thread.url or None)
        return thread

    async def _pump(self, driver: MuseDriver, thread: ThreadRef) -> bool:
        """Poll until the job finishes. Returns True when results should be landed."""
        cursor = self.registry.get(self.job_id).cursor
        last_activity = time.time()
        while True:
            self._check_cancel()
            self._flush_user_messages(driver, thread)
            events, cursor = await driver.stream_events(thread, cursor)
            for event in events:
                last_activity = time.time()
                if event.kind == "done":
                    self.registry.append_event(self.job_id, event.text or "done", kind="done")
                    return True
                if event.kind == "needs_input":
                    question = event.text
                    kind, payload = parse_sentinel(event.text)
                    if kind == "needs_input" and payload:
                        question = payload
                    self.registry.append_event(self.job_id, question, kind="needs_input")
                    self.registry.update(self.job_id, state=st.NEEDS_INPUT, pending_question=question)
                    return False
                if event.kind == "error":
                    raise UpstreamError(event.text or "Muse reported an error")
                self.registry.append_event(self.job_id, event.text, kind=event.kind)
            if time.time() - last_activity > IDLE_TIMEOUT:
                # Sentinel missing: fall back to scanning for a manifest in the sandbox.
                files = await driver.list_sandbox_files(thread)
                if any(f.path.endswith(muse.MANIFEST_NAME) for f in files):
                    self.registry.append_event(self.job_id, "sentinel missing; manifest found")
                    return True
                raise UpstreamError(
                    "Muse went quiet without a completion sentinel",
                    hint="Check the thread in Muse; the job record is preserved.",
                )
            await asyncio.sleep(self.poll)

    def _flush_user_messages(self, driver: MuseDriver, thread: ThreadRef) -> None:
        pending = self.paths.jobs / f"{self.job_id}.messages.jsonl"
        if not pending.exists():
            return
        lines = pending.read_text(encoding="utf-8", errors="replace").splitlines()
        new = lines[self.sent_messages :]
        for line in new:
            text = line.strip()
            if not text:
                continue
            # send is async; keep the task referenced so it cannot be GC'd mid-flight
            self._pending_sends.append(asyncio.ensure_future(driver.send(thread, text, None)))
            self.sent_messages += 1
            self.registry.append_event(self.job_id, "steering message sent", kind="info")

    async def _land(self, driver: MuseDriver, thread: ThreadRef) -> Job:
        job = self.registry.get(self.job_id)
        project = Path(job.project or ".").resolve()
        dest = landing.landing_dir(project, job.id)
        await driver.download(thread, str(dest))
        found = manifest.find_manifest(dest, muse.MANIFEST_NAME)
        if not found:
            self.registry.update(
                job.id,
                state=st.DONE,
                landing_dir=str(dest),
                quota_after=(await self._safe_quota(driver)),
            )
            self.registry.append_event(job.id, f"no manifest; raw files in {dest}", kind="info")
            self.audit.write("job_done", job=job.id, manifest=False)
            return self.registry.get(job.id)
        entries = manifest.load(found)
        result = manifest.verify(found.parent, entries)
        # `out/` is the workspace root Muse was told to use; its *contents* are what
        # lands in the project, so the manifest's directory is the review root.
        source = found.parent
        self.registry.update(job.id, quota_after=(await self._safe_quota(driver)))
        if not result.ok:
            self.registry.append_event(job.id, f"manifest mismatch: {result.mismatched}", kind="error")
            self.registry.update(
                job.id, state=st.FAILED, error="manifest verification failed",
                error_code="manifest_error", landing_dir=str(source),
            )
            self.audit.write("job_failed", job=job.id, code="manifest_error")
            return self.registry.get(job.id)
        branch = None
        if job.sync == "git":
            try:
                res = landing.land_to_branch(project, source, job.id)
                branch = res.branch
                self.registry.append_event(job.id, res.message)
            except MuseBirdError as exc:
                self.registry.append_event(job.id, f"git landing skipped: {exc.message}", kind="error")
        self.registry.update(job.id, state=st.DONE, landing_dir=str(source), branch=branch)
        self.registry.append_event(
            job.id, f"verified {len(result.verified)} files; landing at {source}", kind="done"
        )
        self.audit.write(
            "job_done", job=job.id, files=[e.path for e in entries], branch=branch,
            bytes=sum(e.size for e in entries),
        )
        return self.registry.get(job.id)

    async def _safe_quota(self, driver: MuseDriver) -> dict | None:
        try:
            return (await driver.read_quota()).to_dict()
        except Exception:
            return None


async def run_job(job_id: str, *, driver: MuseDriver | None = None, poll: float = POLL_INTERVAL) -> Job:
    return await Worker(job_id, driver=driver, poll=poll).run()


def main(job_id: str) -> int:
    job = asyncio.run(run_job(job_id))
    return 0 if job.state == st.DONE else 1


def spawn(job_id: str) -> int | None:
    """Launch a detached worker; returns its pid when the platform allows."""
    import os
    import subprocess
    import sys

    env = {**os.environ}
    cmd = [sys.executable, "-m", "musebird", "job-run", job_id]
    kwargs: dict = {"env": env, "stdin": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # pragma: no cover - windows
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS|NEW_PROCESS_GROUP
    log = paths().logs / f"{job_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, **kwargs)
    return proc.pid


__all__ = ["Caps", "Worker", "main", "run_job", "spawn"]
