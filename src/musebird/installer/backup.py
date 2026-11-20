"""Timestamped backups before any config edit, plus a manifest for review.

Each backup records the harness and original path so `uninstall` and `musebird
doctor` can explain what was touched.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from musebird.config import Paths, paths


def backup_file(target: Path, harness: str, paths_: Paths | None = None) -> Path | None:
    p = paths_ or paths()
    if not target.exists():
        return None
    p.backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = p.backups / f"{harness}-{target.name}-{stamp}.bak"
    n = 1
    while dest.exists():
        dest = p.backups / f"{harness}-{target.name}-{stamp}-{n}.bak"
        n += 1
    shutil.copy2(target, dest)
    index = p.backups / "index.jsonl"
    with open(index, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"ts": time.time(), "harness": harness, "original": str(target), "backup": str(dest)}
            )
            + "\n"
        )
    return dest


def backups(paths_: Paths | None = None) -> list[dict]:
    p = paths_ or paths()
    index = p.backups / "index.jsonl"
    if not index.exists():
        return []
    out = []
    for line in index.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
