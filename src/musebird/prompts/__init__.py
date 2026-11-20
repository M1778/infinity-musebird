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

    kind is one of `done`, `needs_input`, `none`. The match is anchored to the
    sentinel token wherever it appears in the text, matching the last occurrence
    so a model that quotes the contract earlier does not trigger early.
    """
    needs = NEEDS_INPUT_RE.findall(text)
    if needs:
        return "needs_input", needs[-1][1].strip()
    done = DONE_RE.findall(text)
    if done:
        return "done", done[-1]
    return "none", None
