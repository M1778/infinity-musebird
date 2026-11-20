#!/usr/bin/env python
"""Scan the repository for secrets. Exit 1 and print file:line:rule on any hit.

Deliberately independent of the package's own guard so a bug in one does not hide
a leak from the other. Runs in CI and can be used as a pre-commit hook:

    python scripts/scan_repo.py --staged
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RULES = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("hatch-cookie", re.compile(r"\bhatch_(?:sess|gw|vml|native_auth_device)=[A-Za-z0-9._%-]{16,}")),
    ("basic-auth-url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{6,}@")),
]

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
             ".pytest_cache", ".ruff_cache", ".musebird"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
               ".gz", ".tar", ".zst", ".woff", ".woff2", ".ttf", ".mp4", ".lock"}
# Test fixtures intentionally contain fake values.
FAKE_OK = re.compile(r"(FAKE|REPLACE_ME|YOUR_|xxxx|EXAMPLE|placeholder|dummy)", re.I)
# A line-level escape hatch for fixtures. Only honoured under tests/, so a real
# secret cannot be silenced by adding the marker to source or docs.
ALLOW_MARKER = "secret-scan: allow"
ALLOW_MARKER_DIRS = ("tests/", "tests\\")


def candidates(staged: bool):
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             capture_output=True, text=True, check=True).stdout
        for name in out.splitlines():
            path = Path(name)
            if path.is_file():
                yield path
        return
    for path in Path(".").rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    staged = "--staged" in sys.argv
    hits = 0
    for path in candidates(staged):
        if path.suffix.lower() in SKIP_SUFFIX or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        allow_marker = str(path).startswith(ALLOW_MARKER_DIRS)
        for lineno, line in enumerate(text.splitlines(), 1):
            if FAKE_OK.search(line):
                continue
            if allow_marker and ALLOW_MARKER in text:
                continue
            for name, pattern in RULES:
                if pattern.search(line):
                    print(f"{path}:{lineno}: {name}")
                    hits += 1
                    break
    if hits:
        print(f"\n{hits} potential secret(s) found. Remove them before committing.", file=sys.stderr)
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
