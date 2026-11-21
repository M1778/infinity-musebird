"""Per-harness adapters. One object per harness, three operations: install, uninstall, verify.

Each adapter prefers the harness's own CLI when the binary exists (so we never race
the app's own config writer), then falls back to a backed-up, merge-only file edit
written from the verified formats in docs/HARNESS_NOTES.md.

Adapter contract:
  * never overwrite unrelated configuration
  * take a timestamped backup before the first write
  * return a Change list so `--dry-run` can print exactly what would happen
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from musebird.config import Paths
from musebird.installer import backup, merge
from musebird.installer.detect import Detected, skill_dirs

SKILL_NAMES = ("inf-muse", "inf-muse-tokenburner")


@dataclass
class Change:
    action: str
    target: str | Path
    detail: str = ""

    def __str__(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.action}: {self.target}{suffix}"


@dataclass
class Result:
    harness: str
    ok: bool = True
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    def add(self, action: str, target: str | Path, detail: str = "") -> None:
        self.changes.append(Change(action, str(target), detail))


def packaged_skills() -> Path:
    """Skills shipped inside the wheel, with a repo checkout as fallback."""
    here = Path(__file__).resolve().parents[2]
    bundled = here / "musebird" / "skills"
    if bundled.is_dir():
        return bundled
    repo = here.parent / "skills"
    if repo.is_dir():
        return repo
    raise FileNotFoundError("bundled skills not found; reinstall the package")


class Adapter:
    name = "base"

    def __init__(self, detected: Detected, paths_: Paths) -> None:
        self.h = detected
        self.paths = paths_

    # -- to override -----------------------------------------------------
    def mcp_installed(self) -> bool:
        raise NotImplementedError

    def install_mcp(self, dry_run: bool) -> list[Change]:
        raise NotImplementedError

    def uninstall_mcp(self, dry_run: bool) -> list[Change]:
        raise NotImplementedError

    # -- shared ----------------------------------------------------------
    def install_skills(self, dry_run: bool) -> list[Change]:
        changes: list[Change] = []
        source = packaged_skills()
        for target_root in skill_dirs(self.h):
            for name in SKILL_NAMES:
                src = source / name
                if not src.is_dir():
                    continue
                dest = target_root / name
                if dest.exists():
                    same = _tree_equal(src, dest)
                    if same:
                        changes.append(Change("skill ok", dest))
                        continue
                    if not dry_run:
                        backup.backup_file(dest / "SKILL.md", self.name, self.paths)
                        shutil.rmtree(dest)
                    changes.append(Change("skill update", dest))
                else:
                    changes.append(Change("skill install", dest))
                if not dry_run:
                    target_root.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src, dest)
        return changes

    def uninstall_skills(self, dry_run: bool) -> list[Change]:
        changes: list[Change] = []
        for target_root in skill_dirs(self.h):
            for name in SKILL_NAMES:
                dest = target_root / name
                if not dest.exists():
                    continue
                if not _owned(dest, self.paths):
                    changes.append(Change("skill kept (not ours)", dest))
                    continue
                changes.append(Change("skill remove", dest))
                if not dry_run:
                    shutil.rmtree(dest, ignore_errors=True)
        return changes

    def verify(self) -> bool:
        return self.mcp_installed()


def _tree_equal(a: Path, b: Path) -> bool:
    for p in a.rglob("*"):
        if p.is_file():
            other = b / p.relative_to(a)
            if not other.is_file() or other.read_bytes() != p.read_bytes():
                return False
    return True


def _owned(dest: Path, paths_: Paths) -> bool:
    """Only delete skills we installed; never a user's hand-written skill."""
    import json

    try:
        state = json.loads(paths_.install_state.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return any(str(dest) == entry for entry in state.get("skills", []))


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        return subprocess.run(
            [exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


class ClaudeAdapter(Adapter):
    """MCP user scope lives in the top-level `mcpServers` of ~/.claude.json."""

    name = "claude"

    def _config(self) -> Path:
        return self.h.config or (self.h.home / ".claude.json")

    def mcp_installed(self) -> bool:
        try:
            data = merge.load_json(self._config())
        except (ValueError, FileNotFoundError):
            return False
        return "musebird" in (data.get("mcpServers") or {})

    def install_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        changes: list[Change] = []
        if self.mcp_installed():
            return [Change("mcp ok", cfg, "user scope")]
        if dry_run:
            return [Change("mcp add", cfg, "user scope, merge")]
        cli = _run(["claude", "mcp", "get", "musebird", "--scope", "user"])
        if cli is not None and cli.returncode == 0:
            return [Change("mcp ok", cfg, "discovered via CLI")]
        backup.backup_file(cfg, self.name, self.paths)
        try:
            changed = merge.merge_json_server(
                cfg, {"type": "stdio", "command": "musebird", "args": ["mcp"], "env": {}}
            )
        except ValueError as exc:
            raise RuntimeError(f"{cfg} is not parseable ({exc}); left untouched") from exc
        changes.append(Change("mcp add", cfg, "user scope") if changed else Change("mcp ok", cfg))
        return changes

    def uninstall_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        if not self.mcp_installed():
            return []
        if dry_run:
            return [Change("mcp remove", cfg)]
        backup.backup_file(cfg, self.name, self.paths)
        merge.remove_json_server(cfg)
        return [Change("mcp remove", cfg)]


class CodexAdapter(Adapter):
    """MCP global config: [mcp_servers.musebird] in $CODEX_HOME/config.toml.

    Prefer Codex's own CLI whenever the binary exists. Codex rewrites config.toml
    through a TOML serializer on unrelated operations (installing a plugin, for
    example), and a table we appended by hand was silently dropped that way while
    `[plugins.*]` entries survived. Letting Codex write its own entry keeps it known
    to the app instead of looking like stray text.
    """

    name = "codex"

    def _config(self) -> Path:
        return self.h.config or (self.h.home / ".codex" / "config.toml")

    def mcp_installed(self) -> bool:
        return merge.read_toml_section(self._config()) is not None

    def _cli_targets_this_config(self) -> bool:
        """True only when `codex mcp add` would edit the file we report.

        Codex always writes `$CODEX_HOME/config.toml`, falling back to the process's
        real `~/.codex`. When the detected config is elsewhere — a fixture home, or a
        CODEX_HOME override — shelling out would silently edit a different file than
        the one we verify, so the file merge is used instead.
        """
        import os

        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home, "config.toml").resolve() == self._config().resolve()
        real_home = Path(os.environ.get("HOME") or Path.home())
        return (real_home / ".codex" / "config.toml").resolve() == self._config().resolve()

    def _cli_add(self) -> bool:
        """Register via `codex mcp add`; returns True when the CLI did the work."""
        if not self.h.binary or not self._cli_targets_this_config():
            return False
        existing = _run(["codex", "mcp", "get", "musebird", "--json"], timeout=30)
        if existing is not None and existing.returncode == 0:
            return True
        added = _run(["codex", "mcp", "add", "musebird", "--", "musebird", "mcp"], timeout=60)
        return added is not None and added.returncode == 0

    def install_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        if self.mcp_installed():
            return [Change("mcp ok", cfg, "global")]
        if dry_run:
            return [Change("mcp add", cfg, "global, via codex mcp add")]
        backup.backup_file(cfg, self.name, self.paths)
        if self._cli_add():
            return [Change("mcp add", cfg, "global, via codex mcp add")]
        # No binary: fall back to a backed-up file merge.
        changed = merge.merge_toml_server(cfg, "musebird", ["mcp"])
        return [Change("mcp add", cfg, "global") if changed else Change("mcp ok", cfg)]

    def uninstall_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        if not self.mcp_installed():
            return []
        if dry_run:
            return [Change("mcp remove", cfg)]
        backup.backup_file(cfg, self.name, self.paths)
        if self._cli_targets_this_config():
            _run(["codex", "mcp", "remove", "musebird"], timeout=60)
        if self.mcp_installed():
            merge.remove_toml_server(cfg)
        return [Change("mcp remove", cfg)]


class OpenCodeAdapter(Adapter):
    """V2 config: mcp.servers.<name> = {type: local, command: [...]}."""

    name = "opencode"

    def _config(self) -> Path:
        return self.h.config or (self.h.home / ".config" / "opencode" / "opencode.json")

    def _load(self) -> dict:
        path = self._config()
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        return _parse_jsonc(text)

    def mcp_installed(self) -> bool:
        try:
            data = self._load()
        except ValueError:
            return False
        servers = (data.get("mcp") or {}).get("servers") or {}
        return "musebird" in servers

    def install_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        if self.mcp_installed():
            return [Change("mcp ok", cfg, "global")]
        if dry_run:
            return [Change("mcp add", cfg, "global, merge mcp.servers")]
        backup.backup_file(cfg, self.name, self.paths)
        try:
            data = self._load()
        except ValueError as exc:
            raise RuntimeError(f"{cfg} is not parseable ({exc}); left untouched") from exc
        data.setdefault("$schema", "https://opencode.ai/config.json")
        mcp = data.setdefault("mcp", {})
        servers = mcp.setdefault("servers", {})
        servers["musebird"] = {"type": "local", "command": ["musebird", "mcp"]}
        cfg.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg.with_suffix(cfg.suffix + ".tmp")
        tmp.write_text(_dump_jsonc(data), encoding="utf-8")
        tmp.replace(cfg)
        return [Change("mcp add", cfg, "global")]

    def uninstall_mcp(self, dry_run: bool) -> list[Change]:
        cfg = self._config()
        if not self.mcp_installed():
            return []
        if dry_run:
            return [Change("mcp remove", cfg)]
        backup.backup_file(cfg, self.name, self.paths)
        data = self._load()
        servers = (data.get("mcp") or {}).get("servers") or {}
        servers.pop("musebird", None)
        tmp = cfg.with_suffix(cfg.suffix + ".tmp")
        tmp.write_text(_dump_jsonc(data), encoding="utf-8")
        tmp.replace(cfg)
        return [Change("mcp remove", cfg)]


ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter, "opencode": OpenCodeAdapter}


def adapter_for(detected: Detected, paths_: Paths) -> Adapter:
    return ADAPTERS[detected.name](detected, paths_)


# -- JSONC (OpenCode config may contain comments) --------------------------
def _parse_jsonc(text: str) -> dict:
    import json
    import re

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|\s)//.*$", r"\1", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("config is not a JSON object")
    return data


def _dump_jsonc(data: dict) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
