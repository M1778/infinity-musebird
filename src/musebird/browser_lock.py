"""Single-writer lock for the shared browser profile.

Chromium refuses to open one `user_data_dir` twice ("Failed to create a
ProcessSingleton"), and two musebird processes sharing a profile would race on the
same cookies. This lock serialises them:

* POSIX: `fcntl.flock` on a lock file — released automatically if the process dies.
* Windows: `msvcrt.locking` — best effort, same semantics for our use.
* Optional `timeout`: wait up to N seconds, then raise with a clear message.

The lock is held for the duration of one browser session, not the whole job, so a
queued job can start as soon as the previous one closes its context.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path

from musebird.errors import DriverUnavailable


def clear_stale_singletons(profile: Path) -> list[str]:
    """Remove Chromium's leftover Singleton* links when no process holds them.

    A crashed browser leaves `SingletonLock` pointing at a dead pid, and the next
    launch fails with "Failed to create a ProcessSingleton". We only remove the
    links after the profile lock is held, which means no musebird process is using
    them; a browser owned by something else keeps them and we leave the dir alone.
    """
    removed: list[str] = []
    if not profile.is_dir():
        return removed
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        link = profile / name
        if not link.is_symlink() and not link.exists():
            continue
        with contextlib.suppress(Exception):
            if name == "SingletonLock":
                pid = _singleton_pid(link)
                if pid is not None and _pid_alive(pid):
                    continue  # a live process owns it: leave it alone
            link.unlink()
            removed.append(name)
    return removed


def _singleton_pid(link: Path) -> int | None:
    """The pid Chromium recorded, if this platform stores one.

    POSIX: a symlink whose target is `hostname-<pid>`. Windows: a normal file whose
    content is `<hostname>:<pid>` or a bare pid. Anything unparseable means "unknown",
    which the caller treats as stale.
    """
    try:
        raw = os.readlink(link) if link.is_symlink() else link.read_text(errors="replace")
    except OSError:
        return None
    text = str(raw).strip()
    tail = text.replace(":", "-").rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid exists and is not a zombie."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


class ProfileLock:
    def __init__(self, profile: Path, *, timeout: float = 900.0, poll: float = 0.5) -> None:
        self.path = profile.parent / (profile.name + ".lock")
        # The owner pid lives beside the lock, not inside it: on Windows the lock is
        # a byte-range lock, and truncating the locked byte is not portable. Only the
        # holder writes this file, after it owns the lock.
        self.owner_path = profile.parent / (profile.name + ".lock.owner")
        self.timeout = timeout
        self.poll = poll
        self._fd: int | None = None

    def __enter__(self) -> ProfileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_lock():
                with contextlib.suppress(OSError):
                    self.owner_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                return self
            if time.monotonic() >= deadline:
                os.close(self._fd)
                self._fd = None
                holder = ""
                with contextlib.suppress(OSError):
                    raw = self.path.read_text(encoding="utf-8", errors="replace").strip()
                    holder = f" (pid {raw})" if raw.isdigit() else ""
                raise DriverUnavailable(
                    f"the Muse browser profile is busy{holder}",
                    hint=(
                        "Another job or `musebird login` is using the profile. Wait for it, "
                        "or run `musebird jobs list` then `musebird jobs cancel <id>`. "
                        "If no job is running, a crashed browser left a lock behind: "
                        "`musebird doctor` reports it and `musebird doctor --fix` clears it."
                    ),
                )
            time.sleep(self.poll)

    def _try_lock(self) -> bool:
        assert self._fd is not None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, ImportError):
            return False

    def __exit__(self, *exc: object) -> None:
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(self._fd)
        with contextlib.suppress(OSError):
            self.owner_path.unlink()
        self._fd = None


def force_unlock(profile: Path) -> list[str]:
    """Break a lock left by a crashed process, refusing while a browser is alive.

    Called only from an explicit user action (`musebird doctor --fix`), never from a
    job: silently stealing a live profile would corrupt two sessions at once.
    """
    if lock_status(profile)["held"]:
        holder = lock_status(profile)["pid"]
        if holder is not None:
            try:
                os.kill(holder, 0)
                raise DriverUnavailable(
                    f"refusing to break the lock: pid {holder} is still running",
                    hint="Wait for that process, or stop it yourself first.",
                )
            except (ProcessLookupError, PermissionError):
                pass
    return clear_stale_singletons(profile)


def lock_status(profile: Path) -> dict:
    """Who holds the profile lock, for `musebird doctor` and job errors."""
    lock = profile.parent / (profile.name + ".lock")
    info: dict = {"path": str(lock), "exists": lock.exists(), "pid": None, "held": False}
    if not lock.exists():
        return info
    with contextlib.suppress(OSError):
        raw = (profile.parent / (profile.name + ".lock.owner")).read_text(
            encoding="utf-8", errors="replace"
        ).strip()
        info["pid"] = int(raw) if raw.isdigit() else None
    # Probe with a single non-blocking attempt. On Windows, msvcrt.locking against a
    # byte another handle holds can block inside the OS call instead of returning, so
    # "try with a timeout" is not safe here: exactly one attempt, never a wait.
    probe = ProfileLock(profile, timeout=0.0, poll=0.0)
    try:
        probe._fd = os.open(probe.path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        info["held"] = True
        return info
    got = probe._try_lock()
    info["held"] = not got
    if got:
        probe.__exit__()
    else:
        with contextlib.suppress(OSError):
            os.close(probe._fd)
        probe._fd = None
    return info
