"""Path safety: everything that enters or leaves a project passes here.

Rules enforced:
  * upload paths resolve inside the project root (no `..`, no absolute escape)
  * symlinks are rejected, not followed
  * per-file and total size caps
  * downloaded bundle members cannot escape the landing directory
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from musebird.errors import GuardViolation

DEFAULT_MAX_FILE = 20 * 1024 * 1024
DEFAULT_MAX_TOTAL = 200 * 1024 * 1024
DEFAULT_MAX_FILES = 2000


@dataclass(frozen=True)
class Caps:
    max_file: int = DEFAULT_MAX_FILE
    max_total: int = DEFAULT_MAX_TOTAL
    max_files: int = DEFAULT_MAX_FILES


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` and require it to stay under `root` (symlink safe)."""
    root = root.resolve()
    path = Path(candidate)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if path != root and root not in path.parents:
        raise GuardViolation(f"path escapes the project root: {candidate}")
    return path


def check_upload(paths: list[Path], root: Path, caps: Caps = Caps()) -> list[Path]:
    resolved: list[Path] = []
    total = 0
    count = 0
    for raw in paths:
        p = resolve_within(root, raw)
        if p.is_symlink():
            raise GuardViolation(f"refusing to upload symlink: {raw}")
        if not p.exists():
            raise GuardViolation(f"upload path does not exist: {raw}")
        if p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
                base = Path(dirpath)
                for name in dirnames:
                    if (base / name).is_symlink():
                        raise GuardViolation(f"refusing to upload symlink: {base / name}")
                for name in filenames:
                    f = base / name
                    if f.is_symlink():
                        raise GuardViolation(f"refusing to upload symlink: {f}")
                    total, count = _account(f, total, count, caps)
                    resolved.append(f)
        else:
            total, count = _account(p, total, count, caps)
            resolved.append(p)
    if count > caps.max_files:
        raise GuardViolation(f"too many files: {count} > {caps.max_files}")
    return resolved


def _account(f: Path, total: int, count: int, caps: Caps) -> tuple[int, int]:
    size = f.stat().st_size
    if size > caps.max_file:
        raise GuardViolation(f"file too large ({size} bytes > {caps.max_file}): {f.name}")
    return total + size, count + 1


def safe_member(dest: Path, member: str) -> Path:
    """Validate a tar/zip member path before extraction."""
    if member.startswith(("/", "\\")) or ".." in Path(member).parts or ":" in member.split("/")[0]:
        raise GuardViolation(f"unsafe archive member: {member}")
    target = (dest / member).resolve()
    dest = dest.resolve()
    if target != dest and dest not in target.parents:
        raise GuardViolation(f"archive member escapes landing dir: {member}")
    return target
