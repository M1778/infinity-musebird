from __future__ import annotations

import contextlib
import json

from musebird.drivers.mock import MockDriver
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import Worker


class RecordingDriver(MockDriver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[tuple[str, list[str] | None]] = []

    async def send(self, thread, text, attachments=None):
        self.sent.append((text, attachments))
        await super().send(thread, text, attachments)


async def test_steering_messages_and_uploads_are_relayed(tmp_path, project, sandbox_home):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    driver = RecordingDriver(job.id)
    # Both queues must exist before the worker starts: the worker only reads the
    # part of each file it has not consumed yet.
    (sandbox_home.jobs / f"{job.id}.messages.jsonl").write_text(json.dumps("use postgres") + "\n")
    (sandbox_home.jobs / f"{job.id}.uploads.jsonl").write_text(
        json.dumps([str(project / "extra.txt")]) + "\n"
    )
    (project / "extra.txt").write_text("x\n")

    await Worker(job.id, registry=reg, driver=driver, poll=0.01).run()
    assert any("use postgres" in text for text, _ in driver.sent)
    assert any(attachments and attachments[0].endswith("extra.txt") for _, attachments in driver.sent)
    assert reg.get(job.id).state == st.DONE


async def test_empty_queue_lines_are_skipped(tmp_path, project, sandbox_home):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    driver = RecordingDriver(job.id)
    (sandbox_home.jobs / f"{job.id}.messages.jsonl").write_text("\n\n")
    (sandbox_home.jobs / f"{job.id}.uploads.jsonl").write_text("\n")
    await Worker(job.id, registry=reg, driver=driver, poll=0.01).run()
    assert reg.get(job.id).state == st.DONE
    assert len(driver.sent) == 1  # only the initial task prompt


async def test_answering_needs_input_resumes_on_saved_thread(tmp_path, project, sandbox_home):
    """The full question -> answer -> resume -> done loop, no browser needed."""
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    driver = RecordingDriver(job.id, scenario="needs_input")
    first = await Worker(job.id, registry=reg, driver=driver, poll=0.01).run()
    assert first.state == st.NEEDS_INPUT
    assert first.thread_url  # saved so a fresh worker can reattach

    # The harness relays the user's answer; the MCP layer moves the job back to
    # running and spawns a new worker. Mirror that here with a fresh driver.
    reg.update(job.id, state=st.RUNNING, pending_question=None)
    (sandbox_home.jobs / f"{job.id}.messages.jsonl").write_text(json.dumps("use postgres") + "\n")
    resumed = RecordingDriver(job.id, scenario="needs_input")
    final = await Worker(job.id, registry=reg, driver=resumed, poll=0.01).run()

    assert final.state == st.DONE
    assert any("use postgres" in text for text, _ in resumed.sent)
    # The task prompt is not re-sent: the thread already has it.
    assert not any("You are running inside a MuseBird job" in text for text, _ in resumed.sent)
    assert final.messages_sent == 1


async def test_cancel_flag_stops_a_running_job(tmp_path, project, sandbox_home):
    import asyncio

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")

    class SlowMock(RecordingDriver):
        async def stream_events(self, thread, cursor=0):
            await asyncio.sleep(0.05)
            return await super().stream_events(thread, cursor)

    worker = Worker(job.id, registry=reg, driver=SlowMock(job.id), poll=0.01)
    task = asyncio.ensure_future(worker.run())
    while reg.get(job.id).state != st.RUNNING:
        await asyncio.sleep(0.01)
    reg.request_cancel(job.id)
    with contextlib.suppress(asyncio.CancelledError):
        await task
    final = reg.get(job.id)
    assert final.state == st.CANCELLED
    assert not final.landing_dir  # nothing lands for a cancelled job
