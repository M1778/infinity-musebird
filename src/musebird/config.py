"""Filesystem layout and small shared helpers.

Everything musebird writes lives under one home directory so `musebird uninstall`
and the security documentation have a single path to describe.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "infinity-musebird"
ENV_HOME = "MUSEBIRD_HOME"
ENV_PROJECT = "MUSEBIRD_PROJECT"


def home() -> Path:
    """State directory. Overridable so tests never touch a real home."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path.home() / ".infinity-musebird"


def project_dir() -> Path:
    override = os.environ.get(ENV_PROJECT)
    return Path(override).expanduser().resolve() if override else Path.cwd().resolve()


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def session_file(self) -> Path:
        return self.root / "session.enc"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def quota_cache(self) -> Path:
        return self.root / "quota.json"

    @property
    def audit_log(self) -> Path:
        return self.logs / "audit.jsonl"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals.json"

    @property
    def browser_profile(self) -> Path:
        return self.root / "browser-profile"

    @property
    def install_state(self) -> Path:
        return self.root / "install-state.json"

    def ensure(self) -> Path:
        for p in (self.root, self.jobs, self.logs, self.backups):
            p.mkdir(parents=True, exist_ok=True)
        _restrict(self.root)
        return self.root


def paths() -> Paths:
    return Paths(home())


def _restrict(path: Path) -> None:
    """Best-effort 0700 on POSIX; no-op on Windows (ACLs differ)."""
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def secure_write(path: Path, data: bytes) -> None:
    """Write a file readable only by the current user, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            path.chmod(0o600)
