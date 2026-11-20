from __future__ import annotations

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
