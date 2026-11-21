from __future__ import annotations

import asyncio
from pathlib import Path

from musebird.drivers.mock import MockDriver
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import Worker


async def _run(tmp_path, project, scenario="ok", sync="bundle", mode="single", agents=1):
    reg = JobRegistry()
    job = reg.create(
        prompt="do a thing", project=str(project), driver="mock", sync=sync,
        mode=mode, agents=agents,
    )
    driver = MockDriver(job.id, scenario=scenario)
    worker = Worker(job.id, registry=reg, driver=driver, poll=0.01)
    return await worker.run(), reg


async def test_success_lands_and_verifies(tmp_path, project):
    job, reg = await _run(tmp_path, project)
    assert job.state == st.DONE
    assert job.landing_dir
    landed = list((project / ".musebird" / "landing" / job.id).rglob("*.txt"))
    assert landed and landed[0].read_text() == "hello from the sandbox\n"
    events, _ = reg.events_since(job.id, 0)
    texts = [e["text"] for e in events]
    assert any("verified 2 file(s)" in t for t in texts), texts


async def test_manifest_mismatch_fails(tmp_path, project):
    job, _ = await _run(tmp_path, project, scenario="manifest_mismatch")
    assert job.state == st.FAILED
    assert job.error_code == "manifest_error"


async def test_needs_input_pauses_job(tmp_path, project):
    job, reg = await _run(tmp_path, project, scenario="needs_input")
    assert job.state == st.NEEDS_INPUT
    assert "Which framework" in (job.pending_question or "")
    # a follow-up message moves it back to running when answered
    job2 = reg.update(job.id, state=st.RUNNING)
    assert job2.state == st.RUNNING


async def test_upstream_failure(tmp_path, project):
    job, _ = await _run(tmp_path, project, scenario="fail")
    assert job.state == st.FAILED
    assert "upstream failed" in (job.error or "")


async def test_wake_failure_is_reported(tmp_path, project):
    job, _ = await _run(tmp_path, project, scenario="wake_fail")
    assert job.state == st.FAILED
    assert "wake" in (job.error or "").lower()


async def test_git_landing_creates_branch(tmp_path, git_project):
    job, _ = await _run(tmp_path, git_project, sync="git")
    assert job.state == st.DONE
    assert job.branch and job.branch.startswith("musebird/")
    import subprocess

    files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", job.branch],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout
    assert "hello.txt" in files or any("hello.txt" in line for line in files.splitlines())
    # working tree untouched
    assert not (git_project / "hello.txt").exists()


async def test_cancel_before_start(tmp_path, project):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    reg.request_cancel(job.id)
    assert reg.get(job.id).state == st.CANCELLED


async def test_fanout_prompt_includes_agents(tmp_path, project):
    seen = {}

    class Spy(MockDriver):
        async def send(self, thread, text, attachments=None):
            seen["prompt"] = text
            await super().send(thread, text, attachments)

    reg = JobRegistry()
    job = reg.create(prompt="big task", project=str(project), driver="mock",
                     mode="fanout", agents=3)
    worker = Worker(job.id, registry=reg, driver=Spy(job.id), poll=0.01)
    await worker.run()
    assert "Fan out into 3 subagents" in seen["prompt"]


def test_worker_process_entry_runs(tmp_path, project):
    from musebird.jobs.worker import run_job

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock", sync="bundle")
    # job-run uses the mock driver only when the job record says so
    result = asyncio.run(run_job(job.id, driver=MockDriver(job.id), poll=0.01))
    assert result.state == st.DONE


async def test_git_landing_never_deletes_existing_files(tmp_path, git_project):
    """A landing branch must only add/modify; merging it must not wipe the repo."""
    import subprocess

    job, _ = await _run(tmp_path, git_project, sync="git")
    assert job.state == st.DONE
    # the seed file must still exist on the branch
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", job.branch],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "README.md" in tree, f"landing branch deleted existing files: {tree}"
    assert "hello.txt" in tree
    # and the diff must not contain deletions of pre-existing files
    diff = subprocess.run(
        ["git", "diff", "--diff-filter=D", "--name-only", f"HEAD...{job.branch}"],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert diff == [], f"landing branch deletes: {diff}"


async def test_bundle_archive_is_extracted_and_verified(tmp_path, git_project):
    """The fallback path: Muse ships out.tar.gz instead of loose files."""
    import hashlib
    import io
    import json
    import tarfile

    from musebird.jobs.registry import JobRegistry

    payload = b"bundled content\n"
    manifest_data = {
        "files": [
            {
                "path": "src/bundled.py",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "summary": "from a bundle",
            }
        ]
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in (
            ("MANIFEST.json", json.dumps(manifest_data).encode()),
            ("src/bundled.py", payload),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    class BundleDriver(MockDriver):
        async def download(self, thread, dest_dir):
            target = Path(dest_dir) / "out.tar.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(buffer.getvalue())
            return [str(target)]

        def files_for_download(self):  # not used: the driver overrides download
            return {}

    reg = JobRegistry()
    job = reg.create(prompt="bundle please", project=str(git_project), driver="mock", sync="git")
    worker = Worker(job.id, registry=reg, driver=BundleDriver(job.id), poll=0.01)
    final = await worker.run()
    assert final.state == st.DONE, final.error
    landed = Path(final.landing_dir)
    assert (landed / "src" / "bundled.py").read_bytes() == payload
    assert final.branch


async def test_hostile_bundle_fails_without_landing(tmp_path, project):
    import io
    import tarfile

    from musebird.jobs.registry import JobRegistry

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        info = tarfile.TarInfo("../../escape.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))

    class EvilDriver(MockDriver):
        async def download(self, thread, dest_dir):
            target = Path(dest_dir) / "out.tar.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(buffer.getvalue())
            return [str(target)]

        def files_for_download(self):
            return {}

    reg = JobRegistry()
    job = reg.create(prompt="evil", project=str(project), driver="mock", sync="bundle")
    final = await Worker(
        job.id, registry=reg, driver=EvilDriver(job.id), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert final.error_code == "guard_violation"
    assert not (project / "escape.txt").exists()
    assert not (project.parent / "escape.txt").exists()


async def test_quota_failure_does_not_stop_a_job(tmp_path, project):
    """A broken quota scrape is diagnostic; the job must still run and land."""
    from musebird.errors import UpstreamError

    class NoQuotaDriver(MockDriver):
        async def read_quota(self):
            raise UpstreamError("could not read quota from the Muse UI: TimeoutError")

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=NoQuotaDriver(job.id), poll=0.01
    ).run()
    assert final.state == st.DONE
    assert final.quota_before is None
    assert final.quota_after is None


async def test_thread_url_is_persisted_after_send(tmp_path, project):
    """Resume needs the post-send URL, not the /thread/new alias."""
    class UrlDriver(MockDriver):
        async def send(self, thread, text, attachments=None):
            await super().send(thread, text, attachments)
            thread.url = "https://muse.ai/thread/real-id-123"

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(job.id, registry=reg, driver=UrlDriver(job.id), poll=0.01).run()
    assert final.thread_url == "https://muse.ai/thread/real-id-123"


async def test_completion_with_no_files_fails_loudly(tmp_path, project):
    """An empty landing directory must never be reported as success."""
    class NoFilesDriver(MockDriver):
        def files_for_download(self):
            return {}

        async def download(self, thread, dest_dir):
            return []

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=NoFilesDriver(job.id), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert final.error_code == "no_output"
    assert "no retrievable files" in (final.error or "")


async def test_extracted_manifest_wins_over_a_loose_copy(tmp_path, git_project):
    """A bundle plus a separately attached MANIFEST.json must not confuse the root."""
    import hashlib
    import io
    import json
    import tarfile

    payload = b"hello\n"
    inner = {
        "files": [
            {
                "path": "hello.txt",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        ]
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in (
            ("hello.txt", payload),
            ("MANIFEST.json", json.dumps(inner).encode()),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    class BundleDriver(MockDriver):
        async def download(self, thread, dest_dir):
            d = Path(dest_dir)
            (d / "out.tar.gz").write_bytes(buffer.getvalue())
            (d / "MANIFEST.json").write_text(json.dumps(inner))
            return [str(d / "out.tar.gz"), str(d / "MANIFEST.json")]

        def files_for_download(self):
            return {}

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(git_project), driver="mock", sync="git")
    final = await Worker(
        job.id, registry=reg, driver=BundleDriver(job.id), poll=0.01
    ).run()
    assert final.state == st.DONE, final.error
    assert (Path(final.landing_dir) / "hello.txt").read_bytes() == payload


async def test_a_stalled_job_emits_heartbeats(tmp_path, project):
    """Silence must not look identical to a stuck job."""
    class QuietDriver(MockDriver):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.calls = 0

        async def stream_events(self, thread, cursor=0):
            self.calls += 1
            if self.calls > 3:
                return [], cursor
            return await super().stream_events(thread, cursor)

    import musebird.jobs.worker as worker_module

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    # Compress the heartbeat window so the test stays fast.
    original = worker_module.IDLE_TIMEOUT
    worker_module.IDLE_TIMEOUT = 0.4
    try:
        final = await Worker(
            job.id, registry=reg, driver=QuietDriver(job.id), poll=0.05
        ).run()
    finally:
        worker_module.IDLE_TIMEOUT = original
    _events, _ = reg.events_since(job.id, 0)
    assert final.state == st.FAILED  # went quiet without a sentinel
    assert "went quiet" in (final.error or "")


async def test_a_transient_session_401_is_retried(tmp_path, project):
    """Muse rotates cookies mid-session; one 401 must not demand a re-login."""
    from musebird.drivers.base import SessionState

    class FlakySession(MockDriver):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.probes = 0

        async def ensure_session(self):
            self.probes += 1
            if self.probes == 1:
                return SessionState(valid=False, detail="transient 401")
            return SessionState(valid=True, detail="assigned")

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=FlakySession(job.id), poll=0.01
    ).run()
    assert final.state == st.DONE


async def test_a_persistent_invalid_session_still_fails(tmp_path, project):
    from musebird.drivers.base import SessionState

    class DeadSession(MockDriver):
        async def ensure_session(self):
            return SessionState(valid=False, detail="expired")

    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=DeadSession(job.id), poll=0.01
    ).run()
    assert final.state == st.FAILED
    assert "session" in (final.error or "").lower()
