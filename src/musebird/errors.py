"""Error types shared across musebird.

Every user-visible failure is a MuseBirdError subclass so the CLI and MCP layer
can render one consistent, redacted message instead of leaking tracebacks.
"""

from __future__ import annotations


class MuseBirdError(Exception):
    """Base error. `code` is stable and machine readable; `hint` is a one-line fix."""

    code = "musebird_error"
    hint = ""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        if hint:
            self.hint = hint

    def to_dict(self) -> dict[str, str]:
        out = {"error": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out


class LoginRequired(MuseBirdError):
    code = "login_required"
    hint = "Run `musebird login` on this machine, then retry."


class DriverUnavailable(MuseBirdError):
    code = "driver_unavailable"
    hint = "Run `musebird doctor` to see which component is missing."


class UpstreamError(MuseBirdError):
    code = "upstream_error"


class QuotaExceeded(MuseBirdError):
    code = "quota_exceeded"
    hint = "Wait for your Muse allowance to reset or reduce the job size."


class GuardViolation(MuseBirdError):
    code = "guard_violation"


class JobNotFound(MuseBirdError):
    code = "job_not_found"


class JobConflict(MuseBirdError):
    code = "job_conflict"


class ManifestError(MuseBirdError):
    code = "manifest_error"
