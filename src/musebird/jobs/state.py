"""Job state machine, pure and testable.

A job moves forward through `STATES`; cancellation is allowed from any state that
is not terminal. Invalid transitions raise instead of silently overwriting, because
a job record is the only durable truth about what ran.
"""

from __future__ import annotations

from typing import Final

QUEUED: Final = "queued"
WAKING: Final = "waking"
RUNNING: Final = "running"
NEEDS_INPUT: Final = "needs_input"
DONE: Final = "done"
FAILED: Final = "failed"
CANCELLED: Final = "cancelled"

STATES: Final = (QUEUED, WAKING, RUNNING, NEEDS_INPUT, DONE, FAILED, CANCELLED)
TERMINAL: Final = frozenset({DONE, FAILED, CANCELLED})

ALLOWED: Final[dict[str, frozenset[str]]] = {
    QUEUED: frozenset({WAKING, CANCELLED, FAILED}),
    WAKING: frozenset({RUNNING, FAILED, CANCELLED}),
    RUNNING: frozenset({NEEDS_INPUT, DONE, FAILED, CANCELLED}),
    NEEDS_INPUT: frozenset({RUNNING, DONE, FAILED, CANCELLED}),
    # A failed job can be resumed by an explicit user answer on its saved thread. It
    # is never resumed automatically: retrying costs quota, which is the user's call.
    FAILED: frozenset({RUNNING}),
}


def can_transition(current: str, new: str) -> bool:
    return new in ALLOWED.get(current, frozenset())


def transition(current: str, new: str) -> str:
    if not can_transition(current, new):
        from musebird.errors import JobConflict

        raise JobConflict(f"cannot move job from {current!r} to {new!r}")
    return new


def is_terminal(state: str) -> bool:
    return state in TERMINAL
