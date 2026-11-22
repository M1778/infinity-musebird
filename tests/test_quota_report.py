"""Quota reporting must never claim more precision than it has.

An earlier version exposed `quota_charged = quota_after is not None`. That conflates
"a snapshot exists" with "this job was charged": a null snapshot is unknown cost, not
free, and a non-null one may be unchanged or reflect other activity. These tests pin
the honest representation requested in
docs/MUSE_ISSUES_2026-11-22.md.
"""

from __future__ import annotations

import pytest

from musebird.jobs.registry import Job
from musebird.mcp_server import _quota_report


def _job(before=None, after=None) -> Job:
    return Job(id="j", prompt="x", quota_before=before, quota_after=after)


def _q(used, resets="Nov 27"):
    return {"used": used, "limit": 100.0, "resets_at": resets}


def test_no_terminal_snapshot_is_unknown_not_free():
    report = _quota_report(_job(_q(3.0), None))
    assert report["observed"] is False
    assert report["charge"] == "unknown"
    assert "not zero" in report["note"]
    assert report["delta_used"] is None


def test_no_baseline_cannot_produce_a_delta():
    report = _quota_report(_job(None, _q(5.0)))
    assert report["observed"] is True
    assert report["charge"] == "unknown"
    assert "no baseline" in report["note"]


def test_comparable_snapshots_report_a_delta_with_caveat():
    report = _quota_report(_job(_q(3.0), _q(11.5)))
    assert report["observed"] is True
    assert report["delta_used"] == 8.5
    assert report["charge"] == "observed-delta"
    # Account-level, not per-job billing.
    assert "not an exact per-job cost" in report["note"]


def test_unchanged_snapshot_is_zero_delta_not_a_guess():
    report = _quota_report(_job(_q(4.0), _q(4.0)))
    assert report["delta_used"] == 0.0
    assert report["charge"] == "observed-delta"


def test_a_reset_makes_measurements_incomparable():
    report = _quota_report(_job(_q(90.0, "Nov 27"), _q(2.0, "Dec 04")))
    assert report["charge"] == "unknown"
    assert report["delta_used"] is None
    assert "reset" in report["note"]


def test_usage_falling_is_not_attributed_to_the_job():
    report = _quota_report(_job(_q(40.0), _q(10.0)))
    assert report["charge"] == "unknown"
    assert "not attributable" in report["note"]


def test_missing_percentage_is_not_a_zero():
    report = _quota_report(_job({"used": None, "resets_at": "Nov 27"}, _q(6.0)))
    assert report["charge"] == "unknown"
    assert report["delta_used"] is None


@pytest.mark.parametrize("before,after", [(None, None), (_q(1.0), None), (None, _q(1.0))])
def test_partial_information_never_becomes_a_positive_charge(before, after):
    report = _quota_report(_job(before, after))
    assert report["charge"] == "unknown"
