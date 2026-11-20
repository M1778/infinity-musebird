from __future__ import annotations

from musebird.prompts import build_prompt, parse_sentinel
from musebird.upstream import muse


def test_sentinel_done():
    kind, payload = parse_sentinel(f"all good\n{muse.DONE_SENTINEL} job_1\n")
    assert kind == "done"
    assert payload == "job_1"


def test_sentinel_needs_input():
    text = f"{muse.NEEDS_INPUT_SENTINEL} job_2: Which database?"
    kind, payload = parse_sentinel(text)
    assert kind == "needs_input"
    assert payload == "Which database?"


def test_sentinel_takes_last_occurrence():
    text = (
        f"I will print {muse.DONE_SENTINEL} <job_id> when done.\n"
        f"{muse.DONE_SENTINEL} real_job\n"
    )
    assert parse_sentinel(text) == ("done", "real_job")


def test_no_sentinel():
    assert parse_sentinel("still working") == ("none", None)


def test_prompt_contains_contract_and_task(tmp_path):
    prompt = build_prompt("Build a site", "job_x", mode="fanout", agents=4)
    assert "Build a site" in prompt
    assert "job_x" in prompt
    assert "fanout" in prompt
    assert "4 subagents" in prompt
    assert muse.DONE_SENTINEL in prompt
    assert "MANIFEST.json" in prompt
