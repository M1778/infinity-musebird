"""Audit-log contract.

secret-scan: allow

The spec requires an append-only log of jobs with *no prompt or code content* and a
guarantee that cookie values never reach it. These tests assert both directly: they
grep the raw log file for the exact secrets and prompt text used in the run, so a
future change that starts logging payloads fails here.
"""

from __future__ import annotations

import json

import pytest

from musebird.audit import AuditLog, prompt_hash
from musebird.drivers.mock import MockDriver
from musebird.jobs import state as st
from musebird.jobs.registry import JobRegistry
from musebird.jobs.worker import Worker
from musebird.session.vault import Vault

SECRET_PROMPT = "Refactor the billing module. API key is sk-ant-SENTINEL-DO-NOT-LOG-1234"
SECRET_FILE_BODY = "password = 'hunter2-SENTINEL-DO-NOT-LOG'"


def _raw_log(sandbox_home) -> str:
    path = sandbox_home.audit_log
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_prompt_hash_is_stable_and_not_reversible():
    assert prompt_hash("abc") == prompt_hash("abc")
    assert prompt_hash("abc") != prompt_hash("abd")
    assert len(prompt_hash("abc")) == 64
    assert "abc" not in prompt_hash("abc")


async def test_job_lifecycle_logs_hashes_not_content(tmp_path, project, sandbox_home):
    (project / "billing.py").write_text(SECRET_FILE_BODY + "\n")
    reg = JobRegistry()
    job = reg.create(
        prompt=SECRET_PROMPT,
        project=str(project),
        driver="mock",
        upload_paths=[str(project / "billing.py")],
    )
    final = await Worker(job.id, registry=reg, driver=MockDriver(job.id), poll=0.01).run()
    assert final.state == st.DONE

    raw = _raw_log(sandbox_home)
    assert raw, "the audit log was never written"
    # The exact prompt and file contents must not appear anywhere in the log.
    assert SECRET_PROMPT not in raw
    assert "SENTINEL-DO-NOT-LOG" not in raw
    assert "sk-ant-SENTINEL" not in raw
    assert "hunter2" not in raw
    # The hash *is* recorded, so a run can be identified without retaining it.
    assert prompt_hash(SECRET_PROMPT) in raw


async def test_audit_records_the_operational_facts(tmp_path, project, sandbox_home):
    reg = JobRegistry()
    job = reg.create(prompt="do a thing", project=str(project), driver="mock")
    await Worker(job.id, registry=reg, driver=MockDriver(job.id), poll=0.01).run()

    events = [json.loads(line) for line in _raw_log(sandbox_home).splitlines() if line.strip()]
    kinds = [e["event"] for e in events]
    assert "job_start" in kinds
    assert "job_done" in kinds
    start = next(e for e in events if e["event"] == "job_start")
    assert start["job"] == job.id
    assert start["mode"] == "single"
    assert start["agents"] == 1
    assert len(start["prompt_sha256"]) == 64
    assert "prompt" not in start  # never the raw text


async def test_failures_are_logged_with_a_code_only(tmp_path, project, sandbox_home):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    final = await Worker(
        job.id, registry=reg, driver=MockDriver(job.id, scenario="fail"), poll=0.01
    ).run()
    assert final.state == st.FAILED
    events = [json.loads(line) for line in _raw_log(sandbox_home).splitlines() if line.strip()]
    failed = [e for e in events if e["event"] == "job_failed"]
    assert failed and failed[-1]["code"]
    assert "error" not in failed[-1]  # no message text, just the code


async def test_cookies_never_reach_the_audit_log(tmp_path, project, sandbox_home):
    """The vault holds real cookie values; none may appear in the log."""
    sentinel_cookie = "COOKIE-SENTINEL-DO-NOT-LOG"
    Vault(sandbox_home).save(
        {"hatch_sess": sentinel_cookie, "hatch_native_auth_device": "dev-sentinel"},
        {"account": "someone@example.test"},
    )
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    await Worker(job.id, registry=reg, driver=MockDriver(job.id), poll=0.01).run()

    raw = _raw_log(sandbox_home)
    assert sentinel_cookie not in raw
    assert "dev-sentinel" not in raw
    assert "hatch_sess" not in raw  # not even the cookie name is worth logging


def test_vault_errors_do_not_include_values(sandbox_home, monkeypatch):
    """A failing keychain must not put the secret into the exception text."""
    sentinel = "KEYCHAIN-SENTINEL-DO-NOT-LOG"

    class ExplodingKeyring:
        def set_password(self, service, account, value):
            raise RuntimeError(f"keychain rejected {value}")

        def get_password(self, service, account):
            return None

        def delete_password(self, service, account):
            raise RuntimeError("nope")

    vault = Vault(sandbox_home, keyring_module=ExplodingKeyring())
    # save() falls back to the encrypted file rather than raising with the value.
    vault.save({"hatch_sess": sentinel}, {})
    assert vault.load()["cookies"]["hatch_sess"] == sentinel
    # And the encrypted file must not contain the plaintext.
    assert sentinel.encode() not in sandbox_home.session_file.read_bytes()


def test_audit_log_is_append_only(sandbox_home):
    log = AuditLog(sandbox_home)
    log.write("one", job="a")
    log.write("two", job="b")
    lines = _raw_log(sandbox_home).splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "one"
    assert json.loads(lines[1])["event"] == "two"


@pytest.mark.parametrize("field", ["files", "branch", "bytes"])
async def test_done_event_includes_counts_not_names(tmp_path, project, sandbox_home, field):
    reg = JobRegistry()
    job = reg.create(prompt="x", project=str(project), driver="mock")
    await Worker(job.id, registry=reg, driver=MockDriver(job.id), poll=0.01).run()
    events = [json.loads(line) for line in _raw_log(sandbox_home).splitlines() if line.strip()]
    done = [e for e in events if e["event"] == "job_done"]
    assert done and field in done[-1]
