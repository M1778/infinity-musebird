"""Secret scanning before any file leaves the machine.

Two layers: known credential shapes (regex) and high-entropy strings. A hit is a
hard block with the file name and offset, never the secret value itself.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Ordered by likelihood. Names come from common tooling so the message is actionable.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws-secret", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("hatch-cookie", re.compile(r"\bhatch_(?:sess|gw|vml|native_auth_device)\s*=\s*[^;\s]{8,}")),
    ("basic-auth-url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{6,}@")),
    ("generic-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b\s*[=:]\s*"
        r"['\"]([A-Za-z0-9_\-./+=]{16,})['\"]"
    )),
)

# Only unambiguous placeholders are skipped. Deliberately excludes "EXAMPLE":
# AWS's own documentation key contains it, and a doc key pasted by accident is
# still a credential-shaped string better blocked than sent.
ALLOWLIST_HINTS = ("FAKE", "REPLACE_ME", "YOUR_", "xxxx", "<", "...")


def _placeholder(value: str) -> bool:
    return any(hint in value for hint in ALLOWLIST_HINTS)
MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "kind": self.kind}


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")
_SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".zst",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mp3", ".wav", ".lock",
}


def entropy_findings(rel: str, text: str) -> list[Finding]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _placeholder(line):
            continue
        for match in _ENTROPY_TOKEN.finditer(line):
            tok = match.group(0)
            if _entropy(tok) >= 4.5:
                out.append(Finding(rel, lineno, "high-entropy"))
                break
    return out


def scan_text(rel: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if _placeholder(value):
                continue
            if kind == "generic-assignment" and match.group(1) and _entropy(match.group(1)) < 3.5:
                continue
            lineno = text.count("\n", 0, match.start()) + 1
            key = (lineno, kind)
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(rel, lineno, kind))
    findings.extend(f for f in entropy_findings(rel, text) if (f.line, f.kind) not in seen)
    return findings


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def scan_file(path: Path, root: Path) -> list[Finding]:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.name
    if path.suffix.lower() in _SKIP_EXT or path.stat().st_size > MAX_BYTES:
        return []
    data = path.read_bytes()
    if looks_binary(data):
        return []
    return scan_text(rel, data.decode("utf-8", errors="replace"))


def scan_paths(paths: list[Path], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in paths:
        if p.is_file():
            findings.extend(scan_file(p, root))
    return findings
