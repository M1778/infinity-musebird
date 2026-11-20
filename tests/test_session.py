from __future__ import annotations

from musebird.session import quota as q
from musebird.session.vault import Vault, redact


def test_vault_roundtrip_and_permissions(sandbox_home):
    vault = Vault(sandbox_home)
    vault.save({"hatch_sess": "FAKE", "hatch_native_auth_device": "FAKE2"}, {"account": "a"})
    loaded = vault.load()
    assert loaded["cookies"]["hatch_sess"] == "FAKE"
    data = sandbox_home.session_file.read_bytes()
    assert b"FAKE" not in data or not data.startswith(b"MUSEBIRD1\x01")
    assert oct(sandbox_home.session_file.stat().st_mode)[-3:] == "600"


def test_vault_delete(sandbox_home):
    vault = Vault(sandbox_home)
    vault.save({"a": "b"})
    assert vault.delete() is True
    assert vault.load() is None


def test_vault_corrupt_file_returns_none(sandbox_home):
    sandbox_home.session_file.write_bytes(b"garbage")
    assert Vault(sandbox_home).load() is None


def test_redact():
    assert redact("") == ""
    assert redact("short") == "***"
    assert "***" in redact("a-very-long-secret-value")


def test_quota_parse_english():
    text = (
        "Settings\nGeneral\nUsage\nFree plan\nWeekly limit resets on Sep 30\n"
        "12.75% used\nAdditional tokens\nNever expires\n0% used (2B tokens left)\nLanguage"
    )
    result = q.parse_usage_text(text)
    assert result["found"] is True
    assert result["weekly_used_pct"] == 12.75
    assert result["resets_at"] == "Sep 30"
    assert result["extra_left"].startswith("2B")


def test_quota_parse_localized_and_fullwidth():
    text = "设置\n用量\n免费\n已使用 ３．５％\n剩余 4B"
    result = q.parse_usage_text(text)
    assert result["weekly_used_pct"] == 3.5


def test_quota_missing_percentage():
    assert q.parse_usage_text("nothing useful here")["found"] is False


def test_quota_cache_ttl(sandbox_home, monkeypatch):
    from musebird.session.keepalive import QuotaCache

    cache = QuotaCache(sandbox_home, ttl=60)
    assert cache.get() is None
    cache.put({"weekly_used_pct": 5})
    assert cache.fresh()["weekly_used_pct"] == 5
    monkeypatch.setattr("musebird.session.keepalive.time.time", lambda: 10**10)
    assert cache.fresh() is None
    assert cache.get()["weekly_used_pct"] == 5


def test_session_summary(sandbox_home):
    from musebird.session.keepalive import session_summary

    assert session_summary(sandbox_home)["stored"] is False
    Vault(sandbox_home).save({"hatch_sess": "FAKE"}, {"account": "me"})
    summary = session_summary(sandbox_home)
    assert summary["stored"] is True and summary["account"] == "me"


def test_logout_clears_session_and_browser_profile(sandbox_home):
    import asyncio

    from musebird.session.login import logout

    Vault(sandbox_home).save({"hatch_sess": "FAKE"}, {})
    profile = sandbox_home.browser_profile
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("jar")

    assert asyncio.run(logout(paths_=sandbox_home)) is True
    assert Vault(sandbox_home).load() is None
    assert not profile.exists()


def test_logout_can_keep_profile(sandbox_home):
    import asyncio

    from musebird.session.login import logout

    Vault(sandbox_home).save({"hatch_sess": "FAKE"}, {})
    profile = sandbox_home.browser_profile
    profile.mkdir(parents=True)
    asyncio.run(logout(paths_=sandbox_home, keep_profile=True))
    assert profile.exists()
