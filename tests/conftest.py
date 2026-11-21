"""Shared fixtures. Every test runs against a throwaway home and project."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Never touch a real OS keychain while testing. On Windows, importing keyring
# probes the Credential Manager, which can block for minutes and hang CI; tests also
# must not read or write the developer's real secrets. This is read by keyring at
# import time, so it has to be set before musebird imports it.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
# Belt and braces: musebird itself refuses to import keyring when this is set, so a
# test cannot reach the host keychain even if keyring ignores the backend override.
os.environ["MUSEBIRD_DISABLE_KEYRING"] = "1"

from musebird.config import Paths


def _live_mode() -> bool:
    return os.environ.get("MUSEBIRD_LIVE") == "1"


@pytest.fixture(autouse=True)
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    # Live tests must see the real signed-in vault; a temp home would hide it.
    if _live_mode():
        real = Path(os.environ.get("MUSEBIRD_HOME") or Path.home() / ".infinity-musebird")
        monkeypatch.setenv("MUSEBIRD_HOME", str(real))
        monkeypatch.setenv("MUSEBIRD_HOME_TEST_USER", str(Path.home()))
        return Paths(real)
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


def _can_create_symlinks(tmp_path: Path) -> bool:
    """Windows refuses symlinks without developer mode or elevation."""
    try:
        (tmp_path / ".probe-link").symlink_to(tmp_path / ".probe-target")
        (tmp_path / ".probe-target").write_text("x")
        (tmp_path / ".probe-link").unlink(missing_ok=True)
        return True
    except (OSError, NotImplementedError):
        return False


@pytest.fixture()
def requires_symlinks(tmp_path: Path) -> None:
    if not _can_create_symlinks(tmp_path):
        pytest.skip("this platform/user cannot create symlinks")
