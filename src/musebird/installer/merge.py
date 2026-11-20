"""JSON/TOML merge helpers that never clobber unrelated configuration.

Both merges are idempotent: writing the same server entry twice leaves exactly one
entry. JSON keeps existing formatting semantics (2-space) but is rewritten whole,
which is why a backup is taken first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MCP_ENTRY = {"command": "musebird", "args": ["mcp"], "env": {}}
SERVER_NAME = "musebird"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} does not contain a JSON object")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def merge_json_server(path: Path, server: dict[str, Any] | None = None) -> bool:
    """Insert/refresh `mcpServers.musebird`. Returns True if the file changed."""
    data = load_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers is not an object")
    if servers.get(SERVER_NAME) == (server or MCP_ENTRY):
        return False
    servers[SERVER_NAME] = server or MCP_ENTRY
    save_json(path, data)
    return True


def remove_json_server(path: Path) -> bool:
    if not path.exists():
        return False
    data = load_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return False
    del servers[SERVER_NAME]
    if not servers:
        data.pop("mcpServers", None)
    save_json(path, data)
    return True


TOML_SECTION = "mcp_servers.musebird"


def _split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split a TOML file into (header, body lines) chunks.

    A header is the table name without brackets; `None` is the leading chunk before
    the first table (top-level keys and comments). Line-based on purpose: a regex
    over the whole file easily swallows the sections that follow ours.
    """
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            chunks.append((stripped[1:-1].strip(), [line]))
        else:
            chunks[-1][1].append(line)
    return chunks


def _render(chunks: list[tuple[str | None, list[str]]]) -> str:
    out = "".join("".join(lines) for _, lines in chunks)
    return out.rstrip() + "\n" if out else ""


def merge_toml_server(path: Path, command: str = "musebird", args: list[str] | None = None) -> bool:
    """Add or replace the `[mcp_servers.musebird]` table, preserving every other."""
    args = args or ["mcp"]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = [
        f"[{TOML_SECTION}]\n",
        f'command = "{command}"\n',
        f"args = [{', '.join(json.dumps(a) for a in args)}]\n",
    ]
    chunks = _split_sections(text)
    replaced = False
    for index, (header, old_lines) in enumerate(chunks):
        if header == TOML_SECTION:
            # Keep the blank separator the old table ended with, so re-running the
            # installer is a byte-for-byte no-op instead of a whitespace shuffle.
            trailing = [line for line in old_lines[len(block) :] if not line.strip()]
            chunks[index] = (header, block + trailing)
            replaced = True
            break
    if not replaced:
        if chunks[-1][1] and not "".join(chunks[-1][1]).endswith("\n"):
            chunks[-1][1].append("\n")
        chunks.append(("", ["\n"]))
        chunks.append((TOML_SECTION, block))
    new_text = _render(chunks)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def remove_toml_server(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    chunks = _split_sections(text)
    kept = [c for c in chunks if c[0] != TOML_SECTION]
    if len(kept) == len(chunks):
        return False
    path.write_text(_render(kept), encoding="utf-8")
    return True


def read_toml_section(path: Path) -> dict | None:
    """Minimal reader so the installer can verify its own write without tomllib typing."""
    if not path.exists():
        return None
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:  # Python 3.10 has no tomllib; the regex reader is enough here
        return _regex_toml(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data.get("mcp_servers", {}).get(SERVER_NAME)


def _regex_toml(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^\[{re.escape(TOML_SECTION)}\]\n(.*?)(?=^\[|\Z)", text)
    if not match:
        return None
    body = match.group(1)
    cmd = re.search(r'command\s*=\s*"([^"]*)"', body)
    args = re.search(r"args\s*=\s*\[(.*?)\]", body)
    return {
        "command": cmd.group(1) if cmd else None,
        "args": re.findall(r'"([^"]*)"', args.group(1)) if args else [],
    }
