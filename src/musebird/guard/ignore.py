"""`.musebirdignore` handling: gitignore-style patterns with Secure defaults.

We do not depend on `pathspec`; the subset of gitignore that matters here is
small and implementing it keeps the trust boundary auditable. Supported:
comments, negation, anchoring with `/`, directory-only `dir/`, `*`/`?`/`**`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    ".git/",
    ".musebird/",
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    "*.pyc",
    ".DS_Store",
    "*.cookies*",
    "session*",
    ".aws/",
    ".ssh/",
    ".gnupg/",
    "*.kdbx",
    "data/",
    "landing/",
)


@dataclass(frozen=True)
class Rule:
    pattern: str
    negate: bool
    dir_only: bool
    anchored: bool

    def matches(self, rel: str, is_dir: bool) -> bool:
        """Match the path itself or any ancestor directory against the pattern."""
        parts = rel.split("/")
        for index, part in enumerate(parts):
            target_is_dir = index < len(parts) - 1 or is_dir
            if self.dir_only and not target_is_dir:
                continue
            candidate = "/".join(parts[: index + 1]) if self.anchored else part
            if fnmatch.fnmatchcase(candidate, self.pattern):
                return True
        return False


def parse(text: str) -> list[Rule]:
    rules = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        rules.append(
            Rule(pattern=line.lstrip("/"), negate=negate, dir_only=dir_only, anchored=anchored)
        )
    return rules


class Ignore:
    def __init__(self, project: Path) -> None:
        self.project = project
        text = ""
        cfg = project / ".musebirdignore"
        if cfg.is_file():
            text = cfg.read_text(encoding="utf-8", errors="replace")
        # Two separate rule sets: a project file may freely ignore *more*, but it
        # cannot un-ignore something the secure defaults block. Otherwise a cloned
        # repo could ship `!.env` and have the tool upload secrets.
        self.secure_rules = parse("\n".join(DEFAULT_PATTERNS))
        self.rules = parse(text)

    def ignored(self, path: Path) -> bool:
        try:
            rel = path.resolve().relative_to(self.project.resolve()).as_posix()
        except ValueError:
            return True  # outside the project: always ignored for upload
        is_dir = path.is_dir()
        for rule in self.secure_rules:
            if not rule.negate and rule.matches(rel, is_dir):
                return True  # secure defaults always win
        decision = False
        for rule in self.rules:
            if rule.matches(rel, is_dir):
                decision = not rule.negate
        return decision

    def filter(self, paths: list[Path]) -> tuple[list[Path], list[Path]]:
        keep, dropped = [], []
        for p in paths:
            (dropped if self.ignored(p) else keep).append(p)
        return keep, dropped


_HEX = re.compile(r"^[0-9a-f]+$")
