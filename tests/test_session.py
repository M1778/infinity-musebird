from __future__ import annotations

from musebird.session import quota as q
from musebird.session.vault import FILE_ONLY, Vault, redact


def test_vault_roundtrip_and_permissions(sandbox_home):
    # Pin the file backend: a real OS keychain (macOS/Windows CI) would store the
    # session outside the home dir and this test is about the encrypted file.
    vault = Vault(sandbox_home, keyring_module=FILE_ONLY)
    assert vault.backend == "file"
    vault.save({"hatch_sess": "FAKE-SENTINEL", "hatch_native_auth_device": "FAKE2"}, {"account": "a"})
    assert vault.load()["cookies"]["hatch_sess"] == "FAKE-SENTINEL"
    data = sandbox_home.session_file.read_bytes()
    assert data.startswith(b"MUSEBIRD1")
    assert b"FAKE-SENTINEL" not in data, "session file must be encrypted"
    if __import__("os").name == "posix":
        assert oct(sandbox_home.session_file.stat().st_mode)[-3:] == "600"


def test_vault_refuses_to_claim_keyring_without_one(sandbox_home, monkeypatch):
    """The fail backend reports as keyring but stores nothing; must fall back to file."""
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    from musebird.session import vault as vault_module

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    assert vault_module._usable_keyring() is None
    assert Vault(sandbox_home).backend == "file"


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
    cache.put({"used": 5.0, "limit": 100.0})
    assert cache.fresh()["used"] == 5.0
    # Shift only the cache's view of now. Patching `time.time` patches the global
    # module, which freezes every timer in the process (it starved pytest-timeout on
    # Windows and hung CI).
    monkeypatch.setattr(cache, "_now", lambda: 10**20)
    assert cache.fresh() is None
    assert cache.get()["used"] == 5.0


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


def test_keyring_backend_is_used_when_available(sandbox_home):
    class FakeKeyring:
        def __init__(self):
            self.data = {}

        def set_password(self, service, account, value):
            self.data[(service, account)] = value

        def get_password(self, service, account):
            return self.data.get((service, account))

        def delete_password(self, service, account):
            self.data.pop((service, account), None)

    kr = FakeKeyring()
    vault = Vault(sandbox_home, keyring_module=kr)
    assert vault.backend == "keyring"
    vault.save({"hatch_sess": "FAKE"}, {"account": "a"})
    assert vault.load()["cookies"]["hatch_sess"] == "FAKE"
    assert not sandbox_home.session_file.exists()  # nothing hits the disk
    assert vault.delete() is True
    assert vault.load() is None


def test_keyring_failure_falls_back_to_encrypted_file(sandbox_home):
    class Broken:
        def set_password(self, *a):
            raise RuntimeError("locked")

    vault = Vault(sandbox_home, keyring_module=Broken())
    vault.save({"hatch_sess": "FAKE"}, {})
    assert sandbox_home.session_file.exists()
    # reading goes through the file because the keychain returned nothing
    assert Vault(sandbox_home, keyring_module=FILE_ONLY).load()["cookies"]["hatch_sess"] == "FAKE"
