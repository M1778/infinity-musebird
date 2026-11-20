from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from musebird.errors import GuardViolation, ManifestError
from musebird.sync import bundle, manifest


def _write(root: Path, rel: str, data: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_manifest_roundtrip_and_verify(tmp_path):
    _write(tmp_path, "out/a.txt", b"hello")
    entry = {
        "path": "a.txt",
        "sha256": hashlib.sha256(b"hello").hexdigest(),
        "size": 5,
        "summary": "greeting",
    }
    (tmp_path / "out" / "MANIFEST.json").write_text(json.dumps({"files": [entry]}))
    entries = manifest.load(tmp_path / "out" / "MANIFEST.json")
    result = manifest.verify(tmp_path / "out", entries)
    assert result.ok and result.verified == ["a.txt"]

    _write(tmp_path, "out/a.txt", b"tampered")
    assert not manifest.verify(tmp_path / "out", entries).ok


def test_manifest_rejects_traversal(tmp_path):
    path = tmp_path / "MANIFEST.json"
    path.write_text(json.dumps({"files": [{"path": "../evil", "sha256": "", "size": 0}]}))
    with pytest.raises(ManifestError):
        manifest.load(path)


def test_manifest_missing_and_invalid(tmp_path):
    with pytest.raises(ManifestError):
        manifest.load(tmp_path / "nope.json")
    bad = tmp_path / "MANIFEST.json"
    bad.write_text("[")
    with pytest.raises(ManifestError):
        manifest.load(bad)


def test_find_manifest_nested(tmp_path):
    nested = tmp_path / "merged"
    nested.mkdir()
    (nested / "MANIFEST.json").write_text('{"files": []}')
    assert manifest.find_manifest(tmp_path) == nested / "MANIFEST.json"


def test_tar_extract_rejects_links_and_traversal(tmp_path):
    archive = tmp_path / "out.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(GuardViolation):
        bundle.extract(archive, tmp_path / "dest")

    link_archive = tmp_path / "links.tar"
    with tarfile.open(link_archive, "w") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(GuardViolation):
        bundle.extract(link_archive, tmp_path / "dest2")


def test_zip_extract_ok_and_symlink_blocked(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("a/b.txt", "hi")
    written = bundle.extract(good, tmp_path / "land")
    assert (tmp_path / "land" / "a" / "b.txt").read_text() == "hi"
    assert written

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(GuardViolation):
        bundle.extract(evil, tmp_path / "land2")


def test_zip_oversize_blocked(tmp_path):
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w") as zf:
        zf.writestr("big.bin", b"0" * 2048)
    from musebird.guard.paths import Caps

    with pytest.raises(GuardViolation):
        bundle.extract(big, tmp_path / "land", Caps(max_file=1024))


def test_normal_zip_regular_files_are_accepted(tmp_path):
    """S_IFREG (0o100000) also intersects 0o120000 naively; only S_IFLNK is a link."""
    archive = tmp_path / "normal.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("src/app.py")
        info.external_attr = (0o100644 << 16)  # regular file, 0644
        zf.writestr(info, "print(1)\n")
    written = bundle.extract(archive, tmp_path / "land")
    assert (tmp_path / "land" / "src" / "app.py").read_text() == "print(1)\n"
    assert written


def test_zip_directory_entries_are_accepted(tmp_path):
    archive = tmp_path / "dirs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("src/")
        info.external_attr = (0o40755 << 16) | 0x10
        zf.writestr(info, "")
        zf.writestr("src/a.txt", "x")
    bundle.extract(archive, tmp_path / "land2")
    assert (tmp_path / "land2" / "src" / "a.txt").is_file()


def test_manifest_entry_without_hash_is_rejected(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    entry = manifest.Entry(path="a.txt", sha256="", size=0)
    result = manifest.verify(tmp_path, [entry])
    assert not result.ok
    assert "sha256" in result.mismatched[0]["reason"]


def test_manifest_entry_with_bad_size_is_rejected(tmp_path):
    data = b"hi"
    (tmp_path / "a.txt").write_bytes(data)
    good = hashlib.sha256(data).hexdigest()
    assert not manifest.verify(tmp_path, [manifest.Entry("a.txt", good, 99)]).ok
    assert manifest.verify(tmp_path, [manifest.Entry("a.txt", good, 2)]).ok
    assert not manifest.verify(tmp_path, [manifest.Entry("a.txt", "0" * 64, 2)]).ok


def test_unlisted_files_are_reported(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sneaky.sh").write_text("rm -rf /")
    entries = [manifest.Entry("a.txt", hashlib.sha256(b"x").hexdigest(), 1)]
    assert manifest.find_unlisted(tmp_path, entries) == ["sneaky.sh"]
