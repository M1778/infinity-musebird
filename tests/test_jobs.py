from __future__ import annotations

import pytest

from musebird.errors import JobConflict
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry


def test_transitions_forward_only():
    assert st.transition(st.QUEUED, st.WAKING) == st.WAKING
    assert st.transition(st.WAKING, st.RUNNING) == st.RUNNING
    assert st.transition(st.RUNNING, st.NEEDS_INPUT) == st.NEEDS_INPUT
    assert st.transition(st.NEEDS_INPUT, st.RUNNING) == st.RUNNING
    assert st.transition(st.RUNNING, st.DONE) == st.DONE
    with pytest.raises(JobConflict):
        st.transition(st.DONE, st.RUNNING)


def test_terminal_is_terminal():
    for s in st.TERMINAL:
        assert st.is_terminal(s)
    assert not st.is_terminal(st.NEEDS_INPUT)


def test_create_get_update_roundtrip():
    reg = JobRegistry()
    job = reg.create(prompt="hello", project="/tmp/x")
    assert reg.get(job.id).prompt == "hello"
    reg.update(job.id, state=st.WAKING)
    reg.update(job.id, state=st.RUNNING)
    assert reg.get(job.id).started_at is not None
    reg.update(job.id, state=st.DONE)
    assert reg.get(job.id).finished_at is not None
    with pytest.raises(JobConflict):
        reg.update(job.id, state=st.RUNNING)


def test_events_cursor_incremental():
    reg = JobRegistry()
    job = reg.create(prompt="x")
    reg.append_event(job.id, "one")
    reg.append_event(job.id, "two", kind="progress")
    events, cursor = reg.events_since(job.id, 0)
    assert [e["text"] for e in events] == ["one", "two"]
    assert cursor == 2
    more, cursor2 = reg.events_since(job.id, cursor)
    assert more == []
    assert cursor2 == 2
    reg.append_event(job.id, "three")
    more, _ = reg.events_since(job.id, cursor)
    assert [e["text"] for e in more] == ["three"]


def test_cancel_flag_and_state():
    reg = JobRegistry()
    running = reg.create(prompt="r")
    reg.update(running.id, state=st.WAKING)
    reg.update(running.id, state=st.RUNNING)
    reg.request_cancel(running.id)
    assert reg.cancel_requested(running.id)
    assert reg.get(running.id).state == st.RUNNING  # worker decides when to stop

    queued = reg.create(prompt="q")
    reg.request_cancel(queued.id)
    assert reg.get(queued.id).state == st.CANCELLED


def test_list_is_newest_first_and_skips_corrupt(tmp_path):
    reg = JobRegistry()
    a = reg.create(prompt="a")
    b = reg.create(prompt="b")
    (reg.paths.jobs / "broken.json").write_text("{not json")
    ids = [j.id for j in reg.list()]
    assert ids[0] == b.id
    assert a.id in ids


def test_missing_job_is_clean_error():
    from musebird.errors import JobNotFound

    with pytest.raises(JobNotFound):
        JobRegistry().get("nope")
