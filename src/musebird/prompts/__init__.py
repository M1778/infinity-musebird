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


def build_prompt(task: str, job_id: str, *, mode: str = "single", agents: int = 1) -> str:
    header = [
        "You are running inside a MuseBird job.",
        f"Job id: {job_id}",
        f"Mode: {mode}",
    ]
    if mode == "fanout":
        header.append(f"Fan out into {agents} subagents, cross-review, then merge.")
    body = "\n".join(header) + "\n\n" + contract_text() + "\n\n## Task\n\n" + task.strip() + "\n"
    return body


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
