"""A sentinel-less reply that asks a question must become `needs_input`.

Live failure (job_20261122_172637_45f539): Muse replied in two text chunks asking
"do you confirm you want me to spend the 8 agents / quota?", never emitted
`INF-MUSEBIRD-NEEDS-INPUT`, and the job failed 60 seconds later with `no_output`.
The question was lost and the job could not be answered.

The classifier deliberately avoids a `?` heuristic: real deliverables contain questions
and code. These tests use the exact live text plus the traps the report named.
"""

from __future__ import annotations

import pytest

from musebird.prompts import approval_note, build_prompt
from musebird.prompts.classify import classify

#: The two text events from the failed job, verbatim (trimmed).
LIVE_CHUNK_1 = (
    "I haven't started this one — stopping to check first, like the last pasted "
    "MuseBird contracts."
)
LIVE_CHUNK_2 = (
    "This contract (job_20261122_172637_45f539) asks for a full 8-agent fan-out to "
    "rebuild ZORO from scratch, all work only in out/, with a merged game + manifest "
    "+ out.tar.gz attached.\n\nIn your own words: what is this retry actually testing, "
    "and do you confirm you want me to spend the 8 agents / quota and follow those "
    "sandbox limits?\n\nI won't spawn agents or create files until you confirm that "
    "scope."
)


def test_the_live_two_chunk_reply_becomes_needs_input():
    verdict = classify(LIVE_CHUNK_1 + "\n\n" + LIVE_CHUNK_2)
    assert verdict.kind == "needs_input"
    assert verdict.question
    # The question shown to the user must be the question, not the whole reply.
    assert "confirm" in verdict.question.lower()
    assert len(verdict.question) <= 400


def test_the_live_reply_classifies_the_same_whether_split_or_whole():
    """Muse streamed it in two events; only the settled text is classified."""
    assert classify(LIVE_CHUNK_2).kind == "needs_input"


def test_a_real_question_with_no_context_is_needs_input():
    verdict = classify("Which framework should I use for this project?")
    assert verdict.kind == "needs_input"
    assert "framework" in (verdict.question or "")


def test_an_explicit_sentinel_still_wins():
    from musebird.prompts import parse_sentinel
    from musebird.upstream import muse

    kind, _ = parse_sentinel(f"All done.\n{muse.DONE_SENTINEL} job_1")
    assert kind == "done"


# -- the traps the report warned about -------------------------------------
def test_a_deliverable_with_artefacts_is_completed_even_with_a_question():
    text = (
        "I built the game. The files are in out/ and I wrote out/MANIFEST.json. It works "
        "in Chrome and Firefox.\n\nOne thing to review: is the jump height too high?"
    )
    assert classify(text).kind == "completed"


def test_a_question_inside_a_code_fence_is_not_a_question():
    text = (
        "Done. Here is the handler:\n\n```python\ndef check(x):\n"
        "    return x is not None  # is it set?\n```\n\nI wrote out/MANIFEST.json."
    )
    assert classify(text).kind == "completed"


def test_a_quoted_instruction_is_not_the_users_question():
    text = (
        "> Also: is this correct?\n\n"
        "I applied the change you asked for and wrote out/MANIFEST.json with the hashes."
    )
    assert classify(text).kind == "completed"


def test_a_plain_finished_reply_without_artefacts_is_ambiguous_not_completed():
    """No sentinel, no artefacts, no question: do not claim either way."""
    verdict = classify("I created out/hello.txt containing the text you asked for.")
    assert verdict.kind == "ambiguous"


def test_an_empty_reply_is_ambiguous():
    assert classify("   ").kind == "ambiguous"


def test_a_long_reply_ending_in_a_question_is_not_auto_promoted():
    body = " ".join(["Step completed and verified with real output."] * 30)
    verdict = classify(body + " Do you want a follow-up?")
    assert verdict.kind == "ambiguous"


@pytest.mark.parametrize(
    "phrase",
    [
        "Please confirm the scope before I continue.",
        "Should I proceed with the eight agents?",
        "Let me know whether to merge now.",
        "I need your decision on the tech stack.",
    ],
)
def test_explicit_confirmation_requests_are_needs_input(phrase):
    assert classify(phrase).kind == "needs_input"


def test_waiting_without_a_question_mark_is_still_needs_input():
    text = "I have not started. Waiting for your go-ahead on the agent count."
    assert classify(text).kind == "needs_input"


# -- the prompt must say what is already approved --------------------------
def test_the_prompt_states_pre_approved_scope():
    prompt = build_prompt(
        "build the game",
        "job_x",
        mode="fanout",
        agents=8,
        approval_note=approval_note("fanout", 8),
    )
    assert "ALREADY APPROVED" in prompt
    assert "do not ask again" in prompt
    assert "8 agents" in prompt
    assert "out.tar.gz" in prompt


def test_single_mode_approval_note_mentions_one_job():
    note = approval_note("single", 1)
    assert "single job" in note


# -- resume an answered job without restarting it -------------------------
def test_a_failed_job_can_be_resumed_but_not_a_guard_failure():
    from musebird.jobs import state as st

    assert st.can_transition(st.FAILED, st.RUNNING)
    assert not st.can_transition(st.FAILED, st.DONE)
    assert not st.can_transition(st.FAILED, st.QUEUED)


@pytest.mark.asyncio
async def test_driver_emits_needs_input_for_a_question_without_a_sentinel(monkeypatch):
    """The driver must not turn a question into `completed`."""
    from musebird.drivers.browser.driver import BrowserDriver
    from musebird.upstream import muse

    class Page:
        def locator(self, sel):
            class Empty:
                async def count(self):
                    return 0

            return Empty()

        async def evaluate(self, script, arg=None):
            return []

    driver = BrowserDriver("t")
    driver._page = Page()
    driver._last_text = LIVE_CHUNK_1 + "\n\n" + LIVE_CHUNK_2
    driver._stable = 99
    driver._bubble_count = 1

    async def bubbles():
        return [driver._last_text]

    async def not_generating():
        return False

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(driver, "_read_bubbles", bubbles)
    monkeypatch.setattr(driver, "_read_assistant_text", lambda: _coro(driver._last_text))
    monkeypatch.setattr(driver, "_generating", not_generating)
    monkeypatch.setattr("musebird.drivers.browser.driver.asyncio.sleep", no_sleep)

    from musebird.drivers.base import ThreadRef

    events, _ = await driver.stream_events(ThreadRef(id="t", url="u"), 0)
    kinds = [e.kind for e in events]
    assert "needs_input" in kinds, events
    needs = next(e for e in events if e.kind == "needs_input")
    assert muse.NEEDS_INPUT_SENTINEL in needs.text
    assert "confirm" in needs.text.lower()


async def _coro(value):
    return value
