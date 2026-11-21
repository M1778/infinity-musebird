"""Live tests: require a real Muse account, skipped by default.

Run them deliberately, on a machine where you have signed in:

    MUSEBIRD_LIVE=1 .venv/bin/python -m pytest tests/test_live.py -v -s

Nothing here runs in CI. The point of these tests is to find out which of Muse's
selectors and endpoints still match, so the failure message says what to fix in
`src/musebird/upstream/muse.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import run_job
from musebird.session.vault import Vault

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("MUSEBIRD_LIVE") != "1",
        reason="live tests need MUSEBIRD_LIVE=1 and a signed-in Muse account",
    ),
]


def _driver():
    from musebird.drivers.browser.driver import BrowserDriver

    return BrowserDriver("live-selftest")


@pytest.mark.asyncio
async def test_selftest_reports_every_selector():
    driver = _driver()
    try:
        checks = await driver.selftest()
    finally:
        await driver.close()
    for check in checks:
        print(f"{'ok  ' if check['ok'] else 'FAIL'} {check['check']}: {check['detail']}")
    broken = [c for c in checks if not c["ok"]]
    assert not broken, (
        "selectors or endpoints changed; update src/musebird/upstream/muse.py: "
        + ", ".join(c["check"] for c in broken)
    )


@pytest.mark.asyncio
async def test_session_and_quota_are_readable():
    assert Vault().load(), "run `musebird login` first"
    driver = _driver()
    try:
        state = await driver.ensure_session()
        assert state.valid
        quota = await driver.read_quota()
        print(f"quota: {quota.used}% of {quota.limit} (resets {quota.resets_at})")
        assert quota.used is not None
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_single_job_end_to_end(tmp_path: Path):
    """The real proof: a live job lands verified files on a review branch."""
    assert Vault().load(), "run `musebird login` first"
    project = tmp_path / "live-project"
    project.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    (project / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
        cwd=project,
        check=True,
    )

    registry = JobRegistry()
    job = registry.create(
        prompt=(
            "Create out/hello.txt containing exactly 'hello from the sandbox' "
            "and nothing else. Do not create other files."
        ),
        project=str(project),
        driver="browser",
        sync="git",
    )
    final = await run_job(job.id, poll=2.0)
    assert final.state == st.DONE, (
        f"{final.state}: {final.error}\n"
        f"inspect with: musebird jobs show {job.id} --events"
    )
    assert final.branch, "landing produced no review branch"
    assert final.landing_dir
    landed = Path(final.landing_dir)
    assert (landed / "MANIFEST.json").is_file(), "Muse did not follow the manifest contract"


@pytest.mark.asyncio
async def test_needs_input_resumes_on_the_saved_thread(tmp_path: Path):
    """The full question -> answer -> resume -> land loop, on a real account.

    Verified live 2026-11-21: an under-specified task makes Muse emit
    INF-MUSEBIRD-NEEDS-INPUT with a real question, so this path is reachable and
    must not lose the job.
    """

    from musebird.jobs.registry import JobRegistry
    from musebird.jobs.worker import Worker

    assert Vault().load(), "run `musebird login` first"
    project = tmp_path / "resume-project"
    project.mkdir()
    registry = JobRegistry()
    job = registry.create(
        prompt=(
            "Create out/config.json for a web app. The framework and port must come "
            "from the user, so ask before choosing them."
        ),
        project=str(project),
        driver="browser",
        sync="bundle",
    )

    first = await Worker(job.id, registry=registry, poll=2.0).run()
    assert first.state == st.NEEDS_INPUT, f"{first.state}: {first.error}"
    assert first.pending_question, "needs_input without a question"
    assert first.thread_url and "/thread/" in first.thread_url

    # Answer exactly as the MCP layer does, then let a fresh worker reattach.
    path = Path(os.environ.get("MUSEBIRD_HOME") or Path.home() / ".infinity-musebird")
    (path / "jobs" / f"{job.id}.messages.jsonl").write_text(
        __import__("json").dumps("Use plain JSON with no framework, on port 8080.") + "\n"
    )
    registry.update(job.id, state=st.RUNNING, pending_question=None)

    second = await Worker(job.id, registry=registry, poll=2.0).run()
    assert second.state == st.DONE, f"{second.state}: {second.error}"
    assert second.thread_url == first.thread_url, "resumed on a different thread"
    lands = list(Path(second.landing_dir or project).rglob("config.json"))
    assert lands, "the answered job produced no config.json"
