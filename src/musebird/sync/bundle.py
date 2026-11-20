"""Archive extraction with traversal and symlink protection."""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

from musebird.errors import GuardViolation
from musebird.guard.paths import Caps, safe_member

MAX_ENTRIES = 5000


def _zstd_open(path: Path):
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+

        return zstd.open(path, "rb")
    except ImportError as exc:  # pragma: no cover - depends on interpreter
        raise GuardViolation(
            "this Python cannot read .tar.zst; ask Muse to produce out.tar.gz instead"
        ) from exc


def extract(archive: Path, dest: Path, caps: Caps = Caps()) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    archive = archive.resolve()
    if not archive.exists():
        raise GuardViolation(f"archive not found: {archive.name}")
    if archive.name.endswith((".tar", ".tar.gz", ".tgz")):
        return _extract_tar(archive, dest, caps, opener=None)
    if archive.name.endswith((".tar.zst", ".tzst")):
        return _extract_tar(archive, dest, caps, opener=_zstd_open)
    if archive.name.endswith(".zip"):
        return _extract_zip(archive, dest, caps)
    raise GuardViolation(f"unsupported archive type: {archive.name}")


def _extract_tar(archive: Path, dest: Path, caps: Caps, opener) -> list[str]:
    written: list[str] = []
    total = 0
    with tarfile.open(fileobj=opener(archive) if opener else None, name=None if opener else archive) as tf:
        members = tf.getmembers()
        if len(members) > MAX_ENTRIES:
            raise GuardViolation(f"archive has too many entries: {len(members)}")
        for member in members:
            target = safe_member(dest, member.name)
            if member.issym() or member.islnk():
                raise GuardViolation(f"archive contains a link: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            total += member.size
            if member.size > caps.max_file or total > caps.max_total:
                raise GuardViolation("archive exceeds size caps")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(str(target))
    return written


def _extract_zip(archive: Path, dest: Path, caps: Caps) -> list[str]:
    written: list[str] = []
    total = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise GuardViolation(f"archive has too many entries: {len(infos)}")
        for info in infos:
            target = safe_member(dest, info.filename)
            mode = info.external_attr >> 16
            # Compare the whole file-type field: S_IFREG (0o100000) also passes a
            # naive `mode & 0o120000` check, which rejected every normal archive.
            if (mode & 0o170000) == 0o120000:  # S_IFLNK
                raise GuardViolation(f"archive contains a link: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            if info.file_size > caps.max_file or total > caps.max_total:
                raise GuardViolation("archive exceeds size caps")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(str(target))
    return written
