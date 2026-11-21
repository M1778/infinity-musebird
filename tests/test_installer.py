from __future__ import annotations

import json
from pathlib import Path

import pytest

from musebird.installer import backup, merge
from musebird.installer.detect import claude, codex, opencode
from musebird.installer.installer import doctor, install, uninstall


def _claude_cfg(home: Path) -> Path:
    return home / ".claude.json"


def test_merge_json_is_idempotent_and_preserves(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    assert merge.merge_json_server(cfg) is True
    assert merge.merge_json_server(cfg) is False
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"]["command"] == "x"
    assert data["mcpServers"]["musebird"]["args"] == ["mcp"]
    assert merge.remove_json_server(cfg) is True
    assert "musebird" not in json.loads(cfg.read_text())["mcpServers"]


def test_merge_json_rejects_non_object(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text("[1,2]")
    with pytest.raises(ValueError):
        merge.merge_json_server(cfg)


def test_toml_merge_idempotent_preserves_other_sections(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[model]\nname = "x"\n\n[mcp_servers.other]\ncommand = "y"\n')
    assert merge.merge_toml_server(cfg) is True
    assert merge.merge_toml_server(cfg) is False
    text = cfg.read_text()
    assert '[model]' in text and '[mcp_servers.other]' in text
    assert merge.read_toml_section(cfg)["command"] == "musebird"
    assert merge.remove_toml_server(cfg) is True
    assert merge.read_toml_section(cfg) is None


def test_install_registers_all_three_with_backups(sandbox_home, tmp_path):
    # pretend all harnesses exist with existing config
    (sandbox_home.root / ".claude").mkdir(exist_ok=True)
    _claude_cfg(sandbox_home.root).write_text(json.dumps({"theme": "x"}))
    (sandbox_home.root / ".codex").mkdir(exist_ok=True)
    (sandbox_home.root / ".codex" / "config.toml").write_text('[model]\nname="y"\n')
    oc = sandbox_home.root / ".config" / "opencode"
    oc.mkdir(parents=True)
    (oc / "opencode.json").write_text(json.dumps({"$schema": "https://opencode.ai/config.json",
                                                  "theme": "z"}))

    report = install(paths_=sandbox_home)
    names = {h["harness"] for h in report.harnesses}
    assert names == {"claude", "codex", "opencode"}, report.to_dict()

    # claude
    data = json.loads(_claude_cfg(sandbox_home.root).read_text())
    assert data["theme"] == "x"
    assert data["mcpServers"]["musebird"]["command"] == "musebird"
    # codex
    codex_cfg = sandbox_home.root / ".codex" / "config.toml"
    assert merge.read_toml_section(codex_cfg)["args"] == ["mcp"]
    assert '[model]' in codex_cfg.read_text()
    # opencode v2 shape
    oc_data = json.loads((oc / "opencode.json").read_text())
    assert oc_data["mcp"]["servers"]["musebird"] == {
        "type": "local", "command": ["musebird", "mcp"]
    }
    assert oc_data["theme"] == "z"

    # skills in each documented location
    assert (sandbox_home.root / ".claude" / "skills" / "inf-muse" / "SKILL.md").is_file()
    assert (sandbox_home.root / ".agents" / "skills" / "inf-muse" / "SKILL.md").is_file()
    assert (oc / "skills" / "inf-muse" / "SKILL.md").is_file()

    # backups exist for edited configs
    assert backup.backups(sandbox_home)
    # install state
    state = json.loads(sandbox_home.install_state.read_text())
    assert set(state["harnesses"]) == names

    # idempotent
    again = install(paths_=sandbox_home)
    for h in again.harnesses:
        assert all("skill install" not in c for c in h["changes"]), h


def test_dry_run_writes_nothing(sandbox_home):
    (sandbox_home.root / ".claude").mkdir(exist_ok=True)
    _claude_cfg(sandbox_home.root).write_text("{}")
    before = _claude_cfg(sandbox_home.root).read_text()
    report = install(dry_run=True, paths_=sandbox_home)
    assert report.dry_run
    assert _claude_cfg(sandbox_home.root).read_text() == before
    assert not (sandbox_home.root / ".claude" / "skills").exists()


def test_uninstall_removes_only_ours(sandbox_home, tmp_path):
    (sandbox_home.root / ".claude").mkdir(exist_ok=True)
    cfg = _claude_cfg(sandbox_home.root)
    cfg.write_text(
        json.dumps({"mcpServers": {"keepme": {"command": "k"}}})
    )
    install(paths_=sandbox_home)
    # a hand-written skill must survive
    own = sandbox_home.root / ".claude" / "skills" / "mine"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine")

    uninstall(paths_=sandbox_home)
    data = json.loads(cfg.read_text())
    assert "musebird" not in data["mcpServers"]
    assert "keepme" in data["mcpServers"]
    assert own.is_file() or (own / "SKILL.md").is_file()
    assert not (sandbox_home.root / ".claude" / "skills" / "inf-muse").exists()


def test_broken_config_does_not_kill_install(sandbox_home):
    (sandbox_home.root / ".claude").mkdir(exist_ok=True)
    _claude_cfg(sandbox_home.root).write_text("{ broken json")
    (sandbox_home.root / ".agents").mkdir(exist_ok=True)
    report = install(paths_=sandbox_home)
    claude_entry = next(h for h in report.harnesses if h["harness"] == "claude")
    assert claude_entry["ok"] is False
    assert report.manual  # actionable instruction


def test_doctor_reports_failures_with_fixes(sandbox_home):
    checks = doctor(paths_=sandbox_home)
    assert any(not c.ok for c in checks)
    for c in checks:
        if not c.ok:
            assert c.fix


def test_detect_shape(sandbox_home):
    for h in (claude(), codex(), opencode()):
        assert h.name in ("claude", "codex", "opencode")
        assert isinstance(h.home, Path)


def test_toml_merge_preserves_sections_after_ours(tmp_path):
    """Our table in the middle of the file must not swallow what follows."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[model]\nname = "x"\n\n'
        '[mcp_servers.musebird]\ncommand = "old"\nargs = []\n\n'
        '[ui]\ntheme = "dark"\n\n'
        '[mcp_servers.other]\ncommand = "y"\n'
    )
    assert merge.merge_toml_server(cfg) is True
    text = cfg.read_text()
    assert '[ui]' in text and 'theme = "dark"' in text
    assert '[mcp_servers.other]' in text
    assert text.count("[mcp_servers.musebird]") == 1
    assert merge.read_toml_section(cfg)["command"] == "musebird"

    assert merge.remove_toml_server(cfg) is True
    after = cfg.read_text()
    assert '[ui]' in after and '[mcp_servers.other]' in after
    assert "[mcp_servers.musebird]" not in after


def test_toml_merge_is_idempotent_with_following_sections(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.musebird]\ncommand = "musebird"\nargs = ["mcp"]\n\n[x]\na = 1\n')
    before = cfg.read_text()
    assert merge.merge_toml_server(cfg) is False
    assert cfg.read_text() == before or cfg.read_text().strip() == before.strip()


def test_codex_cli_is_preferred_when_it_targets_the_same_config(monkeypatch):
    """Codex rewrites its own TOML and drops tables it did not write.

    Live evidence 2026-11-21: a hand-appended [mcp_servers.musebird] vanished after
    Codex added a plugin, while [plugins.*] survived. So the adapter must let Codex
    write its own entry whenever the CLI targets the detected file.
    """
    from musebird.config import Paths
    from musebird.installer.adapters import CodexAdapter
    from musebird.installer.detect import Detected

    tmp = Path(__import__("tempfile").mkdtemp())
    (tmp / ".codex").mkdir()
    cfg = tmp / ".codex" / "config.toml"
    cfg.write_text('[plugins."x@y"]\nenabled = true\n')
    monkeypatch.setenv("CODEX_HOME", str(tmp / ".codex"))

    detected = Detected("codex", "/usr/bin/codex", tmp, cfg, True)
    adapter = CodexAdapter(detected, Paths(tmp))
    assert adapter._cli_targets_this_config() is True

    calls: list[list[str]] = []

    def fake_run(cmd, timeout=60):
        calls.append(list(cmd))

        class R:
            # `mcp get` must report "not registered" so `add` runs.
            returncode = 1 if "get" in cmd else 0

        return R()

    monkeypatch.setattr("musebird.installer.adapters._run", fake_run)
    adapter.install_mcp(dry_run=False)
    assert any("add" in c for c in calls), f"the CLI was not used: {calls}"


def test_codex_file_merge_when_the_cli_would_edit_another_file(monkeypatch, tmp_path):
    from musebird.config import Paths
    from musebird.installer.adapters import CodexAdapter
    from musebird.installer.detect import Detected

    monkeypatch.delenv("CODEX_HOME", raising=False)
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[plugins."x@y"]\nenabled = true\n')
    detected = Detected("codex", "/usr/bin/codex", tmp_path, cfg, True)
    adapter = CodexAdapter(detected, Paths(tmp_path))
    # The CLI would write the process's real ~/.codex, not this fixture.
    assert adapter._cli_targets_this_config() is False
    adapter.install_mcp(dry_run=False)
    assert "[mcp_servers.musebird]" in cfg.read_text()
