"""Append-only audit log.

Stores what ran and what left the machine, never prompt bodies or file contents.
Prompt is stored as a sha256 so the same task can be recognised without retention.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from musebird.config import Paths, paths


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, paths_: Paths | None = None) -> None:
        self.paths = paths_ or paths()

    def write(self, event: str, **fields: object) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        # O_APPEND so concurrent workers cannot interleave partial lines.
        fd = os.open(self.paths.audit_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line)

    def read(self, limit: int = 100) -> list[dict]:
        path = self.paths.audit_log
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
