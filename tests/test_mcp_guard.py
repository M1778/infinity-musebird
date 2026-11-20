from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _params(home: Path, project: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "musebird", "mcp"],
        env={
            **os.environ,
            "MUSEBIRD_HOME": str(home),
            "MUSEBIRD_PROJECT": str(project),
        },
    )


async def _call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text if result.content else "{}")


@pytest.mark.asyncio
async def test_fanout_agents_are_capped(sandbox_home):
    project = sandbox_home.root / "fan"
    project.mkdir()
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            started = await _call(
                session, "muse_job_start",
                {"prompt": "big", "mode": "fanout", "agents": 99, "driver": "mock"},
            )
            assert started["ok"] is True
            assert started["agents"] == 8


@pytest.mark.asyncio
async def test_fanout_scales_down_when_quota_low(sandbox_home, project):
    from musebird.session.keepalive import QuotaCache

    QuotaCache().put({"weekly_used_pct": 95, "remaining_ratio": 0.05})
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            started = await _call(
                session, "muse_job_start",
                {"prompt": "big", "mode": "fanout", "agents": 8, "driver": "mock"},
            )
            assert started["agents"] == 2


@pytest.mark.asyncio
async def test_approval_persists_only_after_confirmation(sandbox_home, project):
    (project / "a.txt").write_text("hello\n")
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            first = await _call(
                session, "muse_job_start",
                {"prompt": "x", "upload_paths": ["a.txt"], "driver": "mock"},
            )
            assert first["error"] == "approval_required"
            # declining does not record approval
            approvals = json.loads(sandbox_home.approvals.read_text())
            assert approvals["projects"] == []

            ok = await _call(
                session, "muse_job_start",
                {"prompt": "x", "upload_paths": ["a.txt"], "driver": "mock",
                 "confirm_upload": True},
            )
            assert ok["ok"] is True
            assert ok["files_sent"] == 1
            approvals = json.loads(sandbox_home.approvals.read_text())
            assert str(project) in approvals["projects"]

            # later jobs from the same project need no confirmation
            later = await _call(
                session, "muse_job_start",
                {"prompt": "y", "upload_paths": ["a.txt"], "driver": "mock"},
            )
            assert later["ok"] is True


@pytest.mark.asyncio
async def test_path_escape_blocked(sandbox_home, project):
    outside = sandbox_home.root / "outside.txt"
    outside.write_text("secret\n")
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            result = await _call(
                session, "muse_job_start",
                {"prompt": "x", "upload_paths": [str(outside)], "driver": "mock",
                 "confirm_upload": True},
            )
            assert result["ok"] is False
            assert result["error"] == "guard_violation"


@pytest.mark.asyncio
async def test_ignored_files_are_never_sent(sandbox_home, project):
    (project / ".env").write_text("TOKEN=abc\n")
    (project / "visible.txt").write_text("ok\n")
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            result = await _call(
                session, "muse_job_start",
                {"prompt": "x", "upload_paths": [".env", "visible.txt"], "driver": "mock",
                 "confirm_upload": True},
            )
            assert result["ok"] is True
            assert result["files_sent"] == 1
            assert ".env" in result["ignored"]


@pytest.mark.asyncio
async def test_invalid_mode_and_sync_rejected(sandbox_home, project):
    async with (
        stdio_client(_params(sandbox_home.root, project)) as (r, w),
        ClientSession(r, w) as session,
    ):
            await session.initialize()
            for args in (
                {"prompt": "x", "mode": "bogus"},
                {"prompt": "x", "sync": "dropbox"},
                {"prompt": "", "mode": "single"},
            ):
                result = await _call(session, "muse_job_start", {**args, "driver": "mock"})
                assert result["ok"] is False, args
