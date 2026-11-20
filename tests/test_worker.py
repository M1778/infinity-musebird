from __future__ import annotations

import asyncio

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
    assert any("verified 2 files" in t for t in texts), texts


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
