"""Packaging guards.

Two failure modes are easy to ship and hard to notice:

1. A subpackage missing from the wheel because `.gitignore` matched its directory
   name (`session*` once excluded `src/musebird/session/` entirely).
2. Bundled skills not resolvable after install, so `musebird install` finds nothing
   to copy into a harness.

These tests inspect the source tree and the built wheel when one exists, and fail
fast with the reason.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "musebird"
EXPECTED_SUBPACKAGES = (
    "drivers",
    "drivers/browser",
    "guard",
    "installer",
    "jobs",
    "prompts",
    "session",
    "sync",
    "upstream",
)


@pytest.mark.parametrize("sub", EXPECTED_SUBPACKAGES)
def test_subpackage_has_init(sub):
    assert (SRC / sub / "__init__.py").is_file(), f"missing package marker: {sub}"


@pytest.mark.parametrize("sub", EXPECTED_SUBPACKAGES)
def test_subpackage_is_not_gitignored(sub):
    """A .gitignore rule must never shadow a source package."""
    target = SRC / sub / "__init__.py"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", str(target)],
        cwd=REPO,
        capture_output=True,
    )
    if result.returncode == 128:  # not a git checkout (sdist install)
        pytest.skip("not a git checkout")
    assert result.returncode != 0, f"{target} is gitignored; it would vanish from the sdist"


def test_bundled_skills_resolve():
    from musebird.installer.adapters import packaged_skills

    root = packaged_skills()
    for name in ("inf-muse", "inf-muse-tokenburner"):
        skill = root / name / "SKILL.md"
        assert skill.is_file(), f"missing bundled skill: {skill}"
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---"), "SKILL.md needs frontmatter"
        assert "name:" in text.split("---")[1]
        assert "description:" in text.split("---")[1]


def test_wheel_contains_every_module(tmp_path):
    """Build the wheel if the build backend is available and inspect its contents."""
    dist = REPO / "dist"
    wheels = sorted(dist.glob("*.whl")) if dist.is_dir() else []
    if not wheels:
        pytest.skip("no wheel built; run `uv build` to enable this check")
    wheel = wheels[-1]
    names = zipfile.ZipFile(wheel).namelist()
    for sub in EXPECTED_SUBPACKAGES:
        assert f"musebird/{sub}/__init__.py" in names, f"{sub} missing from {wheel.name}"
    assert "musebird/prompts/contract.md" in names
    assert "musebird/skills/inf-muse/SKILL.md" in names


def test_cli_entrypoint_importable():
    from musebird.cli import build_parser

    parser = build_parser()
    assert parser.prog == "musebird"
    # every documented command must exist
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    commands = set(actions[0].choices)
    for name in (
        "install", "update", "uninstall", "login", "logout", "status", "doctor",
        "mcp", "jobs", "job-run",
    ):
        assert name in commands, f"missing command: {name}"


def test_no_source_file_is_gitignored():
    result = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "src"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    ignored = [line for line in result.stdout.splitlines() if line.endswith(".py")]
    assert not ignored, f"source files are gitignored and would be lost: {ignored}"


def test_python_version_floor_is_declared():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in pyproject
    assert sys.version_info >= (3, 10)
