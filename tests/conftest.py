"""Shared fixtures. Every test runs against a throwaway home and project."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from musebird.config import Paths


@pytest.fixture(autouse=True)
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MUSEBIRD_HOME", str(home))
    monkeypatch.setenv("MUSEBIRD_HOME_TEST_USER", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return Paths(home)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "project"
    proj.mkdir()
    monkeypatch.setenv("MUSEBIRD_PROJECT", str(proj))
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture()
def git_project(project: Path) -> Path:
    import subprocess

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True, env=env)
    (project / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=project, check=True, env=env)
    return project
