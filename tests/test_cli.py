from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ENV = ("MUSEBIRD_HOME", "MUSEBIRD_PROJECT")


def run_cli(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, "-m", "musebird", *args],
        capture_output=True, text=True, timeout=120, env=env, cwd=cwd,
    )


@pytest.fixture()
def cli_env(sandbox_home, project):
    import os

    return {**os.environ, "MUSEBIRD_HOME": str(sandbox_home.root),
            "MUSEBIRD_PROJECT": str(project)}


def test_version():
    out = run_cli("--version")
    assert out.returncode == 0
    assert "musebird" in out.stdout


def test_install_dry_run_then_install(cli_env, sandbox_home):
    (sandbox_home.root / ".claude").mkdir()
    (sandbox_home.root / ".claude.json").write_text("{}")
    dry = run_cli("install", "--dry-run", "--yes", "--skip-login", env=cli_env)
    assert dry.returncode == 0
    assert "Dry run: nothing was written" in dry.stdout
    assert json.loads((sandbox_home.root / ".claude.json").read_text()) == {}

    real = run_cli("install", "--yes", "--skip-login", env=cli_env)
    assert real.returncode == 0
    data = json.loads((sandbox_home.root / ".claude.json").read_text())
    assert "musebird" in data["mcpServers"]


def test_doctor_json(cli_env):
    out = run_cli("doctor", "--json", env=cli_env)
    checks = json.loads(out.stdout)
    assert isinstance(checks, list) and checks
    assert all({"name", "ok", "detail", "fix"} == set(c) for c in checks)
    assert out.returncode == 1  # no session, no harness registered


def test_logout_without_session(cli_env):
    out = run_cli("logout", env=cli_env)
    assert out.returncode == 0
    assert "No stored session" in out.stdout


def test_status_json_no_session(cli_env):
    out = run_cli("status", "--json", env=cli_env)
    data = json.loads(out.stdout)
    assert data["session"]["stored"] is False
    assert data["jobs"] == []


def test_jobs_list_empty(cli_env):
    out = run_cli("jobs", "list", env=cli_env)
    assert out.returncode == 0
    assert "no jobs" in out.stdout


def test_job_run_mock_driver(cli_env, sandbox_home, project):
    """Full process path: create a mock job, run the real worker entry point."""
    from musebird.jobs.registry import JobRegistry

    reg = JobRegistry()
    job = reg.create(prompt="build", project=str(project), driver="mock", sync="bundle")
    out = run_cli("job-run", job.id, env=cli_env)
    assert out.returncode == 0, out.stderr
    final = reg.get(job.id)
    assert final.state == "done"
    assert final.landing_dir
    assert (Path(final.landing_dir) / "hello.txt").is_file()

    shown = run_cli("jobs", "show", job.id, "--events", env=cli_env)
    assert shown.returncode == 0
    assert "done" in shown.stdout

    listed = run_cli("jobs", "list", env=cli_env)
    assert job.id in listed.stdout


def test_jobs_cancel(cli_env, sandbox_home, project):
    from musebird.jobs.registry import JobRegistry

    job = JobRegistry().create(prompt="x", project=str(project), driver="mock")
    out = run_cli("jobs", "cancel", job.id, env=cli_env)
    assert out.returncode == 0
    assert JobRegistry().get(job.id).state == "cancelled"


def test_unknown_harness_fails_cleanly(cli_env):
    out = run_cli("install", "--yes", "--skip-login", "--harness", "nope", env=cli_env)
    assert out.returncode != 0
