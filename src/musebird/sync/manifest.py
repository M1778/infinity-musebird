"""MANIFEST.json parsing and integrity verification.

The manifest is untrusted input from a remote sandbox. Paths are validated
against traversal and each file's size and sha256 are checked before landing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from musebird.errors import ManifestError
from musebird.guard.paths import safe_member


@dataclass(frozen=True)
class Entry:
    path: str
    sha256: str
    size: int
    summary: str = ""


@dataclass(frozen=True)
class VerifyResult:
    entries: list[Entry]
    verified: list[str]
    mismatched: list[dict]

    @property
    def ok(self) -> bool:
        return not self.mismatched


def load(path: Path) -> list[Entry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    raw = data.get("files") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ManifestError("manifest must contain a 'files' list")
    entries: list[Entry] = []
    for item in raw:
        if not isinstance(item, dict) or "path" not in item:
            raise ManifestError("manifest entry missing 'path'")
        rel = str(item["path"]).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise ManifestError(f"unsafe manifest path: {rel!r}")
        entries.append(
            Entry(
                path=rel,
                sha256=str(item.get("sha256", "")).lower(),
                size=int(item.get("size", 0)),
                summary=str(item.get("summary", ""))[:200],
            )
        )
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path, entries: list[Entry]) -> VerifyResult:
    verified: list[str] = []
    mismatched: list[dict] = []
    for entry in entries:
        target = safe_member(root, entry.path)
        if not target.is_file():
            mismatched.append({"path": entry.path, "reason": "missing"})
            continue
        size = target.stat().st_size
        if entry.size and size != entry.size:
            mismatched.append({"path": entry.path, "reason": f"size {size} != {entry.size}"})
            continue
        if entry.sha256 and sha256_file(target) != entry.sha256:
            mismatched.append({"path": entry.path, "reason": "sha256 mismatch"})
            continue
        verified.append(entry.path)
    return VerifyResult(entries=entries, verified=verified, mismatched=mismatched)


def find_manifest(root: Path, name: str = "MANIFEST.json") -> Path | None:
    direct = root / name
    if direct.is_file():
        return direct
    for candidate in sorted(root.rglob(name)):
        if candidate.is_file():
            return candidate
    return None
