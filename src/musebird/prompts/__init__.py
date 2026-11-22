"""Prompt construction: the user's task plus the non-negotiable contract."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from musebird.upstream import muse

FALLBACK_CONTRACT = Path(__file__).with_name("contract.md")


def contract_text() -> str:
    try:
        return resources.files("musebird.prompts").joinpath("contract.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return FALLBACK_CONTRACT.read_text(encoding="utf-8")


def build_prompt(
    task: str,
    job_id: str,
    *,
    mode: str = "single",
    agents: int = 1,
    approval_note: str | None = None,
) -> str:
    """Assemble the task and the contract.

    `approval_note` records decisions the user already made before this job started
    (agent count, quota spend, sandbox limits). Without it Muse may stop to confirm
    scope that was in fact approved, which costs a round trip and can lose the job —
    live evidence: job_20261122_172637_45f539 asked "do you confirm you want me to
    spend the 8 agents / quota?" and nothing had started.
    """
    header = [
        "You are running inside a MuseBird job.",
        f"Job id: {job_id}",
        f"Mode: {mode}",
    ]
    if mode == "fanout":
        header.append(f"Fan out into {agents} subagents, cross-review, then merge.")
    if approval_note:
        header.append(
            "ALREADY APPROVED by the user before this job started — do not ask again: "
            + approval_note
        )
    body = "\n".join(header) + "\n\n" + contract_text() + "\n\n## Task\n\n" + task.strip() + "\n"
    return body


def approval_note(mode: str, agents: int) -> str:
    """The scope facts the user already settled by starting this job."""
    limits = (
        "work only inside out/, never read or write outside it, and attach "
        "out.tar.gz plus out/MANIFEST.json when done"
    )
    if mode == "fanout":
        return f"spending quota on {agents} agents; {limits}."
    return f"spending quota on this single job; {limits}."


DONE_RE = re.compile(rf"{re.escape(muse.DONE_SENTINEL)}\s+(\S+)")
NEEDS_INPUT_RE = re.compile(rf"{re.escape(muse.NEEDS_INPUT_SENTINEL)}\s+(\S+?):\s*(.+)")


def parse_sentinel(text: str) -> tuple[str, str | None]:
    """Parse a batch of streamed text into `(kind, payload)`.

    kind is one of `done`, `needs_input`, `none`. Whichever sentinel appears *last*
    wins: a resumed thread still shows the question that was already answered, and
    the reply to it must not be mistaken for a new question.
    """
    candidates: list[tuple[int, str, str]] = []
    for match in NEEDS_INPUT_RE.finditer(text):
        candidates.append((match.start(), "needs_input", match.group(2).strip()))
    for match in DONE_RE.finditer(text):
        candidates.append((match.start(), "done", match.group(1)))
    if not candidates:
        return "none", None
    _, kind, payload = max(candidates, key=lambda item: item[0])
    return kind, payload
