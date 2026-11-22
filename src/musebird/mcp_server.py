"""MCP server (stdio). Every tool returns fast; work happens in a worker process.

Each tool returns compact JSON with a `summary` string a model can quote directly.
Errors are returned as `{"ok": false, "error": ...}` rather than raised, so the
harness sees an actionable message instead of a protocol failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from musebird import __version__
from musebird.audit import AuditLog
from musebird.config import paths
from musebird.drivers import create_driver
from musebird.errors import MuseBirdError
from musebird.guard import paths as guard_paths
from musebird.guard.ignore import Ignore
from musebird.guard.secrets import scan_paths
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import spawn
from musebird.session.keepalive import QuotaCache
from musebird.session.vault import Vault

DEFAULT_AGENTS_CAP = 8
DEFAULT_LOW_QUOTA_RATIO = 0.25

server = MCPServer(
    "musebird",
    version=__version__,
    instructions=(
        "Muse runs long tasks in its own sandbox. Start a job, then poll status/events. "
        "Results land in a review area; never merge or execute pulled code without the user."
    ),
)


def _ok(summary: str, **fields: Any) -> str:
    return json.dumps({"ok": True, "summary": summary, **fields}, indent=2)


def _err(exc: Exception) -> str:
    payload: dict[str, Any] = (
        exc.to_dict()
        if isinstance(exc, MuseBirdError)
        else {"error": type(exc).__name__, "message": str(exc)}
    )
    payload["ok"] = False
    return json.dumps(payload, indent=2)


async def _probe_session(driver_name: str) -> dict:
    """Probe the live session in a private event loop.

    Playwright spawns a subprocess, and the MCP server's anyio loop forbids that: it
    cancels the wait and takes the whole server down. `run_off_loop` hands the work
    to a thread with plain asyncio.
    """
    from musebird.offloop import run_off_loop

    async def work() -> dict:
        d = create_driver(driver_name, "selftest")
        try:
            state = await d.ensure_session()
            return {
                "valid": state.valid,
                "account": state.account,
                "detail": state.detail,
            }
        finally:
            await d.close()

    try:
        return await run_off_loop(work, timeout=120)
    except Exception as exc:  # a probe failure must not fail the whole call
        return {
            "valid": False,
            "detail": getattr(exc, "message", None) or type(exc).__name__,
        }


#: One-line, actionable advice per failure class. Kept here rather than in the skill
#: so every consumer of muse_job_status gets the same explanation.
_NEXT_STEPS = {
    "no_output": (
        "Muse finished its turn without retrievable files. It may have stopped to ask "
        "a question: read thread_url, then answer with muse_job_message to resume this "
        "job on the same thread."
    ),
    "manifest_error": (
        "Files arrived but did not match their manifest. The landing directory is kept "
        "for inspection; the job was not merged."
    ),
    "login_required": "Run `musebird login`, then start a new job.",
    "guard_violation": "An upload or a returned bundle failed the guard; see the events.",
    "quota_exceeded": "Wait for the allowance to reset, or run a smaller job.",
    "upstream_error": (
        "Muse reported an error or went quiet. Open thread_url and check the events."
    ),
    "driver_unavailable": "Run `musebird doctor` for the missing component.",
}


def _quota_report(job) -> dict:
    """What we can honestly say about this job's quota cost.

    Naming matters: an earlier version exposed `quota_charged`, which reported
    whether a snapshot *existed*. That is not a charge — a null snapshot is unknown
    cost, not free, and a non-null one may be unchanged or affected by other
    concurrent jobs, resets or rounding. Reported honestly instead:

      observed   — a terminal snapshot exists
      delta_used — before/after difference, and only when both exist and the reset
                   date matches (an incomparable pair says nothing)
      charge     — "unknown" unless a comparable delta exists
    """
    before = job.quota_before or None
    after = job.quota_after or None
    report: dict = {
        "observed": after is not None,
        "charge": "unknown",
        "delta_used": None,
        "note": "",
    }
    if after is None:
        report["note"] = (
            "no terminal quota snapshot was captured, so this job's cost is unknown, "
            "not zero"
        )
        return report
    if before is None:
        report["note"] = "a terminal snapshot exists but there is no baseline to compare"
        return report
    if before.get("resets_at") != after.get("resets_at"):
        report["note"] = "the quota window reset between measurements; they are not comparable"
        return report
    used_before, used_after = before.get("used"), after.get("used")
    if used_before is None or used_after is None:
        report["note"] = "a measurement is missing a percentage; no delta is claimed"
        return report
    delta = round(float(used_after) - float(used_before), 4)
    report["delta_used"] = delta
    if delta < 0:
        report["note"] = "usage fell (reset or other activity); not attributable to this job"
        return report
    report["charge"] = "observed-delta"
    report["note"] = (
        "account-level delta only: other concurrent jobs, resets or rounding can "
        "affect it, so it is not an exact per-job cost"
    )
    return report


def _next_step(job) -> str | None:
    if job.pending_question:
        return "Muse asked a question: relay it to the user, then muse_job_message."
    if job.error_code:
        return _NEXT_STEPS.get(job.error_code, "See the events and thread_url for detail.")
    return None


def _registry() -> JobRegistry:
    return JobRegistry(paths())


def _approvals_path() -> Path:
    return paths().approvals


def _approved_projects() -> set[str]:
    path = _approvals_path()
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()).get("projects", []))
    except json.JSONDecodeError:
        return set()


def _approve(project: str) -> None:
    data = {"projects": sorted(_approved_projects() | {project})}
    _approvals_path().write_text(json.dumps(data))


# ---------------------------------------------------------------- status/login
@server.tool()
async def muse_status(driver: str = "browser", selftest: bool = False) -> str:
    """Session validity, VM state, quota snapshot and driver health.

    `selftest` also probes Muse live, so it is the only mode that can say the
    session is actually valid. Without it the answer is based on what is stored
    locally, and the summary says so.
    """
    try:
        from musebird.session.keepalive import session_summary

        vault = Vault()
        summary_data = session_summary()
        cached = QuotaCache().get()
        result: dict[str, Any] = {
            # `stored` means local state exists; `valid` stays null until a live
            # probe runs, because claiming validity we have not checked is a lie.
            "session": {
                "stored": summary_data["stored"],
                "valid": None,
                "backend": vault.backend,
                "expires_at": summary_data.get("expires_at"),
            },
            "quota": cached,
            "driver": driver,
            "jobs": [{"id": j.id, "state": j.state} for j in _registry().list(limit=5)],
        }
        if not summary_data["stored"]:
            result["session"]["login_required"] = True
        if selftest:
            result["session"].update(await _probe_session(driver))
        if result["session"].get("valid") is True:
            status = "signed in (verified)"
        elif result["session"].get("valid") is False:
            status = "session expired"
        elif result["session"]["stored"]:
            status = "signed in locally (not verified)"
        else:
            status = "not signed in"
        summary = f"{status}; quota: {cached or 'unknown'}; driver: {driver}"
        return _ok(summary, **result)
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_login() -> str:
    """Open a browser on this machine so the user can sign in to Muse once."""
    try:
        from musebird.offloop import run_off_loop
        from musebird.session.login import login

        # Non-interactive sign-in gets a short window; a person needs longer and
        # should run `musebird login` in a terminal, which is what the hint says.
        result = await run_off_loop(lambda: login(), timeout=120)
        return _ok("Logged in; session stored locally.", **result.to_dict())
    except Exception as exc:
        return _err(exc)


# --------------------------------------------------------------------- jobs
@server.tool()
async def muse_job_start(
    prompt: str,
    mode: str = "single",
    agents: int = 1,
    upload_paths: list[str] | None = None,
    sync: str = "bundle",
    driver: str = "browser",
    project: str | None = None,
    confirm_upload: bool = False,
) -> str:
    """Start a Muse job. Returns a job_id immediately; poll muse_job_status."""
    try:
        if mode not in ("single", "fanout"):
            raise MuseBirdError("mode must be 'single' or 'fanout'")
        if sync not in ("git", "bundle"):
            raise MuseBirdError("sync must be 'git' or 'bundle'")
        if not prompt.strip():
            raise MuseBirdError("prompt must not be empty")
        project_path = (
            Path(project).expanduser().resolve()
            if project
            else Path(os.environ.get("MUSEBIRD_PROJECT") or Path.cwd()).expanduser().resolve()
        )

        uploads: list[str] = []
        approved_new = False
        if upload_paths:
            resolved = guard_paths.check_upload(
                [Path(p) for p in upload_paths], project_path
            )
            ignore = Ignore(project_path)
            keep, dropped = ignore.filter(resolved)
            findings = scan_paths(keep, project_path)
            if findings:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "guard_violation",
                        "message": "secret scan blocked the upload",
                        "findings": [f.to_dict() for f in findings],
                        "hint": "Remove these values or add the files to .musebirdignore.",
                    },
                    indent=2,
                )
            ignored = [str(p.relative_to(project_path)) for p in dropped]
            key = str(project_path)
            if key not in _approved_projects():
                if not confirm_upload:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": "approval_required",
                            "message": "First upload from this project needs explicit confirmation.",
                            "files": [str(p.relative_to(project_path)) for p in keep],
                            "ignored": ignored,
                            "next": "Show this list to the user, then call again with confirm_upload=true.",
                        },
                        indent=2,
                    )
                _approve(key)
                approved_new = True
            uploads = [str(p) for p in keep]

        if mode == "fanout":
            cap = DEFAULT_AGENTS_CAP
            agents = max(1, min(int(agents), cap))
            ratio = (QuotaCache().get() or {}).get("remaining_ratio")
            if ratio is not None and ratio < DEFAULT_LOW_QUOTA_RATIO and agents > 2:
                agents = 2

        job = _registry().create(
            prompt=prompt,
            mode=mode,
            agents=agents,
            sync=sync,
            driver=driver,
            project=str(project_path),
            upload_paths=uploads,
        )
        spawn(job.id)
        AuditLog().write(
            "job_created", job=job.id, mode=mode, agents=agents, uploads=len(uploads),
            approved_now=approved_new,
        )
        return _ok(
            f"Job {job.id} queued ({mode}, {agents} agent(s)). Poll muse_job_status.",
            job_id=job.id,
            state=job.state,
            mode=mode,
            agents=agents,
            files_sent=len(uploads),
            ignored=ignored if upload_paths else [],
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_job_status(job_id: str) -> str:
    """State, progress summary, elapsed time and quota used for a job."""
    try:
        job = _registry().get(job_id)
        events, _ = _registry().events_since(job_id, max(0, job.cursor - 5))
        elapsed = (job.finished_at or __import__("time").time()) - job.created_at
        return _ok(
            f"{job_id}: {job.state}" + (f" — {job.pending_question}" if job.pending_question else ""),
            job_id=job.id,
            state=job.state,
            mode=job.mode,
            elapsed_seconds=round(elapsed, 1),
            pending_question=job.pending_question,
            error=job.error,
            # A failure the consumer cannot classify is a support ticket waiting to
            # happen. Surface the stored code and the preserved thread so the user can
            # inspect the right conversation instead of guessing.
            error_code=job.error_code,
            thread_url=job.thread_url,
            next_step=_next_step(job),
            landing_dir=job.landing_dir,
            branch=job.branch,
            agents=job.agents,
            recent_events=[e.get("text", "") for e in events],
            quota_before=job.quota_before,
            quota_after=job.quota_after,
            quota=_quota_report(job),
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_job_events(job_id: str, cursor: int = 0) -> str:
    """Incremental log lines since `cursor`. Returns the next cursor."""
    try:
        events, next_cursor = _registry().events_since(job_id, cursor)
        return _ok(
            f"{len(events)} new event(s)",
            job_id=job_id,
            cursor=next_cursor,
            events=events,
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_job_message(job_id: str, message: str) -> str:
    """Send a steering message to a running job."""
    try:
        job = _registry().get(job_id)
        # A job that stopped because Muse asked something (or produced nothing) can be
        # resumed by an explicit answer. Failures caused by our own guard are not
        # resumable: repeating them would just fail again.
        resumable_failure = (
            job.state == st.FAILED
            and job.thread_url
            and job.error_code in ("no_output", "upstream_error")
        )
        if st.is_terminal(job.state) and not resumable_failure:
            raise MuseBirdError(f"job is already {job.state}")
        path = paths().jobs / f"{job_id}.messages.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(message) + "\n")
        registry = _registry()
        registry.append_event(job_id, "queued steering message")
        resumed = False
        if job.state in (st.NEEDS_INPUT, st.FAILED):
            # Answering is what moves the job forward, and a fresh worker reattaches to
            # the saved thread rather than re-sending the task or adding agents.
            registry.update(
                job_id,
                state=st.RUNNING,
                pending_question=None,
                allow_terminal=job.state == st.FAILED,
            )
            registry.clear_cancel(job_id)
            spawn(job_id)
            resumed = True
        return _ok(
            "Message queued; the job resumes on its saved thread."
            if resumed else "Message queued; the worker will relay it.",
            job_id=job_id,
            resumed=resumed,
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_job_cancel(job_id: str) -> str:
    """Cancel a job. The worker stops at its next safe point."""
    try:
        job = _registry().request_cancel(job_id)
        return _ok(f"Cancel requested for {job_id}.", job_id=job_id, state=job.state)
    except Exception as exc:
        return _err(exc)


# -------------------------------------------------------------------- files
@server.tool()
async def muse_files_list(job_id: str) -> str:
    """List files the job landed locally (manifest view)."""
    try:
        job = _registry().get(job_id)
        if not job.landing_dir:
            return _ok("No files landed yet.", job_id=job_id, files=[])
        root = Path(job.landing_dir)
        files = [
            {"path": p.relative_to(root).as_posix(), "size": p.stat().st_size}
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ]
        return _ok(f"{len(files)} file(s) landed.", job_id=job_id, files=files)
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_files_pull(job_id: str, branch: bool = True, worktree: bool = False) -> str:
    """Verify landed files and commit them to a review branch. Never touches the working tree."""
    try:
        from musebird.sync import landing, manifest

        job = _registry().get(job_id)
        if not job.landing_dir:
            raise MuseBirdError("job has not landed files yet")
        root = Path(job.landing_dir)
        found = manifest.find_manifest(root)
        summary: dict[str, Any] = {"job_id": job_id, "landing_dir": str(root)}
        if found:
            entries = manifest.load(found)
            result = manifest.verify(found.parent, entries)
            summary["verified"] = len(result.verified)
            summary["mismatched"] = result.mismatched
            if not result.ok:
                return json.dumps(
                    {"ok": False, "error": "manifest_error", "summary": "hash verification failed",
                     **summary}, indent=2
                )
        if branch:
            res = landing.land_to_branch(Path(job.project), root, job_id, worktree=worktree)
            summary.update({"branch": res.branch, "commit": res.commit, "worktree": res.worktree})
            _registry().update(job_id, branch=res.branch)
        AuditLog().write("files_pulled", job=job_id, branch=summary.get("branch"))
        return _ok(
            (summary.get("branch") and f"Landed on {summary['branch']}; review the diff.")
            or "Files verified in the landing directory.",
            **summary,
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_files_push(job_id: str, paths_in: list[str], confirm: bool = False) -> str:
    """Upload files to the running job's thread, after guard checks."""
    try:
        from musebird.audit import AuditLog as _Audit
        from musebird.guard.ignore import Ignore as _Ignore
        from musebird.guard.secrets import scan_paths as _scan

        job = _registry().get(job_id)
        project = Path(job.project)
        resolved = guard_paths.check_upload([Path(p) for p in paths_in], project)
        keep, _ = _Ignore(project).filter(resolved)
        findings = _scan(keep, project)
        if findings:
            return json.dumps(
                {"ok": False, "error": "guard_violation",
                 "message": "secret scan blocked the upload",
                 "findings": [f.to_dict() for f in findings]}, indent=2
            )
        if not confirm:
            return json.dumps(
                {"ok": False, "error": "approval_required",
                 "message": "Confirm these files before upload.",
                 "files": [str(p.relative_to(project)) for p in keep]}, indent=2
            )
        if st.is_terminal(job.state):
            raise MuseBirdError(f"job is already {job.state}")
        # The worker owns the browser, so it performs the attach. Queue the paths and
        # let it pick them up on the next poll; nothing is uploaded by this process.
        queue = paths().jobs / f"{job_id}.uploads.jsonl"
        with open(queue, "a", encoding="utf-8") as fh:
            fh.write(json.dumps([str(p) for p in keep]) + "\n")
        _Audit().write("files_queued", job=job_id, files=[p.name for p in keep])
        return _ok(
            f"{len(keep)} file(s) queued for upload to job {job_id}.",
            job_id=job_id,
            files=[str(p.relative_to(project)) for p in keep],
        )
    except Exception as exc:
        return _err(exc)


@server.tool()
async def muse_job_wait(job_id: str, timeout_seconds: int = 60) -> str:
    """Bounded wait: returns as soon as the job is terminal or needs input."""
    try:
        registry = _registry()
        waited = 0.0
        while waited < max(1, timeout_seconds):
            job = registry.get(job_id)
            if job.terminal or job.state == st.NEEDS_INPUT:
                break
            await asyncio.sleep(1.0)
            waited += 1.0
        job = registry.get(job_id)
        return _ok(
            f"{job_id}: {job.state}",
            job_id=job.id,
            state=job.state,
            pending_question=job.pending_question,
            landing_dir=job.landing_dir,
            branch=job.branch,
        )
    except Exception as exc:
        return _err(exc)


def main() -> None:
    paths().ensure()
    if not _approvals_path().exists():
        _approvals_path().write_text('{"projects": []}')
    server.run("stdio")


if __name__ == "__main__":
    main()
