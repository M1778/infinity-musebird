"""End-to-end MCP test: launch the real stdio server and drive a full mock job."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _env(home: Path) -> dict[str, str]:
    return {**os.environ, "MUSEBIRD_HOME": str(home), "MUSEBIRD_PROJECT": str(home)}


async def _call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else "{}"
    return json.loads(text)


@pytest.mark.asyncio
async def test_mcp_tools_end_to_end_mock(tmp_path, sandbox_home):
    project = sandbox_home.root / "work"
    project.mkdir()
    (project / "notes.md").write_text("# notes\n")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "musebird", "mcp"],
        env={**_env(sandbox_home.root), "MUSEBIRD_PROJECT": str(project)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {
            "muse_status", "muse_login", "muse_job_start", "muse_job_status",
            "muse_job_events", "muse_job_message", "muse_job_cancel",
            "muse_files_list", "muse_files_pull", "muse_files_push",
        } <= tools

        status = await _call(session, "muse_status", {"driver": "mock"})
        assert status["ok"] is True

        # first upload requires confirmation
        pending = await _call(
            session, "muse_job_start",
            {"prompt": "make a site", "upload_paths": ["notes.md"], "driver": "mock"},
        )
        assert pending["ok"] is False
        assert pending["error"] == "approval_required"
        assert pending["files"] == ["notes.md"]

        started = await _call(
            session, "muse_job_start",
            {"prompt": "make a site", "upload_paths": ["notes.md"], "driver": "mock",
             "confirm_upload": True},
        )
        assert started["ok"] is True
        job_id = started["job_id"]

        # a second upload from the same project is remembered
        again = await _call(
            session, "muse_job_start",
            {"prompt": "second", "upload_paths": ["notes.md"], "driver": "mock"},
        )
        assert again["ok"] is True

        waited = await _call(session, "muse_job_wait", {"job_id": job_id,
                                                       "timeout_seconds": 5})
        assert waited["ok"] is True

        # mock jobs are really executed by a worker; poll until it settles
        final = await _wait_for(session, job_id, {"done", "failed"}, 40)
        assert final["state"] == "done", final
        assert Path(final["landing_dir"]).is_dir()

        files = await _call(session, "muse_files_list", {"job_id": job_id})
        assert files["ok"] is True
        assert "hello.txt" in {f["path"] for f in files["files"]}

        pulled = await _call(session, "muse_files_pull", {"job_id": job_id, "branch": False})
        assert pulled["ok"] is True
        assert pulled["verified"] == 2

        events = await _call(session, "muse_job_events", {"job_id": job_id})
        assert events["ok"] is True
        assert events["cursor"] > 0

        pushed = await _call(
            session, "muse_files_push", {"job_id": job_id, "paths_in": ["notes.md"]}
        )
        assert pushed["ok"] is False and pushed["error"] == "approval_required"

        cancelled = await _call(session, "muse_job_cancel", {"job_id": job_id})
        assert cancelled["ok"] is True


@pytest.mark.asyncio
async def test_mcp_secret_scan_blocks(sandbox_home):
    project = sandbox_home.root / "work2"
    project.mkdir()
    (project / "creds.txt").write_text("ghp_" + "z" * 36 + "\n")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "musebird", "mcp"],
        env={**_env(sandbox_home.root), "MUSEBIRD_PROJECT": str(project), "MUSEBIRD_HOME": str(sandbox_home.root)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await _call(
            session, "muse_job_start",
            {"prompt": "x", "upload_paths": ["creds.txt"], "driver": "mock",
             "confirm_upload": True},
        )
        assert result["ok"] is False
        assert result["error"] == "guard_violation"
        kinds = {f["kind"] for f in result["findings"]}
        assert "github-token" in kinds


@pytest.mark.asyncio
async def test_mcp_unknown_job_is_clean_error(sandbox_home):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "musebird", "mcp"], env=_env(sandbox_home.root)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await _call(session, "muse_job_status", {"job_id": "job_nope"})
        assert result["ok"] is False
        assert result["error"] == "job_not_found"


async def _wait_for(session: ClientSession, job_id: str, states: set[str], seconds: int) -> dict:
    deadline = asyncio.get_running_loop().time() + seconds
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        last = await _call(session, "muse_job_status", {"job_id": job_id})
        if last.get("state") in states:
            return last
        await asyncio.sleep(0.3)
    return last
