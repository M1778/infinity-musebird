"""Durable job registry.

One JSON record per job plus an append-only JSONL event log. Records are written
atomically (temp file + replace) so a crashed worker cannot leave a truncated
record. The worker will be launched as a separate process by the MCP server, so
all coordination between them goes through these files and a cancel flag, never
through shared memory.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from musebird.config import Paths, paths
from musebird.errors import JobNotFound

from . import state as st


def new_job_id() -> str:
    return f"job_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


@dataclass
class Job:
    id: str
    prompt: str
    mode: str = "single"
    agents: int = 1
    budget: str | None = None
    sync: str = "bundle"
    driver: str = "browser"
    project: str = ""
    upload_paths: list[str] = field(default_factory=list)
    state: str = st.QUEUED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cursor: int = 0
    quota_before: dict | None = None
    quota_after: dict | None = None
    error: str | None = None
    error_code: str | None = None
    thread_url: str | None = None
    landing_dir: str | None = None
    branch: str | None = None
    pending_question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def terminal(self) -> bool:
        return st.is_terminal(self.state)


class JobRegistry:
    def __init__(self, paths_: Paths | None = None) -> None:
        self.paths = paths_ or paths()
        self.paths.jobs.mkdir(parents=True, exist_ok=True)

    # -- record io -------------------------------------------------------
    def _record(self, job_id: str) -> Path:
        return self.paths.jobs / f"{job_id}.json"

    def _events_file(self, job_id: str) -> Path:
        return self.paths.jobs / f"{job_id}.jsonl"

    def _cancel_flag(self, job_id: str) -> Path:
        return self.paths.jobs / f"{job_id}.cancel"

    def create(self, **kwargs: Any) -> Job:
        job = Job(id=kwargs.pop("id", new_job_id()), **kwargs)
        job.project = str(job.project or "")
        self._write(job)
        self._events_file(job.id).touch()
        return job

    def _write(self, job: Job) -> None:
        job.updated_at = time.time()
        data = json.dumps(job.to_dict(), indent=2).encode()
        target = self._record(job.id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def get(self, job_id: str) -> Job:
        path = self._record(job_id)
        if not path.exists():
            raise JobNotFound(f"no job named {job_id!r}")
        return Job.from_dict(json.loads(path.read_text()))

    def list(self, limit: int = 20) -> list[Job]:
        files = sorted(self.paths.jobs.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for f in files[:limit]:
            try:
                out.append(Job.from_dict(json.loads(f.read_text())))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def find_running(self) -> Iterator[Job]:
        for job in self.list(limit=200):
            if not job.terminal:
                yield job

    # -- mutation --------------------------------------------------------
    def update(self, job_id: str, *, allow_terminal: bool = False, **fields: Any) -> Job:
        job = self.get(job_id)
        new_state = fields.get("state")
        if new_state and new_state != job.state:
            if job.terminal and not allow_terminal:
                from musebird.errors import JobConflict

                raise JobConflict(f"job {job_id} already {job.state}")
            fields["state"] = st.transition(job.state, new_state)
            if fields["state"] == st.RUNNING and job.started_at is None:
                fields.setdefault("started_at", time.time())
            if st.is_terminal(fields["state"]):
                fields.setdefault("finished_at", time.time())
        for key, value in fields.items():
            if not hasattr(job, key):
                raise AttributeError(f"unknown job field {key!r}")
            setattr(job, key, value)
        self._write(job)
        return job

    def request_cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.terminal:
            return job
        self._cancel_flag(job_id).write_text("1")
        if job.state == st.QUEUED:
            return self.update(job_id, state=st.CANCELLED)
        self.append_event(job_id, "cancel requested", kind="info")
        return job

    def cancel_requested(self, job_id: str) -> bool:
        return self._cancel_flag(job_id).exists()

    # -- events ----------------------------------------------------------
    def append_event(self, job_id: str, text: str, *, kind: str = "info", data: dict | None = None) -> int:
        record = {"ts": time.time(), "kind": kind, "text": text}
        if data:
            record["data"] = data
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._events_file(job_id), "a", encoding="utf-8") as fh:
            fh.write(line)
        job = self.get(job_id)
        job.cursor += 1
        self._write(job)
        return job.cursor

    def events_since(self, job_id: str, cursor: int = 0) -> tuple[list[dict], int]:
        path = self._events_file(job_id)
        if not path.exists():
            return [], cursor
        out: list[dict] = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i < cursor:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out, cursor + len(out)

    def clear_cancel(self, job_id: str) -> None:
        self._cancel_flag(job_id).unlink(missing_ok=True)

    def redact_paths(self, job_id: str) -> None:
        """Drop absolute paths from the record when a job is purged."""
        self._record(job_id).unlink(missing_ok=True)
        self._events_file(job_id).unlink(missing_ok=True)
        self._cancel_flag(job_id).unlink(missing_ok=True)


def short_id() -> str:
    return uuid.uuid4().hex[:8]
