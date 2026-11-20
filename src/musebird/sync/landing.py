"""Landing area and git branch creation. Never touches the working tree."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from musebird.errors import GuardViolation


def landing_root(project: Path) -> Path:
    return project / ".musebird" / "landing"


def landing_dir(project: Path, job_id: str) -> Path:
    target = landing_root(project) / job_id
    target.mkdir(parents=True, exist_ok=True)
    (landing_root(project) / ".gitignore").write_text("*\n")
    return target


def is_git_repo(project: Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, timeout=60, check=check
    )


@dataclass
class BranchResult:
    branch: str
    commit: str | None
    worktree: str | None
    message: str


def branch_name(job_id: str) -> str:
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in job_id)
    return f"musebird/{clean}"


def land_to_branch(
    project: Path,
    source: Path,
    job_id: str,
    *,
    worktree: bool = False,
) -> BranchResult:
    """Commit the *contents* of `source` onto a fresh branch off HEAD.

    Uses a temporary index and a borrowed work tree so the files land at the repo
    root on the branch, while the user's working tree, index and current branch are
    untouched even mid-edit.
    """
    if not is_git_repo(project):
        raise GuardViolation(
            "project is not a git repository; use `sync=bundle` or run `git init` first"
        )
    if not source.is_dir():
        raise GuardViolation(f"landing source is not a directory: {source}")
    branch = branch_name(job_id)
    head = _git(project, "rev-parse", "HEAD", check=False)
    parent = head.stdout.strip() if head.returncode == 0 else None

    git_dir = _git(project, "rev-parse", "--absolute-git-dir").stdout.strip()
    tmp_index = project / ".musebird" / f"index-{job_id}"
    tmp_index.unlink(missing_ok=True)
    # Supply an identity so landing works on a machine with no global git user
    # configured. It applies to this commit only and never rewrites user config.
    env = {
        **os.environ,
        "GIT_INDEX_FILE": str(tmp_index),
        "GIT_AUTHOR_NAME": "musebird",
        "GIT_AUTHOR_EMAIL": "musebird@localhost",
        "GIT_COMMITTER_NAME": "musebird",
        "GIT_COMMITTER_EMAIL": "musebird@localhost",
    }
    base = ["git", f"--git-dir={git_dir}", f"--work-tree={source}"]
    try:
        if parent:
            subprocess.run([*base, "read-tree", parent], cwd=source, env=env, check=True,
                           capture_output=True, text=True)
        # Add only the files that actually exist in the landing area, by name.
        # `git add -A -- .` would stage every parent file missing from the landing
        # directory as a deletion, so merging the review branch would wipe the repo.
        # Landing results only ever add or modify; deletion stays a human decision.
        files = [
            p.relative_to(source).as_posix()
            for p in sorted(source.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(source).parts
        ]
        for start in range(0, len(files), 200):
            subprocess.run(
                [*base, "add", "--", *files[start : start + 200]],
                cwd=source, env=env, check=True, capture_output=True, text=True,
            )
        tree = subprocess.run([*base, "write-tree"], cwd=source, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
        args = ["commit-tree", tree, "-m", f"musebird: land results for {job_id}"]
        if parent:
            args[2:2] = ["-p", parent]
        commit = subprocess.run([*base, *args], cwd=source, env=env, check=True,
                                capture_output=True, text=True).stdout.strip()
        _git(project, "branch", "-f", branch, commit)
    except subprocess.CalledProcessError as exc:
        raise GuardViolation(
            f"git could not commit the landing area: {(exc.stderr or '').strip()[:200]}"
        ) from exc
    finally:
        tmp_index.unlink(missing_ok=True)

    wt_path = None
    if worktree:
        wt_path = str(project / ".musebird" / "worktrees" / job_id)
        Path(wt_path).parent.mkdir(parents=True, exist_ok=True)
        _git(project, "worktree", "add", "--force", wt_path, branch, check=False)
    return BranchResult(
        branch=branch,
        commit=commit,
        worktree=wt_path,
        message=f"results committed to {branch}; review with `git diff HEAD...{branch}`",
    )


def cleanup_landing(project: Path, job_id: str) -> None:
    target = landing_root(project) / job_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
