"""Harness detection. Every check is a path/PATH probe; no harness is executed."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HARNESSES = ("claude", "codex", "opencode")


@dataclass(frozen=True)
class Detected:
    name: str
    binary: str | None
    home: Path
    config: Path | None
    installed: bool

    def to_dict(self) -> dict:
        return {
            "harness": self.name,
            "binary": self.binary,
            "home": str(self.home),
            "config": str(self.config) if self.config else None,
            "installed": self.installed,
        }


def user_home() -> Path:
    return Path(os.environ.get("MUSEBIRD_HOME_TEST_USER") or Path.home()).expanduser()


def _config_home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or user_home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or user_home() / ".config")


def claude() -> Detected:
    home = user_home()
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home) / ".claude.json"
    binary = shutil.which("claude")
    return Detected(
        "claude", binary, home, cfg if cfg.exists() else (cfg if binary else None), bool(binary or (home / ".claude").is_dir() or cfg.exists())
    )


def codex() -> Detected:
    home = user_home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    binary = shutil.which("codex")
    return Detected(
        "codex", binary, home, codex_home / "config.toml",
        bool(binary or codex_home.is_dir()),
    )


def opencode() -> Detected:
    home = user_home()
    base = _config_home() / "opencode"
    json_cfg = base / "opencode.json"
    jsonc_cfg = base / "opencode.jsonc"
    cfg = json_cfg if json_cfg.exists() else jsonc_cfg if jsonc_cfg.exists() else json_cfg
    binary = shutil.which("opencode")
    return Detected("opencode", binary, home, cfg, bool(binary or base.is_dir()))


def all_harnesses() -> list[Detected]:
    return [claude(), codex(), opencode()]


def selected(names: str | None) -> list[Detected]:
    found = all_harnesses()
    if not names:
        return [h for h in found if h.installed]
    wanted = {n.strip().lower() for n in names.split(",") if n.strip()}
    unknown = wanted - set(HARNESSES)
    if unknown:
        raise ValueError(f"unknown harness(es): {', '.join(sorted(unknown))}")
    return [h for h in found if h.name in wanted]


def skill_dirs(h: Detected) -> list[Path]:
    """Where skills go, most-preferred first.

    Codex loads `~/.agents/skills` (documented) and also `$CODEX_HOME/skills`
    (works, undocumented) -> install to the documented one only.
    OpenCode discovers `~/.config/opencode/skills` plus `~/.claude/skills` and
    `~/.agents/skills` for compatibility, so one install covers all three.
    """
    if h.name == "claude":
        return [Path(os.environ.get("CLAUDE_CONFIG_DIR") or h.home) / ".claude" / "skills"]
    if h.name == "codex":
        return [h.home / ".agents" / "skills"]
    return [_config_home() / "opencode" / "skills"]
