"""Classify a settled Muse reply that carries no completion sentinel.

A reply with no `INF-MUSEBIRD-DONE` may be a finished task whose sentinel the model
forgot, or it may be **asking the user a question**. The distinction is expensive to
get wrong: a question misread as completion becomes a terminal `no_output` failure and
the existing job can no longer be answered
(`job_20261122_172637_45f539` — Muse asked "do you confirm you want me to spend the 8
agents / quota?" and the job died 60 seconds later).

A `?` heuristic is not acceptable: real deliverables contain questions, quoted
instructions, and code. This module looks for **concrete evidence of asking**, and any
ambiguity is reported as `ambiguous` — never silently treated as finished.

Evidence for `needs_input` (any of):
  * the last non-empty line ends with `?` **and** the reply says it has not started or
    is waiting (`I haven't started`, `waiting for`, `before I continue`, ...)
  * an explicit request for confirmation (`do you confirm`, `please confirm`, `should I`,
    `can you clarify`, `let me know`, ...) near the end of the reply
  * the reply is short and consists only of a question

Deliberately **not** evidence:
  * a question mark alone
  * a question inside a fenced code block or a quotation at the start of the reply
  * a reply that also reports a written manifest/sentinel-like completion phrase
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Phrases that mean "I have stopped and I am waiting for you".
WAITING = (
    "haven't started",
    "have not started",
    "not started",
    "didn't start",
    "did not start",
    "nothing has been started",
    "waiting for your",
    "waiting for you",
    "awaiting your",
    "awaiting confirmation",
    "until you confirm",
    "before i continue",
    "before i start",
    "before proceeding",
    "before i proceed",
    "hold off",
    "paused",
    "stopping to check",
)

#: Phrases that explicitly ask the user to decide something.
CONFIRMATION = (
    "do you confirm",
    "can you confirm",
    "please confirm",
    "confirm that",
    "confirm you want",
    "can you clarify",
    "could you clarify",
    "please clarify",
    "do you want me to",
    "would you like me to",
    "should i proceed",
    "should i start",
    "shall i",
    "let me know",
    "which would you prefer",
    "i need your",
    "tell me which",
)

#: Phrases that prove the task really finished, even without the sentinel.
COMPLETION = (
    "manifest.json",
    "out.tar.gz",
    "please find attached",
    "attached the",
    "i've attached",
    "i have attached",
)

_QUESTION_END = re.compile(r"[?]\s*$")
_FENCE = re.compile(r"```.*?```", re.S)


@dataclass(frozen=True)
class Verdict:
    """`kind` is one of `needs_input`, `ambiguous`, `completed`."""

    kind: str
    question: str | None = None
    reason: str = ""


def _strip_code_and_quotes(text: str) -> str:
    """Remove fenced code and block quotes: code contains `?`, deliverables are prose."""
    text = _FENCE.sub(" ", text)
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines)


def _last_lines(text: str, count: int = 4) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return " ".join(lines[-count:]).lower()


def _extract_question(text: str) -> str:
    """The part the user must answer: trailing sentences that ask something.

    Kept short and sanitised — this text is shown to a user and goes into the job
    record, so it must not carry a whole deliverable.
    """
    clean = _strip_code_and_quotes(text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    picked: list[str] = []
    for sentence in reversed(sentences):
        trimmed = sentence.strip()
        if not trimmed:
            continue
        # Keep the question and the sentence just before it (often its context).
        if "?" in trimmed or any(p in trimmed.lower() for p in CONFIRMATION):
            picked.insert(0, trimmed)
            if len(picked) >= 2:
                break
        elif picked:
            break
    question = " ".join(picked).strip() or clean[-300:]
    return question[:400]


def classify(text: str) -> Verdict:
    """Decide whether a sentinel-less reply is a question, a result, or ambiguous."""
    if not text or not text.strip():
        return Verdict("ambiguous", None, "empty reply")

    prose = _strip_code_and_quotes(text)
    tail = _last_lines(prose)
    lowered = prose.lower()

    finished = any(p in lowered for p in COMPLETION)
    waiting = any(p in tail for p in WAITING) or any(p in lowered for p in WAITING)
    asking = any(p in tail for p in CONFIRMATION)
    ends_with_question = bool(_QUESTION_END.search(prose.strip()))

    # Concrete evidence of asking wins over "no files" — this is the live failure.
    if asking or (ends_with_question and waiting):
        return Verdict(
            "needs_input",
            _extract_question(text),
            "reply asks the user to confirm or choose",
        )

    # Waiting for a decision without an explicit question still needs the user.
    if waiting and not finished:
        return Verdict(
            "needs_input",
            _extract_question(text),
            "reply says it has not started or is waiting",
        )

    # A reported attachment/manifest means the work landed; treat as finished.
    if finished:
        return Verdict("completed", None, "reply references produced artefacts")

    if ends_with_question:
        # A *short* reply that is essentially only a question is asking, not
        # delivering: there is no room in it for a result. A long reply may end with a
        # question and still be a deliverable, so length is the discriminator.
        condensed = " ".join(prose.split())
        if len(condensed) <= 400 and condensed.count("?") >= 1:
            return Verdict(
                "needs_input",
                _extract_question(text),
                "short reply that is only a question",
            )
        return Verdict(
            "ambiguous",
            _extract_question(text),
            "reply ends with a question but reports no waiting and no artefacts",
        )

    return Verdict("ambiguous", None, "no sentinel, no artefacts, no question")
